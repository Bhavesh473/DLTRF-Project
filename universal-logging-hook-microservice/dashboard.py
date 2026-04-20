# dashboard.py - PROFESSIONAL LOGGING DASHBOARD with Auto-Discovery

from flask import Flask, jsonify, render_template_string, request, Response
import json, os, subprocess, re, time, threading
from datetime import datetime, timedelta
from collections import Counter
from functools import lru_cache
import redis

app = Flask(__name__)

# --- Configuration ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_STREAM_KEY = os.getenv("STREAM_KEY", "nginx-log-stream")

VOLUME_THRESHOLD = 200
ERROR_RATIO_THRESHOLD = 0.10
TIME_WINDOW_MINUTES = 5
MAX_EVENTS_RETURN = 1000

# Sensitive patterns (generic - works with any app)
SENSITIVE_PATTERNS = [
    r"\bPOST\b", r"\bPUT\b", r"\bDELETE\b",
    r"login", r"logout", r"\bbasket\b", r"\bcart\b",
    r"/api/", r"/rest/", r"password", r"token", r"auth"
]
SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)

# ── USER ACTIVITY EXCLUSION FILTER ───────────────────────────────────────────
# When "User Activity" is ON, exclude these noise paths.
# Logic: if the path MATCHES this pattern → it is NOT user activity → hide it.
USER_ACTIVITY_NOISE_RE = re.compile(
    r"(^/assets/|^/vendor/|^/media/|^/chunk-|^/socket\.io/|^/styles\."
    r"|^/scripts\.|^/main\.|^/polyfills\.|^/runtime\.|^/confetti-"
    r"|\.js$|\.css$|\.jpg$|\.jpeg$|\.png$|\.gif$|\.svg$|\.ico$"
    r"|\.woff$|\.woff2$|\.ttf$|\.eot$|\.map$"
    r"|/favicon\.|/robots\.txt|/sitemap\.xml"
    r"|EIO=4&transport=polling|EIO=4&transport=websocket)",
    re.IGNORECASE
)
# ─────────────────────────────────────────────────────────────────────────────

# Initialize Redis
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    REDIS_AVAILABLE = True
    print("✅ Redis connection successful!")
except Exception as e:
    redis_client = None
    REDIS_AVAILABLE = False
    print(f"⚠️  Redis unavailable: {e}")

# ========================================
# AUTO-DISCOVERY FUNCTION
# ========================================
@lru_cache(maxsize=1)
def get_discovered_endpoints():
    """
    Automatically discover endpoints from Redis.
    Works with ANY application - no hardcoding!
    """
    try:
        if not REDIS_AVAILABLE or not redis_client:
            return {
                'status': 'error',
                'message': 'Redis not available',
                'endpoints': [],
                'patterns': []
            }

        if not redis_client.exists(REDIS_STREAM_KEY):
            return {
                'status': 'waiting',
                'message': 'Waiting for traffic...',
                'endpoints': [],
                'patterns': []
            }

        total = redis_client.xlen(REDIS_STREAM_KEY)

        if total == 0:
            return {
                'status': 'empty',
                'message': 'No traffic captured yet',
                'endpoints': [],
                'patterns': []
            }

        endpoint_keys = redis_client.smembers('discovered_endpoints')

        if endpoint_keys:
            endpoints = []
            for key in endpoint_keys:
                try:
                    method, path = key.split('|', 1)
                    count = redis_client.hget('endpoint_counts', key) or 0
                    endpoints.append({
                        'method': method,
                        'path': path,
                        'count': int(count),
                        'pattern': re.escape(path)
                    })
                except:
                    continue

            endpoints.sort(key=lambda x: x['count'], reverse=True)
            patterns = [e['pattern'] for e in endpoints]

            return {
                'status': 'active',
                'message': f'Discovered {len(endpoints)} endpoints from {total} requests',
                'endpoints': endpoints,
                'patterns': patterns,
                'total': total
            }

        else:
            # Fallback: Worker not running - read last 1000 events manually
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
                        endpoints[key] = {
                            'method': method,
                            'path': path,
                            'count': 0,
                            'pattern': re.escape(path)
                        }
                    endpoints[key]['count'] += 1
                except:
                    continue

            endpoint_list = list(endpoints.values())
            endpoint_list.sort(key=lambda x: x['count'], reverse=True)
            patterns = [e['pattern'] for e in endpoint_list]

            return {
                'status': 'active',
                'message': f'Discovered {len(endpoint_list)} endpoints (fallback mode)',
                'endpoints': endpoint_list,
                'patterns': patterns,
                'total': total
            }

    except Exception as e:
        return {
            'status': 'error',
            'message': f'Error: {str(e)}',
            'endpoints': [],
            'patterns': []
        }


