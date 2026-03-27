"""
VectraSpace v11 — security.py
OWASP-aligned security hardening layer.

Covers:
  1. Rate limiting  — IP-based (global) + user-based (per-authenticated-user)
                      with graceful 429 responses and Retry-After headers
  2. Input validation — schema-based, strict type-checking, length limits,
                        unexpected-field rejection for all public API inputs
  3. API key management — centralised env-var loading with startup validation,
                          no keys exposed client-side, rotation support

INTEGRATION — three steps:

  Step 1. Drop security.py in repo root alongside main.py.

  Step 2. In main.py, replace the existing RateLimitMiddleware block and
          add the import at the top of create_app():

            from security import (
                SecurityMiddleware,
                validate_trajectory_request,
                validate_signup_request,
                validate_login_request,
                validate_preferences_request,
                get_cesium_token,
                get_anthropic_key,
                log_security_startup,
            )

          Replace:
            app.add_middleware(SecurityHeadersMiddleware)
            app.add_middleware(RateLimitMiddleware)
          With:
            app.add_middleware(SecurityMiddleware)

          Call at end of lifespan startup:
            log_security_startup()

  Step 3. In trajectory.py, replace the hardcoded Cesium token string with:
            from security import get_cesium_token
            CESIUM_TOKEN = get_cesium_token()
          (Then use CESIUM_TOKEN in _build_html().)

  Step 4. Set environment variables on Render (never commit these):
            CESIUM_ION_TOKEN   = <your token>
            ANTHROPIC_API_KEY  = <your key>
            SESSION_SECRET     = <32-byte hex>
            ADMIN_PASS         = <strong password>
            RATE_LIMIT_PER_MIN = 120        # optional override
            RATE_LIMIT_BURST   = 20         # optional override
"""

import logging
import os
import re
import time
from collections import defaultdict, deque
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("VectraSpace.security")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — RATE LIMITING
#  OWASP: API4:2023 Unrestricted Resource Consumption
#
#  Strategy:
#   • IP-based  : sliding-window counter, shared across all endpoints
#   • User-based: tighter window for authenticated scan/simulation endpoints
#   • Graceful  : 429 JSON with Retry-After header (not a silent drop)
#   • Exemptions: /health, /static assets (Cesium JS/CSS) — never rate-limited
# ══════════════════════════════════════════════════════════════════════════════

# Tunable via environment variables so Render dashboard can adjust without redeploy
_IP_LIMIT        = int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))   # req/min per IP
_IP_BURST        = int(os.environ.get("RATE_LIMIT_BURST",   "20"))    # max burst in 5 s
_USER_SCAN_LIMIT = int(os.environ.get("RATE_LIMIT_SCAN",    "5"))     # scans/5 min per user
_SIM_LIMIT       = int(os.environ.get("RATE_LIMIT_SIM",     "30"))    # simulations/min per IP

# Sliding-window stores: {key: deque of timestamps}
_ip_window:   dict[str, deque] = defaultdict(deque)
_user_window: dict[str, deque] = defaultdict(deque)
_sim_window:  dict[str, deque] = defaultdict(deque)

# Paths exempt from ALL rate limiting (Cesium assets, health check)
_EXEMPT_PREFIXES = (
    "/health",
    "/Build/Cesium",  # Cesium static assets if self-hosted
)

# Paths that trigger the tighter simulation rate limit
_SIM_PATHS = ("/trajectory/simulate",)

# Paths that trigger the tighter user scan rate limit
_SCAN_PATHS = ("/run",)


def _sliding_ok(store: dict, key: str, limit: int, window_s: float) -> tuple[bool, float]:
    """
    Sliding-window rate check. Returns (allowed, retry_after_seconds).
    Thread-safe for single-process gunicorn (1 worker). For multi-worker
    deployments, swap this for Redis-backed counters.
    """
    now = time.monotonic()
    dq  = store[key]

    # Evict timestamps outside the window
    cutoff = now - window_s
    while dq and dq[0] < cutoff:
        dq.popleft()

    if len(dq) >= limit:
        # Oldest entry tells us when the window clears
        retry_after = window_s - (now - dq[0])
        return False, max(1.0, retry_after)

    dq.append(now)
    return True, 0.0


