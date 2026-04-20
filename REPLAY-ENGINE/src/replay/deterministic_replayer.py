"""
deterministic_replayer.py — DLTRF Stateful Replay Engine
=========================================================

Core replay model: "Replay Intent, Not State"
─────────────────────────────────────────────
Recorded traffic contains stale state artifacts:
  - Session IDs that may not exist in the restored DB
  - CSRF tokens bound to sessions that were destroyed at login
  - Cookies that are snapshots from specific moments in time

The correct model mirrors how a real browser opens a site fresh:
  1. Start with NO pre-seeded session cookies
  2. Warmup GET /login creates a fresh server-issued session
  3. POST /login: CSRF scrape fetches token bound to THAT fresh session → inject → ✓
  4. Server issues authenticated session → stored automatically
  5. All subsequent requests carry the live authenticated session

Key fixes:
  FIX 1 — Cookie domain stamping:
    _force_harvest_cookies now stamps every harvested cookie with the
    internal Docker hostname (parsed from target_url), bypassing
    http.cookiejar domain-mismatch drops entirely.

  FIX 2 — No pre-seeding:
    Session starts fresh — no recorded cookies injected.
    Server creates sessions naturally, just like a browser.

  FIX 3 — No manual Cookie headers:
    requests.Session manages the cookie jar natively. Manual
    headers["Cookie"] construction is removed everywhere — it caused
    duplicates and stale-cookie pollution.

  FIX 4 — Warmup GET /login:
    A single GET /login before the replay loop primes the session
    so the server issues a fresh session cookie before any POST.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning  # type: ignore[import]
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# ── Infrastructure (graceful fallback for standalone testing) ─────────────────
try:
    from ..adapters.redis_stream_adapter import RedisStreamAdapter
    from ..replay.checkpoint_store import CheckpointStore
    from ..replay.session_manager import SessionManager
except ImportError:
    RedisStreamAdapter = Any  # type: ignore
    CheckpointStore    = Any  # type: ignore
    SessionManager     = Any  # type: ignore

try:
    from ..analysis.report_generator import build_html_report
except ImportError:
    def build_html_report(r: Dict) -> str:  # type: ignore[misc]
        return f"<pre>{json.dumps(r, indent=2)}</pre>"

try:
    from .body_loader import load_request_body, cleanup_spooled_payload
except ImportError:
    try:
        from body_loader import load_request_body, cleanup_spooled_payload  # type: ignore[no-redef]
    except ImportError:
        def load_request_body(evt: Dict) -> bytes:  # type: ignore[misc]
            raw = evt.get("request_body", "")
            if not raw:
                return b""
            try:
                padded = raw + "=" * ((4 - len(raw) % 4) % 4)
                return base64.b64decode(padded)
            except Exception:
                return str(raw).encode("utf-8", errors="replace")

        def cleanup_spooled_payload(evt: Dict) -> None:  # type: ignore[misc]
            pass

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_HTTP_METHODS     = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_CSRF_FORM_FIELDS  = ("_token", "csrfmiddlewaretoken", "authenticity_token", "_wpnonce")
_XSRF_COOKIE_NAMES = ("XSRF-TOKEN", "csrftoken", "CSRF-TOKEN", "_csrf")
_AJAX_PATH_PATTERNS = ("/ajax/", "/permissions/form-row/", "/api/")

_DIVERGENCE_CONFIG_PATH = os.getenv("DIVERGENCE_CONFIG", "divergence_config.yaml")


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _resolve_target_url() -> str:
    env = os.getenv("TARGET_APP_URL", "").strip()
    if env and "my-app" not in env:
        return env
    return f"http://{os.getenv('APP_HOST','my-app')}:{os.getenv('APP_PORT','3000')}"


def _load_divergence_config() -> Dict:
    if not _YAML_AVAILABLE:
        return {}
    try:
        with open(_DIVERGENCE_CONFIG_PATH, encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception:
        return {}


def _decode_body(raw: str) -> bytes:
    if not raw:
        return b""
    try:
        raw += "=" * ((4 - len(raw) % 4) % 4)
        return base64.b64decode(raw)
    except Exception:
        return str(raw).encode("utf-8", errors="replace")


def _is_truncated_multipart(payload: bytes) -> bool:
    if len(payload) < 6:
        return False
    tail = payload[-256:].decode("utf-8", errors="ignore").rstrip()
    return payload[:2] == b"--" and not tail.endswith("--")


def _detect_body_type(payload: bytes) -> str:
    if not payload:
        return "empty"
    peek = payload[:4096].decode("utf-8", errors="ignore")
    if "------" in peek and 'name="' in peek:
        return "multipart"
    if "=" in peek and "&" in peek and peek[:1] not in ("{", "["):
        return "urlencoded"
    s = peek.strip()
    if s.startswith("{") or s.startswith("["):
        return "json"
    return "raw"


def _inject_csrf_urlencoded(payload: bytes, token: str) -> bytes:
    try:
        body   = payload.decode("utf-8", errors="ignore")
        params = urllib.parse.parse_qs(body, keep_blank_values=True)
        replaced = False
        for field in _CSRF_FORM_FIELDS:
            if field in params:
                params[field] = [token]
                replaced = True
        if not replaced:
            params[_CSRF_FORM_FIELDS[0]] = [token]
        encoded = urllib.parse.urlencode(
            {k: v[0] if len(v) == 1 else v for k, v in params.items()}, doseq=True)
        return encoded.encode("utf-8")
    except Exception as exc:
        logger.warning("CSRF URL-encoded inject failed: %s", exc)
        return payload


def _is_ajax_path(path: str) -> bool:
    return any(p in path.lower() for p in _AJAX_PATH_PATTERNS)


def _extract_recorded_host(events: List[Dict]) -> str:
    env_host = os.getenv("RECORDED_HOST", "").strip()
    if env_host:
        return env_host
    for evt in events:
        ref = (evt.get("referer") or "").strip()
        if ref.startswith(("http://", "https://")):
            p = urllib.parse.urlparse(ref)
            if p.netloc:
                return p.netloc
    return ""


def _force_harvest_cookies(
    session: requests.Session,
    resp: requests.Response,
    target_url: str,
) -> None:
    """
    Bypass Python's http.cookiejar RFC-2965 domain validation.

    THE PROBLEM:
      Engine connects to:  http://bookstack:80    (internal Docker hostname)
      Engine spoofs Host:  localhost:3000         (recorded public hostname)

      BookStack issues:  Set-Cookie: bookstack_session=…; Domain=localhost
      http.cookiejar compares Domain=localhost vs actual TCP host=bookstack
      → MISMATCH → cookie silently dropped → empty jar → 419 CSRF mismatch

    THE FIX:
      Read Set-Cookie values from resp.raw.headers (before cookiejar sees them),
      parse them manually, and stamp each cookie with the internal Docker hostname
      (parsed from target_url). session.cookies.set() with an explicit domain
      bypasses all RFC-2965 validation.
    """
    raw_headers = getattr(resp.raw, "headers", None)
    if not raw_headers:
        return

    cookies: List[str] = []
    if hasattr(raw_headers, "getlist"):
        cookies = raw_headers.getlist("Set-Cookie")
    else:
        for k, v in raw_headers.items():
            if k.lower() == "set-cookie":
                cookies.append(v)

    if not cookies:
        return

    internal_domain = urllib.parse.urlparse(target_url).hostname

    injected = 0
    for raw in cookies:
        try:
            parts = [p.strip() for p in raw.split(";")]
            if not parts or "=" not in parts[0]:
                continue
            name, _, value = parts[0].partition("=")
            name  = name.strip()
            value = value.strip()
            if not name:
                continue
            session.cookies.set(name, value, domain=internal_domain)
            injected += 1
            logger.debug("cookie_harvest: saved %s=%s… (domain=%s)", name, value[:12], internal_domain)
        except Exception as exc:
            logger.warning("cookie_harvest: parse error for %r: %s", raw[:80], exc)

    if injected:
        logger.info("cookie_harvest: saved %d cookie(s) (domain=%s)", injected, internal_domain)


# ─────────────────────────────────────────────────────────────────────────────
# DomainMapper
# ─────────────────────────────────────────────────────────────────────────────

class DomainMapper:
    """
    Handles the mismatch between internal Docker address and public hostname.
    Laravel/Rails/Django validate Host, Origin, and Referer against APP_URL.
    """

    def __init__(self, target_url: str, recorded_host: str) -> None:
        self._target          = target_url.rstrip("/")
        self._recorded_host   = recorded_host or urllib.parse.urlparse(target_url).netloc
        scheme                = "https" if "https" in target_url else "http"
        self._recorded_origin = f"{scheme}://{self._recorded_host}"

        if self._recorded_host != urllib.parse.urlparse(target_url).netloc:
            logger.warning(
                "DomainMapper: HOST MISMATCH — recorded=%r internal=%r. "
                "Injecting Host: %s on every request.",
                self._recorded_host,
                urllib.parse.urlparse(target_url).netloc,
                self._recorded_host,
            )

    def build_headers(self, path: str, ua: str, referer: str, ip: str) -> Dict[str, str]:
        ref = referer if referer else f"{self._recorded_origin}{path}"
        headers: Dict[str, str] = {
            "Host":       self._recorded_host,
            "Origin":     self._recorded_origin,
            "Referer":    ref,
            "User-Agent": ua or _DEFAULT_UA,
            "Accept": (
                "application/json, text/plain, */*"
                if _is_ajax_path(path)
                else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
        }
        if _is_ajax_path(path):
            headers["X-Requested-With"] = "XMLHttpRequest"
        if ip:
            headers["X-Forwarded-For"] = ip
            headers["X-Real-IP"]       = ip
        return headers

    def rewrite_to_internal(self, url: str) -> str:
        if not url:
            return url
        if not url.startswith(("http://", "https://")):
            return self._target + "/" + url.lstrip("/")
        if url.startswith(self._target):
            return url
        if url.startswith(self._recorded_origin):
            return self._target + url[len(self._recorded_origin):]
        return url

    @property
    def recorded_host(self) -> str:
        return self._recorded_host

    @property
    def recorded_origin(self) -> str:
        return self._recorded_origin


# ─────────────────────────────────────────────────────────────────────────────
# CsrfRefresher
# ─────────────────────────────────────────────────────────────────────────────

class CsrfRefresher:
    _SANCTUM_PATH = "/sanctum/csrf-cookie"

    _HTML_PATTERNS = [
        r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']{10,})["\']',
        r'<meta[^>]+content=["\']([^"\']{10,})["\'][^>]+name=["\']csrf-token["\']',
        r'<input[^>]+name=["\']_token["\'][^>]+value=["\']([^"\']{10,})["\']',
        r'<input[^>]+value=["\']([^"\']{10,})["\'][^>]+name=["\']_token["\']',
        r'<input[^>]+name=["\']csrfmiddlewaretoken["\'][^>]+value=["\']([^"\']{10,})["\']',
        r'<input[^>]+value=["\']([^"\']{10,})["\'][^>]+name=["\']csrfmiddlewaretoken["\']',
        r'<input[^>]+name=["\']authenticity_token["\'][^>]+value=["\']([^"\']{10,})["\']',
        r'<input[^>]+value=["\']([^"\']{10,})["\'][^>]+name=["\']authenticity_token["\']',
    ]

    def __init__(self, session: requests.Session, target_url: str, dm: DomainMapper) -> None:
        self._session = session
        self._target  = target_url.rstrip("/")
        self._dm      = dm

    def get_token(self, path: str, ua: str, ip: str) -> Optional[str]:
        token = self._try_sanctum(ua, ip)
        if token:
            return token
        token = self._scrape_page(f"{self._target}{path}", ua, ip)
        if token:
            return token
        return self._scrape_page(f"{self._target}/", ua, ip)

    def _try_sanctum(self, ua: str, ip: str) -> Optional[str]:
        url     = f"{self._target}{self._SANCTUM_PATH}"
        headers = self._dm.build_headers(self._SANCTUM_PATH, ua, "", ip)
        try:
            resp = self._session.get(url, headers=headers, timeout=6, verify=False)
            _force_harvest_cookies(self._session, resp, self._target)
            if resp.status_code in (200, 204, 302):
                return self._read_xsrf_from_session()
        except Exception:
            pass
        return None

    def _scrape_page(self, url: str, ua: str, ip: str) -> Optional[str]:
        path    = urllib.parse.urlparse(url).path or "/"
        headers = self._dm.build_headers(path, ua, "", ip)
        try:
            resp = self._session.get(url, headers=headers, timeout=8, verify=False)
            _force_harvest_cookies(self._session, resp, self._target)
            if resp.status_code != 200 or not resp.text:
                return None
            for pat in self._HTML_PATTERNS:
                m = re.search(pat, resp.text, re.IGNORECASE | re.DOTALL)
                if m:
                    return m.group(1).strip()
            token = self._read_xsrf_from_session()
            if token:
                return token
        except Exception:
            pass
        return None

    def _read_xsrf_from_session(self) -> Optional[str]:
        for name in _XSRF_COOKIE_NAMES:
            raw = self._session.cookies.get(name)
            if raw:
                try:
                    return urllib.parse.unquote(raw)
                except Exception:
                    return raw
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DivergenceAnalyser
# ─────────────────────────────────────────────────────────────────────────────
class DivergenceAnalyser:
    def __init__(self) -> None:
        self._cfg = _load_divergence_config()

    def analyse(
        self,
        orig:      Optional[int],
        replay:    int,
        method:    str,
        path:      str,
        location:  str,
        truncated: bool,
        error:     str,
    ) -> Dict[str, Any]:

        if replay == 0 or error:
            return self._r(True, "CRITICAL", f"No response: {error or 'timeout'}", f"{orig} -> 0", "Verify target app is reachable.")

        if orig == replay:
            return {"diverged": False, "tier": "", "is_expected": False, "reason": "", "diff_summary": "", "recommendation": ""}

        # DELEGATION FIX: We removed the old schema logic here.
        # The engine simply flags the divergence as INVESTIGATE and passes it 
        # to report_generator.py which applies the YAML rules perfectly.
        return self._r(True, "INVESTIGATE", "Response differs from recording.", f"{orig} -> {replay}", "See HTML report for classification.")

    @staticmethod
    def _r(diverged: bool, tier: str, reason: str, diff_summary: str, recommendation: str, is_expected: bool = False) -> Dict[str, Any]:
        return {
            "diverged":       diverged,
            "tier":           tier,
            "is_expected":    is_expected,
            "reason":         reason,
            "diff_summary":   diff_summary,
            "recommendation": recommendation,
        }


# ─────────────────────────────────────────────────────────────────────────────
# DeterministicReplayer
# ─────────────────────────────────────────────────────────────────────────────

class DeterministicReplayer:
    """
    Stateful HTTP replay engine. Public contract unchanged:
        replayer = DeterministicReplayer(redis_adapter, checkpoint_store, session_manager)
        report   = await replayer.execute_replay(replay_config)
    """

    def __init__(
        self,
        redis_adapter:    Any,
        checkpoint_store: Any,
        session_manager:  Optional[Any] = None,
    ) -> None:
        self.redis_adapter    = redis_adapter
        self.checkpoint_store = checkpoint_store
        self.session_manager  = session_manager
        self.target_url       = _resolve_target_url().rstrip("/")

        self.replay_id:   str        = ""
        self.results:     List[Dict] = []
        self.divergences: List[Dict] = []
        self.errors:      List[Dict] = []

        self._session:  Optional[requests.Session] = None
        self._dm:       Optional[DomainMapper]     = None
        self._csrf:     Optional[CsrfRefresher]    = None
        self._analyser: DivergenceAnalyser         = DivergenceAnalyser()

        logger.info("DeterministicReplayer ready — target=%s", self.target_url)

    # ── Public API ────────────────────────────────────────────────────────────

    async def execute_replay(self, replay_config: Dict[str, Any]) -> Dict[str, Any]:
        self._reset(replay_config)
        t0 = time.time()

        max_events       = int(replay_config.get("max_events", 1000))
        checkpoint_every = int(replay_config.get("checkpoint_every", 10))
        start_ts         = replay_config.get("start_ts", "0") or "0"
        end_ts           = replay_config.get("end_ts",   "+") or "+"

        if not getattr(self.redis_adapter, "redis_client", None):
            try:
                await self.redis_adapter.connect()
            except Exception as exc:
                logger.warning("Redis connect warning: %s", exc)

        raw    = await self.redis_adapter.read_messages_by_range(
            start_id=start_ts, end_id=end_ts, count=max_events)
        events = self._parse_stream(raw)

        if not events:
            logger.warning("No replayable HTTP events found in stream")
            return self._build_report(time.time() - t0)

        logger.info("Replaying %d events against %s", len(events), self.target_url)

        recorded_host = _extract_recorded_host(events)
        self._dm      = DomainMapper(self.target_url, recorded_host)

        # Fresh session — NO pre-seeded cookies.
        # Recorded cookies are stale state artifacts that break the login flow.
        # requests.Session manages the cookie jar natively going forward.
        self._session        = requests.Session()
        self._session.verify = False

        # Constrain connection pooling to prevent stale socket state
        adapter = HTTPAdapter(
            pool_connections=1,
            pool_maxsize=1,
            max_retries=Retry(total=1, connect=1, read=0, status=0),
        )
        self._session.mount("http://",  adapter)
        self._session.mount("https://", adapter)

        self._csrf = CsrfRefresher(self._session, self.target_url, self._dm)

        logger.info(
            "Session initialized FRESH | recorded_host=%r | replay_model=intent",
            self._dm.recorded_host,
        )

        # Warmup GET /login — primes the server to issue a fresh session cookie
        # before any mutating request hits. Mirrors what a real browser does.
        logger.info("Warming up session with GET /login...")
        warmup_headers = self._dm.build_headers("/login", _DEFAULT_UA, "", "")
        try:
            resp = self._session.get(
                f"{self.target_url}/login",
                headers=warmup_headers,
                timeout=10,
                allow_redirects=False,
                verify=False,
            )
            _force_harvest_cookies(self._session, resp, self.target_url)
            logger.info("Warmup complete — session cookies: %s", list(self._session.cookies.keys()))
        except Exception as e:
            logger.warning("Warmup GET /login failed: %s", e)

        # ── REPLAY LOOP ───────────────────────────────────────────────────────
        for i, evt in enumerate(events):
            result = None
            try:
                result = await self._replay_event(evt, i)
            except Exception as exc:
                logger.error(
                    "Event %d (%s %s) crashed: %s",
                    i, evt.get("method", "?"), evt.get("path", "?"), exc,
                    exc_info=True,
                )
                self.errors.append({
                    "event_id": evt.get("event_id", f"evt-{i}"),
                    "method":   evt.get("method"),
                    "path":     evt.get("path"),
                    "error":    str(exc),
                })
                result = self._make_error_result(evt, i, str(exc))
            finally:
                cleanup_spooled_payload(evt)

            self.results.append(result)
            if result["diverged"]:
                self.divergences.append(result)

            if self.session_manager:
                try:
                    sess = await self.session_manager.get_session(self.replay_id)
                    if sess:
                        sess.events_processed     = i + 1
                        sess.divergences_detected = len(self.divergences)
                        sess.progress             = round((i + 1) / len(events) * 100, 1)
                except Exception:
                    pass

            if (i + 1) % checkpoint_every == 0:
                try:
                    await self.checkpoint_store.save_checkpoint(
                        self.replay_id,
                        {"progress": i + 1, "total": len(events)},
                        checkpoint_type="progress",
                    )
                except Exception as exc:
                    logger.debug("Checkpoint save skipped: %s", exc)

        if self.session_manager:
            try:
                await self.session_manager.update_session_status(self.replay_id, "completed")
            except Exception:
                pass

        duration = time.time() - t0
        logger.info(
            "Replay %s done — %d events, %d divergences, %.1fs",
            self.replay_id, len(events), len(self.divergences), duration,
        )
        return self._build_report(duration)

    # ── Per-event replay ──────────────────────────────────────────────────────

    async def _replay_event(self, evt: Dict, index: int) -> Dict:
        """
        Replay one recorded HTTP event.

        Cookie management: requests.Session handles the jar automatically.
        No manual Cookie header construction — that caused duplicates and
        stale-cookie pollution. _force_harvest_cookies stamps any domain-
        mismatched cookies directly into the jar after every response.
        """
        assert self._session and self._dm and self._csrf

        method      = evt.get("method", "GET").upper()
        path        = evt.get("path", "/")
        url         = f"{self.target_url}{path}"
        orig_status = evt.get("original_status")
        ua          = evt.get("user_agent") or _DEFAULT_UA
        ip          = evt.get("ip") or ""
        referer     = evt.get("referer") or ""

        original_payload = load_request_body(evt) or b""
        payload          = original_payload
        body_type        = _detect_body_type(payload)
        truncated        = (body_type == "multipart" and _is_truncated_multipart(payload))

        # Step 1+2: base headers + Content-Type (no manual Cookie header)
        headers          = self._dm.build_headers(path, ua, referer, ip)
        headers_snapshot = dict(headers)   # clean copy for 419 retry

        # ── INJECT RECORDED AUTH HEADER ──────────────────────────────────────────
# For JWT apps (JuiceShop, etc.) the Authorization: Bearer token is captured
# by nginx and must be replayed verbatim. Cookie-based apps (BookStack) have
# no auth_header so this is a no-op for them.
        auth_header = evt.get("auth_header", "").strip()
        if auth_header:
            headers["Authorization"] = auth_header
            headers_snapshot["Authorization"] = auth_header  # keep snapshot in sync
# ─────────────────────────────────────────────────────────────────────────
# ... later in the return dict:

        recorded_ct = evt.get("content_type", "")
        if recorded_ct:
            headers["Content-Type"] = recorded_ct
        elif body_type == "urlencoded":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body_type == "json":
            headers["Content-Type"] = "application/json"

        # Step 3: CSRF refresh for mutating methods
        csrf_applied  = False
        csrf_strategy = ""

        if method in _MUTATING_METHODS:
            token = await asyncio.to_thread(self._csrf.get_token, path, ua, ip)
            if token:
                headers["X-CSRF-TOKEN"] = token
                headers["X-XSRF-TOKEN"] = token
                if body_type == "urlencoded" and payload:
                    payload = _inject_csrf_urlencoded(payload, token)
                    headers["Content-Length"] = str(len(payload))
                elif body_type == "multipart":
                    headers.pop("Content-Length", None)
                csrf_applied  = True
                csrf_strategy = "live_scrape"
                logger.info("CSRF injected for %s %s", method, url)
            else:
                logger.warning("CSRF: no token for %s %s — may 419", method, url)

        # Step 4: send — requests.Session sends its cookie jar automatically
        t0 = time.time()
        replay_status, resp_headers, send_error = await self._send(
            method, url, headers, payload if payload else None)
        response_time_ms = round((time.time() - t0) * 1000, 2)

        # 419 retry with clean headers snapshot + fresh token
        if replay_status == 419 and csrf_applied:
            logger.info("419 on %s %s — retrying with fresh token", method, url)
            for name in _XSRF_COOKIE_NAMES:
                if name in self._session.cookies:
                    del self._session.cookies[name]

            token2 = await asyncio.to_thread(self._csrf.get_token, path, ua, ip)
            if token2:
                headers = dict(headers_snapshot)
                if recorded_ct:
                    headers["Content-Type"] = recorded_ct
                elif body_type == "urlencoded":
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                elif body_type == "json":
                    headers["Content-Type"] = "application/json"

                headers["X-CSRF-TOKEN"] = token2
                headers["X-XSRF-TOKEN"] = token2

                retry_payload = original_payload
                if body_type == "urlencoded" and retry_payload:
                    retry_payload = _inject_csrf_urlencoded(original_payload, token2)
                    headers["Content-Length"] = str(len(retry_payload))
                elif body_type == "multipart":
                    headers.pop("Content-Length", None)

                t_r = time.time()
                replay_status, resp_headers, send_error = await self._send(
                    method, url, headers, retry_payload if retry_payload else None)
                response_time_ms = round((time.time() - t_r) * 1000, 2)
                csrf_strategy = "retry_fresh"
                logger.info("Retry result: %s %s -> %d", method, url, replay_status)

        # PRG: follow redirect once to harvest rotated session cookies
        if (replay_status in (302, 303)
                and method in _MUTATING_METHODS
                and not send_error):
            location = resp_headers.get("Location", "") or resp_headers.get("location", "")
            if location and "login" not in location.lower():
                await self._follow_redirect(location, ua, ip, path)

        location_final = resp_headers.get("Location", "") or resp_headers.get("location", "")
        div = self._analyser.analyse(
            orig=orig_status, replay=replay_status,
            method=method, path=path,
            location=location_final,
            truncated=truncated,
            error=send_error,
        )

        return {
            "event_id":         evt.get("event_id"),
            "seq":              evt.get("seq", 0),
            "method":           method,
            "path":             path,
            "url":              url,
            "timestamp":        evt.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "original_status":  orig_status,
            "replay_status":    replay_status,
            "response_time_ms": response_time_ms,
            "success":          replay_status > 0 and not send_error,
            "diverged":         div["diverged"],
            "tier":             div.get("tier", ""),
            "is_expected":      div.get("is_expected", False),
            "reason":           div.get("reason", ""),
            "diff_summary":     div.get("diff_summary", ""),
            "recommendation":   div.get("recommendation", ""),
            "auth_mode":        "jwt" if auth_header else ("cookie" if self._session.cookies else "none"),
            "auth_was_active":  bool(auth_header or self._session.cookies),
            "csrf_applied":     csrf_applied,
            "csrf_strategy":    csrf_strategy,
            "truncated_upload": truncated,
            "recorded_host":    self._dm.recorded_host,
        }

    # ── HTTP transport ─────────────────────────────────────────────────────────

    async def _send(
        self,
        method:  str,
        url:     str,
        headers: Dict[str, str],
        payload: Optional[bytes],
    ) -> Tuple[int, Dict[str, str], str]:
        assert self._session

        timeout = 30 if (payload and len(payload) > 512_000) else 15

        def _do() -> requests.Response:
            return self._session.request(  # type: ignore[union-attr]
                method=method, url=url, headers=headers, data=payload,
                timeout=timeout, allow_redirects=False, verify=False)

        try:
            resp = await asyncio.to_thread(_do)
            _force_harvest_cookies(self._session, resp, self.target_url)
            return resp.status_code, dict(resp.headers), ""
        except requests.exceptions.Timeout:
            self.errors.append({"url": url, "method": method, "error": "timeout"})
            return 0, {}, "timeout"
        except Exception as exc:
            self.errors.append({"url": url, "method": method, "error": str(exc)})
            return 0, {}, str(exc)

    async def _follow_redirect(self, location: str, ua: str, ip: str, origin_path: str) -> None:
        assert self._session and self._dm

        internal_url = self._dm.rewrite_to_internal(location)
        path         = urllib.parse.urlparse(location).path or "/"
        headers      = self._dm.build_headers(path, ua, f"{self._dm.recorded_origin}{origin_path}", ip)

        def _do() -> requests.Response:
            return self._session.get(  # type: ignore[union-attr]
                internal_url, headers=headers, timeout=10,
                allow_redirects=False, verify=False)

        try:
            resp = await asyncio.to_thread(_do)
            _force_harvest_cookies(self._session, resp, self.target_url)
            logger.debug("PRG: followed to %s (cookies harvested)", internal_url)
        except Exception as exc:
            logger.debug("PRG follow failed for %s: %s", internal_url, exc)

    # ── Stream parsing ─────────────────────────────────────────────────────────

    def _parse_stream(self, raw_messages: List[Any]) -> List[Dict]:
        events: List[Dict] = []

        for msg in raw_messages:
            fields = getattr(msg, "fields", {}) or {}
            p_raw  = fields.get("payload", "{}")
            p: Dict[str, Any] = {}

            try:
                p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
            except Exception:
                if isinstance(p_raw, str):
                    p = self._rescue_shattered_json(p_raw, len(events))
                else:
                    continue

            method = str(p.get("method", "")).upper()
            source = fields.get("source") or p.get("source", "unknown")
            raw_st = p.get("status") or p.get("response_status")

            if source != "app-proxy":
                if method not in _HTTP_METHODS:
                    continue
                try:
                    if raw_st is None or not (100 <= int(raw_st) <= 599):
                        continue
                except (TypeError, ValueError):
                    continue

            status: Optional[int] = None
            if raw_st is not None:
                try:
                    status = int(raw_st)
                except (TypeError, ValueError):
                    pass

            events.append({
                "event_id":        (
                    fields.get("event_id")
                    or p.get("event_id")
                    or getattr(msg, "stream_id", f"msg-{len(events)}")
                ),
                "seq":             int(fields.get("seq", 0) or p.get("seq", 0) or 0),
                "timestamp":       p.get("timestamp", ""),
                "method":          method,
                "path":            p.get("path", "/"),
                "request_body":    p.get("request_body", ""),
                "content_type":    p.get("content_type", ""),
                "auth_header":     p.get("auth_header", ""),
                "cookie_header":   p.get("cookie_header") or p.get("cookie") or "",
                "user_agent":      p.get("user_agent", ""),
                "ip":              p.get("ip", ""),
                "referer":         p.get("referer", ""),
                "original_status": status,
                "source":          source,
            })

        has_seq = any(e["seq"] > 0 for e in events)
        return sorted(
            events,
            key=lambda e: (e["seq"], e["timestamp"]) if has_seq else (0, e["timestamp"]),
        )

    @staticmethod
    def _rescue_shattered_json(raw: str, index: int) -> Dict:
        def extract(pattern: str, default: str = "") -> str:
            m = re.search(pattern, raw, re.IGNORECASE)
            return m.group(1) if m else default

        method_raw = extract(r'"method"\s*:\s*"([^"]+)"', "POST").upper()
        path       = extract(r'"path"\s*:\s*"([^"]+)"', f"/shattered-{index}")
        source     = extract(r'"source"\s*:\s*"([^"]+)"', "app-proxy")
        status_m   = re.search(r'"status"\s*:\s*(\d+)', raw, re.IGNORECASE)
        status     = int(status_m.group(1)) if status_m else 200
        body_m     = re.search(r'"request_body"\s*:\s*"([^"]*)', raw, re.IGNORECASE)
        salvaged   = ""
        if body_m:
            salvaged = body_m.group(1)
            salvaged += "=" * ((4 - len(salvaged) % 4) % 4)

        logger.warning("Rescued shattered JSON for %s %s", method_raw, path)
        return {
            "method":        method_raw,
            "path":          path,
            "status":        status,
            "source":        source,
            "referer":       extract(r'"referer"\s*:\s*"([^"]+)"', ""),
            "user_agent":    extract(r'"user_agent"\s*:\s*"([^"]+)"', ""),
            "ip":            extract(r'"ip"\s*:\s*"([^"]+)"', ""),
            "content_type":  extract(r'"content_type"\s*:\s*"([^"]+)"', ""),
            "cookie_header": (extract(r'"cookie_header"\s*:\s*"([^"]+)"', "")
                              or extract(r'"cookie"\s*:\s*"([^"]+)"', "")),
            "auth_header":   "",
            "request_body":  salvaged,
        }

    # ── Report ─────────────────────────────────────────────────────────────────

    def _build_report(self, duration: float) -> Dict[str, Any]:
        total      = len(self.results)
        expected   = [d for d in self.divergences if d.get("tier") == "EXPECTED"]
        invest     = [d for d in self.divergences if d.get("tier") == "INVESTIGATE"]
        critical   = [d for d in self.divergences if d.get("tier") == "CRITICAL"]
        reproduced = total - len(self.divergences)
        true_repro = round((reproduced + len(expected)) / total * 100, 2) if total else 100.0
        rts        = [r.get("response_time_ms", 0) for r in self.results if r.get("response_time_ms")]

        report: Dict[str, Any] = {
            "replay_id": self.replay_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_events":         total,
                "divergences_found":    len(self.divergences),
                "reproduced_exactly":   reproduced,
                "expected_differences": len(expected),
                "needs_investigation":  len(invest),
                "genuine_bugs":         len(critical),
                "reproducibility_rate": true_repro,
                "true_reproducibility": true_repro,
                "duration_seconds":     round(duration, 2),
                "auth_mode":            "cookie",
                "auth_was_active":      bool(self._session and self._session.cookies),
                "target_url":           self.target_url,
                "recorded_host":        self._dm.recorded_host if self._dm else "",
            },
            "divergences": {"expected": expected, "investigate": invest, "critical": critical},
            "divergence_analysis": {
                "total":   len(self.divergences),
                "by_tier": {
                    "EXPECTED":    len(expected),
                    "INVESTIGATE": len(invest),
                    "CRITICAL":    len(critical),
                },
                "details": self.divergences,
            },
            "all_events": self.results,
            "performance": {
                "avg_response_time_ms": round(sum(rts) / len(rts), 2) if rts else 0,
                "min_response_time_ms": round(min(rts), 2) if rts else 0,
                "max_response_time_ms": round(max(rts), 2) if rts else 0,
            },
            "errors": self.errors,
            "config": self._analyser._cfg  # <--- CRITICAL FIX: Pass the YAML to the renderer!
        }

        os.makedirs("reports", exist_ok=True)
        with open(f"reports/replay_{self.replay_id}.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        try:
            with open(f"reports/replay_{self.replay_id}.html", "w", encoding="utf-8") as f:
                f.write(build_html_report(report))
        except Exception as exc:
            logger.error("HTML report generation failed: %s", exc)

        logger.info(
            "Report saved | events=%d divergences=%d repro=%.1f%%",
            total, len(self.divergences), true_repro,
        )
        return report

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _reset(self, replay_config: Dict[str, Any]) -> None:
        self.replay_id   = replay_config.get("replay_id", f"r-{int(time.time())}")
        self.results     = []
        self.divergences = []
        self.errors      = []

    def _make_error_result(self, evt: Dict, index: int, error: str) -> Dict:
        return {
            "event_id":         evt.get("event_id", f"evt-{index}"),
            "seq":              evt.get("seq", 0),
            "method":           evt.get("method", "?"),
            "path":             evt.get("path", "?"),
            "url":              f"{self.target_url}{evt.get('path', '')}",
            "timestamp":        evt.get("timestamp", ""),
            "original_status":  evt.get("original_status"),
            "replay_status":    0,
            "response_time_ms": 0,
            "success":          False,
            "diverged":         True,
            "tier":             "CRITICAL",
            "is_expected":      False,
            "reason":           f"Engine exception: {error}",
            "diff_summary":     f"Exception: {error}",
            "recommendation":   "Check replay-engine logs for full traceback.",
            "auth_mode":        "cookie",
            "auth_was_active":  True,
            "csrf_applied":     False,
            "csrf_strategy":    "",
            "truncated_upload": False,
            "recorded_host":    self._dm.recorded_host if self._dm else "",
        }