"""
src/api/control_api.py

DLTRF Replay Engine — FastAPI control API.
"""

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, Optional

import yaml
import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles  # <-- Added Import
from pydantic import BaseModel
from ..replay.deterministic_replayer import DeterministicReplayer
from ..replay.session_manager import SessionManager
from ..replay.checkpoint_store import CheckpointStore
from ..adapters.redis_stream_adapter import RedisStreamAdapter
from ..common.metrics import get_metrics
from ..common.logging_config import ReplayLogger

logger = ReplayLogger(__name__)

app = FastAPI(title="DLTRF Replay Engine")
app.mount("/report", StaticFiles(directory="reports"), name="reports") # <-- Added Mount

security = HTTPBearer()

# ─────────────────────────────────────────────────────────────────────────────
# Config loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_dltrf_yaml() -> dict:
    """Load dltrf.yaml from the first location that exists."""
    candidates = [
        os.environ.get("DLTRF_CONFIG", ""),
        "/app/dltrf.yaml",
        "dltrf.yaml",
        "../dltrf.yaml",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                logger.info(f"Loaded dltrf.yaml from {path}")
                return cfg
            except Exception as e:
                logger.warning(f"Could not parse {path}: {e}")
    return {}

def _load_legacy_config() -> dict:
    """Fall back to configs/replay_config.yml for Redis settings."""
    try:
        with open("configs/replay_config.yml", "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}

# Load both configs at startup
_dltrf_cfg  = _load_dltrf_yaml()
_legacy_cfg = _load_legacy_config()

# ── Redis config ──────────────────────────────────────────────────────────────
_redis_cfg = _legacy_cfg.get("redis", {})
REDIS_URL        = os.getenv("REDIS_URL",  _redis_cfg.get("url",        "redis://localhost:6379"))
STREAM_KEY       = os.getenv("STREAM_KEY", _redis_cfg.get("stream_key", "logs:stream"))
CONSUMER_GROUP   = _redis_cfg.get("consumer_group", "replay_group")
CONSUMER_NAME    = _redis_cfg.get("consumer_name",  "replayer-1")
CHECKPOINT_EVERY = int(_redis_cfg.get("checkpoint_every", 10))

# ── Auth token ─────────────────────────────────────────────────────────────────
TOKEN = os.getenv("REPLAY_SHARED_TOKEN", "mysecret")

# ── Target URL from dltrf.yaml ─────────────────────────────────────────────────
def _resolve_target_url() -> str:
    target = _dltrf_cfg.get("target", {})
    if target:
        protocol = target.get("protocol", "http").rstrip(":/")
        host     = target.get("host", "")
        port     = target.get("port", 3000)
        if host:
            url = f"{protocol}://{host}:{port}"
            logger.info(f"Target URL from dltrf.yaml: {url}")
            return url
    fallback = os.getenv("TARGET_APP_URL", "http://my-app:3000")
    logger.info(f"Target URL from env/default: {fallback}")
    return fallback

TARGET_APP_URL = _resolve_target_url()
os.environ["TARGET_APP_URL"] = TARGET_APP_URL

# ── Shared singletons (health check + checkpoint only) ────────────────────────
_redis_client    = redis.Redis.from_url(REDIS_URL)
_checkpoint_store = CheckpointStore(_redis_client)
_session_manager  = SessionManager()

def _make_redis_adapter() -> RedisStreamAdapter:
    return RedisStreamAdapter(
        redis_url      = REDIS_URL,
        stream_key     = STREAM_KEY,
        consumer_group = CONSUMER_GROUP,
        consumer_name  = CONSUMER_NAME,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────
async def verify_token(credentials=Depends(security)):
    if credentials.credentials != TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials

# ─────────────────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────────────────
class StartRequest(BaseModel):
    session_id:                  Optional[str]  = None
    start_ts:                    Optional[str]  = None
    end_ts:                      Optional[str]  = None
    mode:                        str            = "replay"
    speed:                       float          = 1.0
    max_events:                  int            = 1000
    enable_divergence_detection: bool           = True

class StartResponse(BaseModel):
    replay_id: str
    status:    str

class StopRequest(BaseModel):
    replay_id: str

class StopResponse(BaseModel):
    status: str

# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    try:
        await _redis_client.ping()
        return {
            "status":       "healthy",
            "redis":        "connected",
            "target_url":   TARGET_APP_URL,
            "dltrf_config": bool(_dltrf_cfg),
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}")

@app.get("/config")
async def get_config(credentials=Depends(verify_token)):
    target     = _dltrf_cfg.get("target", {})
    state      = _dltrf_cfg.get("state_management", {})
    safe_state = {k: v for k, v in state.items() if "password" not in k.lower()}
    return {
        "target_url":       TARGET_APP_URL,
        "target":           target,
        "state_management": safe_state,
        "hooks":            _dltrf_cfg.get("hooks", {}),
        "redis_url":        REDIS_URL.replace(
            REDIS_URL.split("@")[-1] if "@" in REDIS_URL else "", "***"
        ),
    }

@app.post("/replay/start", response_model=StartResponse, dependencies=[Depends(verify_token)])
async def start_replay(request: StartRequest):
    try:
        replay_id = f"r-{uuid.uuid4().hex[:8]}"

        replay_config: Dict[str, Any] = {
            "replay_id":                   replay_id,
            "session_id":                  request.session_id,
            "start_ts":                    request.start_ts,
            "end_ts":                      request.end_ts,
            "mode":                        request.mode,
            "speed":                       request.speed,
            "max_events":                  request.max_events,
            "enable_divergence_detection": request.enable_divergence_detection,
            "checkpoint_every":            CHECKPOINT_EVERY,
        }

        _session_manager.create_session(replay_id, replay_config)

        adapter  = _make_redis_adapter()
        
        # 🎯 Correctly instantiate the Byte-Level Engine
        replayer = DeterministicReplayer(adapter, _checkpoint_store, _session_manager)

        async def _run():
            try:
                logger.info(f"Starting replay {replay_id}")
                result = await replayer.execute_replay(replay_config)
                logger.info(
                    f"Replay {replay_id} complete: "
                    f"{result.get('summary', {}).get('true_reproducibility', '?')}% repro"
                )
            except Exception as exc:
                logger.error(f"Replay {replay_id} crashed: {exc}", exc_info=True)
                try:
                    session = _session_manager._get_session_sync(replay_id)
                    if session:
                        session.status  = "failed"
                        session.message = str(exc)
                except Exception:
                    pass
            finally:
                try:
                    await adapter.disconnect()
                except Exception:
                    pass

        asyncio.create_task(_run())
        logger.info(f"Replay {replay_id} queued")
        return StartResponse(replay_id=replay_id, status="started")

    except Exception as e:
        logger.error(f"Failed to start replay: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/replay/stop", response_model=StopResponse, dependencies=[Depends(verify_token)])
async def stop_replay(request: StopRequest):
    try:
        ok = await _session_manager.update_session_status(request.replay_id, "stopped")
        if not ok:
            raise HTTPException(status_code=404, detail="Replay session not found")
        logger.info(f"Stopped replay {request.replay_id}")
        return StopResponse(status="stopped")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stop replay: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/replay/status", dependencies=[Depends(verify_token)])
async def get_status(replay_id: str = Query(...)):
    try:
        session = await _session_manager.get_session(replay_id)
        if not session:
            return {
                "replay_id":        replay_id,
                "state":            "not_found",
                "progress":         0.0,
                "events_processed": 0,
                "message":          "Session not found",
            }
        return {
            "replay_id":            replay_id,
            "state":                getattr(session, "status",               "unknown"),
            "progress":             getattr(session, "progress",              0.0),
            "events_processed":     getattr(session, "events_processed",      0),
            "total_events":         getattr(session, "total_events",          0),
            "divergences_detected": getattr(session, "divergences_detected",  0),
            "current_event_details":getattr(session, "current_event_details", {}),
        }
    except Exception as e:
        logger.error(f"Status check failed for {replay_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Status check failed: {e}")

@app.get("/metrics")
async def get_prometheus_metrics():
    return Response(content=get_metrics(), media_type="text/plain")