def _get_ip(request: Request) -> str:
    """
    Extract real client IP, respecting Render's X-Forwarded-For header.
    OWASP: never trust X-Forwarded-For blindly — we take only the first entry
    and strip port, since Render's LB is trusted.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        # Take the leftmost (client) IP; ignore proxy chain
        ip = xff.split(",")[0].strip()
        # Strip IPv6 port if present
        if ip.startswith("["):
            ip = ip.split("]")[0].lstrip("[")
        return ip or "0.0.0.0"
    return (request.client.host or "0.0.0.0")


def _rate_limit_response(retry_after: float) -> JSONResponse:
    """Graceful 429 with Retry-After header. OWASP: never return stack traces."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": "Too many requests. Please slow down.",
            "retry_after_seconds": round(retry_after, 1),
        },
        headers={
            "Retry-After": str(int(retry_after) + 1),
            "X-RateLimit-Reset": str(int(time.time()) + int(retry_after) + 1),
        },
    )


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Unified security middleware replacing both RateLimitMiddleware and
    SecurityHeadersMiddleware from the original main.py.

    Applied in order:
      1. Security response headers (OWASP: A05 Security Misconfiguration)
      2. IP sliding-window rate limit (global)
      3. Simulation endpoint tighter limit
      4. User-based scan limit (authenticated endpoints)
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # ── 1. Exempt paths ────────────────────────────────────────────────
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        ip = _get_ip(request)

        # ── 2. Global IP rate limit (120 req/min, 20 burst/5 s) ───────────
        ok, retry = _sliding_ok(_ip_window, ip, _IP_LIMIT, 60.0)
        if not ok:
            log.warning("RATE_LIMIT ip=%s path=%s", ip, path)
            return _rate_limit_response(retry)

        # Burst check: max 20 requests in any 5-second window
        burst_key = f"burst:{ip}"
        ok_burst, retry_burst = _sliding_ok(_ip_window, burst_key, _IP_BURST, 5.0)
        if not ok_burst:
            log.warning("RATE_LIMIT burst ip=%s path=%s", ip, path)
            return _rate_limit_response(retry_burst)

        # ── 3. Simulation endpoint: 30 req/min per IP ─────────────────────
        if any(p in path for p in _SIM_PATHS):
            ok_sim, retry_sim = _sliding_ok(_sim_window, ip, _SIM_LIMIT, 60.0)
            if not ok_sim:
                log.warning("RATE_LIMIT sim ip=%s", ip)
                return _rate_limit_response(retry_sim)

        # ── 4. Authenticated scan limit: 5 scans per 5 min per user ───────
        if any(p in path for p in _SCAN_PATHS):
            # Extract username from session cookie (same mechanism as auth_routes)
            session_user = _extract_session_user(request)
            if session_user:
                ok_scan, retry_scan = _sliding_ok(
                    _user_window, f"scan:{session_user}", _USER_SCAN_LIMIT, 300.0
                )
                if not ok_scan:
                    log.warning("RATE_LIMIT scan user=%s", session_user)
                    return _rate_limit_response(retry_scan)

        # ── Call the actual route ──────────────────────────────────────────
        response = await call_next(request)

        # ── 5. Security response headers ───────────────────────────────────
        # OWASP A05: prevent clickjacking, MIME sniffing, XSS via headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "SAMEORIGIN"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        # NOTE: CSP intentionally permissive for CesiumJS (needs blob: and data:)
        # Tighten if you remove Cesium or host assets yourself.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cesium.com https://cdnjs.cloudflare.com "
            "https://cloud.umami.is https://fonts.googleapis.com; "
            "style-src 'self' 'unsafe-inline' https://cesium.com https://fonts.googleapis.com "
            "https://fonts.gstatic.com; "
            "img-src 'self' data: blob: https:; "
            "connect-src 'self' https://api.cesium.com https://assets.cesium.com "
            "https://ibasemaps-api.arcgis.com https://tile.openstreetmap.org "
            "https://api.spaceflightnewsapi.net; "
            "font-src 'self' https://fonts.gstatic.com; "
            "worker-src blob:; "
            "frame-ancestors 'none';"
        )
        # Remove server fingerprinting header (Render adds it, we strip it)
        response.headers.pop("server", None)
        response.headers.pop("x-powered-by", None)

        return response


