from fastapi import FastAPI, HTTPException, Request
from contextlib import asynccontextmanager
import redis.asyncio as redis
import json
import os
import logging
import re

REDIS_URL = os.getenv("REDIS_URL", "redis://universal-logging-redis:6379")
STREAM_KEY = os.getenv("STREAM_KEY", "logs:stream")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

redis_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = await redis.from_url(
        REDIS_URL,
        decode_responses=False,
        max_connections=20
    )
    await redis_client.ping()
    logger.info(f"✅ Redis pool ready (max_connections=20): {REDIS_URL}")
    yield
    await redis_client.aclose()

app = FastAPI(title="Redis Stream Forwarder", lifespan=lifespan)

@app.get("/health")
async def health():
    try:
        await redis_client.ping()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unreachable: {e}")


@app.post("/forward")
async def forward_logs(request: Request):
    """
    Accept logs from Fluentd and forward to Redis.
    Now equipped with Shattered JSON Rescue logic.
    """
    try:
        body = await request.body()
        # Use errors='replace' to prevent UTF-8 decode crashes on binary fragments
        body_str = body.decode('utf-8', errors='replace').strip()
        
        if not body_str:
            raise HTTPException(status_code=400, detail="Empty body")
        
        events = []
        
        if '\n' in body_str:
            # NDJSON processing
            for line in body_str.split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    events.append(event)
                except json.JSONDecodeError:
                    # 🎯 THE FIX: Do not drop shattered JSON! Flag it and save the raw string.
                    events.append({"_is_shattered": True, "raw": line})
        else:
            # Single object processing
            try:
                data = json.loads(body_str)
                if isinstance(data, list):
                    events = data
                elif isinstance(data, dict):
                    events = [data]
                else:
                    raise HTTPException(status_code=400, detail="Invalid data type")
            except json.JSONDecodeError:
                # 🎯 THE FIX: Rescue single shattered payloads
                events.append({"_is_shattered": True, "raw": body_str})
        
        if not events:
            return {"status": "success", "added": 0, "failed": 0, "total": 0}
        
        added_count = 0
        failed_count = 0
        
        for event in events:
            try:
                # ── SHATTERED JSON HANDLER ──
                if event.get("_is_shattered"):
                    raw_line = event["raw"]
                    
                    # Extract vital metadata via regex so Redis has an index
                    eid_m = re.search(r'"event_id"\s*:\s*"([^"]+)"', raw_line, re.IGNORECASE)
                    ts_m = re.search(r'"timestamp"\s*:\s*"([^"]+)"', raw_line, re.IGNORECASE)
                    
                    await redis_client.xadd(
                        STREAM_KEY,
                        {
                            "event_id": eid_m.group(1) if eid_m else "*",
                            "timestamp": ts_m.group(1) if ts_m else "",
                            "source": "app-proxy",
                            "level": "INFO",
                            "payload": raw_line  # Push the broken string directly!
                        }
                    )
                    added_count += 1
                    logger.warning("⚠️ Rescued shattered payload and forced into Redis")
                    continue
                    
                # ── NORMAL JSON HANDLER ──
                event_id = event.get("event_id", "*")
                timestamp = event.get("timestamp", "")
                source = event.get("source", "unknown")
                level = event.get("level", "INFO")
                
                payload_json = json.dumps(event, ensure_ascii=False)
                
                await redis_client.xadd(
                    STREAM_KEY,
                    {
                        "event_id": event_id,
                        "timestamp": timestamp,
                        "source": source,
                        "level": level,
                        "payload": payload_json
                    }
                )
                
                added_count += 1
                
            except Exception as e:
                failed_count += 1
                logger.error(f"❌ Failed to push to Redis: {e}")
                continue
        
        return {
            "status": "success",
            "added": added_count,
            "failed": failed_count,
            "total": len(events)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Forward error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8200)