def clear_endpoint_cache():
    while True:
        time.sleep(60)
        get_discovered_endpoints.cache_clear()

threading.Thread(target=clear_endpoint_cache, daemon=True).start()


def is_user_activity(msg: str) -> bool:
    """
    Return True if this log entry counts as a real user action.
    Strategy: exclude known noise paths; everything else is user activity.
    """
    return not USER_ACTIVITY_NOISE_RE.search(msg)


# ========================================
# END AUTO-DISCOVERY
# ========================================

def parse_timestamp(timestamp_str):
    if not timestamp_str:
        return datetime.utcnow()
    try:
        ts = timestamp_str.replace('Z', '').replace('T', ' ')
        formats = [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S"
        ]
        for fmt in formats:
            try:
                return datetime.strptime(ts[:len(fmt)-2 if '.%f' in fmt else len(fmt)], fmt.replace('.%f', ''))
            except:
                continue
        return datetime.fromisoformat(timestamp_str.replace('Z', ''))
    except Exception as e:
        print(f"Error parsing timestamp '{timestamp_str}': {e}")
        return datetime.utcnow()


# 🎯 Fetch 25,000 logs so heavy UI frameworks don't push POST requests out of the window
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
                    # 🎯 DASHBOARD RESCUE BLOCK
                    # If json.loads fails because of Nginx truncation, salvage the routing info so it shows in the UI!
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
            result = subprocess.run(
                # 🎯 Expand Docker tail to prevent blindspots
                ["docker", "logs", "--tail", "25000", container],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10
            )
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

        # ── User Activity filter: exclude static noise ────────────────────────
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


TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"/><title>Professional Log Console</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body{background:#0b1220;color:#e6eef8}.card{background:#0f1724;border:1px solid rgba(255,255,255,0.12)}.card .d-flex.gap-2 strong{color:#c5d3e8;font-size:.95rem;font-weight:600}.card .d-flex.gap-2 span{color:#fff!important;font-weight:700;font-size:1.2rem;text-shadow:0 0 4px rgba(255,255,255,.3)}.applied-filters{font-size:1rem;color:#e0f0ff!important;padding:10px;background:rgba(45,156,219,.15);border-radius:6px;border-left:3px solid #2d9cdb;font-weight:600}h6.mb-0,h6.tiny{color:#e6f4ff!important;font-size:1.1rem!important;font-weight:700!important;text-transform:uppercase;letter-spacing:.5px}#level-chart{background:rgba(255,255,255,.05);border-radius:8px;padding:10px}.badge-ERROR{background:#e02424;font-weight:700}.badge-FATAL{background:#8b0000;font-weight:700}.badge-WARN{background:#ff8c00;color:#000;font-weight:700}.badge-INFO{background:#2d9cdb;font-weight:700}.badge-DEBUG{background:#6b7280;font-weight:700}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,"Roboto Mono",monospace}.tiny{font-size:.82rem;color:#b8c9dc}.meta{font-size:.85rem;background:rgba(255,255,255,.05);padding:12px;border-radius:6px;max-height:300px;overflow-y:auto;border:1px solid rgba(255,255,255,.1)}.no-logs{padding:30px;text-align:center;color:#9fb0c9}.table-row:hover{background:rgba(255,255,255,.08);cursor:pointer}.sensitive-row{background:linear-gradient(90deg,rgba(255,255,0,.08),rgba(255,140,0,.04));border-left:4px solid rgba(255,140,0,.9)}.sensitive-tag{color:#ff8c00;font-weight:700;margin-left:8px;font-size:.9rem;background:rgba(255,140,0,.2);padding:2px 8px;border-radius:4px}.status-badge{padding:8px 14px;border-radius:6px;background:#0f1724;border:2px solid #2d9cdb;font-weight:600;font-size:.85rem;margin-right:20px}.live-on{color:#0f0;font-weight:700;font-size:1.1rem}.live-off{color:#888;font-size:1.1rem}.form-label{color:#d0e0f0!important;font-weight:600!important;font-size:.9rem!important}#sensitive-count{background:rgba(255,140,0,.25);padding:4px 12px;border-radius:6px;font-size:1rem!important}.header-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.dedup-highlight{background:rgba(0,255,0,.1);padding:2px 6px;border-radius:4px;color:#0f0;font-size:.85rem;margin-left:8px}.export-btn{background:#2d9cdb;border:none;color:#fff;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:600;margin:0 5px}.export-btn:hover{background:#1e7ba8}.time-range-btns{display:flex;gap:8px;margin-bottom:15px;flex-wrap:wrap}.time-btn{background:#0f1724;border:1px solid #2d9cdb;color:#e0f0ff;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:.85rem;font-weight:600}.time-btn.active{background:#2d9cdb;color:#fff}
</style>
</head><body>