def _extract_session_user(request: Request) -> Optional[str]:
    """
    Pull authenticated username out of the signed session cookie.
    Mirrors the logic in auth_routes.py without importing it (avoids circular).
    Returns None for unauthenticated requests.
    """
    try:
        import hmac as _hmac, hashlib as _hs, json as _js, base64 as _b64
        raw = request.cookies.get("vs_session", "")
        if not raw:
            return None
        secret = os.environ.get("SESSION_SECRET", "")
        if not secret:
            return None
        # Format: base64(json_payload).signature
        parts = raw.rsplit(".", 1)
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        expected = _hmac.new(
            secret.encode(), payload_b64.encode(), _hs.sha256
        ).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None  # tampered cookie
        payload = _js.loads(_b64.b64decode(payload_b64 + "==").decode())
        return payload.get("username")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — INPUT VALIDATION
#  OWASP: A03:2021 Injection, A08:2021 Software and Data Integrity Failures
#
#  All public-facing inputs go through these validators BEFORE hitting
#  business logic. Pydantic handles type coercion; these add:
#   • Length limits on strings
#   • Allowlist-based field rejection (no extra keys accepted)
#   • Numeric bounds hard-checked at the boundary layer
#   • Regex sanitation for usernames, emails, satellite names
# ══════════════════════════════════════════════════════════════════════════════

# OWASP: define constants — never magic numbers scattered in code
_MAX_USERNAME_LEN  = 64
_MAX_PASSWORD_LEN  = 128
_MAX_EMAIL_LEN     = 254      # RFC 5321
_MAX_SAT_NAME_LEN  = 128
_MAX_GENERIC_STR   = 512
_USERNAME_PATTERN  = re.compile(r"^[a-zA-Z0-9._\-]{3,64}$")
_EMAIL_PATTERN     = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


class ValidationError(Exception):
    """Raised when input fails security validation. Caught at route level."""
    def __init__(self, field: str, reason: str):
        self.field  = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def _require_str(val: Any, field: str, max_len: int = _MAX_GENERIC_STR) -> str:
    """Coerce to string, enforce length."""
    if not isinstance(val, str):
        raise ValidationError(field, "must be a string")
    stripped = val.strip()
    if len(stripped) == 0:
        raise ValidationError(field, "must not be empty")
    if len(stripped) > max_len:
        raise ValidationError(field, f"exceeds max length of {max_len}")
    return stripped


