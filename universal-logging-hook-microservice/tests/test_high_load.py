import sys
import os
import time
import subprocess
import json
from datetime import datetime, timezone

# Add python client lib to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
python_dir = os.path.join(project_root, "src", "integration", "client_libs", "python")
sys.path.insert(0, python_dir)

from universal_logger import UniversalLogger

# Configuration (GENERIC - no app-specific hardcoding)
APP_CONTAINER = os.getenv('APP_HOST', 'target-app')  # ✅ CHANGED
TAIL_LINES = 500
BATCH_SIZE = 200
PAUSE_BETWEEN_BATCHES = 0.2
HIGHLOAD_EVENTS_THRESHOLD = 200
HIGHLOAD_ERROR_RATIO = 0.10
TIME_WINDOW_MINUTES = 5

logger = UniversalLogger("http://localhost:9880")

def read_app_docker_logs(tail_lines=TAIL_LINES):  # ✅ RENAMED
    """Read logs from any application container"""
    try:
        output = subprocess.check_output(
            ["docker", "logs", APP_CONTAINER, "--tail", str(tail_lines)],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30
        )
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to read docker logs from {APP_CONTAINER}: {e}")
        return []
    except Exception as e:
        print(f"✗ Error reading docker logs: {e}")
        return []

    lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]
    return lines

def parse_line_to_event(line):
    # Try JSON first
    try:
        parsed = json.loads(line)
        level = parsed.get("level", parsed.get("severity", "INFO")).upper()
        message = parsed.get("message", line)
        timestamp = parsed.get("timestamp")
        return {"level": level, "message": message, "raw": line, "timestamp": timestamp}
    except Exception:
        # Heuristics
        up = line.upper()
        if "ERROR" in up:
            level = "ERROR"
        elif "WARN" in up or "WARNING" in up:
            level = "WARN"
        elif "FATAL" in up or "CRITICAL" in up:
            level = "FATAL"
        else:
            level = "INFO"
        return {"level": level, "message": line, "raw": line, "timestamp": None}

def make_structured_events_from_lines(lines):
    events = []
    for ln in lines:
        ev = parse_line_to_event(ln)
        events.append(ev)
    return events

def evaluate_highload(events):
    total = len(events)
    if total == 0:
        return {"highload": False, "reason": "no_events", "total": 0}

    errs = sum(1 for e in events if e["level"] in ("ERROR", "FATAL"))
    warns = sum(1 for e in events if e["level"] == "WARN")
    error_warn = errs + warns
    error_ratio = error_warn / total

    events_per_min = total / max(0.001, TIME_WINDOW_MINUTES)

    if total >= HIGHLOAD_EVENTS_THRESHOLD and error_ratio >= HIGHLOAD_ERROR_RATIO:
        return {"highload": True, "reason": "high_volume_and_error_ratio", 
                "total": total, "events_per_min": events_per_min, "error_ratio": error_ratio}

    if total >= HIGHLOAD_EVENTS_THRESHOLD * 5:
        return {"highload": True, "reason": "very_high_volume", 
                "total": total, "events_per_min": events_per_min, "error_ratio": error_ratio}

    return {"highload": False, "reason": "normal", "total": total, 
            "events_per_min": events_per_min, "error_ratio": error_ratio}

def send_logs_in_batches(events, batch_size=BATCH_SIZE):
    total = len(events)
    if total == 0:
        print("No logs found to send.")
        return

    print(f"Start streaming {total} log lines from {APP_CONTAINER} in batches of {batch_size}...")
    sent = 0

    for i in range(0, total, batch_size):
        batch = events[i:i+batch_size]
        batch_sent = 0

        for ev in batch:
            level = ev.get("level", "INFO")
            message = ev.get("message", ev.get("raw", ""))
            source = APP_CONTAINER  # ✅ DYNAMIC
            metadata = {"raw": ev.get("raw", "")}

            try:
                ok = logger.log(level, message, source, metadata)
            except Exception as e:
                ok = False
                print(f"✗ Exception while sending log: {e}")

            if ok:
                batch_sent += 1

        sent += batch_sent
        print(f"Batch {i//batch_size + 1}: sent {batch_sent}/{len(batch)}")
        time.sleep(PAUSE_BETWEEN_BATCHES)

    print(f"Streaming complete: {sent}/{total} logs sent.")

if __name__ == "__main__":
    print(f"=== High-Load Test ({APP_CONTAINER} docker logs -> UniversalLogger) ===")
    lines = read_app_docker_logs()
    events = make_structured_events_from_lines(lines)

    send_logs_in_batches(events, batch_size=BATCH_SIZE)

    result = evaluate_highload(events)
    total = result.get("total", 0)

    if total == 0:
        volume_label = "none"
    elif total < 100:
        volume_label = "low"
    elif total < 1000:
        volume_label = "medium"
    else:
        volume_label = "high"

    verdict = "highload=yes" if result["highload"] else "highload=no"

    print("\n=== High-Load Summary ===")
    print(f"Container: {APP_CONTAINER}")
    print(f"Volume = {total} ({volume_label})")
    print(f"Reason: {result.get('reason')}")
    if "events_per_min" in result:
        print(f"Estimated events/min: {result.get('events_per_min'):.1f}")
    if "error_ratio" in result:
        print(f"Warn+Error ratio: {result.get('error_ratio'):.2%}")
    print(f"Final verdict: {verdict}")
    print("=========================\n")