<div class="status-badge"><span class="tiny">Source: <span id="log-source">Loading...</span> | Redis: <span id="redis-status">Checking...</span></span></div>
<div class="container-fluid p-3">
<div class="d-flex align-items-center mb-3 flex-wrap">
<h2 class="me-3 mb-0">🎯 Universal Logging Console</h2>
<div class="tiny muted me-3">Live: <span id="live-indicator" class="live-off">OFF</span></div>
<div class="header-controls ms-auto">
<button id="live-toggle" class="btn btn-sm btn-outline-light">Start Live</button>
<button class="export-btn" onclick="exportLogs('json')">📥 Export Visible Logs</button>
<div class="form-check form-switch mb-0">
<input class="form-check-input" type="checkbox" id="use-redis" checked>
<label class="form-check-label tiny" for="use-redis" style="color:#e0f0ff!important;font-weight:600">Use Redis</label>
</div>
<div class="form-check form-switch mb-0">
<input class="form-check-input" type="checkbox" id="hide-duplicates" checked>
<label class="form-check-label tiny" for="hide-duplicates" style="color:#00ff00!important;font-weight:700">Hide Duplicates</label>
</div>
<div class="form-check form-switch mb-0">
<input class="form-check-input" type="checkbox" id="user-activity-only">
<label class="form-check-label tiny" for="user-activity-only" style="color:#ffa500!important;font-weight:700">User Activity</label>
</div>
<div class="form-check form-switch mb-0">
<input class="form-check-input" type="checkbox" id="sensitive-only">
<label class="form-check-label tiny" for="sensitive-only" style="color:#e0f0ff!important;font-weight:600">Sensitive Only</label>
</div></div></div>

<div class="card p-3 mb-3">
<h6 class="tiny mb-3">⏱️ Time Range Filter</h6>
<div class="time-range-btns">
<button class="time-btn active" data-range="all">All Time</button>
<button class="time-btn" data-range="15min">Last 15 min</button>
<button class="time-btn" data-range="1hour">Last 1 hour</button>
<button class="time-btn" data-range="4hour">Last 4 hours</button>
<button class="time-btn" data-range="24hours">Last 24 hours</button>
</div>
</div>

