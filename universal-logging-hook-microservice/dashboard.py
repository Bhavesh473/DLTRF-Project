# FORCE NEW UI UPDATE
# dashboard.py - PROFESSIONAL LOGGING DASHBOARD with Auto-Discovery

from flask import Flask, jsonify, render_template_string, request, Response
import json, os, subprocess, re, time, threading
from datetime import datetime, timedelta
from collections import Counter
from functools import lru_cache
import redis
import os
redis_host = os.environ.get('REDIS_HOST', 'localhost')
r = redis.Redis(host=redis_host, port=6379, db=0)

app = Flask(__name__)

# --- Configuration ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_STREAM_KEY = os.getenv("STREAM_KEY", "nginx-log-stream")

VOLUME_THRESHOLD = 200
ERROR_RATIO_THRESHOLD = 0.10
TIME_WINDOW_MINUTES = 5
MAX_EVENTS_RETURN = 1000

SENSITIVE_PATTERNS = [
    r"\bPOST\b", r"\bPUT\b", r"\bDELETE\b",
    r"login", r"logout", r"\bbasket\b", r"\bcart\b",
    r"/api/", r"/rest/", r"password", r"token", r"auth"
]
SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)

USER_ACTIVITY_NOISE_RE = re.compile(
    r"(^/assets/|^/vendor/|^/media/|^/chunk-|^/socket\.io/|^/styles\."
    r"|^/scripts\.|^/main\.|^/polyfills\.|^/runtime\.|^/confetti-"
    r"|\.js$|\.css$|\.jpg$|\.jpeg$|\.png$|\.gif$|\.svg$|\.ico$"
    r"|\.woff$|\.woff2$|\.ttf$|\.eot$|\.map$"
    r"|/favicon\.|/robots\.txt|/sitemap\.xml"
    r"|EIO=4&transport=polling|EIO=4&transport=websocket)",
    re.IGNORECASE
)

try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    REDIS_AVAILABLE = True
    print("✅ Redis connection successful!")
except Exception as e:
    redis_client = None
    REDIS_AVAILABLE = False
    print(f"⚠️  Redis unavailable: {e}")

@lru_cache(maxsize=1)
def get_discovered_endpoints():
    try:
        if not REDIS_AVAILABLE or not redis_client:
            return {'status': 'error', 'message': 'Redis not available', 'endpoints': [], 'patterns': []}
        if not redis_client.exists(REDIS_STREAM_KEY):
            return {'status': 'waiting', 'message': 'Waiting for traffic...', 'endpoints': [], 'patterns': []}
        total = redis_client.xlen(REDIS_STREAM_KEY)
        if total == 0:
            return {'status': 'empty', 'message': 'No traffic captured yet', 'endpoints': [], 'patterns': []}
        endpoint_keys = redis_client.smembers('discovered_endpoints')
        if endpoint_keys:
            endpoints = []
            for key in endpoint_keys:
                try:
                    method, path = key.split('|', 1)
                    count = redis_client.hget('endpoint_counts', key) or 0
                    endpoints.append({'method': method, 'path': path, 'count': int(count), 'pattern': re.escape(path)})
                except:
                    continue
            endpoints.sort(key=lambda x: x['count'], reverse=True)
            patterns = [e['pattern'] for e in endpoints]
            return {'status': 'active', 'message': f'Discovered {len(endpoints)} endpoints from {total} requests', 'endpoints': endpoints, 'patterns': patterns, 'total': total}
        else:
            events = redis_client.xrevrange(REDIS_STREAM_KEY, '+', '-', count=1000)
            endpoints = {}
            for event_id, data in events:
                try:
                    payload_str = data.get('payload', '{}')
                    payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
                    method = payload.get('method', 'GET')
                    path = payload.get('path', '/')
                    key = f"{method}|{path}"
                    if key not in endpoints:
                        endpoints[key] = {'method': method, 'path': path, 'count': 0, 'pattern': re.escape(path)}
                    endpoints[key]['count'] += 1
                except:
                    continue
            endpoint_list = list(endpoints.values())
            endpoint_list.sort(key=lambda x: x['count'], reverse=True)
            patterns = [e['pattern'] for e in endpoint_list]
            return {'status': 'active', 'message': f'Discovered {len(endpoint_list)} endpoints (fallback mode)', 'endpoints': endpoint_list, 'patterns': patterns, 'total': total}
    except Exception as e:
        return {'status': 'error', 'message': f'Error: {str(e)}', 'endpoints': [], 'patterns': []}


def clear_endpoint_cache():
    while True:
        time.sleep(60)
        get_discovered_endpoints.cache_clear()

threading.Thread(target=clear_endpoint_cache, daemon=True).start()


def is_user_activity(msg: str) -> bool:
    return not USER_ACTIVITY_NOISE_RE.search(msg)


