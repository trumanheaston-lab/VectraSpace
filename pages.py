"""
VectraSpace v11 — pages.py
Static HTML page routes. No auth required for most.
HTML content is imported from templates_loader.py.
"""

import datetime
import hashlib as _hash
import logging
import math
import os
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from config import CFG
from templates_loader import (
    LANDING_HTML, DASHBOARD_HTML, SCENARIOS_HTML, CALC_HTML,
    GLOSSARY_HTML, RESEARCH_HTML,
)

log    = logging.getLogger("VectraSpace")
router = APIRouter()


def _get_cesium_token() -> str:
    return os.environ.get(
        "CESIUM_ION_TOKEN",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJqdGkiOiJlMzRmMGI5Ni1hMTM0LTQxMjgtODgzMy04ZGYxN2UzNzYyN2MiLCJpZCI6MzkyNzg4LCJpYXQiOjE3NzE2OTU4OTF9"
        ".lulZ9jWB9A_XCxfui1FpcGmC7A7B49znZpcwn7yg530",
    )


def get_dashboard_html() -> str:
    return DASHBOARD_HTML.replace("__CESIUM_TOKEN__", _get_cesium_token())


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def landing():
    return HTMLResponse(LANDING_HTML)


@router.get("/welcome", response_class=HTMLResponse)
def welcome():
    return HTMLResponse(LANDING_HTML)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(get_dashboard_html())


@router.get("/calculator", response_class=HTMLResponse)
def calculator():
    return HTMLResponse(CALC_HTML)


@router.get("/glossary", response_class=HTMLResponse)
def glossary():
    return HTMLResponse(GLOSSARY_HTML)


@router.get("/scenarios", response_class=HTMLResponse)
def scenarios():
    return HTMLResponse(SCENARIOS_HTML)


@router.get("/research", response_class=HTMLResponse)
def research():
    return HTMLResponse(RESEARCH_HTML)


@router.get("/education/orbital-mechanics", response_class=HTMLResponse)
def edu_orbital():
    from templates_loader import EDU_ORBITAL_HTML
    return HTMLResponse(EDU_ORBITAL_HTML)


@router.get("/education/collision-prediction", response_class=HTMLResponse)
def edu_collision():
    from templates_loader import EDU_COLLISION_HTML
    return HTMLResponse(EDU_COLLISION_HTML)


@router.get("/education/perturbations", response_class=HTMLResponse)
def edu_perturbations():
    from templates_loader import EDU_PERTURBATIONS_HTML
    return HTMLResponse(EDU_PERTURBATIONS_HTML)


@router.get("/education/debris-modeling", response_class=HTMLResponse)
def edu_debris():
    from templates_loader import EDU_DEBRIS_HTML
    return HTMLResponse(EDU_DEBRIS_HTML)


# ── Utility endpoints ────────────────────────────────────────────────────────

@router.get("/health")
def health():
    return {"status": "ok",
            "time": datetime.datetime.utcnow().isoformat(),
            "version": "v11"}


@router.get("/tle-status")
def tle_status():
    import os, json
    from pathlib import Path
    import time
    p = Path("tle_cache.txt")
    if not p.exists():
        return JSONResponse({"fresh": False, "age_hours": None, "count": 0,
                             "message": "No TLE data — run a scan"})
    age_h = (time.time() - p.stat().st_mtime) / 3600
    # rough count: 2 lines per satellite
    lines = p.read_text().count("\n")
    count = lines // 2
    fresh = age_h < 24
    return JSONResponse({
        "fresh":     fresh,
        "age_hours": round(age_h, 1),
        "count":     count,
        "message":   (f"{count} sats · updated {round(age_h,1)}h ago"
                      if fresh else f"Stale ({round(age_h,1)}h old) — rescan recommended"),
    })


@router.get("/scan-status")
def scan_status():
    from main import _scan_state
    import time
    last    = _scan_state.get("time", 0)
    running = _scan_state.get("running", False)
    count   = _scan_state.get("count", 0)
    return JSONResponse({
        "last_scan":   last,
        "running":     running,
        "count":       count,
        "age_minutes": round((time.time() - last) / 60, 1) if last else None,
    })


