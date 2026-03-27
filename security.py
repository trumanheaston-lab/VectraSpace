import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

log = logging.getLogger("VectraSpace")

# ---------------------------------------------------
# Security Middleware
# ---------------------------------------------------

class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()

        response = await call_next(request)

        # ---- Remove insecure headers safely ----
        SAFE_REMOVE_HEADERS = [
            "server",
            "x-powered-by",
            "x-frame-options",
        ]

        for h in SAFE_REMOVE_HEADERS:
            if h in response.headers:
                try:
                    # correct way — works with MutableHeaders
                    response.headers.remove(h)
                except Exception:
                    # fallback method (MutableHeaders always supports __delitem__)
                    try:
                        del response.headers[h]
                    except KeyError:
                        pass

        # ---- Add safe recommended headers ----
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        elapsed = (time.time() - start) * 1000
        log.debug(f"{request.method} {request.url.path} → {response.status_code} ({elapsed:.1f}ms)")

        return response


# ---------------------------------------------------
# One-time startup log
# ---------------------------------------------------

def log_security_startup():
    log.info("Security middleware loaded — headers sanitized safely.")

# Router for config (if you use it)
from fastapi import APIRouter
config_router = APIRouter()
