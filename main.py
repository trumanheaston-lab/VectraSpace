"""
VectraSpace v11 — main.py
FastAPI app factory. All routes are registered here via APIRouter includes.
Start command (Render / gunicorn):
    gunicorn main:app -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse

from config import CFG
from database import init_db
from users import create_user, load_users, save_users

log = logging.getLogger("VectraSpace")

# ── IP-level rate limit (120 req/min default) ────────────────────────────────
_ip_hits: dict = {}
_IP_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        ip = (request.client.host or "0.0.0.0")
        now = time.time()
        _ip_hits[ip] = [t for t in _ip_hits.get(ip, []) if now - t < 60]
        if len(_ip_hits[ip]) >= _IP_LIMIT:
            return JSONResponse(
                {"detail": "Rate limit exceeded — try again shortly."}, status_code=429
            )
        _ip_hits[ip].append(now)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


# ── Auto-scan background task ────────────────────────────────────────────────
_scan_state: dict = {"time": 0, "running": False, "count": 0}
AUTO_SCAN_INTERVAL_H = 6


async def _auto_scan_loop():
    import functools
    from pipeline import run_pipeline

    await asyncio.sleep(90)  # grace period after startup
    while True:
        if not _scan_state["running"]:
            try:
                _scan_state["running"] = True
                log.info("[auto-scan] Running scheduled scan...")
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    functools.partial(
                        run_pipeline, CFG, run_mode="scheduled", user_id="__auto__"
                    ),
                )
                _scan_state["count"] = len(result.get("conjunctions", []))
                _scan_state["time"] = time.time()
                log.info(f"[auto-scan] Done — {_scan_state['count']} conjunctions")
            except Exception as e:
                log.warning(f"[auto-scan] Error: {e}")
            finally:
                _scan_state["running"] = False
        await asyncio.sleep(AUTO_SCAN_INTERVAL_H * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    init_db(CFG)
    _ensure_admin()
    task = asyncio.create_task(_auto_scan_loop())
    log.info("[startup] VectraSpace v11 ready")
    yield
    # ── Shutdown ─────────────────────────────────────────────
    task.cancel()


def _ensure_admin():
    admin_user = os.environ.get("ADMIN_USER", "admin").strip().lower()
    admin_pass = (
        os.environ.get("ADMIN_PASS", "").strip()
        or os.environ.get("ADMIN_PASSCODE", "").strip()
        or "VectraSpace2526"
    )
    if admin_pass == "VectraSpace2526":
        log.warning("[startup] No ADMIN_PASS set — using default. Set ADMIN_PASS in env!")
    try:
        existing = load_users(CFG)
        if admin_user not in existing:
            create_user(admin_user, admin_pass, "admin", cfg=CFG)
            log.info(f"[startup] Created admin user '{admin_user}'")
        else:
            if existing[admin_user].get("role") != "admin":
                existing[admin_user]["role"] = "admin"
                save_users(existing, CFG)
            log.info(f"[startup] Admin user '{admin_user}' OK")
    except Exception as e:
        log.warning(f"[startup] Could not init admin user: {e}")


def create_app() -> FastAPI:
    app = FastAPI(
        title="VectraSpace API",
        description="VectraSpace v11 — Orbital Safety Platform",
        version="11.0",
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)

    # ── Shared state (demo results, last scan, etc.) ──────────
    app.state.demo_result = None
    app.state.user_results = {}
    app.state.last_result = None
    app.state.scan_state = _scan_state

    # ── Routers ───────────────────────────────────────────────
    from pages import router as pages_router
    from auth_routes import router as auth_router
    from satellites import router as sat_router
    from admin import router as admin_router

    app.include_router(pages_router)
    app.include_router(auth_router)
    app.include_router(sat_router)
    app.include_router(admin_router)

    return app


# Module-level `app` used by gunicorn/uvicorn
app = create_app()


# ── CLI entrypoint ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="VectraSpace v11")
    parser.add_argument("--headless", action="store_true",
                        help="Run pipeline once without web server")
    parser.add_argument("--create-user", nargs=3, metavar=("USERNAME", "PASSWORD", "ROLE"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.create_user:
        username, password, role = args.create_user
        create_user(username, password, role, cfg=CFG)
        sys.exit(0)

    if args.headless:
        from scheduler import run_headless
        run_headless(CFG)
    else:
        import uvicorn
        import threading
        import webbrowser

        url = f"http://localhost:{args.port}"
        log.info(f"Dashboard: {url}/dashboard")
        if not args.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        uvicorn.run("main:app", host=args.host, port=args.port, reload=False)
