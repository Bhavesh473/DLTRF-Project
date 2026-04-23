"""
divergence_detector.py

Loads classification rules from divergence_config.yaml — no hardcoded patterns.
To add/fix a rule: edit the YAML, restart the container. Zero Python changes.

App-agnostic: handles JWT Bearer apps (Juice Shop, SPAs) and session cookie apps
(BookStack/Laravel, WordPress, Rails, Django) without any code changes.

Priority:
  1. custom_rules in YAML     (your app-specific overrides)
  2. Pattern rules from YAML  (generic HTTP / REST conventions)
  3. Claude API               (root cause for anything unrecognised)
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml as _yaml
from deepdiff import DeepDiff
import requests as http_requests

logger = logging.getLogger(__name__)

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-20250514"

_CONFIG_PATH = os.getenv(
    "DIVERGENCE_CONFIG",
    str(Path(__file__).parent.parent.parent / "divergence_config.yaml"),
)


# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_config() -> Dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
        logger.info(f"Loaded divergence config from {_CONFIG_PATH}")
        return cfg
    except FileNotFoundError:
        logger.warning(
            f"divergence_config.yaml not found at {_CONFIG_PATH}. "
            "Using built-in defaults."
        )
        return {}
    except Exception as e:
        logger.error(f"Failed to read divergence_config.yaml: {e}. Using defaults.")
        return {}


_CFG: Dict = _load_config()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _frags(cfg: Dict, key: str) -> tuple:
    return tuple(str(x).lower() for x in cfg.get(key, []))


def _match(path: str, frags: tuple) -> bool:
    p = path.lower()
    return any(x in p for x in frags)


def _is_id_path(path: str) -> bool:
    return bool(re.search(r"/\d+(/|$|\?)", path))


def _status_match(rule_val, actual: int) -> bool:
    if str(rule_val) == "*":
        return True
    try:
        return int(rule_val) == actual
    except (TypeError, ValueError):
        return False


def _expected(reason: str, rec: str) -> Dict:
    return {"tier": "EXPECTED",    "is_expected": True,  "reason": reason, "recommendation": rec}

def _investigate(reason: str, rec: str) -> Dict:
    return {"tier": "INVESTIGATE", "is_expected": False, "reason": reason, "recommendation": rec}

def _critical(reason: str, rec: str) -> Dict:
    return {"tier": "CRITICAL",    "is_expected": False, "reason": reason, "recommendation": rec}

_TIER_BUILDERS = {
    "EXPECTED":    _expected,
    "INVESTIGATE": _investigate,
    "CRITICAL":    _critical,
}


# ─────────────────────────────────────────────────────────────────────────────
# Core classifier
# ─────────────────────────────────────────────────────────────────────────────

def classify(method: str, path: str,
             o_st: Optional[int], r_st: Optional[int],
             diff: str,
             cfg: Optional[Dict] = None) -> Dict:
    if cfg is None:
        cfg = _CFG

    o = o_st or 0
    r = r_st or 0
    m = method.upper()
    p = path.lower()

    expiry_hours = cfg.get("jwt_expiry_hours", 12)

    # ── Step 1: custom_rules from YAML ────────────────────────────────────────
    for rule in cfg.get("custom_rules", []):
        rule_method = str(rule.get("method", "*")).upper()
        if rule_method != "*" and rule_method != m:
            continue
        frag = str(rule.get("path_contains", "")).lower()
        if frag and frag not in p:
            continue
        if not _status_match(rule.get("recorded_status", "*"), o):
            continue
        if not _status_match(rule.get("replay_status",   "*"), r):
            continue
        tier    = str(rule.get("tier", "INVESTIGATE")).upper()
        builder = _TIER_BUILDERS.get(tier, _investigate)
        return builder(
            str(rule.get("reason",         "Custom rule match.")),
            str(rule.get("recommendation", "See divergence_config.yaml custom_rules.")),
        )

    # ── Step 2: global noise — status transitions always EXPECTED ─────────────
    for trans in cfg.get("global_noise", {}).get("status_transitions", []):
        if _status_match(trans.get("from", "*"), o) and \
           _status_match(trans.get("to",   "*"), r):
            return _expected(
                str(trans.get("reason",         "Global noise rule.")),
                str(trans.get("recommendation", "Excluded from repro rate.")),
            )

    # ── Step 3: WebSocket stale session ──────────────────────────────────────
    ws_frags = _frags(cfg, "websocket_path_fragments")
    if not ws_frags:
        ws_frags = ("socket.io", "/ws/", "websocket")
    if r == 400 and _match(p, ws_frags):
        return _expected(
            "WebSocket/Socket.IO session ID (sid=) expired when the original "
            "session ended. Replaying a stale sid returns 400 — correct protocol behaviour.",
            "WebSocket sessions can't be replayed from logs. Excluded automatically.",
        )

    # ── Step 4: Auth redirect bypassed by session injection (GET 302→200) ─────
    # Recording started before login → GET returned 302 to /login.
    # During replay, session cookie is injected → server serves content (200).
    # This is correct replay behaviour — session injection works as intended.
    if o == 302 and r == 200 and m == "GET":
        return _expected(
            "GET request recorded as redirect-to-login (302) — user was "
            "unauthenticated at that point in the recording. During replay, "
            "the session cookie was injected so the server served content "
            "directly (200) instead of redirecting. This is correct behaviour.",
            "Not a bug. Start recording AFTER logging in to avoid pre-login "
            "redirects appearing in the report.",
        )

    # ── Step 5: CSRF token mismatch (419) ────────────────────────────────────
    # 419 = Laravel Page Expired — CSRF token in form body (_token) is stale.
    # CSRF tokens are one-time-use and bound to the session.
    #
    # Why this happens:
    #   - Laravel stores CSRF token INSIDE the session record.
    #   - If SESSION_DRIVER=file (default), sessions are stored as files in
    #     storage/framework/sessions/. DB checkpoint (mysqldump) cannot restore
    #     these files → Laravel finds no session → rejects _token → 419.
    #
    # Permanent fix:
    #   Add SESSION_DRIVER=database to your app's environment.
    #   Sessions are then stored in MySQL → DB checkpoint restores them →
    #   CSRF token in session matches the _token in the recorded POST body → 200.
    #
    # Until SESSION_DRIVER=database is set, all form POSTs will 419.
    # Classified EXPECTED because it is a known framework limitation.
    if r == 419:
        return _expected(
            "CSRF token mismatch (419 Page Expired). Laravel generates a "
            "one-time CSRF token stored inside the session. During replay the "
            "recorded _token in the POST body does not match the current "
            "session's token — because the session was stored as a file "
            "(SESSION_DRIVER=file, the Laravel default) which is NOT restored "
            "by the DB checkpoint. The session file is missing → Laravel "
            "rejects the request.",
            "Set SESSION_DRIVER=database in your app's docker-compose environment. "
            "Sessions will then be stored in MySQL → checkpoint restore brings "
            "back the exact session (and CSRF token) from recording time → "
            "form POSTs will succeed. Re-record after applying this fix.",
        )

    # ── Step 6: Auth endpoint — session injected flips login response ─────────
    auth_frags = _frags(cfg, "auth_path_fragments")
    if not auth_frags:
        auth_frags = ("login", "signin", "authenticate")
    if m == "POST" and _match(p, auth_frags):
        if o == 401 and r == 200:
            return _expected(
                "Auth token injected onto the login request during replay. "
                "Original returned 401 (unauthenticated during recording). "
                "Replay attached the recorded session cookie — server accepted it → 200. "
                "Replay framework artefact, not an app bug.",
                "Strip the Cookie header from auth endpoints before replaying "
                "login requests to avoid this.",
            )
        if r == 429:
            return _investigate(
                "Login endpoint rate-limited (429) — brute-force protection "
                "triggered by rapid repeated login attempts during replay.",
                "Slow down replay speed (.\replay-and-view.ps1 -Speed 0.5) "
                "to avoid triggering rate limiters.",
            )
        if r == 419:
            # Login form POST with stale CSRF — same root cause as step 5
            return _expected(
                "Login form POST returned 419 CSRF mismatch. The _token "
                "field in the login form is session-bound and stale at replay time.",
                "Set SESSION_DRIVER=database so sessions (and CSRF tokens) "
                "are restored by the DB checkpoint. Re-record after applying this fix.",
            )
        if r == 401:
            return _investigate(
                "Login returned 401 during replay. Possible causes: rate-limit "
                "lockout, credentials changed since recording, or account deactivated.",
                "Check if the app has brute-force protection. Re-record with "
                "fresh credentials if needed.",
            )

    # ── Step 7: 405 Method Not Allowed on POST ────────────────────────────────
    # POST → 405 typically means the request hit an error/fallback route that
    # only accepts GET. This happens when session auth fails silently and
    # Laravel/Rails routes the unauthenticated request differently.
    if r == 405 and m == "POST":
        return _investigate(
            f"POST {path} returned 405 Method Not Allowed during replay "
            f"(was {o} during recording). The request likely hit an error "
            "handler that doesn't accept POST — caused by session auth failure. "
            "Root cause: SESSION_DRIVER=file sessions are not restored by the "
            "DB checkpoint.",
            "Set SESSION_DRIVER=database in your app environment. This ensures "
            "session state is restored by checkpoint.sh before every replay.",
        )

    # ── Step 8: Reversed creation (400/409 → 201) — DB was reset ─────────────
    creation_frags = _frags(cfg, "resource_creation_path_fragments")
    generic_rest_re = r"/api/[A-Za-z]*[sS]/?(\?|$)"
    if o in (400, 409) and m == "POST" and r == 201:
        if _match(p, creation_frags) or re.search(generic_rest_re, path, re.IGNORECASE):
            return _expected(
                f"POST to creation endpoint returned 201 during replay "
                f"(was {o} during recording). DB was reset since recording — "
                "resource no longer exists so creation succeeds.",
                "Use a stable test dataset to avoid DB state drift.",
            )

    # ── Step 9: Duplicate resource creation (200/201 → 400/409/500) ──────────
    if o in (200, 201) and m == "POST" and r in (400, 409, 500):
        if _match(p, creation_frags) or re.search(generic_rest_re, path, re.IGNORECASE):
            note = (" Server returned 500 instead of 409 — minor app quality issue."
                    if r == 500 else "")
            return _expected(
                f"POST to creation endpoint returned {r} during replay "
                f"(was {o} during recording). Resource already exists from "
                f"the recording session — DB unique constraint fires.{note}",
                "Expected with DB checkpoint in use. No action needed.",
            )

    # ── Step 10: Duplicate collection-item add ────────────────────────────────
    collection_frags = _frags(cfg, "collection_add_path_fragments")
    if not collection_frags:
        collection_frags = ("basketitem", "cartitem", "orderitem", "lineitem")
    if o in (200, 201) and m == "POST" and r in (400, 409, 500):
        if _match(p, collection_frags):
            return _expected(
                f"POST to collection endpoint returned {r} during replay "
                f"(was {o} during recording). Item already exists from recording.",
                "Expected. Flush DB between sessions to avoid this.",
            )

    # ── Step 11: Checkout already placed ─────────────────────────────────────
    checkout_frags = _frags(cfg, "checkout_path_fragments")
    if not checkout_frags:
        checkout_frags = ("checkout", "purchase", "payment/process")
    if o in (200, 201) and r in (400, 409, 422, 500) and m == "POST":
        if _match(p, checkout_frags):
            return _expected(
                f"Checkout returned {r} during replay (was {o} during recording). "
                "Order was already placed in the original session.",
                "Checkout is non-replayable without resetting order state.",
            )

    # ── Step 12: File upload / multipart body lost ────────────────────────────
    upload_frags = _frags(cfg, "upload_path_fragments")
    if not upload_frags:
        upload_frags = ("upload", "avatar", "attachment", "media")
    if _match(p, upload_frags):
        if r in (400, 419, 422, 500) and o in (200, 201, 302, 204):
            return _expected(
                f"Upload endpoint returned {r} during replay "
                f"(was {o} during recording). Nginx cannot capture "
                "multipart/form-data bodies — file content is logged as empty. "
                "Replayer sent an empty body → server rejected it.",
                "Not fixable via replay. Test file upload endpoints with a "
                "dedicated integration test that sends the actual file.",
            )

    # ── Step 13: DELETE → 404 on second replay ────────────────────────────────
    if m == "DELETE" and o in (200, 204) and r == 404:
        return _expected(
            "DELETE replayed but resource is already gone — deleted during "
            "the original session. 404 on a second DELETE is expected.",
            "No action needed.",
        )

    # ── Step 14: GET/PUT → 400 on dynamic resource (ID drift) ────────────────
    if o in (200, 304) and r == 400 and _is_id_path(p) and m in ("GET", "PUT", "PATCH"):
        return _expected(
            f"{m} on a dynamic resource ID returned 400 during replay "
            f"(was {o} during recording). Resource got a different auto-increment "
            "ID on replay — old ID no longer belongs to this user.",
            "Expected when replaying sessions after DB has grown. "
            "Use database snapshots for stable replay.",
        )

    # ── Step 15: GET/PUT → 404 on dynamic resource ───────────────────────────
    if o == 200 and r == 404 and _is_id_path(p) and m in ("GET", "PUT", "PATCH"):
        return _investigate(
            f"{m} on a dynamic resource returned 404 during replay "
            "(was 200 during recording). Resource may have been created "
            "mid-session and doesn't exist in current DB state.",
            "Check if this resource was created earlier in the same session. "
            "Replay needs DB state matching the recording start.",
        )

    # ── Step 16: 304 → 401 — auth expired on cached request ──────────────────
    if o == 304 and r == 401:
        return _expected(
            "Request was browser-cached (304) during recording. "
            f"During replay the server received it fresh but auth expired → 401.",
            f"Re-record a fresh session. Auth expires after ~{expiry_hours} hours.",
        )

    # ── Step 17: Rate limiting ────────────────────────────────────────────────
    if r == 429:
        return _investigate(
            "Server returned 429 Too Many Requests. Replay fires requests "
            "faster than a human — rate limiters can trigger.",
            "Use -Speed 0.5 flag to replay slower. Check if the app has "
            "configurable rate-limit thresholds for testing.",
        )

    # ── Step 18: Transient infra errors ──────────────────────────────────────
    if r in (502, 503, 504):
        return _investigate(
            f"Server returned {r} during replay — transient infrastructure "
            "issue (gateway timeout, container restart, overload).",
            f"Re-run the replay. If {r} recurs consistently, investigate "
            "server stability.",
        )

    # ── Step 19: 401 Unauthorized ─────────────────────────────────────────────
    if r == 401:
        return _investigate(
            "Endpoint returned 401 during replay. Original succeeded because "
            "the user was authenticated. Auth token or session cookie may have "
            "expired or wasn't injected correctly.",
            f"Check auth expiry (~{expiry_hours} hours). Re-record if stale. "
            "For cookie-based apps: ensure SESSION_DRIVER=database is set so "
            "sessions survive the DB checkpoint restore.",
        )

    # ── Step 20: 403 Forbidden ────────────────────────────────────────────────
    if r == 403:
        return _investigate(
            "403 Forbidden during replay. CSRF token mismatch, role change, "
            "or IP-based access control.",
            "Check if the endpoint requires CSRF tokens or specific roles. "
            "For Laravel/Rails: set SESSION_DRIVER=database so sessions "
            "are restored by checkpoint.",
        )

    # ── Step 21: Remaining 5xx — genuine crash ────────────────────────────────
    if r >= 500:
        return _critical(
            f"Server returned {r} during replay (was {o} during recording). "
            "Application crashed with identical inputs — genuine non-determinism.",
            "Examine server logs at replay time. This is a real bug.",
        )

    # ── Step 22: Status class change ─────────────────────────────────────────
    if o > 0 and (o // 100) != (r // 100):
        return _investigate(
            f"Response class changed {o//100}xx → {r//100}xx. "
            "Server returned a fundamentally different response category. "
            "Usually missing session state, changed DB records, or "
            "operation ordering that differs between recording and replay.",
            "Most common causes: session not restored (set SESSION_DRIVER=database), "
            "CSRF token mismatch (419), or a resource created mid-session that "
            "doesn't exist at replay time.",
        )

    # ── Step 23: Same status, body differed ──────────────────────────────────
    return _investigate(
        f"Status matched ({o}) but response body differed. "
        "Non-deterministic fields: timestamps, auto-generated IDs, "
        "random tokens, or mutable records that changed between runs.",
        "Check diff_summary to identify which fields changed. "
        "Add non-deterministic field paths to divergence_config.yaml "
        "custom_rules to suppress them.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Claude API fallback — INVESTIGATE cases only
# ─────────────────────────────────────────────────────────────────────────────

def _ask_claude(method, path, o_st, r_st, diff_summary) -> Optional[Dict]:
    prompt = (
        "You are classifying HTTP replay divergences for DLTRF "
        "(Deterministic Log Test Replay Framework).\n\n"
        f"  Method:   {method}\n"
        f"  Path:     {path}\n"
        f"  Recorded: {o_st}\n"
        f"  Replayed: {r_st}\n"
        f"  Diff:     {diff_summary}\n\n"
        "Tiers:\n"
        "  EXPECTED   = harmless replay artefact (cache 304→200, WebSocket 400, "
        "duplicate DB insert → 400/409/500, session injected on login, "
        "419 CSRF mismatch, 429, transient 503)\n"
        "  INVESTIGATE = real difference needing a look (auth, missing resource) "
        "but NOT a confirmed bug\n"
        "  CRITICAL   = genuine non-determinism — same input, reproducibly different "
        "output, not explained by state or infrastructure\n\n"
        "419 = ALWAYS EXPECTED (CSRF token mismatch — one-time-use, not an app bug).\n"
        "POST → 400/409/500 on creation/collection endpoints = EXPECTED.\n"
        "302 → 419 = EXPECTED (CSRF on form POST redirect).\n"
        "502/503/504 = INVESTIGATE, not CRITICAL.\n"
        "405 on POST = INVESTIGATE (session auth issue).\n\n"
        "Reply ONLY with valid JSON, no markdown:\n"
        '{"tier":"EXPECTED","is_expected":true,"reason":"one sentence",'
        '"recommendation":"one sentence"}'
    )
    try:
        resp = http_requests.post(
            CLAUDE_API_URL,
            headers={"Content-Type": "application/json"},
            json={
                "model":      CLAUDE_MODEL,
                "max_tokens": 300,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=8,
        )
        resp.raise_for_status()
        raw = "".join(
            b.get("text", "") for b in resp.json().get("content", [])
            if b.get("type") == "text"
        ).strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$",        "", raw)
        return json.loads(raw.strip())
    except Exception as e:
        logger.debug(f"Claude unavailable ({type(e).__name__}) — config classifier only")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DivergenceDetector
# ─────────────────────────────────────────────────────────────────────────────

class DivergenceDetector:
    """
    Compares original vs replay HTTP responses and classifies divergences.

    App-agnostic: handles JWT Bearer (Juice Shop, SPAs) and session cookie
    apps (BookStack/Laravel, WordPress, Rails, Django) without code changes.

    Rules from divergence_config.yaml — no hardcoded patterns in Python.
    To add/fix a rule: edit the YAML, restart the container.
    """

    def __init__(self, use_ai_analysis: bool = True):
        self.use_ai = use_ai_analysis
        self._cfg   = _load_config()
        self._cache: Dict[str, Dict] = {}
        logger.info(
            f"DivergenceDetector ready — "
            f"config={_CONFIG_PATH}, AI={'on' if use_ai_analysis else 'off'}"
        )

    def compare_responses(
        self,
        original: Dict[str, Any],
        replay:   Dict[str, Any],
        event_id: str,
        method:   str = "GET",
        path:     str = "/",
    ) -> Dict[str, Any]:
        orig_cmp = {"status": original.get("status"), "body": original.get("body")}
        repl_cmp = {"status": replay.get("status"),   "body": replay.get("body")}

        diff = DeepDiff(orig_cmp, repl_cmp, ignore_order=True, verbose_level=2)

        if not diff:
            return {
                "event_id": event_id, "diverged": False,
                "method":   method,   "path":     path,
                "message":  "Exact match",
            }

        diff_summary = self._summarise(diff, original, replay)
        analysis     = self._run(method, path,
                                 original.get("status"), replay.get("status"),
                                 diff_summary)

        return {
            "event_id":        event_id,
            "diverged":        True,
            "method":          method,
            "path":            path,
            "original_status": original.get("status"),
            "replay_status":   replay.get("status"),
            "diff_summary":    diff_summary,
            "tier":            analysis.get("tier",           "INVESTIGATE"),
            "is_expected":     analysis.get("is_expected",    False),
            "reason":          analysis.get("reason",         ""),
            "recommendation":  analysis.get("recommendation", ""),
        }

    def get_summary(self, divergences: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_tier: Dict[str, List] = {"EXPECTED": [], "INVESTIGATE": [], "CRITICAL": []}
        for d in divergences:
            by_tier.setdefault(d.get("tier", "INVESTIGATE"), []).append(d)
        return {
            "total_divergences":   len(divergences),
            "expected":            len(by_tier["EXPECTED"]),
            "needs_investigation": len(by_tier["INVESTIGATE"]),
            "critical":            len(by_tier["CRITICAL"]),
            "by_tier":             by_tier,
        }

    def _run(self, method, path, o_st, r_st, diff_summary):
        key = f"{method}|{o_st}|{r_st}|{path[:80]}"
        if key in self._cache:
            return self._cache[key]

        result = classify(method, path, o_st, r_st, diff_summary, self._cfg)

        # Claude only runs on INVESTIGATE — cannot override EXPECTED from config
        if result["tier"] == "INVESTIGATE" and self.use_ai:
            ai = _ask_claude(method, path, o_st, r_st, diff_summary)
            if ai and ai.get("tier") in ("EXPECTED", "INVESTIGATE", "CRITICAL"):
                result = ai

        self._cache[key] = result
        return result

    def _summarise(self, diff, original, replay) -> str:
        parts = []
        o, r = original.get("status"), replay.get("status")
        if o != r:
            parts.append(f"Status code changed: {o} → {r}")
        if "values_changed" in diff:
            for k, c in list(diff["values_changed"].items())[:3]:
                parts.append(f"Value at {k}: {c.get('old_value')} → {c.get('new_value')}")
        if "dictionary_item_added"   in diff:
            parts.append(f"{len(diff['dictionary_item_added'])} field(s) added")
        if "dictionary_item_removed" in diff:
            parts.append(f"{len(diff['dictionary_item_removed'])} field(s) removed")
        if "iterable_item_added"     in diff:
            parts.append(f"{len(diff['iterable_item_added'])} list item(s) added")
        if "iterable_item_removed"   in diff:
            parts.append(f"{len(diff['iterable_item_removed'])} list item(s) removed")
        if "type_changes"            in diff:
            parts.append(f"Type changed for {len(diff['type_changes'])} field(s)")
        return " | ".join(parts) if parts else str(diff)[:300]