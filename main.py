"""
VectraSpace v11 — FastAPI application entrypoint.
This version includes the trajectory API router and guards token imports.
"""

import os
import logging
import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CFG
from database import init_db
from users import create_user, load_users, save_users

# Security & rate-limit layer
from security import SecurityMiddleware, log_security_startup, config_router

log = logging.getLogger("VectraSpace")

_scan_state: dict = {"time": 0, "running": False, "count": 0}
AUTO_SCAN_INTERVAL_H = 6

async def _auto_scan_loop():
    import functools
    from pipeline import run_pipeline

    await asyncio.sleep(30)
    while True:
        if not _scan_state["running"]:
            try:
                _scan_state["running"] = True
                log.info("[auto-scan] starting next run")
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    functools.partial(run_pipeline, CFG, "scheduled", "__auto__")
                )
                _scan_state["time"] = time.time()
                _scan_state["count"] = len(result.get("conjunctions", []))
            except Exception as e:
                log.error(f"[auto-scan] crashed: {e}")
            finally:
                _scan_state["running"] = False

        await asyncio.sleep(AUTO_SCAN_INTERVAL_H * 3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    init_db(CFG)
    _ensure_admin()
    log_security_startup()
    task = asyncio.create_task(_auto_scan_loop())
    log.info("[startup] ready")
    yield
    task.cancel()

def _ensure_admin():
    admin_user = os.environ.get("ADMIN_USER", "admin").lower().strip()
    admin_pass = (
        os.environ.get("ADMIN_PASS", "").strip() or
        os.environ.get("ADMIN_PASSCODE", "").strip()
    )
    if not admin_pass:
        log.warning("[startup] default admin password used")

    try:
        existing = load_users(CFG)
        if admin_user not in existing:
            create_user(admin_user, admin_pass, "admin", cfg=CFG)
            log.info("[startup] admin created")
        else:
            existing[admin_user]["role"] = "admin"
            save_users(existing, CFG)
    except Exception as e:
        log.error(f"[startup] user init failed: {e}")

def create_app() -> FastAPI:
    app = FastAPI(
        title="VectraSpace API v11",
        version="11.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityMiddleware)

    app.state.scan_state = _scan_state

    # — existing routers —
    from pages import router as pages_router
    from auth_routes import router as auth_router
    from satellites import router as sat_router
    from admin import router as admin_router
    from trajectory import router as trajectory_router

    app.include_router(pages_router)
    app.include_router(auth_router)
    app.include_router(sat_router)
    app.include_router(admin_router)
    app.include_router(config_router)

    # — trajectory API (under /api/tools) —
    app.include_router(
        trajectory_router,
        prefix="/api/tools",
        tags=["tools"],
    )

    return app

app = create_app()