<div class="row g-3">
<div class="col-12 col-md-3">
<div class="card p-3 mb-3">
<h6 class="tiny">🔍 Filters</h6>
<div class="mb-2"><label class="form-label tiny">Level</label>
<select id="level-filter" class="form-select form-select-sm"><option value="">All</option><option>ERROR</option><option>FATAL</option><option>WARN</option><option>INFO</option><option>DEBUG</option></select></div>
<div class="mb-2"><label class="form-label tiny">Source</label>
<input id="source-filter" class="form-control form-control-sm" placeholder="app name, redis, etc."></div>
<div class="mb-2"><label class="form-label tiny">Search</label>
<input id="text-search" class="form-control form-control-sm" placeholder="login, api, error"></div>
<button id="apply-filters" class="btn btn-sm btn-primary mt-2">Apply Filters</button>
</div>
<div class="card p-3 mb-3">
<h6 class="tiny">📊 Metrics <span id="sensitive-count"></span></h6>
<div class="d-flex gap-2 flex-column">
<div><strong>Sensitive:</strong> <span id="metric-sensitive">0</span></div>
<div><strong>Total:</strong> <span id="metric-total">0</span></div>
<div><strong>Filtered:</strong> <span id="metric-filtered">0</span></div>
<div><strong>Errors:</strong> <span id="metric-errs" style="color:#ff6b6b!important">0</span></div>
<div><strong>Warns:</strong> <span id="metric-warns" style="color:#ff8c00!important">0</span></div>
<div><strong>Events/min:</strong> <span id="metric-epm">0</span></div>
</div></div>
</div>
<div class="col-12 col-md-9">
<div class="card p-3 mb-3">
<div class="d-flex align-items-center mb-2">
<h6 class="mb-0">📝 Real-Time Log Tail</h6>
</div>
<div id="applied-filters" class="applied-filters">No filters applied • Auto-discovering endpoints...</div>
<div class="mb-3" style="max-width:500px"><canvas id="level-chart" height="90"></canvas></div>
<div id="logs-container" style="max-height:60vh;overflow:auto"></div>
</div></div></div></div>
<template id="row-tpl">
<div class="p-2 table-row" role="button" style="border-bottom:1px solid rgba(255,255,255,.05)">
<div class="d-flex">
<div style="width:140px" class="mono tiny" data-ts></div>
<div style="width:90px" class="tiny" data-level></div>
<div class="flex-fill" data-message style="padding-right:10px;color:#e6f4ff;font-weight:500"></div>
<div style="width:180px" class="tiny text-end" data-source></div>
</div>
<div class="mt-1 small meta" data-meta style="display:none"></div>
</div>
</template>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
let live=false,pollInterval=null,currentTimeRange='all';const POLL_MS=2000,MAX_ROWS=200;let levelChart=null;

function initChart(){const t=document.getElementById("level-chart").getContext("2d");levelChart=new Chart(t,{type:"bar",data:{labels:["ERROR","WARN","INFO"],datasets:[{label:"Count",data:[0,0,0],backgroundColor:["#e02424","#ff8c00","#2d9cdb"]}]},options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{color:"#b8c9dc"}},x:{ticks:{color:"#b8c9dc"}}}}})}

function updateChartFromMetrics(t){levelChart||initChart();const e=t.errs||0,r=t.warns||0,a=Math.max(0,(t.total||0)-e-r);levelChart.data.datasets[0].data=[e,r,a];levelChart.update()}

function getFilterParams(){return{level:document.getElementById("level-filter").value.trim(),source:document.getElementById("source-filter").value.trim(),search:document.getElementById("text-search").value.trim(),sensitive_only:document.getElementById("sensitive-only").checked,use_redis:document.getElementById("use-redis").checked?"1":"0",hide_duplicates:document.getElementById("hide-duplicates").checked?"1":"0",user_activity_only:document.getElementById("user-activity-only").checked?"1":"0",time_range:currentTimeRange}}

function updateAppliedFiltersDisplay(discoveredCount){const t=getFilterParams(),e=t.level||"any",r=t.source||"any",a=t.search||"*",s=t.sensitive_only?" • Sensitive only":"",i="1"===t.use_redis?" • Redis":" • Docker",o="1"===t.hide_duplicates?' • <span class="dedup-highlight">Deduplicated</span>':"",n="1"===t.user_activity_only?' • <span style="color:#ffa500">🎯 User Activity (static assets hidden)</span>':"",l=currentTimeRange!=="all"?" • ⏱️ "+currentTimeRange:"",d=discoveredCount?" • 🔍 "+discoveredCount+" endpoints":"";document.getElementById("applied-filters").innerHTML=`Filters: Level=${e} • Source=${r} • Text="${a}"${s}${i}${o}${n}${l}${d}`}

function humanTime(t){if(!t)return"---";try{const e=new Date(t);if(isNaN(e))return t;const r=Math.floor((Date.now()-e.getTime())/1e3);return r<60?r+"s ago":r<3600?Math.floor(r/60)+"m ago":e.toLocaleString()}catch(e){return t}}

function badgeFor(t){return"ERROR"===t?'<span class="badge badge-ERROR">ERROR</span>':"FATAL"===t?'<span class="badge badge-FATAL">FATAL</span>':"WARN"===t?'<span class="badge badge-WARN">WARN</span>':"INFO"===t?'<span class="badge badge-INFO">INFO</span>':'<span class="badge badge-DEBUG">DEBUG</span>'}