def parse_timestamp(timestamp_str):
    if not timestamp_str:
        return datetime.utcnow()
    try:
        ts = timestamp_str.replace('Z', '').replace('T', ' ')
        formats = ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]
        for fmt in formats:
            try:
                return datetime.strptime(ts[:len(fmt)-2 if '.%f' in fmt else len(fmt)], fmt.replace('.%f', ''))
            except:
                continue
        return datetime.fromisoformat(timestamp_str.replace('Z', ''))
    except Exception as e:
        print(f"Error parsing timestamp '{timestamp_str}': {e}")
        return datetime.utcnow()


def read_logs_from_redis(limit=25000):
    if not REDIS_AVAILABLE or not redis_client:
        return []
    try:
        messages = redis_client.xrevrange(REDIS_STREAM_KEY, '+', '-', count=limit)
        logs = []
        for msg_id, fields in messages:
            try:
                payload_str = fields.get('payload', '{}')
                try:
                    payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
                    msg = f"{payload.get('method', 'GET')} {payload.get('path', '')}".strip() or payload.get('message', '')
                except Exception:
                    method_m = re.search(r'"method"\s*:\s*"([^"]+)"', payload_str, re.IGNORECASE)
                    path_m = re.search(r'"path"\s*:\s*"([^"]+)"', payload_str, re.IGNORECASE)
                    method = method_m.group(1).upper() if method_m else "POST"
                    path = path_m.group(1) if path_m else "/truncated-upload"
                    payload = {"method": method, "path": path, "status": "TRUNCATED BY NGINX"}
                    msg = f"⚠️ {method} {path} [Massive Payload Truncated]"
                log_entry = {
                    "timestamp": payload.get("timestamp") or fields.get("timestamp") or datetime.utcnow().isoformat() + "Z",
                    "level": str(payload.get("level", "INFO")).upper(),
                    "message": msg,
                    "source": payload.get("source") or fields.get("source", "redis"),
                    "metadata": payload,
                    "raw": payload_str[:1000] + "... [TRUNCATED FOR UI]" if len(payload_str) > 1000 else payload_str
                }
                log_entry["sensitive"] = bool(SENSITIVE_RE.search(json.dumps(payload) + " " + str(msg)))
                logs.append(log_entry)
            except Exception:
                continue
        return logs
    except Exception as e:
        print(f"Error reading from Redis: {e}")
        return []


