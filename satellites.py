"""
VectraSpace — satellites.py
/run (SSE scan), /conjunctions, /cdm, /sat-info, /debris/simulate,
/demo-results, /feedback, /history.
Auth removed — all routes are public. Scans run as user_id="public".
"""

import asyncio
import concurrent.futures
import io
import json
import logging
import math
import os
import sqlite3
import zipfile
from typing import Optional

import numpy as np
import requests
from fastapi import APIRouter, Request
from fastapi.responses import (JSONResponse, PlainTextResponse,
                                StreamingResponse)

from config import CFG, Config
from database import (init_db, log_conjunctions_to_db, fetch_covariance_cache,
                      generate_cdm)
from conjunction import check_conjunctions, Conjunction
from debris import generate_debris_cloud

log    = logging.getLogger("VectraSpace")
router = APIRouter()

USER_ID_PUBLIC = "public"


# ── /scan-running ─────────────────────────────────────────────────────────────

@router.get("/scan-running")
def scan_running():
    from main import _scan_state
    return JSONResponse({"running": _scan_state.get("running", False)})


# ── /run — SSE scan stream ────────────────────────────────────────────────────

@router.get("/run")
async def run_scan(
    request: Request,
    num_leo: int = 100,
    num_meo: int = 50,
    num_geo: int = 20,
    time_window_hours: float = 12,
    collision_alert_km: float = 10.0,
    refine_threshold_km: float = 50.0,
    pc_alert_threshold: float = 1e-4,
    alert_email: Optional[str] = None,
    pushover_user_key: Optional[str] = None,
):
    async def event_stream():
        def _ev(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        try:
            run_cfg = Config(
                num_leo=num_leo, num_meo=num_meo, num_geo=num_geo,
                time_window_hours=time_window_hours,
                collision_alert_km=collision_alert_km,
                refine_threshold_km=refine_threshold_km,
                pc_alert_threshold=pc_alert_threshold,
                alert_email_to=alert_email or CFG.alert_email_to,
                alert_email_from=CFG.alert_email_from,
                alert_smtp_host=CFG.alert_smtp_host,
                pushover_token=CFG.pushover_token,
                pushover_user_key=pushover_user_key or CFG.pushover_user_key,
            )

            yield _ev({"type": "progress", "pct": 5,
                       "text": "Fetching covariance data..."})
            await asyncio.sleep(0)

            loop     = asyncio.get_event_loop()
            cov_data = {}
            with concurrent.futures.ThreadPoolExecutor() as pool:
                try:
                    cov_data = await loop.run_in_executor(
                        pool, lambda: fetch_covariance_cache(run_cfg))
                except Exception:
                    pass

            yield _ev({"type": "progress", "pct": 15,
                       "text": "Starting orbital scan..."})
            await asyncio.sleep(0)

            import logging as _log
            sse_logs = []

            class SSEHandler(_log.Handler):
                def emit(self, record):
                    sse_logs.append(self.format(record))

            handler = SSEHandler()
            handler.setFormatter(_log.Formatter("%(message)s"))
            _log.getLogger("VectraSpace").addHandler(handler)

            from pipeline import run_pipeline
            import functools

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = loop.run_in_executor(
                    pool,
                    functools.partial(run_pipeline, run_cfg,
                                      covariance_cache=cov_data,
                                      run_mode="interactive",
                                      user_id=USER_ID_PUBLIC,
                                      user_prefs={}),
                )

                pct_steps = [30, 45, 60, 72, 82]
                pct_msgs  = [
                    "Propagating LEO satellites...",
                    "Propagating MEO satellites...",
                    "Propagating GEO satellites...",
                    "Screening conjunction pairs...",
                    "Estimating collision probabilities...",
                ]
                step_idx  = 0
                last_step = loop.time()
                last_ping = loop.time()

                while not future.done():
                    while sse_logs:
                        yield _ev({"type": "log", "text": sse_logs.pop(0)})
                    now_t = loop.time()
                    if step_idx < len(pct_steps) and now_t - last_step >= 4.0:
                        yield _ev({"type": "progress",
                                   "pct": pct_steps[step_idx],
                                   "text": pct_msgs[step_idx]})
                        step_idx += 1; last_step = now_t
                    if now_t - last_ping >= 20.0:
                        yield _ev({"type": "ping"})
                        last_ping = now_t
                    await asyncio.sleep(0.3)

                while sse_logs:
                    yield _ev({"type": "log", "text": sse_logs.pop(0)})

                result = await future

            _log.getLogger("VectraSpace").removeHandler(handler)

            # ── Serialise tracks ──────────────────────────────────
            tracks_json = []
            for t in result["tracks"]:
                step = max(1, len(t.positions) // 120)
                geo  = []
                for pos in t.positions[::step]:
                    x, y, z = pos
                    r   = math.sqrt(x**2 + y**2 + z**2)
                    lat = math.degrees(math.asin(z / r))
                    lon = math.degrees(math.atan2(y, x))
                    alt = (r - 6371) * 1000
                    geo.append([lon, lat, alt])
                tracks_json.append({"name": t.name, "regime": t.regime,
                                    "positions": geo, "geodetic": True})

            # ── Serialise conjunctions ────────────────────────────
            conj_json = []
            for c in result["conjunctions"]:
                t1 = next((t for t in result["tracks"] if t.name == c.sat1), None)
                t2 = next((t for t in result["tracks"] if t.name == c.sat2), None)
                if t1 and t2:
                    idx = int(np.argmin(np.abs(t1.times_min - c.time_min)))
                    mx  = (t1.positions[idx] + t2.positions[idx]) / 2
                    r   = math.sqrt(mx[0]**2 + mx[1]**2 + mx[2]**2)
                    mid = [math.degrees(math.atan2(mx[1], mx[0])),
                           math.degrees(math.asin(mx[2] / r)),
                           (r - 6371) * 1000]
                else:
                    mid = [0, 0, 400000]
                maneuver_data = None
                if c.maneuver:
                    m = c.maneuver
                    maneuver_data = {
                        "delta_v_rtn":           m.delta_v_rtn,
                        "delta_v_magnitude":     m.delta_v_magnitude,
                        "burn_epoch_offset_min": m.burn_epoch_offset_min,
                        "safe_dist_achieved_km": m.safe_dist_achieved_km,
                        "method":                m.method,
                        "advisory_note":         m.advisory_note,
                        "feasible":              m.feasible,
                    }
                conj_json.append({
                    "sat1": c.sat1, "sat2": c.sat2,
                    "regime1": c.regime1, "regime2": c.regime2,
                    "min_dist_km": c.min_dist_km, "time_min": c.time_min,
                    "pc_estimate": c.pc_estimate,
                    "covariance_source": c.covariance_source,
                    "debris": c.debris, "maneuver": maneuver_data,
                    "midpoint": mid,
                })

            serialised = {"tracks": tracks_json, "conjunctions": conj_json}

            # Store for demo mode
            try:
                from main import app as _app
                _app.state.demo_result = serialised
                _app.state.last_result = result
            except Exception:
                pass

            yield _ev({"type": "progress", "pct": 98,
                       "text": f"Scan complete — {len(conj_json)} conjunction(s)"})
            yield _ev({"type": "done", "data": serialised})

        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
            yield _ev({"type": "error", "text": str(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── /conjunctions ─────────────────────────────────────────────────────────────

@router.get("/conjunctions")
def get_conjunctions():
    con  = sqlite3.connect(CFG.db_path)
    rows = con.execute(
        "SELECT * FROM conjunctions ORDER BY run_time DESC LIMIT 200"
    ).fetchall()
    cols = ["id", "run_time", "sat1", "sat2", "regime1", "regime2",
            "min_dist_km", "time_min", "pc_estimate", "user_id"]
    return JSONResponse([dict(zip(cols, r)) for r in rows])


@router.get("/demo-results")
def demo_results():
    try:
        from main import app as _app
        if _app.state.demo_result:
            return JSONResponse(_app.state.demo_result)
    except Exception:
        pass
    try:
        con    = sqlite3.connect(CFG.db_path)
        latest = con.execute(
            "SELECT run_time FROM conjunctions ORDER BY run_time DESC LIMIT 1"
        ).fetchone()
        if not latest:
            return JSONResponse({}, status_code=404)
        rows = con.execute(
            "SELECT sat1,sat2,regime1,regime2,min_dist_km,time_min,pc_estimate "
            "FROM conjunctions WHERE run_time=?",
            (latest[0],),
        ).fetchall()
        conj = [{"sat1": r[0], "sat2": r[1], "regime1": r[2], "regime2": r[3],
                 "min_dist_km": r[4], "time_min": r[5], "pc_estimate": r[6],
                 "covariance_source": "assumed", "debris": False, "maneuver": None,
                 "midpoint": [0, 0, 400000]} for r in rows]
        return JSONResponse({"tracks": [], "conjunctions": conj})
    except Exception:
        return JSONResponse({}, status_code=404)


@router.get("/history")
def get_history():
    con = sqlite3.connect(CFG.db_path)
    daily = con.execute(
        "SELECT substr(run_time,1,10) day, COUNT(*) cnt "
        "FROM conjunctions GROUP BY day ORDER BY day DESC LIMIT 30"
    ).fetchall()
    pairs = con.execute(
        "SELECT sat1,sat2,COUNT(*) cnt,MIN(min_dist_km) closest "
        "FROM conjunctions GROUP BY sat1,sat2 ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    regimes = con.execute(
        "SELECT regime1||'/'||regime2 pair, COUNT(*) cnt "
        "FROM conjunctions GROUP BY pair ORDER BY cnt DESC"
    ).fetchall()
    return JSONResponse({
        "daily":     [{"day": r[0], "count": r[1]} for r in daily],
        "top_pairs": [{"sat1": r[0], "sat2": r[1], "count": r[2],
                       "closest": r[3]} for r in pairs],
        "regimes":   [{"pair": r[0], "count": r[1]} for r in regimes],
    })


# ── /cdm ──────────────────────────────────────────────────────────────────────

@router.get("/cdm/{idx}")
def download_cdm(idx: int):
    con  = sqlite3.connect(CFG.db_path)
    rows = con.execute(
        "SELECT * FROM conjunctions ORDER BY run_time DESC LIMIT 50"
    ).fetchall()
    cols = ["id", "run_time", "sat1", "sat2", "regime1", "regime2",
            "min_dist_km", "time_min", "pc_estimate", "user_id"]
    if idx >= len(rows):
        return PlainTextResponse("Not found", status_code=404)
    r = dict(zip(cols, rows[idx]))
    c = Conjunction(
        sat1=r["sat1"], sat2=r["sat2"],
        regime1=r["regime1"], regime2=r["regime2"],
        min_dist_km=r["min_dist_km"], time_min=r["time_min"],
        pc_estimate=r["pc_estimate"],
    )
    cdm_text = generate_cdm(c, r["run_time"])
    s1 = str(r["sat1"] or "UNK")[:8].replace(" ", "_")
    s2 = str(r["sat2"] or "UNK")[:8].replace(" ", "_")
    return PlainTextResponse(cdm_text, headers={
        "Content-Disposition": f'attachment; filename="VS_CDM_{s1}_{s2}.cdm"'
    })


@router.get("/cdm/zip/all")
def download_all_cdms():
    con    = sqlite3.connect(CFG.db_path)
    latest = con.execute(
        "SELECT run_time FROM conjunctions ORDER BY run_time DESC LIMIT 1"
    ).fetchone()
    if not latest:
        return JSONResponse({"error": "No conjunctions yet"}, status_code=404)
    rows = con.execute(
        "SELECT * FROM conjunctions WHERE run_time=?", (latest[0],)
    ).fetchall()
    cols = ["id", "run_time", "sat1", "sat2", "regime1", "regime2",
            "min_dist_km", "time_min", "pc_estimate", "user_id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, row in enumerate(rows):
            r  = dict(zip(cols, row))
            c  = Conjunction(
                sat1=r["sat1"], sat2=r["sat2"],
                regime1=r["regime1"], regime2=r["regime2"],
                min_dist_km=r["min_dist_km"], time_min=r["time_min"],
                pc_estimate=r["pc_estimate"],
            )
            s1 = str(r["sat1"] or "UNK")[:8].replace(" ", "_")
            s2 = str(r["sat2"] or "UNK")[:8].replace(" ", "_")
            zf.writestr(f"CDM_{i+1:03d}_{s1}_{s2}.cdm",
                        generate_cdm(c, r["run_time"]))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="VectraSpace_CDMs.zip"'
    })


# ── /sat-info ─────────────────────────────────────────────────────────────────

@router.get("/sat-info/{sat_name}")
async def satellite_info(sat_name: str):
    """Calls Anthropic API server-side — API key never sent to browser."""
    import functools
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        clean = sat_name.strip().upper()
        return JSONResponse({
            "fullName": clean, "noradId": None, "country": "Unknown",
            "launchDate": None, "launchSite": None, "orbitType": "Unknown",
            "periodMin": None, "inclinationDeg": None, "apogeeKm": None,
            "perigeeKm": None, "rcsSize": None, "operationalStatus": "Unknown",
            "owner": "Unknown", "objectType": "UNKNOWN", "missionType": "Unknown",
            "note": "Set ANTHROPIC_API_KEY for detailed satellite information.",
        })

    def _call_sync(name: str, key: str) -> dict:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 800,
                "system": (
                    "You are a satellite data formatter. Given a satellite name, "
                    "return ONLY a JSON object (no markdown, no explanation) with these fields: "
                    "fullName, noradId, country, launchDate, launchSite, orbitType, periodMin, "
                    "inclinationDeg, apogeeKm, perigeeKm, rcsSize, operationalStatus, owner, "
                    "objectType, missionType. "
                    "missionType must be one of: Communications, Earth Observation, Navigation, "
                    "Scientific, Military, Weather, Technology Demo, Human Spaceflight, "
                    "Space Station, Debris, or Unknown. Use null for missing fields."
                ),
                "messages": [{"role": "user", "content": f"Satellite name: {name}"}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(
            None, functools.partial(_call_sync, sat_name, api_key)
        )
        return JSONResponse(info)
    except json.JSONDecodeError:
        return JSONResponse({"error": "Could not parse satellite data"},
                            status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ── /debris/simulate ──────────────────────────────────────────────────────────

@router.get("/debris/simulate")
async def simulate_debris(
    sat_name: str = "",
    event_type: str = "COLLISION",
    n_debris: int = 50,
):
    try:
        from main import app as _app
        last = _app.state.last_result
    except Exception:
        last = None
    if not last:
        return JSONResponse({"error": "No scan results — run a scan first."},
                            status_code=400)

    all_tracks = last.get("tracks", [])
    ts         = last.get("ts")
    if ts is None:
        return JSONResponse({"error": "No timescale available."}, status_code=400)

    parent = next((t for t in all_tracks if t.name == sat_name), None)
    if not parent:
        return JSONResponse({"error": f"'{sat_name}' not found in last scan."},
                            status_code=404)

    debris_list = generate_debris_cloud(parent, event_type, n_debris, ts)
    tracks_json = []
    for t in debris_list:
        step = max(1, len(t.positions) // 120)
        geo  = []
        for pos in t.positions[::step]:
            x, y, z = pos
            r   = math.sqrt(x**2 + y**2 + z**2)
            lat = math.degrees(math.asin(z / r))
            lon = math.degrees(math.atan2(y, x))
            alt = (r - 6371) * 1000
            geo.append([lon, lat, alt])
        tracks_json.append({"name": t.name, "regime": t.regime, "positions": geo})

    debris_conj = check_conjunctions(debris_list + all_tracks, CFG, ts)
    conj_json   = [
        {"sat1": c.sat1, "sat2": c.sat2, "regime1": c.regime1, "regime2": c.regime2,
         "min_dist_km": c.min_dist_km, "time_min": c.time_min,
         "pc_estimate": c.pc_estimate, "covariance_source": c.covariance_source,
         "debris": True, "maneuver": None, "midpoint": [0, 0, 400000]}
        for c in debris_conj if c.debris
    ]
    return JSONResponse({"debris_tracks": tracks_json, "conjunctions": conj_json})


# ── /feedback ─────────────────────────────────────────────────────────────────

@router.post("/feedback")
async def submit_feedback(request: Request):
    import uuid
    import datetime
    from pathlib import Path
    from alerts import send_email

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    fb_type = str(body.get("type", "other"))[:50]
    message = str(body.get("message", "")).strip()[:2000]
    email   = str(body.get("email", "")).strip()[:200]
    user    = str(body.get("user", "anonymous"))[:100]

    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    entry = {
        "id":        uuid.uuid4().hex[:8],
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "type":      fb_type, "message": message,
        "email":     email,   "user":    user,
    }
    fb_path = Path(CFG.db_path).parent / "feedback.json"
    try:
        existing = json.loads(fb_path.read_text()) if fb_path.exists() else []
    except Exception:
        existing = []
    existing.append(entry)
    fb_path.write_text(json.dumps(existing, indent=2))

    try:
        send_email(
            f"[VectraSpace Feedback] {fb_type.upper()} from {user}",
            f"<pre>{message}</pre><p>From: {user} ({email})</p>",
            CFG.alert_email_to or CFG.alert_email_from,
            CFG, plain=message,
        )
    except Exception as e:
        log.warning(f"Could not email feedback: {e}")

    return JSONResponse({"ok": True, "id": entry["id"]})