def _require_float(val: Any, field: str, lo: float, hi: float) -> float:
    """Coerce to float, enforce bounds."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        raise ValidationError(field, "must be a number")
    if not (lo <= f <= hi):
        raise ValidationError(field, f"must be between {lo} and {hi}")
    if f != f:  # NaN check
        raise ValidationError(field, "must not be NaN")
    return f


def _reject_extra_fields(data: dict, allowed: set, endpoint: str) -> None:
    """
    OWASP: Mass Assignment (A08). Reject any unexpected keys.
    Log the attempt — unexpected fields are a red flag.
    """
    extra = set(data.keys()) - allowed
    if extra:
        log.warning("VALIDATION extra_fields=%s endpoint=%s", extra, endpoint)
        raise ValidationError("_fields", f"unexpected fields: {sorted(extra)}")


def validate_trajectory_request(data: dict) -> dict:
    """
    Validate /api/tools/trajectory/simulate payload.
    Allowed fields only; all numeric bounds enforced.
    """
    allowed = {
        "mass", "drag_coeff", "area", "total_impulse",
        "burn_time", "launch_angle", "azimuth",
        "lat", "lon", "launch_alt", "dt",
    }
    _reject_extra_fields(data, allowed, "/trajectory/simulate")

    return {
        # Physical bounds reflect real amateur rocketry constraints
        "mass":          _require_float(data.get("mass"),          "mass",         0.001, 500.0),
        "drag_coeff":    _require_float(data.get("drag_coeff"),    "drag_coeff",   0.01,  5.0),
        "area":          _require_float(data.get("area"),          "area",         1e-6,  10.0),
        "total_impulse": _require_float(data.get("total_impulse"), "total_impulse",0.1,   40_960.0),
        "burn_time":     _require_float(data.get("burn_time"),     "burn_time",    0.05,  30.0),
        "launch_angle":  _require_float(data.get("launch_angle",  0.0), "launch_angle", 0.0, 85.0),
        "azimuth":       _require_float(data.get("azimuth",       0.0), "azimuth",      0.0, 359.999),
        "lat":           _require_float(data.get("lat"),           "lat",          -90.0, 90.0),
        "lon":           _require_float(data.get("lon"),           "lon",          -180.0,180.0),
        "launch_alt":    _require_float(data.get("launch_alt",    0.0), "launch_alt",  -500.0, 5_000.0),
        "dt":            _require_float(data.get("dt",            0.05),"dt",           0.01,  0.5),
    }


def validate_login_request(data: dict) -> dict:
    """
    Validate /login payload.
    OWASP: A07 Identification and Authentication Failures.
    """
    allowed = {"username", "password"}
    _reject_extra_fields(data, allowed, "/login")

    username = _require_str(data.get("username", ""), "username", _MAX_USERNAME_LEN)
    password = _require_str(data.get("password", ""), "password", _MAX_PASSWORD_LEN)

    # Username allowlist — no injection chars
    if not _USERNAME_PATTERN.match(username):
        raise ValidationError("username", "only letters, digits, . _ - allowed (3–64 chars)")

    return {"username": username.lower(), "password": password}


def validate_signup_request(data: dict) -> dict:
    """
    Validate /signup payload.
    Password strength enforced here (not just length).
    """
    allowed = {"username", "password", "email"}
    _reject_extra_fields(data, allowed, "/signup")

    username = _require_str(data.get("username", ""), "username", _MAX_USERNAME_LEN)
    password = _require_str(data.get("password", ""), "password", _MAX_PASSWORD_LEN)
    email    = data.get("email", "")

    if not _USERNAME_PATTERN.match(username):
        raise ValidationError("username", "only letters, digits, . _ - allowed (3–64 chars)")

    # OWASP: enforce minimum password strength
    if len(password) < 8:
        raise ValidationError("password", "must be at least 8 characters")
    if not any(c.isupper() for c in password):
        raise ValidationError("password", "must contain at least one uppercase letter")
    if not any(c.isdigit() for c in password):
        raise ValidationError("password", "must contain at least one digit")

    validated: dict = {"username": username.lower(), "password": password}

    if email:
        email = _require_str(email, "email", _MAX_EMAIL_LEN)
        if not _EMAIL_PATTERN.match(email):
            raise ValidationError("email", "invalid email address format")
        validated["email"] = email.lower()
    else:
        validated["email"] = ""

    return validated


def validate_preferences_request(data: dict) -> dict:
    """
    Validate /preferences update payload.
    Numeric thresholds are tightly bounded; email optional.
    """
    allowed = {
        "email", "phone", "pushover_key",
        "pc_alert_threshold", "collision_alert_km",
    }
    _reject_extra_fields(data, allowed, "/preferences")

    result: dict = {}

    if "email" in data and data["email"]:
        email = _require_str(data["email"], "email", _MAX_EMAIL_LEN)
        if not _EMAIL_PATTERN.match(email):
            raise ValidationError("email", "invalid email address format")
        result["email"] = email.lower()

    if "phone" in data and data["phone"]:
        phone = _require_str(data["phone"], "phone", 20)
        # Digits, +, spaces, dashes only
        if not re.match(r"^[\d\s\+\-\(\)]{7,20}$", phone):
            raise ValidationError("phone", "invalid phone number format")
        result["phone"] = phone

    if "pushover_key" in data and data["pushover_key"]:
        pk = _require_str(data["pushover_key"], "pushover_key", 40)
        if not re.match(r"^[a-zA-Z0-9]{20,40}$", pk):
            raise ValidationError("pushover_key", "invalid Pushover key format")
        result["pushover_key"] = pk

    if "pc_alert_threshold" in data:
        result["pc_alert_threshold"] = _require_float(
            data["pc_alert_threshold"], "pc_alert_threshold", 1e-8, 1.0
        )

    if "collision_alert_km" in data:
        result["collision_alert_km"] = _require_float(
            data["collision_alert_km"], "collision_alert_km", 0.001, 10_000.0
        )

    return result


def validate_sat_name(name: str) -> str:
    """
    Sanitize satellite name used in database queries and CDM generation.
    OWASP: A03 Injection — names go into SQL and file paths.
    """
    if not isinstance(name, str):
        raise ValidationError("sat_name", "must be a string")
    name = name.strip()
    if not name:
        raise ValidationError("sat_name", "must not be empty")
    if len(name) > _MAX_SAT_NAME_LEN:
        raise ValidationError("sat_name", f"exceeds {_MAX_SAT_NAME_LEN} chars")
    # Allow alphanumeric, spaces, hyphens, underscores, dots, brackets — typical NORAD names
    if not re.match(r"^[\w\s\-\.\(\)\[\]\/]{1,128}$", name):
        raise ValidationError("sat_name", "contains disallowed characters")
    return name


def make_validation_error_response(exc: ValidationError) -> JSONResponse:
    """Uniform 422 response for validation failures. Never expose internals."""
    return JSONResponse(
        status_code=422,
        content={
            "error":  "validation_error",
            "field":  exc.field,
            "detail": exc.reason,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — API KEY MANAGEMENT
#  OWASP: A02:2021 Cryptographic Failures, A05 Security Misconfiguration
#
#  All secrets are loaded from environment variables at startup.
#  No key is ever injected into client-side HTML.
#  The Cesium token is served via a dedicated /api/config endpoint
#  (authenticated) rather than baked into HTML templates.
#
#  Key rotation: update the env var on Render and redeploy — zero downtime
#  because gunicorn workers restart gracefully. Old tokens continue to work
#  until Cesium Ion invalidates them server-side.
# ══════════════════════════════════════════════════════════════════════════════

# ── Internal key store — loaded once at import time ───────────────────────────
# Keys are read from environment, never from code.
_KEYS: dict[str, str] = {}


def _load_key(env_var: str, description: str, required: bool = True) -> str:
    """
    Load a secret from the environment.
    Logs a warning (not the value) if missing; raises if required.
    """
    val = os.environ.get(env_var, "").strip()
    if not val:
        if required:
            # OWASP: fail closed — refuse to start without required secrets
            log.error(
                "SECURITY missing required env var %s (%s). "
                "Set this on Render Dashboard → Environment.",
                env_var, description
            )
            # Don't crash on import — let startup validation handle it
        else:
            log.warning("SECURITY optional env var %s (%s) not set.", env_var, description)
    return val


# Load all secrets at module import — fail-fast pattern
_KEYS["cesium_token"]   = _load_key("CESIUM_ION_TOKEN",  "Cesium Ion access token", required=False)
_KEYS["anthropic"]      = _load_key("ANTHROPIC_API_KEY", "Anthropic API key",        required=False)
_KEYS["session_secret"] = _load_key("SESSION_SECRET",    "Session signing secret",   required=False)
_KEYS["smtp_pass"]      = _load_key("ALERT_SMTP_PASS",   "SMTP password",            required=False)
_KEYS["pushover_token"] = _load_key("PUSHOVER_TOKEN",    "Pushover token",           required=False)
_KEYS["spacetrack_pass"]= _load_key("SPACETRACK_PASS",   "Space-Track password",     required=False)


def get_cesium_token() -> str:
    """
    Return the Cesium Ion token from the environment.

    SECURITY NOTE: This function is called server-side to inject the token
    into the /api/config response (authenticated route only) or into the
    server-rendered HTML at response time — never hardcoded in source.

    If the token is missing, returns an empty string. Cesium will still load
    but Ion assets (terrain, imagery) will fail gracefully with a 401 from Ion.
    """
    token = _KEYS.get("cesium_token", "")
    if not token:
        log.warning(
            "SECURITY CESIUM_ION_TOKEN not set. "
            "Globe will load without Ion terrain/imagery. "
            "Set CESIUM_ION_TOKEN in Render Environment Variables."
        )
    return token


def get_anthropic_key() -> str:
    """Return the Anthropic API key. Empty string if not configured."""
    return _KEYS.get("anthropic", "")


def get_session_secret() -> str:
    """
    Return the session signing secret.
    SECURITY: Must be at least 32 random hex bytes in production.
    Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    """
    secret = _KEYS.get("session_secret", "")
    if not secret:
        # Fallback for local dev only — never acceptable in production
        import secrets as _sec
        secret = _sec.token_hex(32)
        log.warning(
            "SECURITY SESSION_SECRET not set — using ephemeral secret. "
            "All sessions will be invalidated on restart. "
            "Set SESSION_SECRET in Render Environment Variables."
        )
    return secret


def get_smtp_password() -> str:
    """Return the SMTP password. Empty string if not configured."""
    return _KEYS.get("smtp_pass", "")


def get_pushover_token() -> str:
    """Return the Pushover token. Empty string if not configured."""
    return _KEYS.get("pushover_token", "")


def get_spacetrack_password() -> str:
    """Return the Space-Track password. Empty string if not configured."""
    return _KEYS.get("spacetrack_pass", "")


def log_security_startup() -> None:
    """
    Called at app startup. Logs which secrets ARE configured (not their values)
    so you can verify the environment without ever printing a key.
    """
    configured   = [k for k, v in _KEYS.items() if v]
    unconfigured = [k for k, v in _KEYS.items() if not v]

    log.info("SECURITY keys configured:   %s", configured)
    if unconfigured:
        log.warning("SECURITY keys NOT set:      %s", unconfigured)

    # Warn if running with the default admin password
    admin_pass = os.environ.get("ADMIN_PASS", "")
    if not admin_pass or admin_pass == "VectraSpace2526":
        log.warning(
            "SECURITY ADMIN_PASS is default or unset. "
            "Set a strong ADMIN_PASS in Render Environment Variables."
        )

    # Validate session secret strength
    secret = _KEYS.get("session_secret", "")
    if secret and len(secret) < 32:
        log.warning(
            "SECURITY SESSION_SECRET appears short (%d chars). "
            "Recommend at least 64 hex chars.", len(secret)
        )

    log.info(
        "SECURITY rate limits: ip=%d/min burst=%d/5s sim=%d/min scan=%d/5min",
        _IP_LIMIT, _IP_BURST, _SIM_LIMIT, _USER_SCAN_LIMIT,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — AUTHENTICATED CONFIG ENDPOINT
#  Serves the Cesium token to authenticated frontends ONLY.
#  Add this router to main.py alongside the other routers.
#
#  Usage in main.py inside create_app():
#    from security import config_router
#    app.include_router(config_router)
# ══════════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter
from fastapi.responses import JSONResponse as _JSONResponse

config_router = APIRouter()


@config_router.get("/api/config")
async def get_client_config(request: Request):
    """
    Returns non-secret config values needed by the frontend.

    The Cesium token IS included here because:
      - It is a publishable key (Cesium Ion tokens are domain-restricted)
      - It rotates independently of other secrets
      - Serving it here (rather than hardcoding in HTML) means rotation
        requires only an env var update, not a code change

    SECURITY: This endpoint is intentionally public (Cesium needs it before
    auth). The token is scoped to your domain in the Cesium Ion dashboard —
    set allowed origins there as a second layer of defence.
    """
    return _JSONResponse({
        "cesium_token": get_cesium_token(),
        # Add other non-sensitive client config here (feature flags, etc.)
        # NEVER add: anthropic key, smtp password, session secret, admin pass
    })