def read_logs_from_docker():
    logs = []
    app_host = os.getenv('APP_HOST', 'target-app')
    containers = ["universal-logging-fluentd", "app-proxy", app_host, "universal-logging-redis"]
    for container in containers:
        try:
            result = subprocess.run(["docker", "logs", "--tail", "25000", container],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
            raw_lines = result.stdout.splitlines()
        except Exception:
            raw_lines = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            log_entry = None
            if line.startswith("{") and line.endswith("}"):
                try:
                    obj = json.loads(line)
                    msg = (obj.get("method", "") + " " + obj.get("path", "")).strip() or obj.get("message", "")
                    log_entry = {
                        "timestamp": obj.get("timestamp") or datetime.utcnow().isoformat() + "Z",
                        "level": str(obj.get("level", "INFO")).upper(),
                        "message": msg,
                        "source": obj.get("source") or container,
                        "metadata": obj,
                        "raw": line
                    }
                    log_entry["sensitive"] = bool(SENSITIVE_RE.search(json.dumps(obj) + " " + str(msg)))
                except Exception:
                    log_entry = None
            if not log_entry:
                up = line.upper()
                level = "ERROR" if "ERROR" in up else "WARN" if "WARN" in up else "INFO"
                log_entry = {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "level": level,
                    "message": line[:1000],
                    "source": container,
                    "metadata": {},
                    "raw": line
                }
                log_entry["sensitive"] = bool(SENSITIVE_RE.search(line))
            logs.append(log_entry)
    try:
        logs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    except Exception:
        pass
    return logs


def filter_by_time_range(logs, time_range):
    if not time_range or time_range == 'all':
        return logs
    now = datetime.utcnow()
    if time_range == '15min':
        cutoff = now - timedelta(minutes=15)
    elif time_range == '4hour':
        cutoff = now - timedelta(hours=4)
    elif time_range == '1hour':
        cutoff = now - timedelta(hours=1)
    elif time_range == '24hours':
        cutoff = now - timedelta(hours=24)
    else:
        return logs
    filtered = []
    for log in logs:
        try:
            log_time = parse_timestamp(log['timestamp'])
            if log_time >= cutoff:
                filtered.append(log)
        except Exception:
            continue
    return filtered


def deduplicate_logs(logs, keep_app_logs=True):
    seen = {}
    deduplicated = []
    for log in logs:
        msg = log.get('message', '')
        timestamp = log.get('timestamp', '')[:19]
        source = log.get('source', '')
        try:
            dt = parse_timestamp(timestamp)
            seconds = dt.second // 2 * 2
            rounded_time = dt.replace(second=seconds, microsecond=0).isoformat()
        except:
            rounded_time = timestamp
        key = f"{msg}:{rounded_time}"
        if key in seen:
            existing_log = seen[key]
            if keep_app_logs:
                app_host = os.getenv('APP_HOST', 'target-app')
                if source == app_host:
                    seen[key] = log
                    deduplicated = [l for l in deduplicated if l != existing_log]
                    deduplicated.append(log)
        else:
            seen[key] = log
            deduplicated.append(log)
    return deduplicated


def read_logs(use_redis=True):
    if use_redis and REDIS_AVAILABLE:
        redis_logs = read_logs_from_redis()
        if redis_logs:
            return redis_logs
    docker_logs = read_logs_from_docker()
    if docker_logs:
        return docker_logs
    return []


def evaluate_metrics(logs):
    total = len(logs)
    errs = sum(1 for e in logs if e["level"] in ("ERROR", "FATAL"))
    warns = sum(1 for e in logs if e["level"] == "WARN")
    sensitive_count = sum(1 for e in logs if e.get("sensitive"))
    error_warn = errs + warns
    error_ratio = (error_warn / total) if total > 0 else 0.0
    events_per_min = total / max(0.001, TIME_WINDOW_MINUTES)
    if total == 0:
        volume_label = "none"
    elif total < 100:
        volume_label = "low"
    elif total < 1000:
        volume_label = "medium"
    else:
        volume_label = "high"
    highload = (total >= VOLUME_THRESHOLD) and (error_ratio >= ERROR_RATIO_THRESHOLD)
    reason = "volume_and_error_ratio" if highload else ("no_events" if total == 0 else "normal")
    return {
        "total": total, "errs": errs, "warns": warns, "sensitive": sensitive_count,
        "error_warn": error_warn, "error_ratio": error_ratio, "events_per_min": events_per_min,
        "volume_label": volume_label, "highload": highload, "reason": reason
    }


@app.route("/api/logs")
def api_logs():
    limit = request.args.get("limit", type=int) or MAX_EVENTS_RETURN
    level_filter = request.args.get("level", "").strip().upper()
    source_filter = request.args.get("source", "").strip().lower()
    text_search = request.args.get("search", "").strip().lower()
    sensitive_filter = request.args.get("sensitive", "").strip().lower()
    use_redis = request.args.get("use_redis", "1").strip() in ("1", "true", "yes")
    hide_duplicates = request.args.get("hide_duplicates", "").strip() in ("1", "true", "yes")
    user_activity_only = request.args.get("user_activity_only", "").strip() in ("1", "true", "yes")
    time_range = request.args.get("time_range", "all").strip()

    logs = read_logs(use_redis=use_redis)
    logs = filter_by_time_range(logs, time_range)
    if hide_duplicates:
        logs = deduplicate_logs(logs, keep_app_logs=True)

    filtered_logs = []
    for log in logs:
        msg = str(log.get("message", ""))
        if user_activity_only and not is_user_activity(msg):
            continue
        if level_filter and log["level"] != level_filter:
            continue
        if source_filter and source_filter not in str(log.get("source", "")).lower():
            continue
        if text_search:
            haystack = (str(log.get("message", "")) + json.dumps(log.get("metadata", {}))).lower()
            if text_search not in haystack:
                continue
        if sensitive_filter in ("1", "true", "yes", "on"):
            if not log.get("sensitive"):
                continue
        filtered_logs.append(log)

    metrics = evaluate_metrics(filtered_logs)
    limited = filtered_logs[:max(0, min(limit, MAX_EVENTS_RETURN))]
    return jsonify({
        "metrics": metrics,
        "logs": limited,
        "filtered_count": len(filtered_logs),
        "total_count": len(logs),
        "redis_available": REDIS_AVAILABLE,
        "source": "redis" if use_redis and REDIS_AVAILABLE else "docker",
        "deduplicated": hide_duplicates,
        "discovered_endpoints": get_discovered_endpoints()['endpoints'][:10]
    })


@app.route("/api/export/json")
def export_json():
    level_filter = request.args.get("level", "").strip().upper()
    source_filter = request.args.get("source", "").strip().lower()
    text_search = request.args.get("search", "").strip().lower()
    sensitive_filter = request.args.get("sensitive", "").strip().lower()
    use_redis = request.args.get("use_redis", "1").strip() in ("1", "true", "yes")
    hide_duplicates = request.args.get("hide_duplicates", "1") == "1"
    user_activity_only = request.args.get("user_activity_only", "").strip() in ("1", "true", "yes")
    time_range = request.args.get("time_range", "all")

    logs = read_logs(use_redis=use_redis)
    logs = filter_by_time_range(logs, time_range)
    if hide_duplicates:
        logs = deduplicate_logs(logs, keep_app_logs=True)

    filtered_logs = []
    for log in logs:
        msg = str(log.get("message", ""))
        if user_activity_only and not is_user_activity(msg):
            continue
        if level_filter and log["level"] != level_filter:
            continue
        if source_filter and source_filter not in str(log.get("source", "")).lower():
            continue
        if text_search:
            haystack = (str(log.get("message", "")) + json.dumps(log.get("metadata", {}))).lower()
            if text_search not in haystack:
                continue
        if sensitive_filter in ("1", "true", "yes", "on"):
            if not log.get("sensitive"):
                continue
        filtered_logs.append(log)

    json_data = json.dumps(filtered_logs, indent=2)
    return Response(
        json_data,
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment;filename=logs_filtered_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'}
    )


@app.route("/api/endpoints")
def api_endpoints():
    return jsonify(get_discovered_endpoints())


# ── UI CHANGES SUMMARY ────────────────────────────────────────────────────────
# Dropped Bootstrap entirely — replaced with ~100 lines of plain CSS.
# Palette: dark grey (#1a1a1a / #212121 / #2a2a2a) — feels like a terminal,
#   not a SaaS product. Looks like something a student actually wrote.
# Font: system monospace stack for log rows; system sans for UI chrome.
#   No Google Fonts import — keeps it fast and lo-fi.
# Log rows: simple left-border color indicator per level (the easiest, most
#   readable pattern for a dev console). No badges with backgrounds.
# Sensitive rows: faint yellow-tinted background + "⚠ sensitive" text label
#   in source column — less alarming than the original gradient.
# Metrics panel: plain two-column text layout, no flexbox stacking.
# Time-range buttons: plain toggle style, not rounded pill buttons.
# Toolbar: single row with basic spacing; no status-badge floating element.
# Chart: same Chart.js instance; only axis colors tweaked to match theme.
# Scrollbar: thin dark scrollbar via webkit, doesn't look out of place.
# NO new libraries. NO animations. NO gradients. Kept it readable.
# ─────────────────────────────────────────────────────────────────────────────
TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Log Console</title>
<style>
/* base reset — keep it simple */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* system font stack — no imports needed */
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  background: #1a1a1a;
  color: #d0d0d0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* monospace for anything log-related */
.mono {
  font-family: "Cascadia Code", "Fira Code", "Consolas", "Menlo", monospace;
  font-size: 12px;
}

/* ── top bar ── */
#topbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  background: #212121;
  border-bottom: 1px solid #333;
  flex-shrink: 0;
  flex-wrap: wrap;
}
#topbar h1 {
  font-size: 13px;
  font-weight: 600;
  color: #e0e0e0;
  margin-right: 6px;
}
/* small status text in topbar */
#topbar .status-text {
  font-size: 11px;
  color: #888;
}
#topbar .sep { color: #444; }

