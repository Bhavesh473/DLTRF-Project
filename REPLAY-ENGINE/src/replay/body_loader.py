#body_loader.py

import base64
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SPOOL_PREFIX = "__FILE__:"


def load_request_body(log_entry: dict) -> Optional[bytes]:
    """
    Retrieve the raw request body from a DLTRF log entry.

    Handles three cases:
      1. Spooled binary  — request_body starts with '__FILE__:'
      2. Inline Base64   — request_body is a b64 string (small bodies, legacy)
      3. Empty           — no body (GET requests, bodyless POSTs)

    Returns raw bytes or None. Caller decides how to handle None
    (e.g., send request with no body).
    """
    raw = log_entry.get("request_body", "")

    if not raw:
        return None

    # ── Case 1: spooled binary file ───────────────────────────────────────────
    if raw.startswith(SPOOL_PREFIX):
        spool_path = raw[len(SPOOL_PREFIX):]

        if not os.path.isfile(spool_path):
            logger.error(
                "Spooled payload missing: %s  "
                "(volume not mounted, or file was cleaned up before replay)",
                spool_path
            )
            return None

        try:
            with open(spool_path, "rb") as f:
                body = f.read()
            logger.debug(
                "Loaded spooled payload: %s  (%d bytes)", spool_path, len(body)
            )
            return body
        except OSError as e:
            logger.error("Failed to read spooled payload %s: %s", spool_path, e)
            return None

    # ── Case 2: inline Base64 ─────────────────────────────────────────────────
    try:
        # Pad to 4-char boundary before decoding.
        padded = raw + "=" * (-len(raw) % 4)
        return base64.b64decode(padded, validate=False)
    except Exception as e:
        logger.error("Failed to decode inline b64 body: %s", e)
        return None


def cleanup_spooled_payload(log_entry: dict) -> None:
    """
    Delete the spooled .bin file after the Replay Engine has consumed it.
    Call this after each request is successfully replayed to prevent the
    payloads volume from growing unbounded across capture sessions.
    """
    raw = log_entry.get("request_body", "")
    if not raw.startswith(SPOOL_PREFIX):
        return

    spool_path = raw[len(SPOOL_PREFIX):]
    try:
        Path(spool_path).unlink(missing_ok=True)
        logger.debug("Cleaned up spooled payload: %s", spool_path)
    except OSError as e:
        logger.warning("Could not delete spooled payload %s: %s", spool_path, e)