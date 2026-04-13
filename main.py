"""
VectraSpace — main.py
Application factory. Auth removed — all routes are public.
users.py and auth_routes.py deleted from project.
"""

import os
import logging
import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from contextlib import asynccontextmanager

from config import CFG
from database import init_db

from security import SecurityMiddleware, log_security_startup, config_router
from pages import router as pages_router
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
                # FIX Bug 3: use keyword args so positional order doesn't matter
                result = await loop.run_in_executor(
                    None,
                    functools.partial(
                        run_pipeline, CFG,
                        covariance_cache={},
                        run_mode="scheduled",
                        user_id="__auto__",
                        user_prefs={},
                    )
                )
                # Store result for demo mode
                try:
                    app.state.last_result = result
                except Exception:
                    pass
            except Exception as e:
                log.exception(f"[auto-scan] failed: {e}")
            finally:
                _scan_state["running"] = False

        await asyncio.sleep(AUTO_SCAN_INTERVAL_H * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(CFG)
    log_security_startup()
    # FIX Bug 2: initialise app.state so satellites.py can write to it safely
    app.state.demo_result = None
    app.state.last_result = None
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
            content={"error": "Internal server error", "exception": str(exc)},
        )

    app.include_router(pages_router)
    app.include_router(sat_router)
    app.include_router(admin_router)
    app.include_router(config_router)
    app.include_router(trajectory_router, prefix="/api/tools", tags=["tools"])

    return app


app = create_app()
