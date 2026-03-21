"""
VectraSpace v11 — tle.py
TLE fetching (CelesTrak + Space-Track) and regime bucketing.
"""

import logging
import os
import time
from typing import Optional

import requests
from skyfield.api import load, wgs84

from config import Config

log = logging.getLogger("VectraSpace")

SPACETRACK_LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
SPACETRACK_TLE_URL   = (
    "https://www.space-track.org/basicspacedata/query/class/gp"
    "/decay_date/null-val/epoch/%3Enow-30/orderby/norad_cat_id/format/tle"
)


def fetch_spacetrack_tles() -> Optional[str]:
    user = os.environ.get("SPACETRACK_USER")
    pwd  = os.environ.get("SPACETRACK_PASS")
    if not user or not pwd:
        log.warning("Space-Track credentials not found — skipping")
        return None
    try:
        session = requests.Session()
        resp = session.post(SPACETRACK_LOGIN_URL,
                            data={"identity": user, "password": pwd}, timeout=30)
        resp.raise_for_status()
        if "Login" in resp.text:
            log.warning("Space-Track login failed — check credentials")
            return None
        log.info("  ✓ Space-Track login successful")
        resp = session.get(SPACETRACK_TLE_URL, timeout=60)
        resp.raise_for_status()
        log.info(f"  ✓ Space-Track: {len(resp.text.splitlines())//2} TLEs")
        return resp.text
    except Exception as e:
        log.warning(f"  ✗ Space-Track fetch failed: {e}")
        return None


def fetch_tles(cfg: Config):
    """Return (satellites_list, timescale). Refreshes cache if stale."""
    cache_fresh = False
    if os.path.exists(cfg.tle_cache_file):
        age_h = (time.time() - os.path.getmtime(cfg.tle_cache_file)) / 3600
        cache_fresh = age_h < cfg.tle_max_age_hours
        if cache_fresh:
            log.info(f"TLE cache is {age_h:.1f}h old — using cached data")

    if not cache_fresh:
        log.info("Downloading fresh TLEs...")
        all_lines = []
        for url in cfg.tle_sources:
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                all_lines.append(resp.text)
                log.info(f"  ✓ CelesTrak: {url.split('GROUP=')[1].split('&')[0]}")
            except Exception as e:
                log.warning(f"  ✗ CelesTrak {url}: {e}")
        st_data = fetch_spacetrack_tles()
        if st_data:
            all_lines.append(st_data)
        if all_lines:
            with open(cfg.tle_cache_file, "w") as f:
                f.write("\n".join(all_lines))
            log.info(f"TLE cache updated ({len(all_lines)} sources)")
        else:
            log.error("All TLE sources failed")
            raise RuntimeError("No TLE data available")

    ts         = load.timescale()
    satellites = load.tle_file(cfg.tle_cache_file)
    log.info(f"Loaded {len(satellites)} satellites")
    return satellites, ts


def filter_by_regime(satellites, ts) -> dict:
    regimes = {
        "LEO": (160,    2_000),
        "MEO": (2_000,  35_786),
        "GEO": (35_786, 50_000),
    }
    buckets = {r: [] for r in regimes}
    now = ts.now()
    for sat in satellites:
        try:
            alt = wgs84.height_of(sat.at(now)).km
            for name, (lo, hi) in regimes.items():
                if lo <= alt < hi:
                    buckets[name].append(sat)
                    break
        except Exception:
            pass
    for name, sats in buckets.items():
        log.info(f"  {name}: {len(sats)} satellites")
    return buckets