@router.get("/satellite-of-the-day")
def satellite_of_the_day():
    FEATURED = [
        {"name":"ISS (ZARYA)","norad":25544,"type":"Space Station",
         "fun_fact":"The ISS has been continuously inhabited since November 2, 2000.",
         "color":"#4a9eff","operator":"NASA / Roscosmos / ESA / JAXA / CSA"},
        {"name":"HUBBLE SPACE TELESCOPE","norad":20580,"type":"Observatory",
         "fun_fact":"Hubble has made over 1.5 million observations and enabled 21,000+ papers.",
         "color":"#a78bfa","operator":"NASA / ESA"},
        {"name":"TERRA","norad":25994,"type":"Earth Observation",
         "fun_fact":"Terra carries five instruments monitoring Earth's entire surface simultaneously.",
         "color":"#34d399","operator":"NASA"},
        {"name":"GPS BIIR-2  (PRN 13)","norad":24876,"type":"Navigation",
         "fun_fact":"GPS time signals are accurate to 20–30 ns — without them your phone drifts metres/second.",
         "color":"#f59e0b","operator":"US Space Force"},
        {"name":"SENTINEL-2A","norad":40697,"type":"Earth Observation",
         "fun_fact":"Sentinel-2A images all Earth land every 10 days at 10 m — free and open data.",
         "color":"#34d399","operator":"ESA"},
        {"name":"STARLINK-1007","norad":44713,"type":"Communications",
         "fun_fact":"Starlink satellites perform thousands of autonomous collision avoidance maneuvers per year.",
         "color":"#60a5fa","operator":"SpaceX"},
        {"name":"JASON-3","norad":41240,"type":"Ocean Monitoring",
         "fun_fact":"Jason-3 measures sea surface height to within 2.5 cm — tracking sea level rise.",
         "color":"#06b6d4","operator":"NOAA / EUMETSAT / NASA / CNES"},
    ]
    day_key = datetime.datetime.utcnow().strftime("%Y-%j")
    idx     = int(_hash.md5(day_key.encode()).hexdigest(), 16) % len(FEATURED)
    sat     = FEATURED[idx].copy()

    try:
        from skyfield.api import load as _sf
        import os
        cache = CFG.tle_cache_file
        if os.path.exists(cache):
            ts   = _sf.timescale()
            sats = _sf.tle_file(cache)
            target = next(
                (s for s in sats if str(sat["norad"]) in str(s.model.satnum) or
                 sat["name"].split()[0] in s.name.upper()), None
            )
            if target:
                t   = ts.now()
                sub = target.at(t).subpoint()
                alt = round(sub.elevation.km, 0)
                r   = 6371 + alt
                mu  = 398600.4418
                v   = round((mu / r) ** 0.5, 2)
                T   = round(2 * math.pi * (r**3 / mu)**0.5 / 60, 1)
                sat.update({"alt_km": int(alt), "velocity_kms": v,
                            "period_min": T, "live": True})
    except Exception:
        pass

    if not sat.get("live"):
        STATIC = {
            25544: (420, 7.66, 92.9), 20580: (547, 7.59, 95.4),
            25994: (705, 7.48, 99.0), 24876: (20200, 3.87, 718),
            40697: (786, 7.45, 100.4), 44713: (550, 7.61, 95.5),
            41240: (1336, 5.80, 112.4),
        }
        a, v, T = STATIC.get(sat["norad"], (500, 7.62, 94.6))
        sat.update({"alt_km": a, "velocity_kms": v, "period_min": T, "live": False})

    sat["updated_utc"] = datetime.datetime.utcnow().strftime("%H:%M UTC")
    return JSONResponse(sat)


@router.get("/api/live-sats")
async def live_sats_api(limit: int = 80, regime: str = "LEO"):
    import os, math as _m
    from datetime import datetime, timezone
    try:
        from skyfield.api import load as _sf
        ts   = _sf.timescale()
        now  = ts.now()
        cache = CFG.tle_cache_file
        if not os.path.exists(cache):
            import urllib.request as _ur
            raw = _ur.urlopen(
                "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle",
                timeout=8
            ).read().decode()
            open(cache, "w").write(raw)
        sats    = _sf.tle_file(cache, reload=False)
        results = []
        for s in sats[:min(limit * 3, 600)]:
            try:
                pos = s.at(now).position.km
                alt = _m.sqrt(pos[0]**2+pos[1]**2+pos[2]**2) - 6371.0
                if regime == "LEO" and not (160 < alt < 2000): continue
                if regime == "MEO" and not (2000 <= alt < 35000): continue
                if regime == "GEO" and not (35000 <= alt < 37000): continue
                results.append({"name": s.name, "alt": round(alt,1),
                                 "x": round(pos[0],1), "y": round(pos[1],1), "z": round(pos[2],1)})
                if len(results) >= limit: break
            except Exception:
                continue
        return JSONResponse({"sats": results, "count": len(results),
                             "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "regime": regime})
    except Exception as e:
        return JSONResponse({"error": str(e), "sats": [], "count": 0}, status_code=500)


@router.get("/api/news")
async def news_api(content_type: str = "articles", limit: int = 12,
                   offset: int = 0, search: str = ""):
    import urllib.request as _ur, json as _j, urllib.parse as _up
    try:
        valid = {"articles","blogs","reports"}
        ct    = content_type if content_type in valid else "articles"
        params = f"?limit={min(limit,40)}&offset={offset}"
        if search:
            params += "&search=" + _up.quote(search[:200])
        url = f"https://api.spaceflightnewsapi.net/v4/{ct}/{params}"
        req = _ur.Request(url, headers={"User-Agent": "VectraSpace/1.0"})
        with _ur.urlopen(req, timeout=10) as resp:
            data = _j.loads(resp.read().decode())
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e), "count": 0, "results": []}, status_code=502)


@router.get("/research/tle.json")
def research_tle_json():
    import json
    from pathlib import Path
    p = Path("catalog.json")
    if p.exists():
        try:
            return JSONResponse(json.loads(p.read_text()))
        except Exception:
            pass
    return JSONResponse({"error": "No TLE data — run a scan first."}, status_code=404)


@router.get("/research/tle.csv")
def research_tle_csv():
    import json
    from pathlib import Path
    p = Path("catalog.json")
    if p.exists():
        try:
            data  = json.loads(p.read_text())
            lines = ["name,line1,line2"]
            for e in (data if isinstance(data, list) else []):
                n, l1, l2 = (str(e.get(k,"")).replace(",","") for k in ("name","line1","line2"))
                lines.append(f"{n},{l1},{l2}")
            return PlainTextResponse("\n".join(lines), media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=vectraspace_tle.csv"})
        except Exception:
            pass
    return PlainTextResponse("No TLE data", status_code=404)
