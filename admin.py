"""
VectraSpace — admin.py
/admin (HTML console) and /admin/data (JSON stats endpoint).
Auth removed — all routes are public. The admin console shows
platform-wide conjunction stats and scan history.
"""

import datetime
import logging
import os
import sqlite3

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from config import CFG

log    = logging.getLogger("VectraSpace")
router = APIRouter()


# ── Admin HTML ────────────────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
def admin_page():
    from templates_loader import ADMIN_HTML
    return HTMLResponse(ADMIN_HTML)


# ── Admin data JSON ───────────────────────────────────────────────────────────

@router.get("/admin/data")
def admin_data():
    now        = datetime.datetime.utcnow()
    thirty_ago = (now - datetime.timedelta(days=30)).date().isoformat()

    try:
        with sqlite3.connect(CFG.db_path) as con:
            total_conj = con.execute(
                "SELECT COUNT(*) FROM conjunctions"
            ).fetchone()[0]

            total_runs = con.execute(
                "SELECT COUNT(DISTINCT run_time) FROM conjunctions"
            ).fetchone()[0]

            recent_rows = con.execute(
                "SELECT run_time, user_id, sat1, sat2, regime1, regime2, "
                "min_dist_km, pc_estimate "
                "FROM conjunctions ORDER BY id DESC LIMIT 50"
            ).fetchall()
            recent_out = [
                {
                    "run_time":    r[0],
                    "user_id":     r[1] or "public",
                    "sat1":        r[2],
                    "sat2":        r[3],
                    "regime1":     r[4],
                    "regime2":     r[5],
                    "min_dist_km": r[6],
                    "pc_estimate": r[7],
                }
                for r in recent_rows
            ]

            daily_raw = con.execute(
                "SELECT DATE(run_time) day, COUNT(DISTINCT run_time) cnt "
                "FROM conjunctions WHERE DATE(run_time)>=? "
                "GROUP BY day ORDER BY day DESC",
                (thirty_ago,),
            ).fetchall()
            daily_out = [{"day": r[0], "count": r[1]} for r in daily_raw]

            regime_raw = con.execute(
                "SELECT regime1||'/'||regime2 pair, COUNT(*) cnt "
                "FROM conjunctions GROUP BY pair ORDER BY cnt DESC LIMIT 6"
            ).fetchall()
            regime_out = [{"pair": r[0], "count": r[1]} for r in regime_raw]

    except Exception:
        total_conj = 0
        total_runs = 0
        recent_out = []
        daily_out  = []
        regime_out = []

    return JSONResponse({
        "total_users":         0,      # users table removed
        "total_scan_runs":     total_runs,
        "total_conjunctions":  total_conj,
        "new_users_7d":        0,
        "users":               [],
        "recent_conjunctions": recent_out,
        "daily_scans":         daily_out,
        "regime_breakdown":    regime_out,
        "umami_url": os.environ.get("UMAMI_SCRIPT_URL",
                                    "https://cloud.umami.is/script.js"),
        "umami_id":  os.environ.get("UMAMI_WEBSITE_ID",
                                    "4e12fc04-8b26-4e42-8b69-0700a95c7d30"),
    })
