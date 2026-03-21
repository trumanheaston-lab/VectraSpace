"""
VectraSpace v11 — admin.py
/admin (HTML console) and /admin/data (JSON stats endpoint).
Requires role == 'admin'.
"""

import datetime
import logging
import os
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from config import CFG
from users import get_current_user, load_users, save_users

log    = logging.getLogger("VectraSpace")
router = APIRouter()


# ── Admin HTML (imported from templates_loader) ───────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user = get_current_user(request, CFG)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/login?next=/admin", status_code=303)
    from templates_loader import ADMIN_HTML
    return HTMLResponse(ADMIN_HTML)


# ── Admin data JSON ───────────────────────────────────────────────────────────

@router.get("/admin/data")
def admin_data(request: Request):
    user = get_current_user(request, CFG)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)

    now      = datetime.datetime.utcnow()
    week_ago = (now - datetime.timedelta(days=7)).isoformat()

    all_users = list(load_users(CFG).values())
    new_7d    = sum(1 for u in all_users if u.get("created_at","0") >= week_ago)

    try:
        with sqlite3.connect(CFG.db_path) as con:
            scan_counts = {r[0]: r[1] for r in con.execute(
                "SELECT user_id, COUNT(*) FROM conjunctions GROUP BY user_id"
            ).fetchall()}
    except Exception:
        scan_counts = {}

    users_out = [
        {
            "username":   u.get("username",""),
            "email":      u.get("email",""),
            "role":       u.get("role","operator"),
            "approved":   u.get("approved", True),
            "created_at": u.get("created_at",""),
            "scan_count": scan_counts.get(u.get("username",""), 0),
        }
        for u in sorted(all_users, key=lambda x: x.get("created_at",""), reverse=True)
    ]

    try:
        with sqlite3.connect(CFG.db_path) as con:
            total_conj = con.execute("SELECT COUNT(*) FROM conjunctions").fetchone()[0]
            total_runs = con.execute(
                "SELECT COUNT(DISTINCT run_time) FROM conjunctions"
            ).fetchone()[0]
            recent = con.execute(
                "SELECT run_time,user_id,sat1,sat2,regime1,regime2,min_dist_km,pc_estimate "
                "FROM conjunctions ORDER BY id DESC LIMIT 50"
            ).fetchall()
            recent_out = [{"run_time":r[0],"user_id":r[1] or "anon","sat1":r[2],"sat2":r[3],
                           "regime1":r[4],"regime2":r[5],"min_dist_km":r[6],"pc_estimate":r[7]}
                          for r in recent]
            thirty_ago = (now - datetime.timedelta(days=30)).date().isoformat()
            daily_raw  = con.execute(
                "SELECT DATE(run_time) day, COUNT(DISTINCT run_time) cnt "
                "FROM conjunctions WHERE DATE(run_time)>=? GROUP BY day ORDER BY day DESC",
                (thirty_ago,),
            ).fetchall()
            daily_out  = [{"day": r[0], "count": r[1]} for r in daily_raw]
            regime_raw = con.execute(
                "SELECT regime1||'/'||regime2 pair, COUNT(*) cnt "
                "FROM conjunctions GROUP BY pair ORDER BY cnt DESC LIMIT 6"
            ).fetchall()
            regime_out = [{"pair": r[0], "count": r[1]} for r in regime_raw]
    except Exception:
        total_conj = 0; total_runs = 0
        recent_out = []; daily_out = []; regime_out = []

    return JSONResponse({
        "total_users":         len(all_users),
        "total_scan_runs":     total_runs,
        "total_conjunctions":  total_conj,
        "new_users_7d":        new_7d,
        "users":               users_out,
        "recent_conjunctions": recent_out,
        "daily_scans":         daily_out,
        "regime_breakdown":    regime_out,
        "umami_url":  os.environ.get("UMAMI_SCRIPT_URL",  "https://cloud.umami.is/script.js"),
        "umami_id":   os.environ.get("UMAMI_WEBSITE_ID",  "4e12fc04-8b26-4e42-8b69-0700a95c7d30"),
    })