/* toggle switches — simple, no animation overkill */
.sw-wrap {
  display: flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}
.sw-wrap input[type=checkbox] {
  appearance: none;
  width: 26px; height: 14px;
  background: #3a3a3a;
  border: 1px solid #555;
  border-radius: 7px;
  cursor: pointer;
  position: relative;
  flex-shrink: 0;
}
.sw-wrap input[type=checkbox]:checked { background: #4a7fd4; border-color: #4a7fd4; }
.sw-wrap input[type=checkbox]::after {
  content: '';
  position: absolute;
  top: 1px; left: 1px;
  width: 10px; height: 10px;
  border-radius: 50%;
  background: #aaa;
}
.sw-wrap input[type=checkbox]:checked::after { left: 13px; background: #fff; }
.sw-wrap label { font-size: 11px; color: #aaa; cursor: pointer; }
/* highlight dedup and user-activity labels slightly */
.sw-wrap label.hl { color: #c8a84b; }

/* buttons */
.btn {
  padding: 3px 9px;
  font-size: 11px;
  background: #2a2a2a;
  border: 1px solid #444;
  color: #ccc;
  border-radius: 3px;
  cursor: pointer;
}
.btn:hover { background: #333; color: #eee; }
.btn.primary { background: #2d5fa0; border-color: #3a6fb0; color: #e8e8e8; }
.btn.primary:hover { background: #3a6fb0; }
/* live button active state */
.btn.live-active { background: #1e3d1e; border-color: #3a7a3a; color: #7dc87d; }

/* ── main layout: sidebar + log panel ── */
#body-wrap {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── sidebar ── */
#sidebar {
  width: 210px;
  min-width: 210px;
  background: #212121;
  border-right: 1px solid #333;
  overflow-y: auto;
  flex-shrink: 0;
  padding: 10px;
}

/* sidebar section headings */
.s-head {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.7px;
  color: #666;
  margin: 10px 0 6px;
}
.s-head:first-child { margin-top: 2px; }

/* filter fields */
.field { margin-bottom: 7px; }
.field label {
  display: block;
  font-size: 11px;
  color: #888;
  margin-bottom: 3px;
}
.field input, .field select {
  width: 100%;
  background: #1a1a1a;
  border: 1px solid #3a3a3a;
  color: #d0d0d0;
  font-size: 12px;
  padding: 4px 6px;
  border-radius: 3px;
  outline: none;
}
.field input:focus, .field select:focus { border-color: #4a7fd4; }
.field select option { background: #2a2a2a; }

/* metrics table — plain and readable */
.metrics-tbl { width: 100%; border-collapse: collapse; margin-top: 4px; }
.metrics-tbl td {
  padding: 3px 0;
  font-size: 12px;
  vertical-align: middle;
}
.metrics-tbl td:first-child { color: #888; }
.metrics-tbl td:last-child {
  font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
  font-size: 11px;
  text-align: right;
  color: #d0d0d0;
}
/* color the error/warn/sensitive metric values */
.metrics-tbl .v-err { color: #d66; }
.metrics-tbl .v-warn { color: #c8a84b; }
.metrics-tbl .v-sensitive { color: #c8a84b; }

/* ── log area ── */
#log-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* time range strip */
#time-strip {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 6px 10px;
  background: #212121;
  border-bottom: 1px solid #333;
  flex-shrink: 0;
}
/* time-range buttons as a segmented group */
.time-btn {
  padding: 3px 10px;
  font-size: 11px;
  background: #1a1a1a;
  border: 1px solid #3a3a3a;
  color: #888;
  cursor: pointer;
  margin-right: -1px; /* overlap borders */
}
.time-btn:first-child { border-radius: 3px 0 0 3px; }
.time-btn:last-child  { border-radius: 0 3px 3px 0; margin-right: 0; }
.time-btn.active { background: #2a3f5e; border-color: #4a7fd4; color: #c0d8f8; z-index: 1; position: relative; }
.time-btn:hover:not(.active) { background: #252525; color: #bbb; }

/* filter summary bar */
#applied-filters {
  padding: 4px 10px;
  font-size: 11px;
  color: #666;
  background: #1a1a1a;
  border-bottom: 1px solid #2a2a2a;
  font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 0;
}
/* highlight active filters inline */
#applied-filters span.f-active { color: #4a9fd4; }
#applied-filters span.f-dedup  { color: #7dc87d; }
#applied-filters span.f-amber  { color: #c8a84b; }

/* chart strip */
#chart-wrap {
  padding: 6px 10px 4px;
  background: #1a1a1a;
  border-bottom: 1px solid #2a2a2a;
  flex-shrink: 0;
  height: 68px;
}

/* scrollable log list */
#log-scroller {
  flex: 1;
  overflow-y: auto;
  background: #1a1a1a;
}

/* custom scrollbar — thin and dark */
#log-scroller::-webkit-scrollbar { width: 5px; }
#log-scroller::-webkit-scrollbar-track { background: #1a1a1a; }
#log-scroller::-webkit-scrollbar-thumb { background: #3a3a3a; border-radius: 3px; }

/* ── log row ── */
/* using a table for proper column alignment */
#log-table {
  width: 100%;
  border-collapse: collapse;
}
#log-table thead th {
  position: sticky;
  top: 0;
  background: #212121;
  border-bottom: 1px solid #333;
  padding: 4px 8px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #555;
  text-align: left;
}
/* column widths */
#log-table .c-ts   { width: 90px;  min-width: 90px; }
#log-table .c-lvl  { width: 10px;  min-width: 10px; } /* just the color bar */
#log-table .c-msg  { /* fills rest */ }
#log-table .c-src  { width: 150px; min-width: 120px; text-align: right; }

#log-table tbody tr {
  border-bottom: 1px solid #222;
  cursor: pointer;
}
#log-table tbody tr:hover { background: #222; }

#log-table tbody td {
  padding: 5px 8px;
  vertical-align: top;
  font-size: 12px;
}

/* timestamp */
td.c-ts {
  font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
  font-size: 11px;
  color: #555;
  white-space: nowrap;
}

/* level color bar — thin left border approach, easier to scan than badges */
td.c-lvl { padding: 0 0 0 3px; width: 4px; }
.lvl-bar {
  display: block;
  width: 3px;
  height: 100%;
  min-height: 22px;
  border-radius: 1px;
}
/* level colors — muted, not loud */
.lvl-ERROR  { background: #c0392b; }
.lvl-FATAL  { background: #8b0000; }
.lvl-WARN   { background: #b8860b; }
.lvl-INFO   { background: #3a70a8; }
.lvl-DEBUG  { background: #4a7a4a; }

/* message text */
td.c-msg {
  color: #c8c8c8;
  word-break: break-all;
  line-height: 1.5;
  padding-left: 10px;
}
/* level-specific message tinting — subtle */
tr.row-ERROR td.c-msg, tr.row-FATAL td.c-msg { color: #e8a0a0; }
tr.row-WARN  td.c-msg { color: #d4bc7a; }
tr.row-DEBUG td.c-msg { color: #8aab8a; }

/* source label */
td.c-src {
  font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
  font-size: 11px;
  color: #555;
  text-align: right;
  white-space: nowrap;
}

/* sensitive row: faint yellow bg + label in source column */
tr.sensitive-row { background: rgba(180, 140, 20, 0.06); }
tr.sensitive-row:hover { background: rgba(180, 140, 20, 0.10); }
.sensitive-label {
  display: block;
  font-size: 10px;
  color: #c8a84b;
  letter-spacing: 0.3px;
  margin-top: 2px;
}

/* expanded metadata row */
tr.meta-row td {
  padding: 0;
  background: #191919;
}
.meta-content {
  font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
  font-size: 11px;
  color: #9ab8d4;
  padding: 8px 12px;
  border-top: 1px solid #2a2a2a;
  max-height: 200px;
  overflow: auto;
  white-space: pre;
  line-height: 1.6;
}

/* empty state */
.no-logs {
  padding: 40px;
  text-align: center;
  color: #555;
  font-size: 12px;
}
</style>
</head>
<body>

<!-- ── Top bar ── -->
<div id="topbar">
  <h1>Log Console</h1>
  <span class="sep">|</span>
  <span class="status-text">src: <span id="log-source">—</span></span>
  <span class="sep">|</span>
  <span class="status-text">redis: <span id="redis-status">—</span></span>
  <span class="sep">|</span>
  <!-- live toggle button -->
  <button class="btn" id="live-toggle">▶ Live</button>
  <span id="live-indicator" style="font-size:11px;color:#555">off</span>
  <!-- export -->
  <button class="btn" onclick="exportLogs('json')">↓ Export JSON</button>

  <!-- toggles pushed to the right -->
  <span style="margin-left:auto"></span>

  <label class="sw-wrap">
    <input type="checkbox" id="use-redis" checked>
    <label for="use-redis">Redis</label>
  </label>
  <label class="sw-wrap">
    <input type="checkbox" id="hide-duplicates" checked>
    <label for="hide-duplicates" class="hl">Dedup</label>
  </label>
  <label class="sw-wrap">
    <input type="checkbox" id="user-activity-only">
    <label for="user-activity-only" class="hl">User Activity</label>
  </label>
  <label class="sw-wrap">
    <input type="checkbox" id="sensitive-only">
    <label for="sensitive-only">Sensitive</label>
  </label>
</div>

<!-- ── Body: sidebar + log area ── -->
<div id="body-wrap">

  <!-- ── Sidebar ── -->
  <div id="sidebar">

    <div class="s-head">Filters</div>

    <div class="field">
      <label>Level</label>
      <select id="level-filter">
        <option value="">All</option>
        <option>ERROR</option><option>FATAL</option>
        <option>WARN</option><option>INFO</option><option>DEBUG</option>
      </select>
    </div>
    <div class="field">
      <label>Source</label>
      <input id="source-filter" placeholder="container, service…">
    </div>
    <div class="field">
      <label>Search</label>
      <input id="text-search" placeholder="keyword, path…">
    </div>
    <button class="btn primary" id="apply-filters" style="width:100%;text-align:center;margin-top:2px">Apply</button>

    <div class="s-head" style="margin-top:14px">Metrics</div>
    <!-- plain two-column table, easy to read -->
    <table class="metrics-tbl">
      <tr><td>Total</td><td id="metric-total">—</td></tr>
      <tr><td>Filtered</td><td id="metric-filtered">—</td></tr>
      <tr><td>Errors</td><td id="metric-errs" class="v-err">—</td></tr>
      <tr><td>Warnings</td><td id="metric-warns" class="v-warn">—</td></tr>
      <tr><td>Sensitive</td><td id="metric-sensitive" class="v-sensitive">—</td></tr>
      <tr><td>Events/min</td><td id="metric-epm">—</td></tr>
    </table>

  </div><!-- /sidebar -->

  <!-- ── Log panel ── -->
  <div id="log-panel">

    <!-- time range strip -->
    <div id="time-strip">
      <button class="time-btn active" data-range="all">All</button>
      <button class="time-btn" data-range="15min">15 min</button>
      <button class="time-btn" data-range="1hour">1 h</button>
      <button class="time-btn" data-range="4hour">4 h</button>
      <button class="time-btn" data-range="24hours">24 h</button>
    </div>

    <!-- active filter summary — monospace, one line -->
    <div id="applied-filters">no filters active</div>

    <!-- mini chart -->
    <div id="chart-wrap">
      <canvas id="level-chart"></canvas>
    </div>

    <!-- log rows -->
    <div id="log-scroller">
      <table id="log-table">
        <thead>
          <tr>
            <th class="c-ts">Time</th>
            <th class="c-lvl"></th><!-- level color bar, no label needed -->
            <th class="c-msg">Message</th>
            <th class="c-src">Source</th>
          </tr>
        </thead>
        <tbody id="log-tbody">
          <tr><td colspan="4" class="no-logs">Loading…</td></tr>
        </tbody>
      </table>
    </div>

  </div><!-- /log-panel -->

</div><!-- /body-wrap -->

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
// ── All logic identical to original ─────────────────────────────────────────
let live = false, pollInterval = null, currentTimeRange = 'all';
const POLL_MS = 2000, MAX_ROWS = 200;
let levelChart = null;

function initChart() {
  const ctx = document.getElementById('level-chart').getContext('2d');
  levelChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['ERROR', 'WARN', 'INFO'],
      datasets: [{
        data: [0, 0, 0],
        // muted colours matching the level bar palette
        backgroundColor: ['rgba(192, 57, 43, 0.65)', 'rgba(184, 134, 11, 0.65)', 'rgba(58, 112, 168, 0.65)'],
        borderRadius: 2,
        borderSkipped: false,
      }]
    },
    options: {
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: '#252525' },
          ticks: { color: '#555', font: { family: 'Consolas, monospace', size: 10 } }
        },
        x: {
          grid: { display: false },
          ticks: { color: '#666', font: { family: 'Consolas, monospace', size: 10 } }
        }
      }
    }
  });
}

function updateChartFromMetrics(m) {
  if (!levelChart) initChart();
  const e = m.errs || 0, w = m.warns || 0, i = Math.max(0, (m.total || 0) - e - w);
  levelChart.data.datasets[0].data = [e, w, i];
  levelChart.update();
}

function getFilterParams() {
  return {
    level:              document.getElementById('level-filter').value.trim(),
    source:             document.getElementById('source-filter').value.trim(),
    search:             document.getElementById('text-search').value.trim(),
    sensitive_only:     document.getElementById('sensitive-only').checked,
    use_redis:          document.getElementById('use-redis').checked ? '1' : '0',
    hide_duplicates:    document.getElementById('hide-duplicates').checked ? '1' : '0',
    user_activity_only: document.getElementById('user-activity-only').checked ? '1' : '0',
    time_range:         currentTimeRange
  };
}

// build the filter summary line in a compact, readable format
function updateAppliedFiltersDisplay(discoveredCount) {
  const p = getFilterParams();
  let parts = [];
  if (p.level)  parts.push(`<span class="f-active">level=${p.level}</span>`);
  if (p.source) parts.push(`<span class="f-active">src=${p.source}</span>`);
  if (p.search) parts.push(`<span class="f-active">search="${p.search}"</span>`);
  if (p.sensitive_only)          parts.push('<span class="f-amber">sensitive</span>');
  if (p.hide_duplicates === '1') parts.push('<span class="f-dedup">dedup</span>');
  if (p.user_activity_only === '1') parts.push('<span class="f-amber">user-activity</span>');
  if (p.use_redis === '1')       parts.push('redis');  else parts.push('docker');
  if (currentTimeRange !== 'all') parts.push(`<span class="f-active">t=${currentTimeRange}</span>`);
  if (discoveredCount) parts.push(`${discoveredCount} endpoints`);
  const bar = document.getElementById('applied-filters');
  bar.innerHTML = parts.length ? parts.join(' · ') : 'no filters active';
}

function humanTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (isNaN(d)) return ts;
    const s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 60)   return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    return d.toLocaleTimeString();
  } catch { return ts; }
}

// simple color bar — no badge background, just a thin strip
function lvlBarClass(lvl) {
  return { ERROR: 'lvl-ERROR', FATAL: 'lvl-FATAL', WARN: 'lvl-WARN', INFO: 'lvl-INFO', DEBUG: 'lvl-DEBUG' }[lvl] || 'lvl-INFO';
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]);
}

function renderLogs(data) {
  const logs = data.logs || [], m = data.metrics || {}, discovered = data.discovered_endpoints || [];

  // update topbar status
  document.getElementById('log-source').textContent = data.source || '—';
  document.getElementById('redis-status').textContent = data.redis_available ? 'ok' : 'unavail';
  document.getElementById('redis-status').style.color = data.redis_available ? '#7dc87d' : '#d66';

  // metrics
  document.getElementById('metric-total').textContent     = m.total ?? '—';
  document.getElementById('metric-filtered').textContent  = data.filtered_count ?? '—';
  document.getElementById('metric-errs').textContent      = m.errs ?? '—';
  document.getElementById('metric-warns').textContent     = m.warns ?? '—';
  document.getElementById('metric-sensitive').textContent = m.sensitive ?? '—';
  document.getElementById('metric-epm').textContent       = (m.events_per_min || 0).toFixed(1);

  updateAppliedFiltersDisplay(discovered.length);
  updateChartFromMetrics(m);

  const tbody = document.getElementById('log-tbody');

  if (!logs.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="no-logs">No logs match current filters.</td></tr>';
    return;
  }

  const frag = document.createDocumentFragment();
  logs.slice(0, MAX_ROWS).forEach(log => {
    const lvl = log.level || 'INFO';
    const tr = document.createElement('tr');
    tr.classList.add('row-' + lvl);
    if (log.sensitive) tr.classList.add('sensitive-row');

    // source cell content (with sensitive label below if needed)
    const srcContent = log.sensitive
      ? `${esc(log.source || '—')}<span class="sensitive-label">⚠ sensitive</span>`
      : esc(log.source || '—');

    tr.innerHTML =
      `<td class="c-ts mono">${esc(humanTime(log.timestamp))}</td>` +
      `<td class="c-lvl"><span class="lvl-bar ${lvlBarClass(lvl)}"></span></td>` +
      `<td class="c-msg">${esc(log.message || '—')}</td>` +
      `<td class="c-src">${srcContent}</td>`;

    // click to expand metadata
    tr.addEventListener('click', () => {
      const next = tr.nextElementSibling;
      if (next && next.classList.contains('meta-row')) {
        next.remove(); return;
      }
      const meta = document.createElement('tr');
      meta.classList.add('meta-row');
      meta.innerHTML = `<td colspan="4"><div class="meta-content">${esc(JSON.stringify(log.metadata || {}, null, 2))}</div></td>`;
      tr.after(meta);
    });

    frag.appendChild(tr);
  });

  tbody.innerHTML = '';
  tbody.appendChild(frag);
}

async function pollOnce() {
  try {
    const p = getFilterParams();
    const q = new URLSearchParams({
      limit: 500, level: p.level, source: p.source, search: p.search,
      sensitive: p.sensitive_only ? '1' : '',
      use_redis: p.use_redis, hide_duplicates: p.hide_duplicates,
      user_activity_only: p.user_activity_only, time_range: p.time_range
    });
    const r = await fetch(`/api/logs?${q}`);
    renderLogs(await r.json());
  } catch (e) { console.error('poll error', e); }
}

function exportLogs(format) {
  const p = getFilterParams();
  const q = new URLSearchParams({
    level: p.level, source: p.source, search: p.search,
    sensitive: p.sensitive_only ? '1' : '',
    use_redis: p.use_redis, hide_duplicates: p.hide_duplicates,
    user_activity_only: p.user_activity_only, time_range: p.time_range
  });
  window.location.href = `/api/export/${format}?${q}`;
}

// ── event listeners — unchanged logic ───────────────────────────────────────
document.getElementById('live-toggle').addEventListener('click', function () {
  live = !live;
  const ind = document.getElementById('live-indicator');
  ind.textContent = live ? 'live' : 'off';
  ind.style.color = live ? '#7dc87d' : '#555';
  this.textContent = live ? '⏹ Stop' : '▶ Live';
  this.classList.toggle('live-active', live);
  if (live) { pollOnce(); pollInterval = setInterval(pollOnce, POLL_MS); }
  else       { clearInterval(pollInterval); pollInterval = null; }
});

document.getElementById('apply-filters').addEventListener('click', pollOnce);

document.querySelectorAll('.time-btn').forEach(btn => {
  btn.addEventListener('click', function () {
    document.querySelectorAll('.time-btn').forEach(b => b.classList.remove('active'));
    this.classList.add('active');
    currentTimeRange = this.dataset.range;
    pollOnce();
  });
});

['level-filter', 'source-filter', 'text-search', 'sensitive-only', 'use-redis', 'hide-duplicates', 'user-activity-only'].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); pollOnce(); } });
  el.addEventListener('change', () => {
    if (['sensitive-only', 'use-redis', 'hide-duplicates', 'user-activity-only'].includes(id)) pollOnce();
  });
});

