import os
import logging
import time
import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from contextlib import asynccontextmanager   # ← THIS LINE FIXES THE ERROR

from config import CFG
from database import init_db
from users import create_user, load_users, save_users

from security import SecurityMiddleware, log_security_startup, config_router
from pages import router as pages_router
from auth_routes import router as auth_router
from satellites import router as sat_router
from admin import router as admin_router
from trajectory import router as trajectory_router

log = logging.getLogger("VectraSpace")
logging.basicConfig(level=logging.DEBUG)

_scan_state = {"time": 0, "running": False, "count": 0}
AUTO_SCAN_INTERVAL_H = 6

async def _auto_scan_loop():
    import functools
    from pipeline import run_pipeline

    await asyncio.sleep(20)
    while True:
        if not _scan_state["running"]:
            _scan_state["running"] = True
            try:
                log.info("[auto-scan] starting")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    functools.partial(run_pipeline, CFG, "scheduled", "__auto__")
                )
            except Exception as e:
                log.exception(f"[auto-scan] failed: {e}")
            finally:
                _scan_state["running"] = False

        await asyncio.sleep(AUTO_SCAN_INTERVAL_H * 3600)

def _ensure_admin():
    admin_user = os.environ.get("ADMIN_USER", "admin").lower().strip()
    admin_pass = os.environ.get("ADMIN_PASS") or ""

    try:
        users = load_users(CFG)
        if admin_user not in users:
            create_user(admin_user, admin_pass, "admin", cfg=CFG)
            log.info(f"Created admin '{admin_user}'")
    except Exception as e:
        log.exception(f"Admin init error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(CFG)
    _ensure_admin()
    log_security_startup()
    task = asyncio.create_task(_auto_scan_loop())
    log.info("[startup] ready")
    yield
    task.cancel()

def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(Exception)
    async def all_exception_handler(request: Request, exc: Exception):
        log.exception(f"Unhandled error on {request.url.path}: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "exception": str(exc),
            },
        )

    app.include_router(pages_router)
    app.include_router(auth_router)
    app.include_router(sat_router)
    app.include_router(admin_router)
    app.include_router(config_router)
    app.include_router(trajectory_router, prefix="/api/tools", tags=["tools"])

    return app

app = create_app()