function escapeHtml(t){return String(t).replace(/[&<>"']/g,(t=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[t]))}

function renderLogs(t){const e=document.getElementById("logs-container"),r=t.logs||[],a=t.metrics||{},discovered=t.discovered_endpoints||[];document.getElementById("log-source").innerText=t.source||"unknown";document.getElementById("redis-status").innerHTML=t.redis_available?'<span style="color:#00ff00">✓ Available</span>':'<span style="color:#ff8c00">✗ Unavailable</span>';document.getElementById("metric-total").innerText=a.total||0;document.getElementById("metric-filtered").innerText=t.filtered_count||0;document.getElementById("metric-errs").innerText=a.errs||0;document.getElementById("metric-warns").innerText=a.warns||0;document.getElementById("metric-epm").innerText=(a.events_per_min||0).toFixed(1);document.getElementById("metric-sensitive").innerText=a.sensitive||0;document.getElementById("sensitive-count").innerText=a.sensitive?"Sensitive: "+a.sensitive:"";updateAppliedFiltersDisplay(discovered.length);updateChartFromMetrics(a);if(0===r.length){e.innerHTML='<div class="no-logs">No logs found. Try changing time range or filters.</div>';return}e.innerHTML="";r.slice(0,MAX_ROWS).forEach((t=>{const r=document.getElementById("row-tpl").content.cloneNode(!0);r.querySelector("[data-ts]").innerText=humanTime(t.timestamp);r.querySelector("[data-level]").innerHTML=badgeFor(t.level||"INFO");const a=r.querySelector("[data-message]");a.innerText=t.message||"---";a.style.whiteSpace="normal";a.style.wordWrap="break-word";r.querySelector("[data-source]").innerText=t.source||"---";const s=r.querySelector("[data-meta]");s.innerHTML='<pre style="margin:0">'+escapeHtml(JSON.stringify(t.metadata||{},null,2))+"</pre>";const i=r.querySelector(".table-row");t.sensitive&&(i.classList.add("sensitive-row"),r.querySelector("[data-source]").innerHTML+='<span class="sensitive-tag">SENSITIVE</span>');i.addEventListener("click",(()=>{s.style.display="none"===s.style.display?"block":"none"}));e.appendChild(r)}))}

async function pollOnce(){try{const t=getFilterParams(),e=new URLSearchParams({limit:500,level:t.level,source:t.source,search:t.search,sensitive:t.sensitive_only?"1":"",use_redis:t.use_redis,hide_duplicates:t.hide_duplicates,user_activity_only:t.user_activity_only,time_range:t.time_range}),r=await fetch(`/api/logs?${e}`),a=await r.json();renderLogs(a)}catch(t){console.error("poll error",t)}}

function exportLogs(format){const params=getFilterParams();const queryParams=new URLSearchParams({level:params.level,source:params.source,search:params.search,sensitive:params.sensitive_only?"1":"",use_redis:params.use_redis,hide_duplicates:params.hide_duplicates,user_activity_only:params.user_activity_only,time_range:params.time_range});const url=`/api/export/${format}?${queryParams.toString()}`;window.location.href=url}

document.getElementById("live-toggle").addEventListener("click",(function(){live=!live;const t=document.getElementById("live-indicator");t.innerText=live?"ON":"OFF";t.className=live?"live-on":"live-off";this.innerText=live?"Stop Live":"Start Live";live?(pollOnce(),pollInterval=setInterval(pollOnce,POLL_MS)):(clearInterval(pollInterval),pollInterval=null)}));

document.getElementById("apply-filters").addEventListener("click",(()=>{pollOnce()}));

document.querySelectorAll(".time-btn").forEach(btn=>{btn.addEventListener("click",function(){document.querySelectorAll(".time-btn").forEach(b=>b.classList.remove("active"));this.classList.add("active");currentTimeRange=this.dataset.range;pollOnce()})});

["level-filter","source-filter","text-search","sensitive-only","use-redis","hide-duplicates","user-activity-only"].forEach((t=>{const e=document.getElementById(t);e.addEventListener("keydown",(t=>{"Enter"===t.key&&(t.preventDefault(),pollOnce())}));e.addEventListener("change",(()=>{"sensitive-only"!==t&&"use-redis"!==t&&"hide-duplicates"!==t&&"user-activity-only"!==t||pollOnce()}))}));

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