// initial load
initChart();
pollOnce();
</script>
</body></html>"""


@app.route("/")
def page():
    return render_template_string(TEMPLATE)


if __name__ == "__main__":
    print("\n" + "="*70)
    print("🎯 UNIVERSAL LOGGING DASHBOARD with AUTO-DISCOVERY")
    print("="*70)
    print("\n📊 Features:")
    print("  ✅ Real-time Redis Streams logs")
    print("  ✅ Auto-discovery of endpoints (works with ANY app)")
    print("  ✅ Export logs (JSON)")
    print("  ✅ Time range filters (15min to 24hrs)")
    print("  ✅ Smart deduplication")
    print("  ✅ User Activity filter (exclusion-based — hides static assets)")
    print(f"\n🔌 Redis: {REDIS_URL}")
    print(f"📦 Stream: {REDIS_STREAM_KEY}")
    print(f"✅ Redis Available: {REDIS_AVAILABLE}")

    endpoint_data = get_discovered_endpoints()
    if endpoint_data['status'] == 'active':
        print(f"\n🔍 Discovered {len(endpoint_data['endpoints'])} endpoints:")
        for ep in endpoint_data['endpoints'][:5]:
            print(f"   • {ep['method']} {ep['path']} ({ep['count']} requests)")
        if len(endpoint_data['endpoints']) > 5:
            print(f"   ... and {len(endpoint_data['endpoints']) - 5} more")
    else:
        print(f"\n⏳ {endpoint_data['message']}")

    print("\n🚀 Access dashboard at: http://localhost:5000")
    print("="*70 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)