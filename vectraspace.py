"""VectraSpace v11 — Orbital Safety Platform"""

import os
import sys
import time
import json
import logging
import logging.handlers
import sqlite3
import smtplib
import hashlib
import secrets
import argparse
import requests
import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional, List
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ── Manual .env loader — bypasses dotenv OneDrive issues ──────
def _load_env():
    """Read .env from the same folder as this script and inject into os.environ."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return  # Normal in production — env vars come from platform
    print(f"[INFO] Loading .env from {env_path}")
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            os.environ[key] = val
            print(f"[INFO] Loaded env var: {key}")

_load_env()

import numpy as np
from scipy.optimize import minimize_scalar
from skyfield.api import load, wgs84

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, StreamingResponse as _FAStreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("VectraSpace")

# ╔══════════════════════════════════════════════════════════════╗
# ║  CONFIG                                                      ║
# ╚══════════════════════════════════════════════════════════════╝

@dataclass
class Config:
    num_leo: int = 100
    num_meo: int = 50
    num_geo: int = 20
    num_satellites_per_regime: int = 100

    time_window_hours: float = 12
    coarse_step_minutes: float = 1.0
    refine_threshold_km: float = 50.0
    collision_alert_km: float = 10.0
    pc_alert_threshold: float = 1e-4

    sigma_along:  float = 0.5
    sigma_cross:  float = 0.2
    sigma_radial: float = 0.1

    tle_sources: list = field(default_factory=lambda: [
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle",
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle",
    ])
    tle_cache_file: str = "tle_cache.txt"
    tle_max_age_hours: float = 6.0

    db_path: str = "conjunctions.db"
    output_html: str = "orbital_map.html"

    alert_email_to: Optional[str] = None
    alert_email_from: Optional[str] = None
    alert_smtp_host: str = "smtp.gmail.com"
    alert_webhook_url: Optional[str] = None
    alert_phone: Optional[str] = None

    pushover_token: Optional[str] = None
    pushover_user_key: Optional[str] = None

    maneuver_safe_dist_km: float = 50.0
    vector_chunk_size: int = 50

    session_secret: str = ""
    users_file: str = "users.json"

CFG = Config(
    alert_email_from=os.environ.get("ALERT_EMAIL_FROM", "trumanheaston@gmail.com"),
    alert_email_to=os.environ.get("ALERT_EMAIL_TO", ""),
    alert_smtp_host=os.environ.get("ALERT_SMTP_HOST", "smtp.gmail.com"),
    alert_phone=os.environ.get("ALERT_PHONE"),
    pushover_token=os.environ.get("PUSHOVER_TOKEN"),
    pushover_user_key=os.environ.get("PUSHOVER_USER_KEY"),
    session_secret=os.environ.get("SESSION_SECRET", secrets.token_hex(32)),
    collision_alert_km=float(os.environ.get("COLLISION_ALERT_KM", "10000.0")),
)

# ── In-memory per-user rate limiter for /run ──────────────────
# { username -> [timestamp, ...] }
_run_rate_limits: dict = {}

def _check_run_rate_limit(username: str, window_seconds: int = 300) -> bool:
    """Returns True if user is allowed (< 1 scan per window_seconds)."""
    now = time.time()
    times = [t for t in _run_rate_limits.get(username, []) if now - t < window_seconds]
    _run_rate_limits[username] = times
    if times:
        return False
    _run_rate_limits[username].append(now)
    return True

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 1 — DATA INGESTION                                   ║
# ╚══════════════════════════════════════════════════════════════╝

SPACETRACK_LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
SPACETRACK_TLE_URL  = "https://www.space-track.org/basicspacedata/query/class/gp/decay_date/null-val/epoch/%3Enow-30/orderby/norad_cat_id/format/tle"

def fetch_spacetrack_tles() -> Optional[str]:
    user = os.environ.get("SPACETRACK_USER")
    pwd  = os.environ.get("SPACETRACK_PASS")

    if not user or not pwd:
        log.warning("Space-Track credentials not found — skipping")
        return None

    try:
        session = requests.Session()
        resp = session.post(SPACETRACK_LOGIN_URL,
                            data={"identity": user, "password": pwd},
                            timeout=30)
        resp.raise_for_status()
        if "Login" in resp.text:
            log.warning("Space-Track login failed — check credentials in .env")
            return None
        log.info("  ✓ Space-Track login successful")

        resp = session.get(SPACETRACK_TLE_URL, timeout=60)
        resp.raise_for_status()
        log.info(f"  ✓ Space-Track: downloaded {len(resp.text.splitlines())//2} TLEs")
        return resp.text

    except Exception as e:
        log.warning(f"  ✗ Space-Track fetch failed: {e}")
        return None

def fetch_tles(cfg: Config) -> list:
    cache_fresh = False
    if os.path.exists(cfg.tle_cache_file):
        age_hours = (time.time() - os.path.getmtime(cfg.tle_cache_file)) / 3600
        cache_fresh = age_hours < cfg.tle_max_age_hours
        if cache_fresh:
            log.info(f"TLE cache is {age_hours:.1f}h old — using cached data")

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
            log.info(f"TLE cache updated with {len(all_lines)} sources")
        else:
            log.error("All TLE sources failed — cannot continue")
            raise RuntimeError("No TLE data available")

    ts = load.timescale()
    satellites = load.tle_file(cfg.tle_cache_file)
    log.info(f"Loaded {len(satellites)} satellites total")
    return satellites, ts

def filter_by_regime(satellites, ts) -> dict:
    regimes = {
        "LEO": (160,   2_000),
        "MEO": (2_000, 35_786),
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
        log.info(f"  {name}: {len(sats)} satellites found")
    return buckets

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 2 — PROPAGATION (vectorized)                         ║
# ╚══════════════════════════════════════════════════════════════╝

@dataclass
class SatTrack:
    name: str
    regime: str
    times_min: np.ndarray
    positions: np.ndarray

def propagate_satellites(sat_list: list, regime: str, cfg: Config, ts) -> list:
    now = ts.now()
    num_steps = int(cfg.time_window_hours * 60 / cfg.coarse_step_minutes)
    dt_days = cfg.coarse_step_minutes / 1440.0
    jd_array = now.tt + np.arange(num_steps) * dt_days
    times = ts.tt_jd(jd_array)
    times_min = np.arange(num_steps) * cfg.coarse_step_minutes

    tracks = []
    for sat in sat_list:
        try:
            pos_km = sat.at(times).position.km
            tracks.append(SatTrack(
                name=sat.name,
                regime=regime,
                times_min=times_min,
                positions=pos_km.T,
            ))
        except Exception as e:
            log.debug(f"Skipping {sat.name}: {e}")

    log.info(f"  Propagated {len(tracks)}/{len(sat_list)} {regime} satellites")
    return tracks, times

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 3 — COLLISION DETECTION                              ║
# ╚══════════════════════════════════════════════════════════════╝

@dataclass
class ManeuverSuggestion:
    """Clohessy-Wiltshire minimum-delta-v avoidance maneuver."""
    delta_v_rtn: list
    delta_v_magnitude: float
    burn_epoch_offset_min: float
    safe_dist_achieved_km: float
    method: str = "CW-LINEAR"
    advisory_note: str = "Advisory only. Verify with high-fidelity propagator before execution."
    feasible: bool = True

@dataclass
class Conjunction:
    sat1: str
    sat2: str
    regime1: str
    regime2: str
    min_dist_km: float
    time_min: float
    pc_estimate: float
    covariance_source: str = "assumed"
    debris: bool = False
    maneuver: Optional[object] = None

def estimate_pc_foster(miss_km: float, v_rel_km_s: float,
                        sigma_along: float, sigma_cross: float,
                        sigma_radial: float,
                        cov1: Optional[np.ndarray] = None,
                        cov2: Optional[np.ndarray] = None) -> tuple:
    from scipy.stats import chi2
    source = "assumed"
    if cov1 is not None and cov2 is not None:
        combined = cov1 + cov2
        s_a = float(np.sqrt(max(combined[1, 1], 1e-12)))
        s_c = float(np.sqrt(max(combined[2, 2], 1e-12)))
        s_r = float(np.sqrt(max(combined[0, 0], 1e-12)))
        source = "measured"
    else:
        s_a = sigma_along  * np.sqrt(2)
        s_c = sigma_cross  * np.sqrt(2)
        s_r = sigma_radial * np.sqrt(2)

    sigma_combined = np.sqrt((s_a**2 + s_c**2 + s_r**2) / 3)
    if sigma_combined < 1e-9:
        return 0.0, source
    r_hbr = 0.02
    x = ((miss_km - r_hbr) / sigma_combined) ** 2
    pc = float(1.0 - chi2.cdf(max(x, 0), df=3))
    return min(pc, 1.0), source

def _ellipsoid_overlap_possible(miss_km: float, sigma_along: float,
                                 sigma_cross: float, sigma_radial: float,
                                 n_sigma: float = 5.0) -> bool:
    max_sigma = np.sqrt(2) * max(sigma_along, sigma_cross, sigma_radial)
    return miss_km <= (n_sigma * max_sigma)

def _chunked_min_distances(all_tracks: list, chunk_size: int) -> np.ndarray:
    n = len(all_tracks)
    min_dists = np.full((n, n), np.inf, dtype=np.float32)
    np.fill_diagonal(min_dists, 0.0)

    for i_start in range(0, n, chunk_size):
        i_end = min(i_start + chunk_size, n)
        block_i = np.stack([all_tracks[k].positions for k in range(i_start, i_end)], axis=0)

        for j_start in range(i_start, n, chunk_size):
            j_end = min(j_start + chunk_size, n)
            if j_start == i_start:
                ci = block_i.shape[0]
                for ri in range(ci):
                    for rj in range(ri + 1, ci):
                        diffs = block_i[ri] - block_i[rj]
                        d = float(np.sqrt((diffs**2).sum(axis=1)).min())
                        gi, gj = i_start + ri, j_start + rj
                        min_dists[gi, gj] = d
                        min_dists[gj, gi] = d
            else:
                block_j = np.stack([all_tracks[k].positions for k in range(j_start, j_end)], axis=0)
                diffs = block_i[:, np.newaxis, :, :] - block_j[np.newaxis, :, :, :]
                dists = np.sqrt((diffs**2).sum(axis=3))
                block_mins = dists.min(axis=2)
                for ri in range(block_i.shape[0]):
                    for rj in range(block_j.shape[0]):
                        gi, gj = i_start + ri, j_start + rj
                        if gi < gj:
                            min_dists[gi, gj] = block_mins[ri, rj]
                            min_dists[gj, gi] = block_mins[ri, rj]
    return min_dists

def _refine_pair(t1, t2, coarse_min_idx: int, cfg: Config) -> tuple:
    t_lo = max(0, coarse_min_idx - 1)
    t_hi = min(len(t1.times_min) - 1, coarse_min_idx + 1)
    t_min_lo = t1.times_min[t_lo]
    t_min_hi = t1.times_min[t_hi]

    def dist_at(t_frac_min):
        p1 = (np.interp(t_frac_min, t1.times_min, t1.positions[:, 0]),
              np.interp(t_frac_min, t1.times_min, t1.positions[:, 1]),
              np.interp(t_frac_min, t1.times_min, t1.positions[:, 2]))
        p2 = (np.interp(t_frac_min, t2.times_min, t2.positions[:, 0]),
              np.interp(t_frac_min, t2.times_min, t2.positions[:, 1]),
              np.interp(t_frac_min, t2.times_min, t2.positions[:, 2]))
        return np.sqrt(sum((a - b)**2 for a, b in zip(p1, p2)))

    result = minimize_scalar(dist_at, bounds=(t_min_lo, t_min_hi), method='bounded')
    return result.fun, result.x

def _compute_maneuver(t1, t2, time_min_tca: float, cfg: Config) -> ManeuverSuggestion:
    if time_min_tca < 1.0:
        return ManeuverSuggestion(
            delta_v_rtn=[0, 0, 0], delta_v_magnitude=None,
            burn_epoch_offset_min=0.0, safe_dist_achieved_km=0.0,
            feasible=False,
            advisory_note="TCA too imminent for maneuver (< 60s)."
        )

    idx0 = 0
    r_rel = t1.positions[idx0] - t2.positions[idx0]
    if len(t1.positions) > 1:
        dt_step = t1.times_min[1] * 60
        v_rel = (t1.positions[1] - t1.positions[0] - (t2.positions[1] - t2.positions[0])) / dt_step
    else:
        v_rel = np.zeros(3)

    r1 = np.linalg.norm(t1.positions[idx0])
    mu = 398600.4418
    n = np.sqrt(mu / r1**3)

    tau = time_min_tca * 60
    target = cfg.maneuver_safe_dist_km

    r_rel_mag = float(np.linalg.norm(r_rel))
    r_unit = r_rel / (r_rel_mag + 1e-9)

    needed_sep = max(0.0, target - r_rel_mag)
    dv_t = needed_sep / (2.0 * tau) if tau > 0 else 0.0
    dv_r = -float(np.dot(v_rel, r_unit)) * 0.1
    dv_n = 0.0

    dv_vec = np.array([dv_r, dv_t, dv_n]) * 1000
    dv_mag = float(np.linalg.norm(dv_vec))

    note = "Advisory only. Verify with high-fidelity propagator before execution."
    if dv_mag > 10.0:
        note = f"LARGE_DV ({dv_mag:.1f} m/s). {note}"

    return ManeuverSuggestion(
        delta_v_rtn=[round(float(dv_vec[0]),4), round(float(dv_vec[1]),4), round(float(dv_vec[2]),4)],
        delta_v_magnitude=round(dv_mag, 4),
        burn_epoch_offset_min=0.0,
        safe_dist_achieved_km=round(target, 3),
        feasible=True,
        advisory_note=note,
    )

def check_conjunctions(all_tracks: list, cfg: Config, ts,
                       covariance_cache: Optional[dict] = None) -> list:
    conjunctions = []
    n = len(all_tracks)
    num_pairs = n * (n - 1) // 2
    log.info(f"Checking {num_pairs} pairs — vectorized chunks of {cfg.vector_chunk_size}...")

    try:
        min_dist_matrix = _chunked_min_distances(all_tracks, cfg.vector_chunk_size)
    except MemoryError:
        log.warning("MemoryError in vectorized screening — falling back to for-loop")
        min_dist_matrix = None

    skipped = 0

    # ── ISS/CSS module filter ─────────────────────────────────────────────
    # Objects that are docked/attached modules of the same station will
    # always be close — skip pairs where both names contain the same
    # station keyword to avoid false-positive conjunction alerts.
    STATION_KEYWORDS = [
        "ISS", "ZARYA", "ZVEZDA", "UNITY", "DESTINY", "HARMONY",
        "TRANQUILITY", "SERENITY", "COLUMBUS", "KIBO", "QUEST",
        "PIRS", "POISK", "RASSVET", "NAUKA", "PRICHAL",
        "CSS", "TIANHE", "WENTIAN", "MENGTIAN",
    ]
    def _same_station(n1: str, n2: str) -> bool:
        n1u, n2u = n1.upper(), n2.upper()
        for kw in STATION_KEYWORDS:
            if kw in n1u and kw in n2u:
                return True
        # Also skip if names are identical except for a trailing number/letter
        import re as _re
        base1 = _re.sub(r'[\s\-_][\dA-Z]$', '', n1u)
        base2 = _re.sub(r'[\s\-_][\dA-Z]$', '', n2u)
        return base1 == base2 and len(base1) > 4
    
    for i in range(n):
        t1 = all_tracks[i]
        for j in range(i + 1, n):
            t2 = all_tracks[j]

            # Skip ISS/CSS module pairs — they're always close
            if _same_station(t1.name, t2.name):
                skipped += 1
                continue

            if min_dist_matrix is not None:
                min_dist_coarse = float(min_dist_matrix[i, j])
            else:
                diffs = t1.positions - t2.positions
                min_dist_coarse = float(np.sqrt((diffs**2).sum(axis=1)).min())

            if not _ellipsoid_overlap_possible(
                    min_dist_coarse, cfg.sigma_along, cfg.sigma_cross, cfg.sigma_radial):
                skipped += 1
                continue

            if min_dist_coarse > cfg.refine_threshold_km:
                continue

            diffs = t1.positions - t2.positions
            distances = np.sqrt((diffs**2).sum(axis=1))
            coarse_idx = int(np.argmin(distances))

            min_dist, min_time = _refine_pair(t1, t2, coarse_idx, cfg)

            if min_dist < cfg.collision_alert_km:
                idx = min(coarse_idx, len(t1.positions) - 2)
                dt = cfg.coarse_step_minutes * 60
                v1 = np.linalg.norm(t1.positions[idx+1] - t1.positions[idx]) / dt
                v2 = np.linalg.norm(t2.positions[idx+1] - t2.positions[idx]) / dt
                v_rel = abs(v1 - v2) + 0.5

                cov1 = covariance_cache.get(t1.name) if covariance_cache else None
                cov2 = covariance_cache.get(t2.name) if covariance_cache else None

                pc, cov_source = estimate_pc_foster(
                    min_dist, v_rel,
                    cfg.sigma_along, cfg.sigma_cross, cfg.sigma_radial,
                    cov1=cov1, cov2=cov2
                )

                maneuver = _compute_maneuver(t1, t2, min_time, cfg)
                is_debris = getattr(t1, 'is_debris', False) or getattr(t2, 'is_debris', False)

                conjunctions.append(Conjunction(
                    sat1=t1.name, sat2=t2.name,
                    regime1=t1.regime, regime2=t2.regime,
                    min_dist_km=min_dist,
                    time_min=min_time,
                    pc_estimate=pc,
                    covariance_source=cov_source,
                    debris=is_debris,
                    maneuver=maneuver,
                ))

    conjunctions.sort(key=lambda c: c.min_dist_km)
    log.info(f"Found {len(conjunctions)} conjunctions — {skipped} pairs skipped by pre-filter")
    return conjunctions

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 4 — DATABASE LOGGING                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def init_db(cfg: Config):
    """Initialize DB with auto-migration for v11 schema additions."""
    con = sqlite3.connect(cfg.db_path)

    # Base conjunctions table
    con.execute("""
        CREATE TABLE IF NOT EXISTS conjunctions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time    TEXT,
            sat1        TEXT,
            sat2        TEXT,
            regime1     TEXT,
            regime2     TEXT,
            min_dist_km REAL,
            time_min    REAL,
            pc_estimate REAL
        )
    """)

    existing_cols = [row[1] for row in con.execute("PRAGMA table_info(conjunctions)").fetchall()]
    if "user_id" not in existing_cols:
        con.execute("ALTER TABLE conjunctions ADD COLUMN user_id TEXT")
        log.info("DB migration: added user_id column to conjunctions")

    con.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id              TEXT PRIMARY KEY,
            email                TEXT,
            phone                TEXT,
            pushover_key         TEXT,
            pc_alert_threshold   REAL DEFAULT 0.0001,
            collision_alert_km   REAL DEFAULT 10.0,
            updated_at           TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username      TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'operator',
            email         TEXT DEFAULT '',
            approved      INTEGER DEFAULT 1,
            created_at    TEXT
        )
    """)

    import json as _mj
    from pathlib import Path as _mp
    uj = _mp(cfg.users_file)
    if uj.exists():
        try:
            raw = _mj.loads(uj.read_text())
            users_list = raw if isinstance(raw, list) else list(raw.values())
            migrated = 0
            for u in users_list:
                un = u.get("username", "").strip().lower()
                ph = u.get("password_hash", "")
                if not un or not ph:
                    continue
                existing = con.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone()
                if not existing:
                    con.execute(
                        "INSERT INTO users (username, password_hash, role, email, approved, created_at) VALUES (?,?,?,?,?,?)",
                        (un, ph, u.get("role","operator"), u.get("email",""),
                         1 if u.get("approved", True) else 0,
                         u.get("created_at",""))
                    )
                    migrated += 1
            if migrated:
                log.info(f"PERSIST-01: Migrated {migrated} users from users.json → SQLite")
                uj.rename(str(uj) + ".migrated")
        except Exception as _e:
            log.warning(f"PERSIST-01: users.json migration error: {_e}")

    con.commit()
    return con

def log_conjunctions_to_db(conjunctions: list, con, run_time: str, user_id: Optional[str] = None):
    rows = [(run_time, c.sat1, c.sat2, c.regime1, c.regime2,
             c.min_dist_km, c.time_min, c.pc_estimate, user_id)
            for c in conjunctions]
    con.executemany(
        "INSERT INTO conjunctions (run_time,sat1,sat2,regime1,regime2,min_dist_km,time_min,pc_estimate,user_id) VALUES (?,?,?,?,?,?,?,?,?)",
        rows
    )
    con.commit()
    log.info(f"Logged {len(rows)} conjunctions to database (user_id={user_id})")

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 4b — CDM EXPORT                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def generate_cdm(c, run_time: str) -> str:
    now = datetime.datetime.utcnow()
    tca = now + datetime.timedelta(minutes=c.time_min)
    sat1 = str(c.sat1 or "UNKNOWN").strip()
    sat2 = str(c.sat2 or "UNKNOWN").strip()

    cdm = f"""CCSDS_CDM_VERS                      = 1.0
CREATION_DATE                       = {now.strftime('%Y-%m-%dT%H:%M:%S.000')}
ORIGINATOR                          = VectraSpace/OrbitalSafetyPlatform

MESSAGE_FOR                         = {sat1}
MESSAGE_ID                          = VS11-{now.strftime('%Y%m%d%H%M%S')}-{sat1[:8].replace(' ','_')}

TCA                                 = {tca.strftime('%Y-%m-%dT%H:%M:%S.000')}
MISS_DISTANCE                       = {c.min_dist_km * 1000:.1f} [m]

COLLISION_PROBABILITY               = {c.pc_estimate:.6e}
COLLISION_PROBABILITY_METHOD        = FOSTER_ALFANO

OBJECT                              = OBJECT1
OBJECT_DESIGNATOR                   = {sat1}
CATALOG_NAME                        = {sat1}
OBJECT_NAME                         = {sat1}
INTERNATIONAL_DESIGNATOR            = UNKNOWN
OBJECT_TYPE                         = PAYLOAD
OPERATOR_ORGANIZATION               = UNKNOWN
COVARIANCE_METHOD                   = CALCULATED
MANEUVERABLE                        = N/A
ORBIT_CENTER                        = EARTH
REF_FRAME                           = EME2000
GRAVITY_MODEL                       = EGM-96: 36D 36O
ATMOSPHERIC_MODEL                   = JACCHIA 70
SOLAR_FLUX_UNCERTAINTY              = N/A

OBJECT                              = OBJECT2
OBJECT_DESIGNATOR                   = {sat2}
CATALOG_NAME                        = {sat2}
OBJECT_NAME                         = {sat2}
INTERNATIONAL_DESIGNATOR            = UNKNOWN
OBJECT_TYPE                         = PAYLOAD
OPERATOR_ORGANIZATION               = UNKNOWN
COVARIANCE_METHOD                   = CALCULATED
MANEUVERABLE                        = N/A
ORBIT_CENTER                        = EARTH
REF_FRAME                           = EME2000
GRAVITY_MODEL                       = EGM-96: 36D 36O
ATMOSPHERIC_MODEL                   = JACCHIA 70
SOLAR_FLUX_UNCERTAINTY              = N/A

COMMENT Generated by VectraSpace v11 — Orbital Safety Platform
COMMENT Orbital Regime OBJECT1: {c.regime1}
COMMENT Orbital Regime OBJECT2: {c.regime2}
COMMENT Time to CA: +{int(c.time_min // 60)}h {int(c.time_min % 60):02d}m
"""
    return cdm

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 4c — COVARIANCE INGESTION                            ║
# ╚══════════════════════════════════════════════════════════════╝

SPACETRACK_CDM_URL = (
    "https://www.space-track.org/basicspacedata/query/class/cdm_public"
    "/orderby/TCA desc/limit/100/format/json"
)

def fetch_covariance_cache(cfg: Config) -> dict:
    user = os.environ.get("SPACETRACK_USER")
    pwd  = os.environ.get("SPACETRACK_PASS")
    if not user or not pwd:
        log.debug("No Space-Track credentials — using assumed sigmas")
        return {}
    try:
        session = requests.Session()
        r = session.post(SPACETRACK_LOGIN_URL,
                         data={"identity": user, "password": pwd}, timeout=30)
        r.raise_for_status()
        if "Login" in r.text:
            log.warning("Space-Track login failed — using assumed sigmas")
            return {}
        r = session.get(SPACETRACK_CDM_URL, timeout=60)
        r.raise_for_status()
        records = r.json()

        cov_cache = {}
        for rec in records:
            for obj in ["OBJECT1", "OBJECT2"]:
                prefix = obj
                name_key = f"{prefix}_OBJECT_NAME"
                sat_name = rec.get(name_key, "").strip()
                if not sat_name:
                    continue
                try:
                    CR_R  = float(rec.get(f"{prefix}_CR_R", 0) or 0)
                    CT_R  = float(rec.get(f"{prefix}_CT_R", 0) or 0)
                    CT_T  = float(rec.get(f"{prefix}_CT_T", 0) or 0)
                    CN_R  = float(rec.get(f"{prefix}_CN_R", 0) or 0)
                    CN_T  = float(rec.get(f"{prefix}_CN_T", 0) or 0)
                    CN_N  = float(rec.get(f"{prefix}_CN_N", 0) or 0)
                    cov = np.array([
                        [CR_R, CT_R, CN_R],
                        [CT_R, CT_T, CN_T],
                        [CN_R, CN_T, CN_N],
                    ])
                    if np.all(np.isfinite(cov)) and np.any(np.diag(cov) > 0):
                        cov_cache[sat_name] = cov
                except (ValueError, TypeError):
                    pass

        log.info(f"Loaded covariances for {len(cov_cache)} objects from Space-Track CDM")
        return cov_cache

    except Exception as e:
        log.warning(f"Covariance ingestion failed ({e}) — using assumed sigmas")
        return {}

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 4d — DEBRIS CLOUD SIMULATION                         ║
# ╚══════════════════════════════════════════════════════════════╝

def generate_debris_cloud(parent_track, event_type: str, n_debris: int, ts) -> list:
    import math as _math

    n_debris = min(int(n_debris), 200)

    if event_type == "EXPLOSION":
        mu_lv, sigma_lv = 0.1, 0.5
    else:
        mu_lv, sigma_lv = 0.3, 0.7

    parent_pos = parent_track.positions[0]
    r = _math.sqrt(sum(x**2 for x in parent_pos))

    debris_tracks = []
    rng = np.random.default_rng(seed=42)

    for k in range(n_debris):
        lc = rng.uniform(0.01, 0.5)
        dv_mag = 10 ** rng.normal(mu_lv + 0.5 * _math.log10(lc), sigma_lv)
        dv_mag = float(np.clip(dv_mag, 0.001, 5.0))

        direction = rng.normal(0, 1, 3)
        direction /= np.linalg.norm(direction) + 1e-12
        dv_eci = direction * dv_mag

        synthetic_positions = parent_track.positions.copy()
        for t_idx in range(len(synthetic_positions)):
            dt = parent_track.times_min[t_idx] * 60
            synthetic_positions[t_idx] = parent_track.positions[t_idx] + dv_eci * dt

        track = type('SatTrack', (), {
            'name': f"DEBRIS-{99000 + k:05d}",
            'regime': parent_track.regime,
            'times_min': parent_track.times_min,
            'positions': synthetic_positions,
            'is_debris': True,
        })()
        debris_tracks.append(track)

    log.info(f"Generated {len(debris_tracks)} debris objects from {parent_track.name} ({event_type})")
    return debris_tracks

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 5 — ALERTING                                         ║
# ╚══════════════════════════════════════════════════════════════╝

_EMAIL_CSS = """
  body { margin:0; padding:0; background:#050a0f; font-family:'Courier New',monospace; }
  .wrap { max-width:640px; margin:0 auto; background:#090f17; border:1px solid #0d2137; }
  .header { background:linear-gradient(135deg,#0a1929 0%,#0d2137 100%);
            padding:28px 32px 20px; border-bottom:2px solid #00d4ff; }
  .header .badge { font-size:10px; color:#00d4ff; letter-spacing:4px;
                   text-transform:uppercase; margin-bottom:8px; }
  .header h1 { margin:0; font-size:22px; color:#ffffff; font-weight:700;
               letter-spacing:1px; }
  .header .sub { margin-top:6px; font-size:11px; color:#4a6a85; }
  .meta-bar { background:#0a1520; padding:12px 32px; border-bottom:1px solid #0d2137;
              display:flex; gap:32px; }
  .meta-item { font-size:10px; color:#4a6a85; letter-spacing:1px; }
  .meta-item span { color:#c8dff0; display:block; font-size:12px; margin-top:2px; }
  .body { padding:24px 32px; }
  .section-title { font-size:9px; color:#00d4ff; letter-spacing:3px;
                   text-transform:uppercase; margin-bottom:12px;
                   padding-bottom:6px; border-bottom:1px solid #0d2137; }
  .event-card { background:#0a1520; border:1px solid #0d2137;
                border-left:3px solid #ff4444; border-radius:4px;
                padding:16px 18px; margin-bottom:12px; }
  .event-card.warning { border-left-color:#ffaa44; }
  .event-num { font-size:9px; color:#4a6a85; letter-spacing:2px;
               text-transform:uppercase; margin-bottom:8px; }
  .sat-line { font-size:14px; color:#ffffff; font-weight:700; margin-bottom:10px; }
  .sat-line .arrow { color:#00d4ff; margin:0 8px; }
  .stats { display:flex; gap:0; margin-top:8px; }
  .stat { flex:1; padding:8px 12px; background:#050a0f;
          border-right:1px solid #0d2137; }
  .stat:last-child { border-right:none; }
  .stat .label { font-size:8px; color:#4a6a85; letter-spacing:2px;
                 text-transform:uppercase; margin-bottom:3px; }
  .stat .value { font-size:13px; font-weight:700; }
  .stat .value.danger  { color:#ff4444; }
  .stat .value.warning { color:#ffaa44; }
  .stat .value.info    { color:#00d4ff; }
  .stat .value.muted   { color:#c8dff0; }
  .regime-tag { display:inline-block; font-size:9px; padding:2px 7px;
                border-radius:2px; letter-spacing:1px; margin-top:6px; }
  .regime-LEO { background:rgba(77,166,255,0.15); color:#4da6ff;
                border:1px solid rgba(77,166,255,0.3); }
  .regime-MEO { background:rgba(255,107,107,0.15); color:#ff6b6b;
                border:1px solid rgba(255,107,107,0.3); }
  .regime-GEO { background:rgba(0,255,136,0.15); color:#00ff88;
                border:1px solid rgba(0,255,136,0.3); }
  .celestrak-link { display:block; margin-top:10px; font-size:10px;
                    color:#00d4ff; text-decoration:none; }
  .footer { background:#050a0f; padding:16px 32px;
            border-top:1px solid #0d2137; text-align:center; }
  .footer p { margin:0; font-size:9px; color:#4a6a85; letter-spacing:1px; }
  .all-clear { background:#0a1520; border:1px solid #0d2137;
               border-left:3px solid #00ff88; border-radius:4px;
               padding:20px 18px; text-align:center; }
  .all-clear .icon { font-size:28px; margin-bottom:8px; }
  .all-clear .msg { font-size:13px; color:#00ff88; font-weight:700; }
  .all-clear .sub  { font-size:10px; color:#4a6a85; margin-top:4px; }
  .summary-grid { display:flex; gap:0; margin-bottom:20px; }
  .summary-cell { flex:1; padding:14px 16px; background:#0a1520;
                  border:1px solid #0d2137; text-align:center; }
  .summary-cell .num { font-size:28px; font-weight:700; color:#00d4ff; }
  .summary-cell .lbl { font-size:9px; color:#4a6a85; letter-spacing:2px;
                       text-transform:uppercase; margin-top:4px; }
"""

def _regime_tag(regime: str) -> str:
    return f'<span class="regime-tag regime-{regime}">{regime}</span>'

def _build_html_conjunction_email(alerts: list, run_utc: str, total_sats: int = 0) -> str:
    count = len(alerts)
    events_html = ""
    for i, c in enumerate(alerts, 1):
        h, m = divmod(int(c.time_min), 60)
        sat1 = str(c.sat1 or "UNKNOWN").strip()
        sat2 = str(c.sat2 or "UNKNOWN").strip()
        pc_color = "danger" if c.pc_estimate >= 1e-3 else "warning" if c.pc_estimate >= 1e-5 else "muted"
        dist_color = "danger" if c.min_dist_km < 1.0 else "warning" if c.min_dist_km < 5.0 else "info"
        url1 = f"https://celestrak.org/satcat/records.php?NAME={sat1.replace(' ', '+')}"
        url2 = f"https://celestrak.org/satcat/records.php?NAME={sat2.replace(' ', '+')}"
        card_class = "event-card" if c.pc_estimate >= 1e-4 else "event-card warning"
        events_html += f"""
        <div class="{card_class}">
          <div class="event-num">EVENT {i:02d} OF {count:02d}</div>
          <div class="sat-line">
            {sat1}<span class="arrow">↔</span>{sat2}
          </div>
          <div style="margin-bottom:8px;">
            {_regime_tag(c.regime1)} {_regime_tag(c.regime2)}
          </div>
          <div class="stats">
            <div class="stat">
              <div class="label">Miss Distance</div>
              <div class="value {dist_color}">{c.min_dist_km:.3f} km</div>
            </div>
            <div class="stat">
              <div class="label">Pc Estimate</div>
              <div class="value {pc_color}">{c.pc_estimate:.2e}</div>
            </div>
            <div class="stat">
              <div class="label">Time to CA</div>
              <div class="value info">+{h}h {m:02d}m</div>
            </div>
          </div>
          <a href="{url1}" class="celestrak-link">↗ {sat1} on CelesTrak</a>
          <a href="{url2}" class="celestrak-link">↗ {sat2} on CelesTrak</a>
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{_EMAIL_CSS}</style></head>
<body><div class="wrap">
  <div class="header">
    <div class="badge">VectraSpace // Mission Control</div>
    <h1>⚠ Conjunction Alert</h1>
    <div class="sub">VectraSpace — Orbital Safety Platform — Automated Report</div>
  </div>
  <div class="meta-bar">
    <div class="meta-item">RUN TIME<span>{run_utc}</span></div>
    <div class="meta-item">EVENTS DETECTED<span style="color:#ff4444">{count}</span></div>
    {'<div class="meta-item">SATELLITES TRACKED<span>' + str(total_sats) + '</span></div>' if total_sats else ''}
  </div>
  <div class="body">
    <div class="section-title">Conjunction Events</div>
    {events_html}
  </div>
  <div class="footer">
    <p>VectraSpace v11 — Orbital Safety Platform — trumanheaston@gmail.com</p>
    <p style="margin-top:4px;">Open dashboard → http://localhost:8000/dashboard</p>
  </div>
</div></body></html>"""

def _build_html_complete_email(total_sats: int, conjunctions: list,
                                duration_s: float, run_utc: str) -> str:
    count = len(conjunctions)
    status_color = "#ff4444" if count > 0 else "#00ff88"
    status_text = f"{count} CONJUNCTION(S) DETECTED" if count > 0 else "ALL CLEAR"

    conj_rows = ""
    for c in conjunctions[:5]:
        h, m = divmod(int(c.time_min), 60)
        conj_rows += f"""
        <tr>
          <td style="padding:6px 10px;color:#c8dff0;border-bottom:1px solid #0d2137;">{c.sat1}</td>
          <td style="padding:6px 10px;color:#c8dff0;border-bottom:1px solid #0d2137;">{c.sat2}</td>
          <td style="padding:6px 10px;color:#ff4444;border-bottom:1px solid #0d2137;font-weight:700;">{c.min_dist_km:.2f} km</td>
          <td style="padding:6px 10px;color:#00d4ff;border-bottom:1px solid #0d2137;">+{h}h {m:02d}m</td>
        </tr>"""

    table_html = ""
    if conjunctions:
        table_html = f"""
        <div class="section-title" style="margin-top:20px;">Top Conjunctions</div>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="background:#0a1520;border:1px solid #0d2137;border-radius:4px;">
          <tr style="background:#050a0f;">
            <th style="padding:8px 10px;color:#4a6a85;font-size:9px;letter-spacing:2px;text-align:left;border-bottom:1px solid #0d2137;">SAT 1</th>
            <th style="padding:8px 10px;color:#4a6a85;font-size:9px;letter-spacing:2px;text-align:left;border-bottom:1px solid #0d2137;">SAT 2</th>
            <th style="padding:8px 10px;color:#4a6a85;font-size:9px;letter-spacing:2px;text-align:left;border-bottom:1px solid #0d2137;">MISS DIST</th>
            <th style="padding:8px 10px;color:#4a6a85;font-size:9px;letter-spacing:2px;text-align:left;border-bottom:1px solid #0d2137;">TIME TO CA</th>
          </tr>
          {conj_rows}
        </table>"""

    mins = int(duration_s // 60)
    secs = int(duration_s % 60)

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{_EMAIL_CSS}</style></head>
<body><div class="wrap">
  <div class="header">
    <div class="badge">VectraSpace // Mission Control</div>
    <h1>✓ Propagation Complete</h1>
    <div class="sub">VectraSpace — Orbital Safety Platform — Run Summary</div>
  </div>
  <div class="meta-bar">
    <div class="meta-item">COMPLETED<span>{run_utc}</span></div>
    <div class="meta-item">DURATION<span>{mins}m {secs}s</span></div>
  </div>
  <div class="body">
    <div class="summary-grid">
      <div class="summary-cell">
        <div class="num">{total_sats}</div>
        <div class="lbl">Satellites Tracked</div>
      </div>
      <div class="summary-cell">
        <div class="num" style="color:{status_color}">{count}</div>
        <div class="lbl">Conjunctions Found</div>
      </div>
      <div class="summary-cell">
        <div class="num">{mins}m {secs}s</div>
        <div class="lbl">Run Duration</div>
      </div>
    </div>
    <div style="background:#0a1520;border:1px solid #0d2137;border-left:3px solid {status_color};
                border-radius:4px;padding:14px 18px;text-align:center;margin-bottom:16px;">
      <div style="font-size:13px;font-weight:700;color:{status_color};">{status_text}</div>
    </div>
    {table_html}
  </div>
  <div class="footer">
    <p>VectraSpace v11 — Orbital Safety Platform</p>
    <p style="margin-top:4px;">Open dashboard → http://localhost:8000/dashboard</p>
  </div>
</div></body></html>"""

# ── EMAIL-01: Multi-provider email engine ────────────────────
# Provider is selected by EMAIL_PROVIDER env var.
# Defaults to 'gmail' which uses trumanheaston@gmail.com + App Password.
# All providers share the same public interface: _send_email(subject, html, to, cfg, plain)

def _build_mime_message(subject: str, from_addr: str, to_addr: str,
                         html_body: str, plain_body: str) -> MIMEMultipart:
    """Build a MIME multipart/alternative message."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    msg["X-Mailer"] = "VectraSpace v11"
    fallback_plain = plain_body or "VectraSpace alert — view in an HTML-capable email client."
    msg.attach(MIMEText(fallback_plain, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))
    return msg

def _send_via_gmail(subject: str, html_body: str, to_addr: str,
                    from_addr: str, plain_body: str = "") -> bool:
    """
    Gmail App Password SMTP (trumanheaston@gmail.com).
    Setup: Google Account → Security → 2-Step Verification → App passwords
    Set ALERT_SMTP_PASS to the 16-char app password (no spaces).
    """
    smtp_pass = os.environ.get("ALERT_SMTP_PASS", "").strip()
    if not smtp_pass:
        log.warning("  ✗ Gmail: ALERT_SMTP_PASS not set. "
                    "Generate an App Password at myaccount.google.com/apppasswords")
        return False
    try:
        msg = _build_mime_message(subject, from_addr, to_addr, html_body, plain_body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
            s.login(from_addr, smtp_pass)
            s.send_message(msg)
        log.info(f"  ✓ [Gmail] sent → {to_addr}")
        return True
    except smtplib.SMTPAuthenticationError:
        log.warning("  ✗ Gmail auth failed — check ALERT_SMTP_PASS and that 2FA + App Passwords are enabled")
        return False
    except Exception as e:
        log.warning(f"  ✗ Gmail send failed: {type(e).__name__}: {e}")
        return False

def _send_via_sendgrid(subject: str, html_body: str, to_addr: str,
                        from_addr: str, plain_body: str = "") -> bool:
    """
    SendGrid HTTP API (free tier: 100 emails/day).
    Setup: sendgrid.com → Settings → API Keys → Create Key (Mail Send permission).
    Set SENDGRID_API_KEY=SG.xxxxxx
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not api_key:
        log.warning("  ✗ SendGrid: SENDGRID_API_KEY not set")
        return False
    try:
        payload = {
            "personalizations": [{"to": [{"email": to_addr}]}],
            "from": {"email": from_addr, "name": "VectraSpace"},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": plain_body or subject},
                {"type": "text/html",  "value": html_body or plain_body or subject},
            ],
        }
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 202):
            log.info(f"  ✓ [SendGrid] sent → {to_addr}")
            return True
        log.warning(f"  ✗ SendGrid HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        log.warning(f"  ✗ SendGrid send failed: {e}")
        return False

def _send_via_ses(subject: str, html_body: str, to_addr: str,
                   from_addr: str, plain_body: str = "") -> bool:
    """
    AWS SES via SMTP (very high deliverability, generous free tier).
    Setup: AWS Console → SES → SMTP Settings → Create SMTP Credentials.
    Set AWS_SES_HOST, AWS_SES_USER, AWS_SES_PASS.
    Note: from_addr domain must be verified in SES.
    """
    host = os.environ.get("AWS_SES_HOST", "email-smtp.us-east-1.amazonaws.com")
    user = os.environ.get("AWS_SES_USER", "").strip()
    pwd  = os.environ.get("AWS_SES_PASS", "").strip()
    if not user or not pwd:
        log.warning("  ✗ SES: AWS_SES_USER or AWS_SES_PASS not set")
        return False
    try:
        msg = _build_mime_message(subject, from_addr, to_addr, html_body, plain_body)
        with smtplib.SMTP_SSL(host, 465, timeout=15) as s:
            s.login(user, pwd)
            s.send_message(msg)
        log.info(f"  ✓ [AWS SES] sent → {to_addr}")
        return True
    except Exception as e:
        log.warning(f"  ✗ SES send failed: {type(e).__name__}: {e}")
        return False

def _send_via_postmark(subject: str, html_body: str, to_addr: str,
                        from_addr: str, plain_body: str = "") -> bool:
    """
    Postmark HTTP API (excellent deliverability, 100 free emails/month on free plan).
    Setup: postmarkapp.com → Servers → API Tokens.
    Set POSTMARK_SERVER_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    Note: from_addr must be a verified sender signature in Postmark.
    """
    token = os.environ.get("POSTMARK_SERVER_TOKEN", "").strip()
    if not token:
        log.warning("  ✗ Postmark: POSTMARK_SERVER_TOKEN not set")
        return False
    try:
        payload = {
            "From":     from_addr,
            "To":       to_addr,
            "Subject":  subject,
            "TextBody": plain_body or subject,
            "HtmlBody": html_body or plain_body or subject,
            "MessageStream": "outbound",
        }
        resp = requests.post(
            "https://api.postmarkapp.com/email",
            headers={"X-Postmark-Server-Token": token, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("ErrorCode", 1) == 0:
            log.info(f"  ✓ [Postmark] sent → {to_addr}")
            return True
        log.warning(f"  ✗ Postmark error {data.get('ErrorCode')}: {data.get('Message', '')}")
        return False
    except Exception as e:
        log.warning(f"  ✗ Postmark send failed: {e}")
        return False

# ── Provider dispatch ─────────────────────────────────────────
_EMAIL_PROVIDERS = {
    "gmail":    _send_via_gmail,
    "sendgrid": _send_via_sendgrid,
    "ses":      _send_via_ses,
    "postmark": _send_via_postmark,
}

def _send_email(subject: str, html_body: str, to_addr: str, cfg: Config,
                plain_body: str = "") -> bool:
    """
    Unified email send. Routes to the provider set by EMAIL_PROVIDER env var.
    Defaults to 'gmail' (trumanheaston@gmail.com + App Password).
    Falls back through providers in order if the primary fails.
    """
    if not to_addr or not cfg.alert_email_from:
        return False

    provider_name = os.environ.get("EMAIL_PROVIDER", "gmail").lower().strip()
    provider_fn = _EMAIL_PROVIDERS.get(provider_name)

    if provider_fn is None:
        log.warning(f"  ✗ Unknown EMAIL_PROVIDER='{provider_name}'. "
                    f"Valid options: {', '.join(_EMAIL_PROVIDERS)}")
        return False

    return provider_fn(subject, html_body, to_addr, cfg.alert_email_from, plain_body)

# ── Backwards-compat shim (used internally) ───────────────────
def _smtp_send(subject: str, html_body: str, to_addr: str, cfg: Config,
               plain_body: str = "") -> bool:
    """Legacy shim — delegates to _send_email."""
    return _send_email(subject, html_body, to_addr, cfg, plain_body)

def send_email_alert(html: str, cfg: Config, subject: str = "[VectraSpace] ⚠ Conjunction Alert",
                     to_addr: Optional[str] = None):
    addr = to_addr or cfg.alert_email_to
    if not addr or not cfg.alert_email_from:
        return
    provider = os.environ.get("EMAIL_PROVIDER", "gmail")
    log.info(f"  Sending alert email via {provider} → {addr}")
    ok = _send_email(subject, html, addr, cfg)
    if ok:
        log.info(f"  ✓ Alert email sent to {addr}")

def send_webhook_alert(body: str, cfg: Config):
    if not cfg.alert_webhook_url:
        return
    try:
        resp = requests.post(cfg.alert_webhook_url,
                             json={"text": f"```\n{body}\n```"}, timeout=10)
        resp.raise_for_status()
        log.info("  ✓ Webhook alert sent")
    except Exception as e:
        log.warning(f"  ✗ Webhook failed: {e}")

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

def send_pushover(title: str, message: str, priority: int, cfg: Config,
                  url: str = "http://localhost:8000", url_title: str = "Open Dashboard",
                  pushover_user_key: Optional[str] = None):
    token = cfg.pushover_token or os.environ.get("PUSHOVER_TOKEN")
    user  = pushover_user_key or os.environ.get("PUSHOVER_USER_KEY_RUNTIME") or cfg.pushover_user_key
    if not token or not user:
        return
    try:
        payload = {
            "token":     token,
            "user":      user,
            "title":     title,
            "message":   message,
            "priority":  priority,
            "url":       url,
            "url_title": url_title,
        }
        r = requests.post(PUSHOVER_URL, data=payload, timeout=10)
        r.raise_for_status()
        log.info("  ✓ Pushover notification sent")
    except Exception as e:
        log.warning(f"  ✗ Pushover failed: {e}")

def send_alerts(conjunctions: list, cfg: Config, total_sats: int = 0,
                user_prefs: Optional[dict] = None):
    """Fire conjunction alerts. user_prefs overrides cfg settings for per-user routing."""
    pc_thresh = (user_prefs or {}).get("pc_alert_threshold", cfg.pc_alert_threshold)
    alert_km  = (user_prefs or {}).get("collision_alert_km", cfg.collision_alert_km)

    alerts = [c for c in conjunctions
              if c.pc_estimate >= pc_thresh or c.min_dist_km < alert_km]

    if not alerts:
        log.info("No conjunctions crossed alert thresholds — no alerts sent")
        return

    log.info(f"Sending alerts for {len(alerts)} conjunction(s)...")
    run_utc = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    email_to  = (user_prefs or {}).get("email") or cfg.alert_email_to
    pv_key    = (user_prefs or {}).get("pushover_key") or cfg.pushover_user_key

    html = _build_html_conjunction_email(alerts, run_utc, total_sats)
    send_email_alert(html, cfg, to_addr=email_to)
    send_webhook_alert(f"VectraSpace CONJUNCTION ALERT\n{len(alerts)} event(s) at {run_utc}", cfg)

    top = alerts[:3]
    pv_body = ", ".join(f"{c.sat1[:10]}↔{c.sat2[:10]} ({c.min_dist_km:.1f}km)" for c in top)
    if len(alerts) > 3:
        pv_body += f" ...and {len(alerts)-3} more"
    send_pushover(
        title="VectraSpace ⚠ Conjunction Alert",
        message=pv_body,
        priority=1,
        cfg=cfg,
        pushover_user_key=pv_key,
    )

def send_propagation_complete(total_sats: int, conjunctions: list,
                               duration_s: float, cfg: Config,
                               user_prefs: Optional[dict] = None):
    email_to = (user_prefs or {}).get("email") or cfg.alert_email_to
    pv_key   = (user_prefs or {}).get("pushover_key") or cfg.pushover_user_key

    if not email_to and not cfg.pushover_user_key:
        return

    run_utc = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    count = len(conjunctions)
    mins = int(duration_s // 60)
    secs = int(duration_s % 60)

    log.info("Sending propagation-complete notification...")

    if email_to:
        html = _build_html_complete_email(total_sats, conjunctions, duration_s, run_utc)
        subj = f"[VectraSpace] ✓ Scan Complete — {count} conjunction(s) found"
        send_email_alert(html, cfg, subject=subj, to_addr=email_to)

    send_pushover(
        title="VectraSpace ✓ Scan Complete",
        message=f"{total_sats} sats tracked. {count} conjunction(s). Duration: {mins}m{secs}s.",
        priority=-1,
        cfg=cfg,
        pushover_user_key=pv_key,
    )

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 5b — AUTHENTICATION                                  ║
# ╚══════════════════════════════════════════════════════════════╝

# ── AUTH — stdlib only, no bcrypt/itsdangerous ───────────────────────────────
import hashlib as _hashlib
import hmac as _hmac
import secrets as _secrets

_PBKDF2_ITERS  = 260_000   # OWASP 2023 recommendation for PBKDF2-SHA256
_SESSION_SEP   = "|"       # outer separator — never appears in base64
_login_attempts: dict = {}  # ip -> [timestamp, ...]

def _hash_password(plain: str) -> str:
    """Return 'pbkdf2:sha256:iters:salt:hash' string."""
    salt = _secrets.token_hex(16)
    dk   = _hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), _PBKDF2_ITERS)
    return f"pbkdf2:sha256:{_PBKDF2_ITERS}:{salt}:{dk.hex()}"

def _verify_password(plain: str, stored: str) -> bool:
    """Verify against either new pbkdf2 format or legacy bcrypt format."""
    if not plain or not stored:
        return False
    if stored.startswith("pbkdf2:sha256:"):
        try:
            _, _, iters, salt, hx = stored.split(":")
            dk = _hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), int(iters))
            return _hmac.compare_digest(dk.hex(), hx)
        except Exception:
            return False
    # Legacy bcrypt — try if available
    try:
        import bcrypt as _bcrypt
        return _bcrypt.checkpw(plain.encode(), stored.encode())
    except Exception:
        return False

def _make_session_token(username: str, role: str, secret: str) -> str:
    """Create a signed session token: base64(username\x00role\x00ts)|hmac"""
    import base64, time as _t
    ts      = str(int(_t.time()))
    # Use \x00 as internal separator — it encodes into base64, no collisions
    payload = base64.urlsafe_b64encode(f"{username}\x00{role}\x00{ts}".encode()).decode()
    sig     = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    return f"{payload}{_SESSION_SEP}{sig}"  # | separator never in base64

def _verify_session_token(token: str, secret: str, max_age: int = 2592000):
    """Returns (username, role) or raises ValueError."""
    import base64, time as _t
    if not token or _SESSION_SEP not in token:
        raise ValueError("malformed")
    payload, sig = token.split(_SESSION_SEP, 1)  # split on first | only
    expected = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(sig, expected):
        raise ValueError("bad signature")
    decoded = base64.urlsafe_b64decode(payload.encode()).decode()
    parts   = decoded.split("\x00")  # internal separator
    if len(parts) != 3:
        raise ValueError("malformed payload")
    username, role, ts = parts
    if int(_t.time()) - int(ts) > max_age:
        raise ValueError("expired")
    return username, role

# Keep old name as alias so existing call-sites work
def _make_session_cookie(username: str, role: str, secret: str) -> str:
    return _make_session_token(username, role, secret)

def _verify_session_cookie(token: str, secret: str, max_age: int = 2592000):
    return _verify_session_token(token, secret, max_age)

def get_current_user_from_request(request, cfg) -> "Optional[dict]":
    token = request.cookies.get("vs_session", "")
    if not token:
        return None
    try:
        username, role = _verify_session_token(token, cfg.session_secret)
        return {"username": username, "role": role}
    except Exception:
        return None

def _check_login_rate_limit(ip: str) -> bool:
    import time as _t
    now = _t.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < 300]
    _login_attempts[ip] = attempts
    if len(attempts) >= 20:
        return False
    _login_attempts[ip].append(now)
    return True

def _load_users(cfg) -> dict:
    """Load users from SQLite DB. Falls back to users.json if DB not ready."""
    try:
        con = sqlite3.connect(cfg.db_path)
        # Table may not exist yet if init_db hasn't run — check first
        tbl = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if tbl:
            rows = con.execute(
                "SELECT username, password_hash, role, email, approved, created_at FROM users"
            ).fetchall()
            con.close()
            if rows:
                return {r[0]: {"username":r[0],"password_hash":r[1],"role":r[2],
                               "email":r[3],"approved":bool(r[4]),"created_at":r[5]} for r in rows}
        con.close()
    except Exception as _e:
        log.warning(f"_load_users DB error: {_e}")
    # Fallback to users.json
    p = Path(cfg.users_file)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        if isinstance(raw, list):
            return {u["username"]: u for u in raw if "username" in u}
        if isinstance(raw, dict):
            return raw
    except Exception as e:
        log.warning(f"Failed to load users: {e}")
    return {}

def _save_users(users: dict, cfg) -> None:
    """Save users to SQLite DB (primary) and users.json (backup)."""
    try:
        con = sqlite3.connect(cfg.db_path)
        for u in users.values():
            con.execute("""
                INSERT INTO users (username, password_hash, role, email, approved, created_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash=excluded.password_hash,
                    role=excluded.role,
                    email=excluded.email,
                    approved=excluded.approved
            """, (u["username"], u["password_hash"], u.get("role","operator"),
                  u.get("email",""), 1 if u.get("approved",True) else 0,
                  u.get("created_at","")))
        con.commit()
        con.close()
    except Exception as e:
        log.warning(f"Failed to save users to DB: {e}")
        # Fallback: write JSON
        try:
            Path(cfg.users_file).write_text(json.dumps(list(users.values()), indent=2))
        except Exception:
            pass

def create_user(username: str, password: str, role: str = "operator", cfg=None):
    """Create or overwrite a user account. Safe to call at startup."""
    if cfg is None:
        cfg = CFG
    username = username.strip().lower()
    users    = _load_users(cfg)
    import time as _t
    users[username] = {
        "username":      username,
        "password_hash": _hash_password(password),
        "role":          role,
        "email":         users.get(username, {}).get("email", ""),
        "approved":      True,
        "created_at":    users.get(username, {}).get("created_at", _t.strftime("%Y-%m-%dT%H:%M:%SZ")),
    }
    _save_users(users, cfg)
    log.info(f"User '{username}' saved with role '{role}'")

def _register_user(username: str, email: str, password: str, cfg, approved: bool = True):
    """Register new user. Returns (ok, error_msg)."""
    import time as _t
    username = username.strip().lower()
    email    = email.strip().lower()
    if len(username) < 3:
        return False, "Username must be at least 3 characters"
    if not username.replace("_","").replace("-","").isalnum():
        return False, "Username may only contain letters, numbers, - and _"
    if "@" not in email:
        return False, "Invalid email address"
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    users = _load_users(cfg)
    if username in users:
        return False, "Username already taken"
    if any(u.get("email","").lower() == email for u in users.values()):
        return False, "An account with that email already exists"
    users[username] = {
        "username":      username,
        "password_hash": _hash_password(password),
        "role":          "operator",
        "email":         email,
        "approved":      approved,
        "created_at":    _t.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_users(users, cfg)
    log.info(f"Registered user '{username}' <{email}>")
    return True, ""

def _get_user_prefs(username: str, cfg: Config) -> dict:
    """Load per-user preferences from user_preferences table."""
    try:
        con = sqlite3.connect(cfg.db_path)
        row = con.execute(
            "SELECT email, phone, pushover_key, pc_alert_threshold, collision_alert_km FROM user_preferences WHERE user_id=?",
            (username,)
        ).fetchone()
        if row:
            return {
                "email": row[0], "phone": row[1], "pushover_key": row[2],
                "pc_alert_threshold": row[3] or 1e-4,
                "collision_alert_km": row[4] or 10.0,
            }
    except Exception:
        pass
    return {}

def _save_user_prefs(username: str, prefs: dict, cfg: Config):
    """Upsert per-user preferences."""
    con = sqlite3.connect(cfg.db_path)
    con.execute("""
        INSERT INTO user_preferences (user_id, email, phone, pushover_key, pc_alert_threshold, collision_alert_km, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            email=excluded.email,
            phone=excluded.phone,
            pushover_key=excluded.pushover_key,
            pc_alert_threshold=excluded.pc_alert_threshold,
            collision_alert_km=excluded.collision_alert_km,
            updated_at=excluded.updated_at
    """, (
        username,
        prefs.get("email", ""),
        prefs.get("phone", ""),
        prefs.get("pushover_key", ""),
        float(prefs.get("pc_alert_threshold", 1e-4)),
        float(prefs.get("collision_alert_km", 10.0)),
        datetime.datetime.utcnow().isoformat(),
    ))
    con.commit()

# ── AUTH-02: Password reset token helpers ─────────────────────

def _make_reset_token(username: str, secret: str) -> str:
    """Signed time-limited reset token (stdlib only)."""
    import base64, time as _t
    ts      = str(int(_t.time()))
    payload = base64.urlsafe_b64encode(f"{username}:{ts}".encode()).decode()
    sig     = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def _verify_reset_token(token: str, secret: str, max_age: int = 3600) -> "Optional[str]":
    """Returns username or None if invalid/expired."""
    import base64, time as _t
    try:
        payload, sig = token.rsplit(".", 1)
        expected = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        decoded   = base64.urlsafe_b64decode(payload.encode()).decode()
        username, ts = decoded.split(":", 1)
        if int(_t.time()) - int(ts) > max_age:
            return None
        return username
    except Exception:
        return None

def _update_password(username: str, new_password: str, cfg) -> bool:
    """Hash and persist a new password."""
    users = _load_users(cfg)
    if username not in users:
        return False
    users[username]["password_hash"] = _hash_password(new_password)
    _save_users(users, cfg)
    log.info(f"Password updated for '{username}'")
    return True

def _get_user_email(username: str, cfg: Config) -> Optional[str]:
    """Look up a user's email from users.json or user_preferences."""
    users = _load_users(cfg)
    u = users.get(username, {})
    if u.get("email"):
        return u["email"]
    # Fall back to preferences table
    prefs = _get_user_prefs(username, cfg)
    return prefs.get("email")

# ── Auth page HTML templates ──────────────────────────────────
# Shared CSS injected into all auth pages
_AUTH_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #050a0f; color: #c8dff0; font-family: 'Exo 2', sans-serif;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; padding: 24px 16px;
         overflow-x: hidden; }
  html { overflow-x: hidden; }
  .card { background: #090f17; border: 1px solid #0d2137; border-radius: 8px;
          padding: 40px 36px; width: 100%; max-width: 420px;
          box-shadow: 0 0 40px rgba(0,212,255,0.08); }
  .logo { font-family: 'Share Tech Mono', monospace; font-size: 10px;
          color: #00d4ff; letter-spacing: 4px; margin-bottom: 6px; }
  h1 { font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 6px; }
  .sub { font-size: 11px; color: #4a6a85; font-family: 'Share Tech Mono', monospace;
         margin-bottom: 28px; }
  .section-title { font-size: 9px; color: #00d4ff; letter-spacing: 3px;
                   text-transform: uppercase; margin-bottom: 10px; padding-bottom: 6px;
                   border-bottom: 1px solid #0d2137; margin-top: 20px; }
  label { display: block; font-size: 10px; color: #4a6a85; letter-spacing: 1px;
          text-transform: uppercase; margin-bottom: 5px; margin-top: 12px; }
  input { width: 100%; background: #0a1520; border: 1px solid #0d2137;
          border-radius: 4px; color: #c8dff0; font-family: 'Share Tech Mono', monospace;
          font-size: 13px; padding: 9px 12px; outline: none; transition: border-color 0.2s;
          margin-bottom: 4px; }
  input:focus { border-color: #00d4ff; }
  button, .btn { display: block; width: 100%; margin-top: 20px; padding: 11px;
                 background: transparent; border: 1px solid #00d4ff; border-radius: 4px;
                 color: #00d4ff; font-family: 'Share Tech Mono', monospace; font-size: 12px;
                 letter-spacing: 3px; cursor: pointer; transition: all 0.2s;
                 text-transform: uppercase; text-decoration: none; text-align: center; }
  button:hover, .btn:hover { background: rgba(0,212,255,0.08); }
  .err  { color: #ff4444; font-size: 11px; margin-bottom: 12px;
          font-family: 'Share Tech Mono', monospace; padding: 8px 10px;
          background: rgba(255,68,68,0.08); border-radius: 4px; border-left: 2px solid #ff4444; }
  .ok   { color: #00ff88; font-size: 11px; margin-bottom: 12px;
          font-family: 'Share Tech Mono', monospace; padding: 8px 10px;
          background: rgba(0,255,136,0.08); border-radius: 4px; border-left: 2px solid #00ff88; }
  .nav  { margin-top: 18px; text-align: center; font-size: 10px;
          color: #4a6a85; font-family: 'Share Tech Mono', monospace; line-height: 2; }
  .nav a { color: #00d4ff; text-decoration: none; margin: 0 6px; }
  .nav a:hover { text-decoration: underline; }
  .hint { font-size: 9px; color: #4a6a85; margin-top: 3px; font-family: 'Share Tech Mono', monospace; }
  .pw-rules { font-size: 9px; color: #4a6a85; margin-top: 6px; font-family: 'Share Tech Mono', monospace;
              padding: 6px 8px; background: #040a10; border-radius: 3px; }
  @media (max-width: 480px) {
    body { padding: 0; align-items: flex-start; padding-top: 32px; }
    .card { padding: 28px 20px; border-radius: 0;
            border-left: none; border-right: none; margin: 0; }
    h1 { font-size: 18px; }
  }
"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VectraSpace — Sign In</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">
<style>{AUTH_CSS}</style>

<script defer src="https://cloud.umami.is/script.js" data-website-id="4e12fc04-8b26-4e42-8b69-0700a95c7d30"></script>
</head>
<body>
<div class="card">
  <div class="logo">VectraSpace // Mission Control</div>
  <h1>Sign In</h1>
  <div class="sub">Orbital Safety Platform — v11</div>
  {ERROR}
  <form method="post" action="/login">
    <label for="username">Username</label>
    <input type="text" id="username" name="username" autocomplete="username" required>
    <label for="password">Password</label>
    <input type="password" id="password" name="password" autocomplete="current-password" required>
    <button type="submit">Sign In</button>
  </form>
  <div class="nav">
    <a href="/forgot-password">Forgot password?</a>
    &nbsp;·&nbsp;
    {SIGNUP_LINK}
    &nbsp;·&nbsp;
    <a href="/dashboard">View Demo</a>
  </div>
</div>
</body>
</html>""".replace("{AUTH_CSS}", _AUTH_CSS)

SIGNUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VectraSpace — Create Account</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">
<style>{AUTH_CSS}</style>

<script defer src="https://cloud.umami.is/script.js" data-website-id="4e12fc04-8b26-4e42-8b69-0700a95c7d30"></script>
</head>
<body>
<div class="card">
  <div class="logo">VectraSpace // Mission Control</div>
  <h1>Create Account</h1>
  <div class="sub">Orbital Safety Platform — v11</div>
  {MESSAGE}
  {FORM}
  <div class="nav">
    Already have an account? <a href="/login">Sign in</a>
  </div>
</div>
</body>
</html>""".replace("{AUTH_CSS}", _AUTH_CSS)

SIGNUP_CLOSED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>VectraSpace — Registration</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">
<style>{AUTH_CSS}</style>
</head>
<body>
<div class="card">
  <div class="logo">VectraSpace // Mission Control</div>
  <h1>Registration Closed</h1>
  <div class="sub">Public signups are currently disabled</div>
  <p style="font-size:11px;color:#4a6a85;line-height:1.7;margin-top:16px;">
    VectraSpace accounts are currently invite-only. Contact
    <a href="mailto:trumanheaston@gmail.com" style="color:#00d4ff;">trumanheaston@gmail.com</a>
    to request access.
  </p>
  <div class="nav"><a href="/login">← Back to Sign In</a></div>
</div>
</body>
</html>""".replace("{AUTH_CSS}", _AUTH_CSS)

FORGOT_PASSWORD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VectraSpace — Reset Password</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">
<style>{AUTH_CSS}</style>
</head>
<body>
<div class="card">
  <div class="logo">VectraSpace // Mission Control</div>
  <h1>Reset Password</h1>
  <div class="sub">Enter your username — we'll send a reset link to your registered email</div>
  {MESSAGE}
  {FORM}
  <div class="nav"><a href="/login">← Back to Sign In</a></div>
</div>
</body>
</html>""".replace("{AUTH_CSS}", _AUTH_CSS)

RESET_PASSWORD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>VectraSpace — Set New Password</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">
<style>{AUTH_CSS}</style>
</head>
<body>
<div class="card">
  <div class="logo">VectraSpace // Mission Control</div>
  <h1>Set New Password</h1>
  <div class="sub">Choose a strong password for your account</div>
  {MESSAGE}
  {FORM}
  <div class="nav"><a href="/login">← Back to Sign In</a></div>
</div>
</body>
</html>""".replace("{AUTH_CSS}", _AUTH_CSS)

PREFERENCES_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>VectraSpace — Preferences</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #050a0f; color: #c8dff0; font-family: 'Exo 2', sans-serif;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; padding: 40px 16px; }
  .card { background: #090f17; border: 1px solid #0d2137; border-radius: 8px;
          padding: 40px 36px; width: 460px; box-shadow: 0 0 40px rgba(0,212,255,0.08); }
  .logo { font-family: 'Share Tech Mono', monospace; font-size: 10px;
          color: #00d4ff; letter-spacing: 4px; margin-bottom: 6px; }
  h1 { font-size: 20px; font-weight: 700; color: #fff; margin-bottom: 4px; }
  .sub { font-size: 11px; color: #4a6a85; font-family: 'Share Tech Mono', monospace;
         margin-bottom: 28px; }
  .section-title { font-size: 9px; color: #00d4ff; letter-spacing: 3px;
                   text-transform: uppercase; margin-bottom: 12px; padding-bottom: 6px;
                   border-bottom: 1px solid #0d2137; margin-top: 20px; }
  label { display: block; font-size: 10px; color: #4a6a85; letter-spacing: 1px;
          text-transform: uppercase; margin-bottom: 5px; margin-top: 12px; }
  input { width: 100%; background: #0a1520; border: 1px solid #0d2137;
          border-radius: 4px; color: #c8dff0; font-family: 'Share Tech Mono', monospace;
          font-size: 13px; padding: 9px 12px; outline: none; transition: border-color 0.2s; }
  input:focus { border-color: #00d4ff; }
  button { margin-top: 24px; width: 100%; padding: 11px; background: transparent;
           border: 1px solid #00d4ff; border-radius: 4px; color: #00d4ff;
           font-family: 'Share Tech Mono', monospace; font-size: 12px;
           letter-spacing: 3px; cursor: pointer; transition: all 0.2s; text-transform: uppercase; }
  button:hover { background: rgba(0,212,255,0.08); }
  .ok  { color: #00ff88; font-size: 11px; margin-bottom: 10px; font-family: 'Share Tech Mono', monospace; }
  .err { color: #ff4444; font-size: 11px; margin-bottom: 10px; font-family: 'Share Tech Mono', monospace; }
  .nav { margin-top: 16px; text-align: center; font-size: 10px;
         color: #4a6a85; font-family: 'Share Tech Mono', monospace; }
  .nav a { color: #00d4ff; text-decoration: none; margin: 0 8px; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">VectraSpace // Settings</div>
  <h1>Alert Preferences</h1>
  <div class="sub">Logged in as {USERNAME}</div>
  {MESSAGE}
  <form method="post" action="/preferences">
    <div class="section-title">Notification Routing</div>
    <label>Alert Email</label>
    <input type="email" name="email" value="{EMAIL}" placeholder="you@example.com">
    <label>Pushover User Key</label>
    <input type="text" name="pushover_key" value="{PUSHOVER_KEY}" placeholder="Leave blank to disable">

    <div class="section-title">Detection Thresholds</div>
    <label>Pc Alert Threshold (e.g. 0.0001)</label>
    <input type="number" name="pc_alert_threshold" step="any" value="{PC_THRESH}" placeholder="0.0001">
    <label>Collision Alert Distance (km)</label>
    <input type="number" name="collision_alert_km" step="0.1" value="{ALERT_KM}" placeholder="10.0">

    <button type="submit">Save Preferences</button>
  </form>
  <div class="nav">
    <a href="/">← Dashboard</a>
    &nbsp;·&nbsp;
    <a href="/change-password">Change Password</a>
    &nbsp;·&nbsp;
    <a href="/logout">Sign Out</a>
  </div>
</div>
</body>
</html>"""

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 5c — SCHEDULED AUTONOMOUS RUNS                       ║
# ╚══════════════════════════════════════════════════════════════╝

LOCKFILE = Path("vectraspace.lock")

def _acquire_lock() -> bool:
    if LOCKFILE.exists():
        return False
    try:
        LOCKFILE.write_text(str(os.getpid()))
        return True
    except Exception:
        return False

def _release_lock():
    try:
        LOCKFILE.unlink(missing_ok=True)
    except Exception:
        pass

def generate_task_xml(python_exe: str, script_path: str,
                      interval_hours: int = 6,
                      output_path: str = "VectraSpace_Task.xml") -> str:
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>VectraSpace v11 — Orbital Safety Platform — Scheduled Run</Description>
    <Author>VectraSpace Team</Author>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT{interval_hours}H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>Password</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>{script_path} --headless</Arguments>
      <WorkingDirectory>{Path(script_path).parent}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""
    Path(output_path).write_text(xml, encoding="utf-16")
    log.info(f"Task Scheduler XML written to {output_path}")
    return xml

def run_headless(cfg: Config):
    """Execute the full pipeline without starting the web server."""
    fh = logging.handlers.RotatingFileHandler(
        "vectraspace_scheduled.log", maxBytes=5*1024*1024, backupCount=3
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger("VectraSpace").addHandler(fh)

    if not _acquire_lock():
        log.warning("Lockfile exists — another run is active. Exiting.")
        sys.exit(1)

    try:
        log.info("Starting headless scheduled run...")
        covariance_cache = fetch_covariance_cache(cfg)
        result = _run_pipeline(cfg, covariance_cache=covariance_cache, run_mode="scheduled", user_id=None)
        conj = result["conjunctions"]
        tracks = result["tracks"]
        log.info(f"Headless run complete — {len(tracks)} satellites, {len(conj)} conjunction(s)")
        sys.exit(0)
    except Exception as e:
        log.error(f"Headless run failed: {e}")
        sys.exit(2)
    finally:
        _release_lock()

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 6 — WEB UI + CESIUMJS VISUALIZATION                  ║
# ╚══════════════════════════════════════════════════════════════╝

def _get_cesium_token() -> str:
    # Use env var if set, otherwise fall back to hardcoded default
    return os.environ.get("CESIUM_ION_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJlMzRmMGI5Ni1hMTM0LTQxMjgtODgzMy04ZGYxN2UzNzYyN2MiLCJpZCI6MzkyNzg4LCJpYXQiOjE3NzE2OTU4OTF9.lulZ9jWB9A_XCxfui1FpcGmC7A7B49znZpcwn7yg530")

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>VectraSpace — Orbital Safety Platform</title>
<script src="https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Cesium.js"></script>
<link href="https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Widgets/widgets.css" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:        #050a0f;
    --panel:     #090f17;
    --border:    #0d2137;
    --accent:    #00d4ff;
    --accent2:   #ff4444;
    --accent3:   #00ff88;
    --text:      #c8dff0;
    --muted:     #4a6a85;
    --panel-w:   340px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 100%; height: 100%; overflow: hidden; background: var(--bg); color: var(--text); font-family: 'Exo 2', sans-serif; }

  #app { display: flex; height: 100vh; }
  #sidebar {
    width: var(--panel-w);
    min-width: var(--panel-w);
    background: var(--panel);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    z-index: 10;
    transition: width 0.25s ease, min-width 0.25s ease;
  }
  #sidebar.collapsed {
    width: 42px;
    min-width: 42px;
  }
  #sidebar.collapsed .sidebar-collapsible { display: none; }
  #sidebar.collapsed #sidebar-toggle-btn {
    margin: 0 auto;
    border-left: none;
  }
  #sidebar-toggle-btn {
    background: transparent;
    border: none;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-family: 'Share Tech Mono', monospace;
    font-size: 14px;
    padding: 10px;
    cursor: pointer;
    width: 100%;
    text-align: center;
    transition: color 0.2s, background 0.2s;
    flex-shrink: 0;
  }
  #sidebar-toggle-btn:hover { color: var(--accent); background: rgba(0,212,255,0.05); }
  #globe-container { flex: 1; position: relative; transition: flex 0.25s ease; }
  #cesiumContainer { width: 100%; height: 100%; }

  #header {
    padding: 20px 18px 14px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #0a1929 0%, var(--panel) 100%);
  }
  #header .logo {
    font-family: 'Share Tech Mono', monospace;
    font-size: 11px;
    color: var(--accent);
    letter-spacing: 3px;
    margin-bottom: 4px;
    text-transform: uppercase;
  }
  #header h1 { font-size: 15px; font-weight: 700; color: #fff; letter-spacing: 0.5px; }
  #header .sub {
    font-size: 10px;
    color: var(--muted);
    font-family: 'Share Tech Mono', monospace;
    margin-top: 3px;
  }
  #user-bar {
    padding: 6px 18px;
    background: #040a10;
    border-bottom: 1px solid var(--border);
    font-family: 'Share Tech Mono', monospace;
    font-size: 9px;
    color: var(--muted);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  #user-bar .user-name { color: var(--accent); }
  #user-bar a { color: var(--muted); text-decoration: none; font-size: 9px; letter-spacing: 1px; }
  #user-bar a:hover { color: var(--accent); }

  #scroll { flex: 1; overflow-y: auto; padding: 16px; }
  #scroll::-webkit-scrollbar { width: 4px; }
  #scroll::-webkit-scrollbar-track { background: transparent; }
  #scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  .section { margin-bottom: 20px; }
  .section-title {
    font-family: 'Share Tech Mono', monospace;
    font-size: 9px;
    letter-spacing: 3px;
    color: var(--accent);
    text-transform: uppercase;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }

  .field { margin-bottom: 12px; }
  .field label {
    display: block;
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 4px;
  }
  .field input {
    width: 100%;
    background: #0a1520;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text);
    font-family: 'Share Tech Mono', monospace;
    font-size: 13px;
    padding: 7px 10px;
    outline: none;
    transition: border-color 0.2s;
  }
  .field input:focus { border-color: var(--accent); }
  .field .hint {
    font-size: 9px;
    color: var(--muted);
    margin-top: 3px;
    font-family: 'Share Tech Mono', monospace;
  }

  #run-btn {
    width: 100%;
    padding: 12px;
    background: transparent;
    border: 1px solid var(--accent);
    border-radius: 4px;
    color: var(--accent);
    font-family: 'Share Tech Mono', monospace;
    font-size: 13px;
    letter-spacing: 3px;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.2s;
    position: relative;
    overflow: hidden;
  }
  #run-btn:hover { background: rgba(0,212,255,0.08); box-shadow: 0 0 20px rgba(0,212,255,0.15); }
  #run-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  #run-btn.running {
    border-color: var(--accent3);
    color: var(--accent3);
    animation: pulse-border 1.5s infinite;
  }
  @keyframes pulse-border {
    0%, 100% { box-shadow: 0 0 0 0 rgba(0,255,136,0.3); }
    50% { box-shadow: 0 0 0 6px rgba(0,255,136,0); }
  }

  #run-locked-msg {
    width: 100%;
    padding: 12px;
    background: rgba(74,106,133,0.1);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--muted);
    font-family: 'Share Tech Mono', monospace;
    font-size: 11px;
    letter-spacing: 1px;
    text-align: center;
  }

  #status-bar {
    padding: 8px 16px;
    border-top: 1px solid var(--border);
    font-family: 'Share Tech Mono', monospace;
    font-size: 10px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  #status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
  #status-dot.ready { background: var(--accent3); box-shadow: 0 0 6px var(--accent3); }
  #status-dot.running { background: var(--accent); animation: blink 1s infinite; }
  #status-dot.error { background: var(--accent2); }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
  #status-text { color: var(--muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  #log-panel {
    background: #040a10;
    border: 1px solid var(--border);
    border-radius: 4px;
    height: 140px;
    overflow-y: auto;
    padding: 8px;
    font-family: 'Share Tech Mono', monospace;
    font-size: 10px;
    line-height: 1.6;
  }
  #log-panel::-webkit-scrollbar { width: 3px; }
  #log-panel::-webkit-scrollbar-thumb { background: var(--border); }
  .log-line { color: var(--muted); }
  .log-line.info { color: #5ba3c9; }
  .log-line.ok { color: var(--accent3); }
  .log-line.warn { color: #ffaa44; }
  .log-line.error { color: var(--accent2); }

  #results-list { display: flex; flex-direction: column; gap: 6px; }
  .conj-card {
    background: #0a1520;
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent2);
    border-radius: 4px;
    padding: 8px 10px;
    cursor: pointer;
    transition: all 0.15s;
    font-size: 11px;
  }
  .conj-card:hover { border-color: var(--accent); background: #0d1f30; }
  .conj-card .sats { font-weight: 600; color: #fff; font-size: 11px; margin-bottom: 3px; }
  .conj-card .meta { color: var(--muted); font-family: 'Share Tech Mono', monospace; font-size: 9px; display: flex; gap: 10px; }
  .conj-card .dist { color: var(--accent2); font-weight: 700; }
  .conj-card .pc   { color: #ffaa44; }
  .conj-card .time { color: var(--muted); }
  #no-results { color: var(--muted); font-family: 'Share Tech Mono', monospace; font-size: 10px; text-align: center; padding: 20px 0; }

  #globe-header {
    position: absolute;
    top: 16px; left: 16px;
    background: rgba(5,10,15,0.85);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 14px;
    font-family: 'Share Tech Mono', monospace;
    font-size: 10px;
    color: var(--accent);
    letter-spacing: 2px;
    backdrop-filter: blur(8px);
    pointer-events: none;
  }
  #sat-counter {
    position: absolute;
    top: 16px; right: 16px;
    background: rgba(5,10,15,0.85);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 14px;
    font-family: 'Share Tech Mono', monospace;
    font-size: 10px;
    color: var(--text);
    backdrop-filter: blur(8px);
    pointer-events: none;
    text-align: right;
  }
  #sat-counter span { color: var(--accent); font-size: 18px; font-weight: 700; display: block; }

  #tooltip {
    position: absolute;
    background: rgba(5,10,15,0.95);
    border: 1px solid var(--accent);
    border-radius: 6px;
    padding: 12px 14px;
    font-family: 'Share Tech Mono', monospace;
    font-size: 10px;
    color: var(--text);
    pointer-events: all;
    display: none;
    max-width: 260px;
    backdrop-filter: blur(8px);
    z-index: 100;
  }
  #tooltip .tt-title { color: var(--accent2); font-size: 11px; font-weight: 700; margin-bottom: 6px; }
  #tooltip .tt-row { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 3px; }
  #tooltip .tt-key { color: var(--muted); }
  #tooltip .tt-val { color: #fff; }
  #tooltip .tt-link { color: var(--accent); text-decoration: underline; cursor: pointer;
                      font-size: 9px; margin-top: 8px; display: block; text-align: center; }

  #globe-controls {
    position: absolute;
    bottom: 32px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    gap: 8px;
    align-items: center;
    background: rgba(5,10,15,0.88);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 14px;
    backdrop-filter: blur(8px);
    z-index: 10;
  }
  .ctrl-btn {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--muted);
    font-family: 'Share Tech Mono', monospace;
    font-size: 10px;
    letter-spacing: 1px;
    padding: 5px 10px;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }
  .ctrl-btn:hover { border-color: var(--accent); color: var(--accent); }
  .ctrl-btn.active { border-color: var(--accent); color: var(--accent); background: rgba(0,212,255,0.1); }
  .ctrl-btn.active-green { border-color: var(--accent3); color: var(--accent3); background: rgba(0,255,136,0.1); }
  .ctrl-divider { width: 1px; height: 20px; background: var(--border); margin: 0 4px; }
  #speed-label { font-family: 'Share Tech Mono', monospace; font-size: 10px; color: var(--muted); }

  #risk-slider-wrap { margin: 6px 0; }
  #risk-track { position: relative; padding-bottom: 20px; }
  #risk-track input[type=range] {
    width: 100%; -webkit-appearance: none; appearance: none;
    height: 4px; border-radius: 2px; outline: none;
    background: linear-gradient(to right, #00ff88, #ffaa44, #ff4444, #cc0000);
    cursor: pointer;
  }
  #risk-track input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; width: 14px; height: 14px;
    border-radius: 50%; background: #fff;
    border: 2px solid var(--accent); cursor: pointer;
  }
  #risk-labels { display: flex; justify-content: space-between;
                 font-size: 8px; color: var(--muted);
                 font-family: 'Share Tech Mono', monospace;
                 letter-spacing: 1px; margin-top: 4px; }
  #risk-display { display: flex; justify-content: space-between;
                  align-items: center; margin-top: 4px; }
  #risk-name { font-family: 'Share Tech Mono', monospace; font-size: 11px;
               font-weight: 700; color: var(--accent3); }
  #risk-pc-val { font-family: 'Share Tech Mono', monospace; font-size: 9px; color: var(--muted); }

  #sat-modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.7); z-index: 200;
    align-items: center; justify-content: center;
    backdrop-filter: blur(4px);
  }
  #sat-modal-overlay.open { display: flex; }
  #sat-modal {
    background: #090f17; border: 1px solid var(--accent);
    border-radius: 8px; width: 520px; max-height: 80vh;
    overflow-y: auto; box-shadow: 0 0 40px rgba(0,212,255,0.2);
  }
  #sat-modal::-webkit-scrollbar { width: 4px; }
  #sat-modal::-webkit-scrollbar-thumb { background: var(--border); }
  #sat-modal-header {
    padding: 18px 20px 14px; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: flex-start;
    background: linear-gradient(135deg, #0a1929, #0d2137);
  }
  #sat-modal-header h2 { font-size: 14px; color: #fff; margin: 0; }
  #sat-modal-header .badge { font-size: 9px; color: var(--accent);
    letter-spacing: 2px; text-transform: uppercase; margin-bottom: 4px; }
  #sat-modal-close {
    background: transparent; border: 1px solid var(--border);
    border-radius: 4px; color: var(--muted); cursor: pointer;
    font-size: 14px; padding: 2px 8px; transition: all 0.15s;
  }
  #sat-modal-close:hover { border-color: var(--accent2); color: var(--accent2); }
  #sat-modal-body { padding: 16px 20px; }
  .sat-field { display: flex; justify-content: space-between;
               padding: 7px 0; border-bottom: 1px solid #0d2137;
               font-size: 11px; }
  .sat-field:last-child { border-bottom: none; }
  .sat-field .sf-key { color: var(--muted); font-family: 'Share Tech Mono', monospace;
                       font-size: 9px; letter-spacing: 1px; text-transform: uppercase; }
  .sat-field .sf-val { color: #fff; font-weight: 600; text-align: right; max-width: 60%; }
  #sat-modal-loading { text-align: center; padding: 30px;
    color: var(--muted); font-family: 'Share Tech Mono', monospace;
    font-size: 11px; letter-spacing: 2px; }
  #sat-modal-error { padding: 20px; color: var(--accent2);
    font-family: 'Share Tech Mono', monospace; font-size: 10px; text-align:center; }

  #top-pairs-list .tp-row {
    display: flex; justify-content: space-between;
    padding: 5px 0; border-bottom: 1px solid var(--border);
    font-size: 9px; font-family: 'Share Tech Mono', monospace;
  }
  #top-pairs-list .tp-row .tp-sats { color: var(--text); }
  #top-pairs-list .tp-row .tp-count { color: var(--accent); }
  #top-pairs-list .tp-row .tp-dist { color: var(--accent2); }

  /* ── MOBILE HAMBURGER BUTTON ── */
  #hamburger {
    display: none;
    position: fixed;
    top: 12px; left: 12px;
    z-index: 200;
    background: rgba(7,16,26,0.92);
    border: 1px solid var(--border);
    border-radius: 6px;
    width: 40px; height: 40px;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 5px;
    cursor: pointer;
    backdrop-filter: blur(8px);
    transition: all 0.2s;
  }
  #hamburger:hover { border-color: var(--accent); }
  #hamburger span {
    display: block;
    width: 18px; height: 2px;
    background: var(--text);
    border-radius: 1px;
    transition: all 0.25s;
  }
  #hamburger.open span:nth-child(1) { transform: translateY(7px) rotate(45deg); }
  #hamburger.open span:nth-child(2) { opacity: 0; transform: scaleX(0); }
  #hamburger.open span:nth-child(3) { transform: translateY(-7px) rotate(-45deg); }

  /* ── MOBILE SIDEBAR OVERLAY ── */
  #sidebar-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    z-index: 150;
    backdrop-filter: blur(2px);
  }

  /* ── RESPONSIVE BREAKPOINTS ── */
  @media (max-width: 768px) {
    #hamburger { display: flex; }
    #sidebar-overlay.active { display: block; }

    #sidebar {
      position: fixed;
      top: 0; left: 0;
      height: 100vh;
      z-index: 160;
      transform: translateX(-100%);
      transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      width: 300px !important;
      min-width: 300px !important;
      box-shadow: 4px 0 40px rgba(0,0,0,0.6);
    }
    #sidebar.open {
      transform: translateX(0);
    }

    /* Globe takes full screen on mobile */
    #globe-container {
      width: 100vw;
      height: 100vh;
      height: 100dvh;
    }
    #app {
      height: 100vh;
      height: 100dvh;
    }

    /* Globe overlays: reposition to avoid hamburger button */
    #globe-header {
      top: 12px;
      left: 60px;
      font-size: 8px;
      padding: 6px 10px;
    }
    #sat-counter {
      top: 12px;
      right: 12px;
      font-size: 8px;
      padding: 6px 10px;
    }
    #sat-counter span { font-size: 14px; }

    /* Globe controls: scrollable on mobile */
    #globe-controls {
      bottom: max(16px, env(safe-area-inset-bottom, 16px));
      left: 8px;
      right: 8px;
      transform: none;
      overflow-x: auto;
      border-radius: 6px;
      padding: 8px 12px;
      gap: 8px;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: none;
      z-index: 20;
      flex-wrap: nowrap;
      min-height: 44px;
    }
    #globe-controls::-webkit-scrollbar { display: none; }
    .ctrl-btn { font-size: 10px; padding: 8px 12px; white-space: nowrap; flex-shrink: 0; min-height: 36px; }
    #speed-label { font-size: 9px; flex-shrink: 0; }

    /* Tooltip: full width at bottom on mobile */
    #tooltip {
      left: 8px !important;
      right: 8px !important;
      top: auto !important;
      bottom: 80px;
      max-width: none;
    }

    /* Sat modal: full screen on mobile */
    #sat-modal-overlay { align-items: flex-end; }
    #sat-modal { width: 100%; border-radius: 12px 12px 0 0; max-height: 85vh; }

    /* Status bar: compact */
    #status-bar { padding: 6px 12px; }
    #status-text { font-size: 9px; }

    /* Log panel: shorter on mobile */
    #log-panel { height: 90px; }
  }

  @media (max-width: 480px) {
    #sidebar { width: 280px !important; min-width: 280px !important; }
    #globe-header { display: none; }
  }
</style>

<script defer src="https://cloud.umami.is/script.js" data-website-id="4e12fc04-8b26-4e42-8b69-0700a95c7d30"></script>
</head>
<body>
<div id="app">

  <!-- ── MOBILE HAMBURGER ── -->
  <button id="hamburger" onclick="toggleSidebar()" aria-label="Toggle menu">
    <span></span><span></span><span></span>
  </button>
  <div id="sidebar-overlay" onclick="toggleSidebar()"></div>

  <!-- ── SIDEBAR ── -->
  <div id="sidebar">
    <button id="sidebar-toggle-btn" onclick="toggleSidebar()" title="Toggle sidebar">◀</button>
    <div class="sidebar-collapsible" style="flex:1;display:flex;flex-direction:column;overflow:hidden;">
    <div id="header">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
        <div class="logo">VectraSpace // Mission Control</div>
        <a href="/welcome" style="font-family:'Share Tech Mono',monospace;font-size:8px;
                           letter-spacing:2px;color:var(--muted);text-decoration:none;
                           padding:3px 8px;border:1px solid var(--border);border-radius:3px;
                           text-transform:uppercase;transition:all 0.2s;"
           onmouseover="this.style.color='var(--accent)';this.style.borderColor='var(--accent)'"
           onmouseout="this.style.color='var(--muted)';this.style.borderColor='var(--border)'">
          ← Home
        </a>
      </div>
      <h1>Orbital Safety Platform</h1>
      <div class="sub">VectraSpace v11 — Public Release</div>
    </div>
    <!-- User bar: populated by /me endpoint -->
    <div id="user-bar">
      <span id="user-label">Loading...</span>
      <span id="user-actions"></span>
    </div>

    <div id="scroll">

      <!-- Demo mode banner (shown when not logged in) -->
      <div id="demo-banner" style="display:none;margin-bottom:12px;padding:8px 10px;
           background:rgba(255,170,68,0.08);border:1px solid #ffaa44;border-radius:4px;
           font-family:Share Tech Mono,monospace;font-size:9px;color:#ffaa44;letter-spacing:1px;">
        DEMO MODE — Showing latest public scan.<br>
        <a href="/login" style="color:#00d4ff;text-decoration:none;">Sign in</a> to run your own scans and set personal alerts.
      </div>

      <!-- Detection Settings -->
      <div class="section">
        <div class="section-title">Satellites per Regime</div>
        <div style="display:flex;gap:8px;">
          <div class="field" style="flex:1;margin-bottom:0">
            <label>LEO</label>
            <input type="number" id="num_leo" placeholder="100" min="1" max="1000">
          </div>
          <div class="field" style="flex:1;margin-bottom:0">
            <label>MEO</label>
            <input type="number" id="num_meo" placeholder="50" min="1" max="500">
          </div>
          <div class="field" style="flex:1;margin-bottom:0">
            <label>GEO</label>
            <input type="number" id="num_geo" placeholder="20" min="1" max="200">
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Detection Parameters</div>
        <div class="field">
          <label>Time Window (hours)</label>
          <input type="number" id="time_window" value="12" min="1" max="72">
        </div>
        <div class="field">
          <label>Collision Alert Threshold (km)</label>
          <input type="number" id="alert_km" value="10" min="0.1" step="0.1">
        </div>
        <div class="field">
          <label>Refinement Threshold (km)</label>
          <input type="number" id="refine_km" value="50" min="1">
          <div class="hint">Candidates below this get refined</div>
        </div>

        <div class="field">
          <label>Alert Risk Level</label>
          <div id="risk-slider-wrap">
            <div id="risk-track">
              <div id="risk-fill"></div>
              <input type="range" id="risk-slider" min="0" max="3" step="1" value="1"
                     oninput="updateRiskSlider(this.value)">
              <div id="risk-labels">
                <span>LOW</span><span>MODERATE</span><span>HIGH</span><span>CRITICAL</span>
              </div>
            </div>
            <div id="risk-display">
              <span id="risk-name">MODERATE</span>
              <span id="risk-pc-val">Pc ≥ 1×10⁻⁴</span>
            </div>
          </div>
          <input type="hidden" id="pc_thresh" value="0.0001">
          <div class="hint">Minimum probability of collision to trigger alert</div>
        </div>
      </div>

      <!-- Alert Settings (only shown when logged in) -->
      <div class="section" id="alert-settings-section" style="display:none;">
        <div class="section-title">Alert Settings</div>
        <div class="field">
          <label>Alert Email</label>
          <input type="email" id="alert_email" placeholder="you@example.com">
          <div class="hint">Leave blank to use saved preferences</div>
        </div>
        <div class="field">
          <label>Pushover Key</label>
          <input type="text" id="pushover_key" placeholder="Leave blank to use saved preferences">
        </div>
        <div style="text-align:right;margin-top:-4px;">
          <a href="/preferences" style="color:var(--accent);font-family:Share Tech Mono,monospace;font-size:9px;letter-spacing:1px;">⚙ Edit Saved Preferences →</a>
        </div>
      </div>

      <!-- F-07: Debris Simulation -->
      <div class="section">
        <div class="section-title">Debris Simulation</div>
        <div id="debris-form" style="display:none;">
          <div class="field">
            <label>Parent Satellite</label>
            <select id="debris_sat" style="width:100%;background:#0a1520;border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:Share Tech Mono,monospace;font-size:11px;padding:7px 10px;outline:none;">
              <option value="">— run a scan first —</option>
            </select>
          </div>
          <div style="display:flex;gap:8px;">
            <div class="field" style="flex:1;margin-bottom:0">
              <label>Event Type</label>
              <select id="debris_type" style="width:100%;background:#0a1520;border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:Share Tech Mono,monospace;font-size:11px;padding:7px 10px;outline:none;">
                <option value="COLLISION">COLLISION</option>
                <option value="EXPLOSION">EXPLOSION</option>
              </select>
            </div>
            <div class="field" style="flex:1;margin-bottom:0">
              <label>Count</label>
              <input type="number" id="debris_count" placeholder="50" min="10" max="200">
            </div>
          </div>
          <button onclick="simulateFragmentation()" style="width:100%;margin-top:10px;padding:9px;background:transparent;border:1px solid #ff6644;border-radius:4px;color:#ff6644;font-family:Share Tech Mono,monospace;font-size:11px;letter-spacing:2px;cursor:pointer;transition:all 0.2s;"
            onmouseover="this.style.background='rgba(255,102,68,0.1)'"
            onmouseout="this.style.background='transparent'">
            💥 SIMULATE FRAGMENTATION
          </button>
        </div>
        <div id="debris-locked" style="color:var(--muted);font-family:Share Tech Mono,monospace;font-size:9px;text-align:center;padding:8px 0;letter-spacing:1px;">
          Run a scan to enable
        </div>
      </div>

      <!-- Run -->
      <div class="section">
        <button id="run-btn" onclick="runDetection()" style="display:none;">▶ EXECUTE SCAN</button>
        <div id="first-run-tip" style="display:none;margin-top:8px;padding:8px 10px;
             background:rgba(0,212,255,0.06);border:1px solid rgba(0,212,255,0.2);
             border-radius:4px;font-family:'Share Tech Mono',monospace;font-size:8px;
             letter-spacing:1px;color:var(--accent);line-height:1.6;">
          👆 Click to run your first scan<br>
          <span style="color:var(--muted);">Fetches live TLEs · detects conjunctions · populates globe</span>
          <button onclick="document.getElementById('first-run-tip').style.display='none';
                           try{localStorage.setItem('vs_seen_tip','1')}catch(e){}"
                  style="display:block;margin-top:6px;background:transparent;border:none;
                         color:var(--muted);font-family:'Share Tech Mono',monospace;
                         font-size:8px;cursor:pointer;letter-spacing:1px;">
            ✕ dismiss
          </button>
        </div>
        <div id="run-locked-msg" style="display:none;">
          <a href="/login" style="color:var(--accent);text-decoration:none;">Sign in</a> to run your own scans
        </div>
      </div>

      <!-- Log -->
      <div class="section">
        <div class="section-title">Live Log</div>
        <div id="log-panel"><div class="log-line">Initializing...</div></div>
      </div>

      <!-- Results -->
      <div class="section">
        <div class="section-title">
          Conjunctions <span id="conj-count" style="color:var(--accent2)"></span>
          <button id="export-all-btn" onclick="exportAllCDMs()"
            style="float:right;background:transparent;border:1px solid var(--muted);
                   border-radius:3px;color:var(--muted);font-family:'Share Tech Mono',monospace;
                   font-size:8px;padding:2px 6px;cursor:pointer;letter-spacing:1px;display:none;">
            ⬇ ZIP ALL
          </button>
        </div>
        <div id="results-list">
          <div id="no-results">Run a scan to see results</div>
        </div>
      </div>

      <!-- Historical Trends -->
      <div class="section">
        <div class="section-title" style="cursor:pointer;user-select:none;" onclick="toggleHistory()">
          Historical Trends
          <span id="history-toggle" style="float:right;color:var(--muted);">▶</span>
        </div>
        <div id="history-panel" style="display:none;">
          <div style="margin-bottom:10px;">
            <canvas id="chart-daily" height="140"></canvas>
          </div>
          <div style="margin-bottom:10px;">
            <canvas id="chart-regimes" height="140"></canvas>
          </div>
          <div id="top-pairs-list"></div>
          <button onclick="loadHistory()"
            style="width:100%;margin-top:8px;background:transparent;border:1px solid var(--border);
                   border-radius:4px;color:var(--muted);font-family:'Share Tech Mono',monospace;
                   font-size:9px;padding:6px;cursor:pointer;letter-spacing:2px;">
            ↺ REFRESH
          </button>
        </div>
      </div>

    </div><!-- /scroll -->

    <!-- TLE Freshness indicator -->
    <div id="tle-freshness-bar" style="padding:6px 12px;border-top:1px solid var(--border);
         background:rgba(0,0,0,0.2);font-family:'Share Tech Mono',monospace;font-size:8px;
         letter-spacing:1px;display:flex;align-items:center;gap:6px;color:var(--muted);">
      <span id="tle-dot" style="width:6px;height:6px;border-radius:50%;background:var(--muted);flex-shrink:0;"></span>
      <span id="tle-text">Checking TLE status...</span>
    </div>
    <div id="status-bar">
      <div id="status-dot"></div>
      <div id="status-text">Initializing...</div>
    </div>
    </div><!-- end sidebar-collapsible -->
  </div><!-- /sidebar -->

  <!-- ── GLOBE ── -->
  <div id="globe-container">
    <!-- Cesium init overlay -->
    <div id="cesium-init-overlay" style="
        position:absolute;inset:0;z-index:50;
        background:#030508;
        display:flex;flex-direction:column;
        align-items:center;justify-content:center;gap:20px;
        pointer-events:none;">
      <div style="font-family:'Orbitron',sans-serif;font-size:11px;font-weight:700;
                  letter-spacing:4px;color:#00d4ff;text-transform:uppercase;">
        VectraSpace
      </div>
      <div style="width:220px;">
        <div style="font-family:'Share Tech Mono',monospace;font-size:9px;
                    color:#3a5a75;letter-spacing:2px;margin-bottom:8px;
                    text-transform:uppercase;" id="cesium-init-msg">
          Initializing Globe...
        </div>
        <div style="background:#0a1520;border:1px solid #0d2137;border-radius:3px;
                    height:3px;overflow:hidden;">
          <div id="cesium-init-bar" style="
              height:100%;width:0%;
              background:linear-gradient(90deg,#00d4ff,#00ff88);
              border-radius:3px;
              transition:width 0.4s ease;"></div>
        </div>
      </div>
    </div>
    <div id="cesiumContainer"></div>
    <div id="globe-header">VECTRASPACE // LIVE ORBITAL TRACKING</div>
    <div id="sat-counter">
      <span id="sat-count">—</span>
      SATELLITES
    </div>
    <div id="tooltip"></div>
    <div id="globe-controls">
      <span style="font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:1px;margin-right:4px;">ORBITS</span>
      <button class="ctrl-btn active" id="filter-all"    onclick="setOrbitFilter('all')">ALL</button>
      <button class="ctrl-btn"        id="filter-alerts" onclick="setOrbitFilter('alerts')">ALERTS ONLY</button>
      <button class="ctrl-btn"        id="filter-none"   onclick="setOrbitFilter('none')">NONE</button>
      <div class="ctrl-divider"></div>
      <button class="ctrl-btn" id="anim-btn" onclick="toggleAnimation()">▶ ANIMATE</button>
      <button class="ctrl-btn" id="speed-down" onclick="changeSpeed(-1)">−</button>
      <span id="speed-label">1×</span>
      <button class="ctrl-btn" id="speed-up" onclick="changeSpeed(1)">+</button>
      <button class="ctrl-btn" id="reset-btn" onclick="resetClock()">RESET</button>
    </div>
  </div>

</div><!-- /app -->

<!-- ── SATELLITE INFO MODAL ── -->
<div id="sat-modal-overlay" onclick="closeSatModal(event)">
  <div id="sat-modal">
    <div id="sat-modal-header">
      <div>
        <div class="badge">VECTRASPACE // SATELLITE RECORD</div>
        <h2 id="sat-modal-title">Loading...</h2>
      </div>
      <button id="sat-modal-close" onclick="closeSatModal()">✕</button>
    </div>
    <div id="sat-modal-body">
      <div id="sat-modal-loading">FETCHING SATELLITE DATA...</div>
    </div>
  </div>
</div>

<script>
// ── CESIUM INIT ──────────────────────────────────────────────

Cesium.Ion.defaultAccessToken = '__CESIUM_TOKEN__';

// ── MOBILE SIDEBAR TOGGLE ─────────────────────────────────────────────────
function toggleSidebar() {
  const sidebar  = document.getElementById('sidebar');
  const overlay  = document.getElementById('sidebar-overlay');
  const hamburger = document.getElementById('hamburger');
  const isOpen = sidebar.classList.contains('open');
  sidebar.classList.toggle('open', !isOpen);
  overlay.classList.toggle('active', !isOpen);
  hamburger.classList.toggle('open', !isOpen);
}

// Close sidebar when a result card is clicked on mobile
function closeSidebarOnMobile() {
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar.classList.contains('open')) toggleSidebar();
  }
}

let viewer;
let viewerReady = false;

let satEntities = [];
let conjEntities = [];
let conjData = [];
let alertSatNames = new Set();
let orbitFilter = 'all';
let animPlaying = false;
let animSpeeds = [1, 10, 60, 300, 600];
let animSpeedIdx = 0;
let startJulian = null;
let currentUser = null;  // {username, role} or null

const COLORS = {
  LEO: Cesium.Color.fromCssColorString('#4da6ff').withAlpha(0.9),
  MEO: Cesium.Color.fromCssColorString('#ff6b6b').withAlpha(0.9),
  GEO: Cesium.Color.fromCssColorString('#00ff88').withAlpha(0.9),
};

async function initCesium() {
  // ── Terrain: Cesium World Terrain (Ion) for photorealistic 3D elevation ──
  let terrainProvider;
  try {
    terrainProvider = await Cesium.createWorldTerrainAsync({
      requestWaterMask: false,
      requestVertexNormals: false,  // skip extra requests for faster load
    });
  } catch(e) {
    console.warn('World terrain unavailable — using ellipsoid fallback');
    terrainProvider = new Cesium.EllipsoidTerrainProvider();
  }

  viewer = new Cesium.Viewer('cesiumContainer', {
    terrainProvider: terrainProvider,
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    animation: false,
    timeline: false,
    fullscreenButton: false,
    infoBox: false,
    selectionIndicator: false,
    skyBox: new Cesium.SkyBox({
      sources: {
        positiveX: 'https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Assets/Textures/SkyBox/tycho2t3_80_px.jpg',
        negativeX: 'https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Assets/Textures/SkyBox/tycho2t3_80_mx.jpg',
        positiveY: 'https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Assets/Textures/SkyBox/tycho2t3_80_py.jpg',
        negativeY: 'https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Assets/Textures/SkyBox/tycho2t3_80_my.jpg',
        positiveZ: 'https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Assets/Textures/SkyBox/tycho2t3_80_pz.jpg',
        negativeZ: 'https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Assets/Textures/SkyBox/tycho2t3_80_mz.jpg',
      }
    }),
    contextOptions: { requestWebgl2: true },
    shadows: false,
    orderIndependentTranslucency: false,
  });

  // ── Imagery: Bing-style aerial from Ion (photorealistic, fast) ────────────
  viewer.imageryLayers.removeAll();
  try {
    const aerial = await Cesium.createWorldImageryAsync({
      style: Cesium.IonWorldImageryStyle.AERIAL
    });
    viewer.imageryLayers.add(new Cesium.ImageryLayer(aerial));
  } catch(e) {
    console.warn('World imagery unavailable — using OSM fallback');
    viewer.imageryLayers.add(new Cesium.ImageryLayer(
      new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/', maximumLevel: 18 })
    ));
  }

  // ── Scene settings: photorealistic but load-optimised ────────────────────
  viewer.scene.globe.enableLighting = true;
  viewer.scene.globe.atmosphereLightIntensity = 12.0;
  viewer.scene.globe.showGroundAtmosphere = true;
  viewer.scene.globe.depthTestAgainstTerrain = false;
  viewer.scene.globe.maximumScreenSpaceError = 4;        // fewer tiles = faster
  viewer.scene.globe.tileCacheSize = 100;
  viewer.scene.atmosphere.brightnessShift = 0.1;
  viewer.scene.fog.enabled = true;
  viewer.scene.fog.density = 0.0002;
  viewer.scene.skyAtmosphere.show = true;
  viewer.scene.sun = new Cesium.Sun();
  viewer.scene.moon = new Cesium.Moon();
  viewer.scene.highDynamicRange = false;                 // skip HDR pass
  viewer.scene.globe.translucency.enabled = false;
  viewer.clock.currentTime = Cesium.JulianDate.now();
  viewer.clock.shouldAnimate = false;

  viewer.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(0, 20, 25000000),
    orientation: { heading: 0, pitch: -Cesium.Math.PI_OVER_TWO, roll: 0 }
  });

  // Tooltip setup
  const tooltip = document.getElementById('tooltip');
  let tooltipHovered = false;
  let tooltipHideTimer = null;

  function showTooltip(x, y) {
    tooltip.style.display = 'block';
    tooltip.style.left = (x + 16) + 'px';
    tooltip.style.top  = (y - 10) + 'px';
  }
  function hideTooltipNow() {
    tooltip.style.display = 'none';
    tooltipHovered = false;
  }

  tooltip.addEventListener('mouseenter', () => {
    tooltipHovered = true;
    if (tooltipHideTimer) { clearTimeout(tooltipHideTimer); tooltipHideTimer = null; }
  });
  tooltip.addEventListener('mouseleave', () => {
    tooltipHovered = false;
    tooltipHideTimer = setTimeout(hideTooltipNow, 150);
  });
  tooltip.addEventListener('click', (e) => {
    const link = e.target.closest('[data-satname]');
    if (link) { e.stopPropagation(); openSatInfo(link.dataset.satname); }
  });

  viewer.screenSpaceEventHandler.setInputAction(movement => {
    const picked = viewer.scene.pick(movement.endPosition);
    if (Cesium.defined(picked) && Cesium.defined(picked.id)) {
      const entity = picked.id;
      try {
        const data = JSON.parse(entity.description.getValue());
        if (data.type === 'conjunction') {
          const c = conjData[data.idx];
          const h = Math.floor(c.time_min / 60);
          const m = Math.floor(c.time_min % 60);
          tooltip.innerHTML = `
            <div class="tt-title">⚠ CONJUNCTION EVENT</div>
            <div class="tt-row"><span class="tt-key">SAT 1</span><span class="tt-val">${c.sat1}</span></div>
            <div class="tt-row"><span class="tt-key">SAT 2</span><span class="tt-val">${c.sat2}</span></div>
            <div class="tt-row"><span class="tt-key">REGIMES</span><span class="tt-val">${c.regime1} / ${c.regime2}</span></div>
            <div class="tt-row"><span class="tt-key">MISS DIST</span><span class="tt-val" style="color:#ff4444">${c.min_dist_km.toFixed(3)} km</span></div>
            <div class="tt-row"><span class="tt-key">Pc</span><span class="tt-val" style="color:#ffaa44">${c.pc_estimate.toExponential(2)}</span></div>
            <div class="tt-row"><span class="tt-key">TIME TO CA</span><span class="tt-val">+${h}h ${m.toString().padStart(2,'0')}m</span></div>
            <a class="tt-link" data-satname="${c.sat1}">🛰 ${c.sat1} — View Info</a>
            <a class="tt-link" data-satname="${c.sat2}">🛰 ${c.sat2} — View Info</a>
          `;
        } else {
          tooltip.innerHTML = `
            <div class="tt-title">${data.name}</div>
            <div class="tt-row"><span class="tt-key">REGIME</span><span class="tt-val">${data.regime}</span></div>
            <a class="tt-link" data-satname="${data.name}">🛰 View Satellite Info</a>
          `;
        }
        showTooltip(movement.endPosition.x, movement.endPosition.y);
      } catch(e) { hideTooltipNow(); }
    } else {
      if (!tooltipHovered) {
        tooltipHideTimer = setTimeout(hideTooltipNow, 150);
      }
    }
  }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);

  viewer.screenSpaceEventHandler.setInputAction(click => {
    const picked = viewer.scene.pick(click.position);
    if (Cesium.defined(picked) && Cesium.defined(picked.id)) {
      try {
        const data = JSON.parse(picked.id.description.getValue());
        if (data.type === 'conjunction') {
          const c = conjData[data.idx];
          viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(
              c.midpoint[0], c.midpoint[1], c.midpoint[2] + 3000000
            ),
            duration: 2.0,
          });
          document.querySelectorAll('.conj-card').forEach((el,i) => {
            el.style.borderLeftColor = i === data.idx ? 'var(--accent)' : 'var(--accent2)';
          });
        }
      } catch(e) {}
    }
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

  viewerReady = true;
  console.log('Cesium viewer ready');
  // Complete + dismiss the init overlay
  const _bar = document.getElementById('cesium-init-bar');
  const _msg = document.getElementById('cesium-init-msg');
  const _overlay = document.getElementById('cesium-init-overlay');
  if (window._cesiumInitInterval) clearInterval(window._cesiumInitInterval);
  if (_bar) _bar.style.width = '100%';
  if (_msg) _msg.textContent = 'Ready';
  if (_overlay) {
    setTimeout(() => {
      _overlay.style.transition = 'opacity 0.6s ease';
      _overlay.style.opacity = '0';
      setTimeout(() => { _overlay.style.display = 'none'; }, 650);
    }, 300);
  }
}

// ── CESIUM INIT PROGRESS ─────────────────────────────────────────────────────
(function() {
  const bar = document.getElementById('cesium-init-bar');
  const msg = document.getElementById('cesium-init-msg');
  if (!bar) return;
  const steps = [
    [10, 'Loading terrain...'],
    [30, 'Connecting to Ion...'],
    [55, 'Fetching imagery...'],
    [75, 'Building scene...'],
    [90, 'Almost ready...'],
  ];
  let i = 0;
  const interval = setInterval(() => {
    if (i < steps.length) {
      bar.style.width = steps[i][0] + '%';
      msg.textContent = steps[i][1];
      i++;
    } else {
      clearInterval(interval);
    }
  }, 600);
  // Store cleanup ref for when Cesium is ready
  window._cesiumInitInterval = interval;
  window._cesiumInitBar = bar;
  window._cesiumInitMsg = msg;
})();

initCesium();

// ── CHECK CURRENT USER ────────────────────────────────────────
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const btn = document.getElementById('sidebar-toggle-btn');
  const collapsed = sidebar.classList.toggle('collapsed');
  btn.textContent = collapsed ? '▶' : '◀';
  btn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
  try { localStorage.setItem('vs_sidebar_collapsed', collapsed ? '1' : '0'); } catch(e) {}
}

function initSidebarState() {
  try {
    if (localStorage.getItem('vs_sidebar_collapsed') === '1') {
      const sidebar = document.getElementById('sidebar');
      const btn = document.getElementById('sidebar-toggle-btn');
      sidebar.classList.add('collapsed');
      btn.textContent = '▶';
    }
  } catch(e) {}
}

async function updateTLEStatus() {
  try {
    const res = await fetch('/tle-status');
    if (!res.ok) return;
    const d = await res.json();
    const dot  = document.getElementById('tle-dot');
    const text = document.getElementById('tle-text');
    if (!dot || !text) return;
    dot.style.background  = d.fresh ? '#00ff88' : '#ffaa44';
    text.style.color      = d.fresh ? '#4a8a65' : '#aa6600';
    text.textContent      = d.message || 'TLE unknown';
  } catch(e) {}
}

function maybeShowFirstRunTip() {
  try {
    if (localStorage.getItem('vs_seen_tip') === '1') return;
    const tip = document.getElementById('first-run-tip');
    if (tip) tip.style.display = 'block';
  } catch(e) {}
}

async function initUserState() {
  try {
    const res = await fetch('/me');
    if (res.status === 401) {
      currentUser = null;
    } else {
      currentUser = await res.json();
    }
  } catch(e) {
    currentUser = null;
  }

  const userLabel = document.getElementById('user-label');
  const userActions = document.getElementById('user-actions');
  const runBtn = document.getElementById('run-btn');
  const runLocked = document.getElementById('run-locked-msg');
  const alertSettings = document.getElementById('alert-settings-section');
  const demoBanner = document.getElementById('demo-banner');

  if (currentUser && currentUser.username) {
    userLabel.innerHTML = `Logged in as <span class="user-name">${currentUser.username}</span>`;
    const adminLnk = currentUser.role === 'admin' ? ' &nbsp; <a href="/admin" style="color:#ff6b6b;">⬡ Admin</a>' : '';
    userActions.innerHTML = '<a href="/preferences">⚙ Prefs</a>' + adminLnk + ' &nbsp; <a href="/logout">Sign out</a>';
    runBtn.style.display = 'block';
    maybeShowFirstRunTip();
    runLocked.style.display = 'none';
    alertSettings.style.display = 'block';
    demoBanner.style.display = 'none';
    setStatus('Ready — authenticated as ' + currentUser.username, 'ready');
    // Load demo/public results for now
    loadDemoResults();
  } else {
    userLabel.textContent = 'Demo Mode';
    userActions.innerHTML = '<a href="/login">Sign In</a>';
    runBtn.style.display = 'none';
    runLocked.style.display = 'block';
    alertSettings.style.display = 'none';
    demoBanner.style.display = 'block';
    setStatus('Demo mode — showing latest public scan', 'ready');
    loadDemoResults();
  }
}

async function loadDemoResults() {
  try {
    const res = await fetch('/demo-results');
    if (!res.ok) { addLog('No public scan data available yet', 'warn'); return; }
    const data = await res.json();
    if (data.tracks && data.tracks.length > 0) {
      alertSatNames = new Set();
      (data.conjunctions || []).forEach(c => {
        alertSatNames.add(c.sat1);
        alertSatNames.add(c.sat2);
      });
      plotSatellites(data.tracks);
      plotConjunctions(data.conjunctions || []);
      renderResults(data.conjunctions || []);
      if (typeof populateDebrisSatList === 'function' && data.tracks.length) {
        populateDebrisSatList(data.tracks.map(t => t.name));
      }
      addLog(`Demo: ${data.tracks.length} satellites, ${(data.conjunctions||[]).length} conjunction(s)`, 'ok');
    } else {
      addLog('No public scan data yet — run a scan to populate', 'warn');
    }
  } catch(e) {
    addLog('Demo data unavailable', 'warn');
  }
}

initSidebarState();
initUserState();
updateTLEStatus();
setInterval(updateTLEStatus, 5 * 60 * 1000);

// ── PLOT SATELLITES ──────────────────────────────────────────
function plotSatellites(tracks) {
  satEntities.forEach(e => {
    if (e.dot) viewer.entities.remove(e.dot);
    if (e.trail) viewer.entities.remove(e.trail);
  });
  satEntities = [];

  const now = Cesium.JulianDate.now();
  startJulian = now.clone();
  const end = Cesium.JulianDate.addSeconds(now, (tracks[0]?.positions.length || 120) * 60, new Cesium.JulianDate());
  viewer.clock.startTime = now.clone();
  viewer.clock.stopTime = end.clone();
  viewer.clock.currentTime = now.clone();
  viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP;
  viewer.clock.multiplier = animSpeeds[animSpeedIdx];
  viewer.clock.shouldAnimate = false;

  tracks.forEach(track => {
    const color = COLORS[track.regime] || Cesium.Color.WHITE;
    const isAlert = alertSatNames.has(track.name);

    const sampledPos = new Cesium.SampledPositionProperty();
    sampledPos.interpolationDegree = 2;
    sampledPos.interpolationAlgorithm = Cesium.HermitePolynomialApproximation;

    const cartPositions = [];
    track.positions.forEach((p, i) => {
      const cart = Cesium.Cartesian3.fromDegrees(p[0], p[1], p[2]);
      cartPositions.push(cart);
      const t = Cesium.JulianDate.addSeconds(now, i * 60, new Cesium.JulianDate());
      sampledPos.addSample(t, cart);
    });

    if (cartPositions.length === 0) return;

    const dot = viewer.entities.add({
      position: sampledPos,
      point: {
        pixelSize: isAlert ? 7 : (track.regime === 'GEO' ? 5 : 3),
        color: isAlert ? Cesium.Color.YELLOW : color,
        outlineColor: isAlert ? Cesium.Color.RED : Cesium.Color.BLACK.withAlpha(0.5),
        outlineWidth: isAlert ? 2 : 1,
        scaleByDistance: new Cesium.NearFarScalar(1e6, 2.0, 5e7, 0.5),
      },
      label: {
        text: track.name,
        font: '9px Share Tech Mono',
        fillColor: isAlert ? Cesium.Color.YELLOW : color,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        pixelOffset: new Cesium.Cartesian2(8, 0),
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 8e6),
        show: isAlert,
      },
      description: JSON.stringify({ name: track.name, regime: track.regime }),
    });

    const trail = cartPositions.length > 1 ? viewer.entities.add({
      polyline: {
        positions: cartPositions,
        width: isAlert ? 2.0 : 1.2,
        material: new Cesium.ColorMaterialProperty(
          isAlert ? Cesium.Color.YELLOW.withAlpha(0.7) : color.withAlpha(0.5)
        ),
        arcType: Cesium.ArcType.NONE,
      }
    }) : null;

    satEntities.push({ dot, trail, name: track.name, isAlert });
  });

  document.getElementById('sat-count').textContent = tracks.length;
  applyOrbitFilter();
}

function plotConjunctions(conjunctions) {
  conjEntities.forEach(e => viewer.entities.remove(e));
  conjEntities = [];
  conjData = conjunctions;

  conjunctions.forEach((c, idx) => {
    const pos = Cesium.Cartesian3.fromDegrees(c.midpoint[0], c.midpoint[1], c.midpoint[2]);
    const entity = viewer.entities.add({
      position: pos,
      point: {
        pixelSize: 14,
        color: Cesium.Color.RED.withAlpha(0.85),
        outlineColor: Cesium.Color.YELLOW,
        outlineWidth: 2,
        scaleByDistance: new Cesium.NearFarScalar(1e6, 2.5, 5e7, 1.0),
      },
      label: {
        text: `⚠ ${c.min_dist_km.toFixed(1)}km`,
        font: 'bold 11px Exo 2',
        fillColor: Cesium.Color.YELLOW,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        pixelOffset: new Cesium.Cartesian2(0, -20),
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 3e7),
      },
      description: JSON.stringify({type:'conjunction', idx}),
    });
    conjEntities.push(entity);
  });
}

function setOrbitFilter(mode) {
  orbitFilter = mode;
  ['all','alerts','none'].forEach(m => {
    document.getElementById('filter-'+m).className =
      'ctrl-btn' + (m === mode ? ' active' : '');
  });
  applyOrbitFilter();
}

function applyOrbitFilter() {
  satEntities.forEach(e => {
    let showDot = true;
    let showTrail = true;
    if (orbitFilter === 'none') { showTrail = false; }
    else if (orbitFilter === 'alerts') { showTrail = e.isAlert; showDot = e.isAlert; }
    if (e.dot) e.dot.show = showDot;
    if (e.trail) e.trail.show = showTrail;
  });
}

function toggleAnimation() {
  animPlaying = !animPlaying;
  viewer.clock.shouldAnimate = animPlaying;
  const btn = document.getElementById('anim-btn');
  btn.textContent = animPlaying ? '⏸ PAUSE' : '▶ ANIMATE';
  btn.className = animPlaying ? 'ctrl-btn active-green' : 'ctrl-btn';
}

function changeSpeed(dir) {
  animSpeedIdx = Math.max(0, Math.min(animSpeeds.length - 1, animSpeedIdx + dir));
  const s = animSpeeds[animSpeedIdx];
  viewer.clock.multiplier = s;
  document.getElementById('speed-label').textContent = s < 60 ? s+'×' : s >= 600 ? '600×' : (s/60).toFixed(0)+'m/s';
}

function resetClock() {
  if (startJulian) {
    viewer.clock.currentTime = startJulian.clone();
    viewer.clock.shouldAnimate = false;
    animPlaying = false;
    document.getElementById('anim-btn').textContent = '▶ ANIMATE';
    document.getElementById('anim-btn').className = 'ctrl-btn';
  }
}

const logPanel = document.getElementById('log-panel');
function addLog(text, type='info') {
  const line = document.createElement('div');
  line.className = 'log-line ' + type;
  const time = new Date().toTimeString().slice(0,8);
  line.textContent = `[${time}] ${text}`;
  logPanel.appendChild(line);
  logPanel.scrollTop = logPanel.scrollHeight;
}
function clearLog() { logPanel.innerHTML = ''; }

function setStatus(text, state='ready') {
  document.getElementById('status-text').textContent = text;
  const dot = document.getElementById('status-dot');
  dot.className = state;
}

function renderResults(conjunctions) {
  const list = document.getElementById('results-list');
  const count = document.getElementById('conj-count');
  const exportBtn = document.getElementById('export-all-btn');
  count.textContent = conjunctions.length ? `(${conjunctions.length})` : '';

  if (!conjunctions.length) {
    list.innerHTML = '<div id="no-results" style="color:var(--accent3);font-family:Share Tech Mono,monospace;font-size:10px;text-align:center;padding:20px 0">✓ No conjunctions detected</div>';
    exportBtn.style.display = 'none';
    return;
  }

  exportBtn.style.display = 'inline-block';

  list.innerHTML = conjunctions.map((c, idx) => {
    const h = Math.floor(c.time_min / 60);
    const m = Math.floor(c.time_min % 60);
    const pcColor = c.pc_estimate >= 1e-3 ? '#ff4444' : c.pc_estimate >= 1e-5 ? '#ffaa44' : '#4a6a85';
    const covBadge = c.covariance_source === 'measured'
      ? '<span style="color:#00ff88;font-size:8px;margin-left:6px;font-family:Share Tech Mono,monospace;">COV:REAL</span>' : '';
    const debrisBadge = c.debris
      ? '<span style="color:#ff6644;font-size:8px;margin-left:4px;font-family:Share Tech Mono,monospace;">DEBRIS</span>' : '';
    let maneuverHTML = '';
    if (c.maneuver && c.maneuver.feasible && c.maneuver.delta_v_magnitude != null) {
      const dv = Number(c.maneuver.delta_v_magnitude).toFixed(2);
      const rtn = c.maneuver.delta_v_rtn || [0,0,0];
      maneuverHTML = `
        <div style="margin-top:6px;padding:5px 7px;background:#0a1015;border-left:2px solid #ffaa44;border-radius:2px;font-family:'Share Tech Mono',monospace;">
          <div style="font-size:8px;color:#ffaa44;letter-spacing:1px;margin-bottom:2px;">Δv MANEUVER <span style="color:#4a6a85;">(CW-LINEAR)</span></div>
          <div style="font-size:10px;color:#ffaa44;">${dv} m/s</div>
          <div style="font-size:8px;color:#4a6a85;">R:${Number(rtn[0]).toFixed(3)} T:${Number(rtn[1]).toFixed(3)} N:${Number(rtn[2]).toFixed(3)}</div>
          <div style="font-size:7px;color:#4a6a85;margin-top:2px;font-style:italic;">${c.maneuver.advisory_note || ''}</div>
        </div>`;
    } else if (c.maneuver && !c.maneuver.feasible) {
      maneuverHTML = `<div style="margin-top:5px;font-size:8px;color:#ff4444;font-family:'Share Tech Mono',monospace;">Δv: ${c.maneuver.advisory_note || 'Infeasible'}</div>`;
    }
    return `
      <div class="conj-card" onclick="flyToConjunction(${idx})">
        <div class="sats">${c.sat1} ↔ ${c.sat2}${covBadge}${debrisBadge}</div>
        <div class="meta">
          <span class="dist">${c.min_dist_km.toFixed(2)} km</span>
          <span class="pc" style="color:${pcColor}">Pc ${c.pc_estimate.toExponential(1)}</span>
          <span class="time">+${h}h${m.toString().padStart(2,'0')}m</span>
        </div>
        ${maneuverHTML}
        <div style="margin-top:6px;display:flex;gap:4px;">
          <button onclick="event.stopPropagation();downloadCDM(${idx})"
            style="flex:1;background:transparent;border:1px solid var(--border);border-radius:3px;
                   color:var(--muted);font-family:'Share Tech Mono',monospace;font-size:8px;
                   padding:3px;cursor:pointer;letter-spacing:1px;transition:all 0.15s;"
            onmouseover="this.style.borderColor='var(--accent)';this.style.color='var(--accent)'"
            onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--muted)'">
            ⬇ CDM
          </button>
          <button data-satname="${c.sat1}" onclick="event.stopPropagation();openSatInfo(this.dataset.satname)"
            style="flex:1;background:transparent;border:1px solid var(--border);border-radius:3px;
                   color:var(--muted);font-family:'Share Tech Mono',monospace;font-size:8px;
                   padding:3px;cursor:pointer;letter-spacing:1px;transition:all 0.15s;"
            onmouseover="this.style.borderColor='var(--accent)';this.style.color='var(--accent)'"
            onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--muted)'">
            ℹ ${c.sat1.length > 12 ? c.sat1.slice(0,12)+'…' : c.sat1}
          </button>
          <button data-satname="${c.sat2}" onclick="event.stopPropagation();openSatInfo(this.dataset.satname)"
            style="flex:1;background:transparent;border:1px solid var(--border);border-radius:3px;
                   color:var(--muted);font-family:'Share Tech Mono',monospace;font-size:8px;
                   padding:3px;cursor:pointer;letter-spacing:1px;transition:all 0.15s;"
            onmouseover="this.style.borderColor='var(--accent)';this.style.color='var(--accent)'"
            onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--muted)'">
            ℹ ${c.sat2.length > 12 ? c.sat2.slice(0,12)+'…' : c.sat2}
          </button>
        </div>
      </div>`;
  }).join('');
}

function flyToConjunction(idx) {
  const c = conjData[idx];
  if (!c) return;
  closeSidebarOnMobile();
  viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(c.midpoint[0], c.midpoint[1], c.midpoint[2] + 3000000),
    duration: 2.0,
  });
}

const RISK_LEVELS = [
  { name: 'LOW',      pc: 1e-6,  label: 'Pc ≥ 1×10⁻⁶', color: '#00ff88' },
  { name: 'MODERATE', pc: 1e-4,  label: 'Pc ≥ 1×10⁻⁴', color: '#ffaa44' },
  { name: 'HIGH',     pc: 1e-3,  label: 'Pc ≥ 1×10⁻³', color: '#ff6644' },
  { name: 'CRITICAL', pc: 1e-2,  label: 'Pc ≥ 1×10⁻²', color: '#ff4444' },
];
function updateRiskSlider(val) {
  const r = RISK_LEVELS[parseInt(val)];
  document.getElementById('risk-name').textContent = r.name;
  document.getElementById('risk-name').style.color = r.color;
  document.getElementById('risk-pc-val').textContent = r.label;
  document.getElementById('pc_thresh').value = r.pc;
}
updateRiskSlider(1);

// ── F-07: Debris simulation ───────────────────────────────────
let debrisEntities = [];

function populateDebrisSatList(trackNames) {
  const sel = document.getElementById('debris_sat');
  sel.innerHTML = trackNames.map(n => `<option value="${n}">${n}</option>`).join('');
  document.getElementById('debris-form').style.display = 'block';
  document.getElementById('debris-locked').style.display = 'none';
}

async function simulateFragmentation() {
  const satName = document.getElementById('debris_sat').value;
  const eventType = document.getElementById('debris_type').value;
  const count = Math.min(200, parseInt(document.getElementById('debris_count').value) || 50);
  if (!satName) { addLog('Select a parent satellite first', 'warn'); return; }

  addLog(`Simulating ${eventType} of ${satName} — ${count} fragments...`, 'info');
  try {
    const res = await fetch(`/debris/simulate?sat_name=${encodeURIComponent(satName)}&event_type=${eventType}&n_debris=${count}`);
    const data = await res.json();
    if (data.error) { addLog('Fragmentation error: ' + data.error, 'error'); return; }

    debrisEntities.forEach(e => { if(e.dot) viewer.entities.remove(e.dot); if(e.trail) viewer.entities.remove(e.trail); });
    debrisEntities = [];

    const debColor = Cesium.Color.WHITE.withAlpha(0.8);
    const now = Cesium.JulianDate.now();

    data.debris_tracks.forEach(track => {
      const cartPositions = track.positions.map(p => Cesium.Cartesian3.fromDegrees(p[0], p[1], p[2]));
      if (cartPositions.length === 0) return;

      const sampledPos = new Cesium.SampledPositionProperty();
      sampledPos.interpolationDegree = 1;
      sampledPos.interpolationAlgorithm = Cesium.HermitePolynomialApproximation;
      cartPositions.forEach((cart, i) => {
        const t = Cesium.JulianDate.addSeconds(now, i * 60, new Cesium.JulianDate());
        sampledPos.addSample(t, cart);
      });

      const dot = viewer.entities.add({
        position: sampledPos,
        point: { pixelSize: 2, color: debColor, outlineWidth: 0,
                 scaleByDistance: new Cesium.NearFarScalar(1e6, 1.5, 5e7, 0.3) },
        description: JSON.stringify({ name: track.name, regime: track.regime }),
      });
      const trail = cartPositions.length > 1 ? viewer.entities.add({
        polyline: {
          positions: cartPositions,
          width: 0.8,
          material: new Cesium.ColorMaterialProperty(Cesium.Color.WHITE.withAlpha(0.25)),
          arcType: Cesium.ArcType.NONE,
        }
      }) : null;
      debrisEntities.push({ dot, trail, name: track.name, isAlert: false });
    });

    document.getElementById('sat-count').textContent = satEntities.length + debrisEntities.length;
    addLog(`${data.debris_tracks.length} debris entities added to globe`, 'ok');

    if (data.conjunctions && data.conjunctions.length > 0) {
      addLog(`${data.conjunctions.length} debris-conjunction(s) detected`, 'warn');
      renderResults([...conjData, ...data.conjunctions]);
    }
  } catch(e) {
    addLog('Fragmentation simulation failed: ' + e.message, 'error');
  }
}

// ── CDM DOWNLOAD ─────────────────────────────────────────────
function downloadCDM(idx) { window.open(`/cdm/${idx}`, '_blank'); }
function exportAllCDMs() { window.open('/cdm/zip/all', '_blank'); }

// ── SATELLITE INFO MODAL — SEC-01: server-side via /sat-info/ ─
async function openSatInfo(satName) {
  const overlay = document.getElementById('sat-modal-overlay');
  const title   = document.getElementById('sat-modal-title');
  const body    = document.getElementById('sat-modal-body');

  title.textContent = satName;
  body.innerHTML = '<div id="sat-modal-loading">FETCHING SATELLITE DATA...</div>';
  overlay.classList.add('open');

  try {
    // SEC-01: All Anthropic API calls happen server-side via /sat-info/{name}
    const res = await fetch(`/sat-info/${encodeURIComponent(satName)}`);
    if (!res.ok) {
      throw new Error(`Server returned ${res.status}`);
    }
    const info = await res.json();

    if (info.error) {
      body.innerHTML = `
        <div style="padding:16px 0;">
          <div class="sat-field"><span class="sf-key">Name</span><span class="sf-val">${satName}</span></div>
          <div style="margin-top:16px;text-align:center;">
            <a href="https://celestrak.org/satcat/records.php?NAME=${encodeURIComponent(satName)}"
               target="_blank"
               style="color:var(--accent);font-family:'Share Tech Mono',monospace;font-size:10px;
                      border:1px solid var(--accent);padding:8px 16px;border-radius:4px;
                      text-decoration:none;display:inline-block;">
              ↗ VIEW FULL RECORD ON CELESTRAK
            </a>
          </div>
        </div>`;
      return;
    }

    // Mission type badge colour
    const missionColors = {
      'Communications': '#00d4ff', 'Earth Observation': '#00ff88',
      'Navigation': '#ffaa44', 'Scientific': '#aa88ff',
      'Military': '#ff4444', 'Weather': '#44aaff',
      'Technology Demo': '#ffdd44', 'Human Spaceflight': '#ff88aa',
      'Space Station': '#ff88aa', 'Debris': '#888888', 'Unknown': '#4a6a85',
    };
    const mType = info.missionType || 'Unknown';
    const mColor = missionColors[mType] || '#4a6a85';
    const missionBadge = `<span style="background:${mColor}22;color:${mColor};
      border:1px solid ${mColor}55;border-radius:3px;padding:2px 8px;
      font-size:9px;letter-spacing:1px;text-transform:uppercase;">${mType}</span>`;

    const fields = [
      ['Full Name',       info.fullName],
      ['NORAD ID',        info.noradId],
      ['Country',         info.country || info.owner],
      ['Mission Type',    missionBadge],
      ['Object Class',    info.objectType],
      ['Launch Date',     info.launchDate],
      ['Launch Site',     info.launchSite],
      ['Orbit Type',      info.orbitType],
      ['Period',          info.periodMin ? `${info.periodMin} min` : null],
      ['Inclination',     info.inclinationDeg ? `${info.inclinationDeg}°` : null],
      ['Apogee',          info.apogeeKm ? `${info.apogeeKm} km` : null],
      ['Perigee',         info.perigeeKm ? `${info.perigeeKm} km` : null],
      ['RCS Size',        info.rcsSize],
      ['Status',          info.operationalStatus],
    ];

    body.innerHTML = fields.filter(([,v]) => v && v !== 'Unknown' && v !== null).map(([k, v]) =>
      `<div class="sat-field"><span class="sf-key">${k}</span><span class="sf-val">${v}</span></div>`
    ).join('') + `
      <div style="margin-top:16px;text-align:center;">
        <a href="https://celestrak.org/satcat/records.php?NAME=${encodeURIComponent(satName)}"
           target="_blank"
           style="color:var(--accent);font-family:'Share Tech Mono',monospace;font-size:10px;
                  border:1px solid var(--accent);padding:8px 16px;border-radius:4px;
                  text-decoration:none;display:inline-block;">
          ↗ VIEW FULL RECORD ON CELESTRAK
        </a>
      </div>`;
  } catch(e) {
    body.innerHTML = `<div id="sat-modal-error">Could not load satellite data.<br><br>
      <a href="https://celestrak.org/satcat/records.php?NAME=${encodeURIComponent(satName)}"
         target="_blank" style="color:var(--accent);">↗ Open on CelesTrak directly</a></div>`;
  }
}

function closeSatModal(e) {
  if (!e || e.target === document.getElementById('sat-modal-overlay')) {
    document.getElementById('sat-modal-overlay').classList.remove('open');
  }
}

// ── HISTORICAL TRENDS ─────────────────────────────────────────
let historyOpen = false;
let chartDaily = null;
let chartRegimes = null;

function toggleHistory() {
  historyOpen = !historyOpen;
  document.getElementById('history-panel').style.display = historyOpen ? 'block' : 'none';
  document.getElementById('history-toggle').textContent = historyOpen ? '▼' : '▶';
  if (historyOpen) loadHistory();
}

async function loadHistory() {
  try {
    const res = await fetch('/history');
    if (res.status === 401) { addLog('Sign in to view history', 'warn'); return; }
    const data = await res.json();

    const dailyCtx = document.getElementById('chart-daily').getContext('2d');
    if (chartDaily) chartDaily.destroy();
    chartDaily = new Chart(dailyCtx, {
      type: 'line',
      data: {
        labels: data.daily.map(d => d.day).reverse(),
        datasets: [{
          label: 'Conjunctions / Day',
          data: data.daily.map(d => d.count).reverse(),
          borderColor: '#00d4ff',
          backgroundColor: 'rgba(0,212,255,0.1)',
          borderWidth: 1.5,
          pointRadius: 3,
          pointBackgroundColor: '#00d4ff',
          tension: 0.3,
          fill: true,
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#4a6a85', font: { family: 'Share Tech Mono', size: 9 } } } },
        scales: {
          x: { ticks: { color: '#4a6a85', font: { size: 8 } }, grid: { color: '#0d2137' } },
          y: { ticks: { color: '#4a6a85', font: { size: 8 } }, grid: { color: '#0d2137' } }
        }
      }
    });

    const regCtx = document.getElementById('chart-regimes').getContext('2d');
    if (chartRegimes) chartRegimes.destroy();
    chartRegimes = new Chart(regCtx, {
      type: 'doughnut',
      data: {
        labels: data.regimes.map(r => r.pair),
        datasets: [{
          data: data.regimes.map(r => r.count),
          backgroundColor: ['#4da6ff','#ff6b6b','#00ff88','#ffaa44','#aa44ff','#00d4ff'],
          borderColor: '#090f17',
          borderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { position: 'bottom', labels: { color: '#4a6a85', font: { family: 'Share Tech Mono', size: 8 }, boxWidth: 10 } } }
      }
    });

    const pairDiv = document.getElementById('top-pairs-list');
    if (data.top_pairs.length) {
      pairDiv.innerHTML = '<div style="font-size:9px;color:var(--accent);letter-spacing:2px;margin:10px 0 6px;font-family:Share Tech Mono,monospace;">TOP RECURRING PAIRS</div>' +
        data.top_pairs.map(p => `
          <div class="tp-row">
            <span class="tp-sats">${p.sat1.slice(0,10)} ↔ ${p.sat2.slice(0,10)}</span>
            <span class="tp-count">${p.count}×</span>
            <span class="tp-dist">${p.closest.toFixed(1)}km</span>
          </div>`).join('');
    } else {
      pairDiv.innerHTML = '<div style="color:var(--muted);font-size:9px;text-align:center;padding:8px;font-family:Share Tech Mono,monospace;">No history yet</div>';
    }
  } catch(e) {
    console.error('History load failed:', e);
  }
}

// ── RUN DETECTION ─────────────────────────────────────────────
async function runDetection() {
  if (!currentUser) {
    addLog('Not authenticated — please sign in', 'error');
    return;
  }

  const btn = document.getElementById('run-btn');
  btn.disabled = true;
  btn.className = 'running';
  btn.textContent = '⟳ SCANNING...';
  clearLog();
  setStatus('Initializing scan...', 'running');

  // Progress bar
  let pb = document.getElementById('vs-pb');
  let pbWrap = document.getElementById('vs-pb-wrap');
  let pbLbl = document.getElementById('vs-pb-lbl');
  if (!pb) {
    const w = document.createElement('div');
    w.id = 'vs-pb-wrap';
    w.style.cssText = 'margin:8px 0 4px;background:#0a1520;border:1px solid #0d2137;border-radius:4px;height:6px;overflow:hidden;';
    const b = document.createElement('div');
    b.id = 'vs-pb';
    b.style.cssText = 'height:100%;width:0%;background:linear-gradient(90deg,#00d4ff,#00ff88);border-radius:4px;transition:width 0.5s ease;';
    w.appendChild(b);
    const l = document.createElement('div');
    l.id = 'vs-pb-lbl';
    l.style.cssText = 'font-size:9px;color:#4a6a85;letter-spacing:1px;margin-top:3px;font-family:Share Tech Mono,monospace;';
    btn.parentNode.insertBefore(w, btn);
    btn.parentNode.insertBefore(l, btn);
    pb = b; pbWrap = w; pbLbl = l;
  }
  pbWrap.style.display = 'block';
  pb.style.width = '0%';
  pbLbl.textContent = 'INITIALIZING...';

  function setProgress(pct, msg) { pb.style.width = pct+'%'; pbLbl.textContent = (msg||'').toUpperCase(); }
  function resetBtn() {
    btn.disabled = false; btn.className = ''; btn.textContent = '▶ EXECUTE SCAN';
    setTimeout(() => { pbWrap.style.display='none'; pbLbl.textContent=''; }, 3000);
  }

  function _intVal(id, def) {
    const v = parseInt(document.getElementById(id).value);
    if (isNaN(v)) { addLog(`${id} not set — using default: ${def}`, 'warn'); return def; }
    return v;
  }
  function _floatVal(id, def) {
    const v = parseFloat(document.getElementById(id).value);
    if (isNaN(v)) { addLog(`${id} not set — using default: ${def}`, 'warn'); return def; }
    return v;
  }

  const params = {
    num_leo: _intVal('num_leo', 100),
    num_meo: _intVal('num_meo', 50),
    num_geo: _intVal('num_geo', 20),
    time_window_hours: _floatVal('time_window', 12),
    collision_alert_km: _floatVal('alert_km', 10),
    refine_threshold_km: _floatVal('refine_km', 50),
    pc_alert_threshold: parseFloat(document.getElementById('pc_thresh').value),
    alert_email: document.getElementById('alert_email') ? document.getElementById('alert_email').value || null : null,
    pushover_user_key: document.getElementById('pushover_key') ? document.getElementById('pushover_key').value || null : null,
  };

  try {
    const evtSource = new EventSource('/run?' + new URLSearchParams(params));

    evtSource.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'log') {
        const level = msg.text.includes('✓') ? 'ok' : msg.text.includes('✗') || msg.text.includes('ERROR') ? 'error' : msg.text.includes('WARNING') ? 'warn' : 'info';
        addLog(msg.text, level);
        setStatus(msg.text.slice(0, 60), 'running');
      } else if (msg.type === 'progress') {
        setProgress(msg.pct, msg.text);
        setStatus(msg.text.slice(0, 60), 'running');
      } else if (msg.type === 'rate_limit') {
        evtSource.close();
        setProgress(0, 'Rate limited');
        addLog('Rate limit: ' + msg.text, 'warn');
        setStatus('Rate limited — wait before next scan', 'error');
        resetBtn();
      } else if (msg.type === 'auth_error') {
        evtSource.close();
        setProgress(0, 'Auth required');
        addLog('Authentication required', 'error');
        setStatus('Please sign in', 'error');
        resetBtn();
      } else if (msg.type === 'done') {
        evtSource.close();
        setProgress(100, 'Complete!');
        const results = msg.data;
        addLog(`Scan complete — ${results.conjunctions.length} conjunction(s) found`, 'ok');
        setStatus(`Done — ${results.conjunctions.length} conjunction(s)`, 'ready');
        plotConjunctions(results.conjunctions);
        alertSatNames = new Set();
        results.conjunctions.forEach(c => { alertSatNames.add(c.sat1); alertSatNames.add(c.sat2); });
        plotSatellites(results.tracks);
        renderResults(results.conjunctions);
        const trackNames = results.tracks.map(t => t.name);
        if (typeof populateDebrisSatList === 'function') populateDebrisSatList(trackNames);
        resetBtn();
        viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(0, 20, 25000000), duration: 2.0 });
      } else if (msg.type === 'error') {
        evtSource.close();
        setProgress(0, 'Error');
        addLog('ERROR: ' + msg.text, 'error');
        setStatus('Scan failed', 'error');
        resetBtn();
      }
    };

    evtSource.onerror = () => {
      evtSource.close();
      setProgress(0, 'Connection lost');
      addLog('Connection lost', 'error');
      setStatus('Connection error', 'error');
      resetBtn();
    };

  } catch(err) {
    setProgress(0, 'Error');
    addLog('Failed to start scan: ' + err.message, 'error');
    setStatus('Error', 'error');
    resetBtn();
  }
}
</script>
</body>
</html>
"""

def get_dashboard_html() -> str:
    """SEC-02: Inject Cesium token server-side from environment."""
    return DASHBOARD_HTML.replace("__CESIUM_TOKEN__", _get_cesium_token())

LANDING_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VectraSpace — Orbital Mechanics & Space Safety Education</title>
<meta name="description" content="Learn orbital mechanics, Space Situational Awareness, and the physics behind Kessler Syndrome through interactive simulations and deep-dive technical chapters.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:ital,wght@0,400;0,500;1,400&family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --ink:     #080c12;
  --ink2:    #0d1320;
  --ink3:    #131d2e;
  --panel:   #0f1925;
  --border:  rgba(255,255,255,0.07);
  --border2: rgba(255,255,255,0.13);
  --text:    #ccd6e0;
  --muted:   #4e6478;
  --faint:   #2a3d50;
  --accent:  #4a9eff;
  --accent2: #7bc4ff;
  --green:   #34d399;
  --amber:   #f59e0b;
  --red:     #f87171;
  --serif:   'Instrument Serif', Georgia, serif;
  --mono:    'DM Mono', monospace;
  --sans:    'Outfit', sans-serif;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  background: var(--ink);
  color: var(--text);
  font-family: var(--sans);
  line-height: 1.6;
  overflow-x: hidden;
}

/* ── STARFIELD ── */
#starfield {
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  overflow: hidden;
}
.star {
  position: absolute; border-radius: 50%; background: #fff;
  animation: twinkle var(--dur, 4s) ease-in-out infinite var(--delay, 0s);
}
@keyframes twinkle {
  0%, 100% { opacity: var(--a1, 0.6); transform: scale(1); }
  50% { opacity: var(--a2, 0.15); transform: scale(0.7); }
}

/* ── NAV ── */
nav {
  position: fixed; top: 0; left: 0; right: 0; z-index: 200;
  height: 64px; padding: 0 48px;
  display: flex; align-items: center; justify-content: space-between;
  transition: background 0.4s, border-color 0.4s;
  border-bottom: 1px solid transparent;
}
nav.scrolled {
  background: rgba(8,12,18,0.94);
  border-bottom-color: var(--border);
  backdrop-filter: blur(20px);
}
.nav-brand {
  display: flex; align-items: center; gap: 12px; text-decoration: none;
}
.nav-logo-mark {
  width: 32px; height: 32px;
  background: conic-gradient(from 0deg, #4a9eff 0deg, #7bc4ff 90deg, transparent 90deg, transparent 180deg, #4a9eff 180deg, #4a9eff 270deg, transparent 270deg);
  border-radius: 50%; position: relative; animation: spin-slow 20s linear infinite;
}
.nav-logo-mark::after {
  content: ''; position: absolute; inset: 6px;
  background: var(--ink); border-radius: 50%;
}
@keyframes spin-slow { to { transform: rotate(360deg); } }
.nav-brand-name {
  font-family: var(--sans); font-size: 17px; font-weight: 700;
  color: #fff; letter-spacing: -0.3px;
}
.nav-brand-name em { color: var(--accent); font-style: normal; }
.nav-links {
  display: flex; gap: 32px; list-style: none; align-items: center;
}
.nav-links a {
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.5px;
  color: var(--muted); text-decoration: none; transition: color 0.2s;
}
.nav-links a:hover { color: var(--text); }
.nav-cta {
  font-family: var(--mono); font-size: 11px; letter-spacing: 1px;
  text-transform: uppercase; padding: 9px 20px;
  border: 1px solid var(--accent); border-radius: 4px;
  color: var(--accent); text-decoration: none;
  transition: all 0.2s; white-space: nowrap;
}
.nav-cta:hover { background: var(--accent); color: var(--ink); }

/* ── HERO ── */
#hero {
  position: relative; z-index: 1;
  min-height: 100vh;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; padding: 120px 24px 80px;
}
.hero-orbit-system {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  pointer-events: none; overflow: hidden;
}
.orbit-ring {
  position: absolute; border-radius: 50%;
  border: 1px solid rgba(74,158,255,0.12);
  animation: orbit-spin linear infinite;
}
.orbit-ring-1 { width: 520px; height: 520px; animation-duration: 40s; }
.orbit-ring-2 { width: 780px; height: 520px; border-color: rgba(74,158,255,0.07); animation-duration: 65s; transform: rotate(30deg); }
.orbit-ring-3 { width: 1100px; height: 700px; border-color: rgba(74,158,255,0.05); animation-duration: 90s; animation-direction: reverse; transform: rotate(-20deg); }
@keyframes orbit-spin { to { transform: rotate(360deg); } }
.orbit-ring-2 { animation-name: orbit-spin2; }
.orbit-ring-3 { animation-name: orbit-spin3; }
@keyframes orbit-spin2 { from { transform: rotate(30deg); } to { transform: rotate(390deg); } }
@keyframes orbit-spin3 { from { transform: rotate(-20deg); } to { transform: rotate(-380deg); } }

.orbit-sat {
  position: absolute; width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent); box-shadow: 0 0 10px var(--accent), 0 0 20px rgba(74,158,255,0.4);
}
.orbit-sat-2 { background: var(--green); box-shadow: 0 0 10px var(--green); }
.orbit-sat-3 { background: var(--amber); box-shadow: 0 0 10px var(--amber); }

.hero-eyebrow {
  display: inline-flex; align-items: center; gap: 8px;
  font-family: var(--mono); font-size: 10px; letter-spacing: 3px;
  text-transform: uppercase; color: var(--accent);
  background: rgba(74,158,255,0.08); border: 1px solid rgba(74,158,255,0.25);
  padding: 7px 16px; border-radius: 2px; margin-bottom: 32px;
  animation: fadeUp 0.9s ease both;
}
.eyebrow-dot {
  width: 5px; height: 5px; border-radius: 50%; background: var(--green);
  animation: pulse-dot 2.4s ease infinite;
}
@keyframes pulse-dot { 0%,100%{transform:scale(1);opacity:1} 50%{transform:scale(0.5);opacity:0.4} }

.hero-title {
  font-family: var(--serif);
  font-size: clamp(52px, 8vw, 108px);
  font-weight: 400; line-height: 1.0; color: #fff;
  letter-spacing: -2px; margin-bottom: 12px;
  animation: fadeUp 0.9s 0.1s ease both;
}
.hero-title-italic {
  font-style: italic; color: var(--accent2);
}
.hero-title-line2 {
  display: block; font-size: clamp(28px, 4vw, 56px);
  color: rgba(255,255,255,0.55); font-weight: 400; font-style: normal;
  letter-spacing: -0.5px; margin-top: 4px;
}

.hero-desc {
  font-size: 18px; font-weight: 300; line-height: 1.8;
  color: var(--muted); max-width: 620px; margin: 28px auto 48px;
  animation: fadeUp 0.9s 0.2s ease both;
}
.hero-desc strong { color: var(--text); font-weight: 500; }

.hero-actions {
  display: flex; gap: 14px; justify-content: center; flex-wrap: wrap;
  margin-bottom: 80px;
  animation: fadeUp 0.9s 0.3s ease both;
}
.btn-primary-hero {
  font-family: var(--mono); font-size: 12px; letter-spacing: 2px;
  text-transform: uppercase; padding: 14px 36px;
  background: var(--accent); color: var(--ink); border: none;
  border-radius: 3px; cursor: pointer; text-decoration: none;
  font-weight: 500; transition: all 0.2s;
}
.btn-primary-hero:hover { background: var(--accent2); transform: translateY(-1px); }
.btn-secondary-hero {
  font-family: var(--mono); font-size: 12px; letter-spacing: 2px;
  text-transform: uppercase; padding: 14px 32px;
  background: transparent; color: var(--text);
  border: 1px solid var(--border2); border-radius: 3px;
  cursor: pointer; text-decoration: none; transition: all 0.2s;
}
.btn-secondary-hero:hover { border-color: var(--text); }

.hero-scroll {
  position: absolute; bottom: 40px; left: 50%; transform: translateX(-50%);
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  font-family: var(--mono); font-size: 9px; letter-spacing: 2px;
  color: var(--muted); text-transform: uppercase; cursor: pointer;
  animation: fadeUp 1.2s 0.6s ease both; text-decoration: none;
}
.scroll-line {
  width: 1px; height: 40px; background: linear-gradient(to bottom, var(--accent), transparent);
  animation: scroll-pulse 2s ease infinite;
}
@keyframes scroll-pulse { 0%,100%{opacity:1;transform:scaleY(1)} 50%{opacity:0.3;transform:scaleY(0.6)} }

@keyframes fadeUp {
  from { opacity: 0; transform: translateY(24px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ── TICKER ── */
.ticker-bar {
  position: relative; z-index: 1; overflow: hidden;
  background: var(--panel); border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  padding: 10px 0;
}
.ticker-inner {
  display: flex; gap: 0; white-space: nowrap;
  animation: ticker 30s linear infinite;
}
@keyframes ticker { from { transform: translateX(0); } to { transform: translateX(-50%); } }
.tick-item {
  display: inline-flex; align-items: center; gap: 10px;
  font-family: var(--mono); font-size: 10px; letter-spacing: 1.5px;
  color: var(--muted); text-transform: uppercase; padding: 0 36px;
  flex-shrink: 0;
}
.tick-sep { color: var(--faint); }
.tick-item.hi { color: var(--accent); }
.tick-item.warn { color: var(--amber); }
.tick-item.ok { color: var(--green); }

/* ── SECTION SHARED ── */
section { position: relative; z-index: 1; }
.section-wrap { max-width: 1160px; margin: 0 auto; padding: 0 48px; }
.section-label {
  font-family: var(--mono); font-size: 10px; letter-spacing: 3px;
  color: var(--accent); text-transform: uppercase; margin-bottom: 12px;
}
.section-title {
  font-family: var(--serif); font-size: clamp(32px, 4vw, 52px);
  font-weight: 400; color: #fff; line-height: 1.15; margin-bottom: 16px;
  letter-spacing: -0.5px;
}
.section-title em { font-style: italic; color: var(--accent2); }
.section-body {
  font-size: 16px; font-weight: 300; color: var(--muted);
  line-height: 1.8; max-width: 560px;
}

/* ── REVEAL ANIMATION ── */
.reveal { opacity: 0; transform: translateY(28px); transition: opacity 0.7s ease, transform 0.7s ease; }
.reveal.visible { opacity: 1; transform: translateY(0); }
.reveal-delay-1 { transition-delay: 0.1s; }
.reveal-delay-2 { transition-delay: 0.2s; }
.reveal-delay-3 { transition-delay: 0.3s; }

/* ── MISSION SECTION ── */
#mission { padding: 120px 0; }
.mission-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 80px; align-items: center;
}
.mission-visual {
  position: relative; display: flex; align-items: center; justify-content: center;
  height: 400px;
}
.mission-globe {
  width: 260px; height: 260px; border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, #1a4a7a, #0a1f3a 60%, #060e1a);
  box-shadow:
    0 0 0 1px rgba(74,158,255,0.2),
    0 0 60px rgba(74,158,255,0.08),
    inset 0 0 40px rgba(0,0,0,0.6);
  position: relative; overflow: hidden;
}
.globe-grid-line {
  position: absolute; border: 1px solid rgba(74,158,255,0.1);
}
.globe-lat { width: 100%; height: 0; top: var(--t); left: 0; }
.globe-lon {
  width: 0; height: 100%;
  left: var(--l); top: 0;
  border: none; border-left: 1px solid rgba(74,158,255,0.1);
}
.globe-glow {
  position: absolute; width: 80px; height: 80px; border-radius: 50%;
  background: radial-gradient(circle, rgba(74,158,255,0.25) 0%, transparent 70%);
  top: 10px; left: 20px;
}
.orbit-path {
  position: absolute; border-radius: 50%; border: 1px solid;
  width: var(--w); height: var(--w);
  top: 50%; left: 50%; transform: translate(-50%, -50%) rotate(var(--r));
}
.orbit-path-1 { --w:310px; border-color: rgba(74,158,255,0.3); animation: orb 8s linear infinite; }
.orbit-path-2 { --w:380px; border-color: rgba(52,211,153,0.2); animation: orb2 12s linear infinite; transform: translate(-50%,-50%) rotate(45deg); }
.orbit-path-3 { --w:450px; border-color: rgba(245,158,11,0.15); animation: orb3 18s linear infinite; transform: translate(-50%,-50%) rotate(-30deg); }
@keyframes orb  { to { transform: translate(-50%,-50%) rotate(360deg); } }
@keyframes orb2 { from { transform: translate(-50%,-50%) rotate(45deg); } to { transform: translate(-50%,-50%) rotate(405deg); } }
@keyframes orb3 { from { transform: translate(-50%,-50%) rotate(-30deg); } to { transform: translate(-50%,-50%) rotate(330deg); } }
.orb-sat {
  position: absolute; width: 8px; height: 8px; border-radius: 50%;
  top: -4px; left: calc(50% - 4px); box-shadow: 0 0 12px currentColor;
}

.mission-stats {
  display: flex; flex-direction: column; gap: 24px; margin-top: 48px;
}
.mission-stat {
  display: flex; gap: 20px; align-items: flex-start;
  padding: 20px 24px; background: var(--panel);
  border: 1px solid var(--border); border-radius: 6px;
  transition: border-color 0.2s;
}
.mission-stat:hover { border-color: var(--border2); }
.mission-stat-num {
  font-family: var(--serif); font-size: 36px; color: var(--accent);
  line-height: 1; flex-shrink: 0; width: 80px; text-align: right;
}
.mission-stat-label { font-size: 13px; color: var(--muted); line-height: 1.6; }
.mission-stat-label strong { color: var(--text); display: block; font-size: 14px; margin-bottom: 2px; }

/* ── SSA SECTION ── */
#ssa { padding: 120px 0; background: linear-gradient(180deg, transparent 0%, rgba(74,158,255,0.02) 50%, transparent 100%); }
.ssa-header { text-align: center; margin-bottom: 72px; }
.ssa-header .section-body { margin: 0 auto; text-align: center; max-width: 640px; }
.ssa-pillars {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 1px; background: var(--border);
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
}
.ssa-pillar {
  background: var(--ink2); padding: 36px 32px;
  position: relative; overflow: hidden; transition: background 0.2s;
}
.ssa-pillar::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: var(--pillar-color, var(--accent));
  transform: scaleX(0); transition: transform 0.35s ease;
}
.ssa-pillar:hover { background: var(--panel); }
.ssa-pillar:hover::before { transform: scaleX(1); }
.ssa-pillar-icon {
  font-size: 28px; margin-bottom: 20px; display: block;
  filter: drop-shadow(0 0 8px var(--pillar-color, var(--accent)));
}
.ssa-pillar-title {
  font-family: var(--mono); font-size: 11px; letter-spacing: 2px;
  text-transform: uppercase; color: #fff; margin-bottom: 12px;
}
.ssa-pillar-body { font-size: 13px; color: var(--muted); line-height: 1.7; }
.ssa-pillar-tag {
  display: inline-block; font-family: var(--mono); font-size: 8px;
  letter-spacing: 1px; padding: 3px 8px; border-radius: 2px;
  margin-top: 14px; text-transform: uppercase;
  background: rgba(74,158,255,0.08); color: var(--accent);
  border: 1px solid rgba(74,158,255,0.2);
}

/* ── KESSLER SECTION ── */
#kessler { padding: 120px 0; }
.kessler-inner {
  display: grid; grid-template-columns: 1fr 1fr; gap: 80px; align-items: start;
}
.kessler-cascade {
  display: flex; flex-direction: column; gap: 0;
}
.cascade-step {
  display: flex; gap: 20px; position: relative;
  padding-bottom: 32px; cursor: default;
}
.cascade-step:last-child { padding-bottom: 0; }
.cascade-step::before {
  content: ''; position: absolute;
  left: 19px; top: 40px; bottom: 0; width: 1px;
  background: linear-gradient(to bottom, var(--step-color, var(--border2)), transparent);
}
.cascade-step:last-child::before { display: none; }
.cascade-num {
  width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0;
  background: var(--ink3); border: 1px solid var(--step-color, var(--border));
  display: flex; align-items: center; justify-content: center;
  font-family: var(--mono); font-size: 11px; color: var(--step-color, var(--muted));
  transition: all 0.2s; position: relative; z-index: 1;
}
.cascade-step:hover .cascade-num {
  background: color-mix(in srgb, var(--step-color, var(--accent)), transparent 85%);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--step-color, var(--accent)), transparent 88%);
}
.cascade-title {
  font-family: var(--mono); font-size: 12px; letter-spacing: 1px;
  color: #fff; margin-bottom: 6px; padding-top: 9px;
  transition: color 0.2s;
}
.cascade-step:hover .cascade-title { color: var(--step-color, var(--accent)); }
.cascade-body { font-size: 13px; color: var(--muted); line-height: 1.65; }

.kessler-data {
  display: flex; flex-direction: column; gap: 16px;
  position: sticky; top: 100px;
}
.kd-card {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 6px; padding: 24px; position: relative; overflow: hidden;
}
.kd-card::after {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(135deg, transparent 60%, var(--card-tint, rgba(74,158,255,0.03)) 100%);
}
.kd-label {
  font-family: var(--mono); font-size: 9px; letter-spacing: 2px;
  color: var(--muted); text-transform: uppercase; margin-bottom: 8px;
}
.kd-value {
  font-family: var(--serif); font-size: 36px; color: #fff; line-height: 1;
  margin-bottom: 4px;
}
.kd-desc { font-size: 12px; color: var(--muted); line-height: 1.5; }
.kd-bar {
  margin-top: 14px; height: 4px; background: var(--ink3); border-radius: 2px; overflow: hidden;
}
.kd-bar-fill {
  height: 100%; border-radius: 2px;
  background: linear-gradient(to right, var(--bar-color, var(--accent)), color-mix(in srgb, var(--bar-color, var(--accent)), transparent 30%));
  animation: bar-fill 1.6s 0.5s ease both;
  transform-origin: left;
}
@keyframes bar-fill { from { transform: scaleX(0); } to { transform: scaleX(1); } }

/* ── SIMULATION CAPABILITIES ── */
#simulation { padding: 120px 0; }
.sim-header { margin-bottom: 64px; }
.sim-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;
  margin-bottom: 40px;
}
.sim-card {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 6px; padding: 28px 24px; transition: all 0.2s; position: relative;
}
.sim-card:hover { border-color: var(--border2); transform: translateY(-2px); }
.sim-card-tag {
  font-family: var(--mono); font-size: 8px; letter-spacing: 2px;
  text-transform: uppercase; padding: 3px 8px; border-radius: 2px;
  margin-bottom: 16px; display: inline-block;
  background: rgba(74,158,255,0.08); color: var(--accent);
  border: 1px solid rgba(74,158,255,0.2);
}
.sim-card-tag.green {
  background: rgba(52,211,153,0.08); color: var(--green);
  border-color: rgba(52,211,153,0.2);
}
.sim-card-tag.amber {
  background: rgba(245,158,11,0.08); color: var(--amber);
  border-color: rgba(245,158,11,0.2);
}
.sim-card-tag.red {
  background: rgba(248,113,113,0.08); color: var(--red);
  border-color: rgba(248,113,113,0.2);
}
.sim-card-icon { font-size: 22px; margin-bottom: 14px; }
.sim-card-title {
  font-family: var(--mono); font-size: 12px; letter-spacing: 1px;
  color: #fff; margin-bottom: 8px; text-transform: uppercase;
}
.sim-card-body { font-size: 13px; color: var(--muted); line-height: 1.65; }
.sim-card-stat {
  margin-top: 16px; font-family: var(--mono); font-size: 10px;
  color: var(--faint); letter-spacing: 1px;
}
.sim-card-stat span { color: var(--accent); }

.sim-terminal {
  background: #060c14; border: 1px solid var(--border);
  border-radius: 8px; overflow: hidden;
  box-shadow: 0 0 80px rgba(74,158,255,0.06), 0 40px 80px rgba(0,0,0,0.6);
}
.sim-terminal-bar {
  background: #0a111c; border-bottom: 1px solid var(--border);
  padding: 12px 20px; display: flex; align-items: center; gap: 14px;
}
.terminal-dots { display: flex; gap: 7px; }
.terminal-dots span { width: 10px; height: 10px; border-radius: 50%; }
.td-r { background: #ff5f57; } .td-y { background: #febc2e; } .td-g { background: #28c840; }
.terminal-title {
  font-family: var(--mono); font-size: 9px; letter-spacing: 2px;
  color: var(--muted); text-transform: uppercase; margin-left: auto;
}
.sim-terminal-body {
  padding: 24px 28px; font-family: var(--mono); font-size: 11px;
  line-height: 2.0;
}
.tl { display: block; }
.tp { color: var(--accent); } .tc { color: var(--text); }
.to { color: var(--muted); } .tok { color: var(--green); }
.tw { color: var(--amber); } .tv { color: var(--accent2); }
.te { color: var(--red); }
.cursor-blink { display: inline-block; width: 8px; height: 14px; background: var(--accent); animation: cursor-blink 1s step-end infinite; vertical-align: middle; }
@keyframes cursor-blink { 50% { opacity: 0; } }

/* ── LEARN SECTION ── */
#learn { padding: 120px 0; background: linear-gradient(180deg, transparent 0%, rgba(74,158,255,0.015) 50%, transparent 100%); }
.learn-header { text-align: center; margin-bottom: 72px; }
.learn-header .section-body { margin: 0 auto; text-align: center; }
.chapters-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px;
}
.chapter-card {
  display: block; text-decoration: none;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; overflow: hidden; transition: all 0.25s;
  position: relative;
}
.chapter-card:hover {
  border-color: var(--ch-color, var(--accent));
  transform: translateY(-3px);
  box-shadow: 0 16px 48px rgba(0,0,0,0.4), 0 0 0 0.5px var(--ch-color, var(--accent));
}
.chapter-card-accent {
  height: 3px; background: var(--ch-color, var(--accent));
  transform: scaleX(0); transform-origin: left; transition: transform 0.3s ease;
}
.chapter-card:hover .chapter-card-accent { transform: scaleX(1); }
.chapter-card-body { padding: 32px; }
.chapter-number {
  font-family: var(--mono); font-size: 9px; letter-spacing: 3px;
  color: var(--ch-color, var(--accent)); text-transform: uppercase;
  margin-bottom: 12px;
}
.chapter-title {
  font-family: var(--serif); font-size: 26px; font-weight: 400;
  color: #fff; line-height: 1.2; margin-bottom: 12px;
  letter-spacing: -0.3px;
}
.chapter-desc { font-size: 13px; color: var(--muted); line-height: 1.65; }
.chapter-topics {
  margin-top: 20px; display: flex; flex-wrap: wrap; gap: 6px;
}
.topic-pill {
  font-family: var(--mono); font-size: 9px; letter-spacing: 1px;
  padding: 3px 10px; border-radius: 20px;
  background: rgba(255,255,255,0.04); color: var(--muted);
  border: 1px solid var(--border); text-transform: uppercase;
  transition: all 0.2s;
}
.chapter-card:hover .topic-pill { border-color: rgba(255,255,255,0.12); color: var(--text); }
.chapter-footer {
  border-top: 1px solid var(--border); padding: 16px 32px;
  display: flex; justify-content: space-between; align-items: center;
  font-family: var(--mono); font-size: 10px; color: var(--muted);
}
.chapter-read-link {
  color: var(--ch-color, var(--accent)); letter-spacing: 1.5px;
  text-transform: uppercase; font-size: 9px;
  display: flex; align-items: center; gap: 6px;
}
.chapter-read-link::after {
  content: '→'; transition: transform 0.2s;
}
.chapter-card:hover .chapter-read-link::after { transform: translateX(4px); }

/* ── DATA SECTION ── */
#data { padding: 100px 0; }
.data-metrics {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 1px; background: var(--border);
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
}
.data-metric {
  background: var(--panel); padding: 40px 32px; text-align: center;
  position: relative; overflow: hidden; transition: background 0.2s;
}
.data-metric:hover { background: var(--ink3); }
.data-metric-glyph {
  position: absolute; bottom: -10px; right: -10px;
  font-family: var(--serif); font-size: 80px; color: rgba(255,255,255,0.02);
  line-height: 1; pointer-events: none;
}
.data-metric-val {
  font-family: var(--serif); font-size: 48px; color: var(--accent);
  line-height: 1; margin-bottom: 8px; display: block;
}
.c2 .data-metric-val { color: var(--green); }
.c3 .data-metric-val { color: var(--amber); }
.c4 .data-metric-val { color: #a78bfa; }
.data-metric-label {
  font-family: var(--mono); font-size: 9px; letter-spacing: 2px;
  color: var(--muted); text-transform: uppercase;
}

/* ── CTA ── */
#cta { padding: 120px 0; }
.cta-box {
  max-width: 820px; margin: 0 auto;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 12px; padding: 80px 64px; text-align: center;
  position: relative; overflow: hidden;
}
.cta-box::before {
  content: ''; position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent 0%, var(--accent) 50%, transparent 100%);
}
.cta-glow {
  position: absolute; bottom: -100px; left: 50%; transform: translateX(-50%);
  width: 500px; height: 300px;
  background: radial-gradient(ellipse, rgba(74,158,255,0.08) 0%, transparent 70%);
  pointer-events: none;
}
.cta-eyebrow {
  font-family: var(--mono); font-size: 10px; letter-spacing: 3px;
  color: var(--green); text-transform: uppercase; margin-bottom: 20px;
}
.cta-title {
  font-family: var(--serif); font-size: clamp(32px, 4vw, 50px);
  color: #fff; font-weight: 400; line-height: 1.2; margin-bottom: 16px;
  letter-spacing: -0.5px;
}
.cta-title em { font-style: italic; color: var(--accent2); }
.cta-body {
  font-size: 16px; font-weight: 300; color: var(--muted);
  line-height: 1.8; margin-bottom: 44px; max-width: 520px; margin-left: auto; margin-right: auto;
}
.cta-buttons {
  display: flex; gap: 14px; justify-content: center; flex-wrap: wrap;
  position: relative; z-index: 1;
}

/* ── FOOTER ── */
footer {
  position: relative; z-index: 1;
  border-top: 1px solid var(--border);
  padding: 40px 48px;
  display: flex; justify-content: space-between; align-items: center;
}
.footer-brand { font-family: var(--sans); font-size: 14px; font-weight: 600; color: var(--muted); }
.footer-brand em { color: var(--accent); font-style: normal; }
.footer-links {
  display: flex; gap: 28px; list-style: none;
}
.footer-links a {
  font-family: var(--mono); font-size: 10px; letter-spacing: 1px;
  color: var(--muted); text-decoration: none;
  text-transform: uppercase; transition: color 0.2s;
}
.footer-links a:hover { color: var(--text); }
.footer-copy {
  font-family: var(--mono); font-size: 9px; color: var(--faint);
  letter-spacing: 1px;
}

/* ── DIVIDER ── */
.section-divider {
  height: 1px; margin: 0;
  background: linear-gradient(90deg, transparent, var(--border), transparent);
  position: relative; z-index: 1;
}

/* ── RESPONSIVE ── */
@media (max-width: 960px) {
  nav { padding: 0 24px; }
  .nav-links { display: none; }
  .section-wrap { padding: 0 24px; }
  .mission-grid, .kessler-inner { grid-template-columns: 1fr; gap: 48px; }
  .ssa-pillars { grid-template-columns: 1fr; }
  .sim-grid { grid-template-columns: 1fr 1fr; }
  .chapters-grid { grid-template-columns: 1fr; }
  .data-metrics { grid-template-columns: repeat(2, 1fr); }
  .mission-visual { height: 280px; }
  footer { flex-direction: column; gap: 20px; text-align: center; }
  .kessler-data { position: static; }
}
@media (max-width: 600px) {
  .sim-grid { grid-template-columns: 1fr; }
  .data-metrics { grid-template-columns: 1fr 1fr; }
  .cta-box { padding: 48px 24px; }
  #hero { padding: 100px 16px 60px; }
  .hero-title { letter-spacing: -1px; }
}
</style>
</head>
<body>

<!-- STARFIELD -->
<div id="starfield"></div>

<!-- NAV -->
<nav id="nav">
  <a href="#" class="nav-brand">
    <div class="nav-logo-mark"></div>
    <span class="nav-brand-name">Vectra<em>Space</em></span>
  </a>
  <ul class="nav-links">
    <li><a href="#mission">Mission</a></li>
    <li><a href="#ssa">What is SSA?</a></li>
    <li><a href="#kessler">Kessler Syndrome</a></li>
    <li><a href="#learn">Deep Dives</a></li>
    <li><a href="#simulation">Simulation</a></li>
  </ul>
  <a href="/dashboard" class="nav-cta">Open Dashboard</a>
</nav>

<!-- HERO -->
<section id="hero">
  <div class="hero-orbit-system">
    <div class="orbit-ring orbit-ring-1">
      <div class="orbit-sat" style="color:#4a9eff;"></div>
    </div>
    <div class="orbit-ring orbit-ring-2">
      <div class="orbit-sat orbit-sat-2" style="top:-4px;left:calc(50% - 4px);"></div>
    </div>
    <div class="orbit-ring orbit-ring-3">
      <div class="orbit-sat orbit-sat-3" style="top:calc(50% - 4px);left:-4px;"></div>
    </div>
  </div>

  <div class="hero-eyebrow">
    <span class="eyebrow-dot"></span>
    Space Situational Awareness &amp; Education Platform
  </div>

  <h1 class="hero-title">
    <span class="hero-title-italic">Understanding</span>
    <span class="hero-title-line2">the Crowded Cosmos</span>
  </h1>

  <p class="hero-desc">
    <strong>27,000+ tracked objects.</strong> A growing debris field that could cascade into an
    uncontrollable chain reaction. VectraSpace is built to help you understand orbital mechanics,
    Space Situational Awareness, and the orbital collision physics that define the future of spaceflight.
  </p>

  <div class="hero-actions">
    <a href="#learn" class="btn-primary-hero">Start Learning</a>
    <a href="/dashboard" class="btn-secondary-hero">Live Simulation →</a>
  </div>

  <a href="#mission" class="hero-scroll">
    <div class="scroll-line"></div>
    Explore
  </a>
</section>

<!-- TICKER -->
<div class="ticker-bar">
  <div class="ticker-inner" id="ticker">
    <span class="tick-item ok"><span class="tick-sep">◆</span> Tracked Objects: 27,000+</span>
    <span class="tick-item warn"><span class="tick-sep">◆</span> Estimated Debris &gt;1mm: 130 Million</span>
    <span class="tick-item hi"><span class="tick-sep">◆</span> ISS Altitude: 408 km LEO</span>
    <span class="tick-item"><span class="tick-sep">◆</span> Collision Risk Method: Foster-Alfano Pc</span>
    <span class="tick-item warn"><span class="tick-sep">◆</span> Fengyun-1C 2007: Largest Single Debris Event</span>
    <span class="tick-item ok"><span class="tick-sep">◆</span> SGP4 Propagation: 1-Minute Resolution</span>
    <span class="tick-item"><span class="tick-sep">◆</span> Sun-Synchronous i ≈ 97.8° — RAAN Drifts +0.9856°/day</span>
    <span class="tick-item hi"><span class="tick-sep">◆</span> Kessler Syndrome: Self-Sustaining Cascade</span>
    <span class="tick-item"><span class="tick-sep">◆</span> J₂ Coefficient: 1.08263 × 10⁻³</span>
    <span class="tick-item ok"><span class="tick-sep">◆</span> Tracked Objects: 27,000+</span>
    <span class="tick-item warn"><span class="tick-sep">◆</span> Estimated Debris &gt;1mm: 130 Million</span>
    <span class="tick-item hi"><span class="tick-sep">◆</span> ISS Altitude: 408 km LEO</span>
    <span class="tick-item"><span class="tick-sep">◆</span> Collision Risk Method: Foster-Alfano Pc</span>
    <span class="tick-item warn"><span class="tick-sep">◆</span> Fengyun-1C 2007: Largest Single Debris Event</span>
    <span class="tick-item ok"><span class="tick-sep">◆</span> SGP4 Propagation: 1-Minute Resolution</span>
    <span class="tick-item"><span class="tick-sep">◆</span> Sun-Synchronous i ≈ 97.8° — RAAN Drifts +0.9856°/day</span>
    <span class="tick-item hi"><span class="tick-sep">◆</span> Kessler Syndrome: Self-Sustaining Cascade</span>
    <span class="tick-item"><span class="tick-sep">◆</span> J₂ Coefficient: 1.08263 × 10⁻³</span>
  </div>
</div>

<!-- MISSION -->
<section id="mission">
  <div class="section-wrap">
    <div class="mission-grid">
      <div class="reveal">
        <div class="section-label">// Our Mission</div>
        <h2 class="section-title">The orbital environment<br>is <em>running out of time</em></h2>
        <p class="section-body" style="margin-bottom:32px;">
          Every satellite launch adds to the most complex coordination problem humanity has ever faced.
          Without shared understanding of the physics, the risks, and the mitigation strategies,
          we risk losing access to the orbits that power modern civilization.
        </p>
        <p class="section-body">
          VectraSpace is a scientific educational platform backed by real SGP4 propagation,
          collision probability physics, and live TLE data — built so students, researchers,
          and engineers can develop genuine orbital intuition.
        </p>

        <div class="mission-stats">
          <div class="mission-stat reveal reveal-delay-1">
            <div class="mission-stat-num">408<span style="font-size:16px;color:var(--muted);">km</span></div>
            <div class="mission-stat-label">
              <strong>ISS orbital altitude (LEO)</strong>
              Where most debris concentration and human spaceflight activity intersects.
            </div>
          </div>
          <div class="mission-stat reveal reveal-delay-2">
            <div class="mission-stat-num" style="color:var(--amber);">10×</div>
            <div class="mission-stat-label">
              <strong>Relative collision velocity at TCA</strong>
              Orbital velocities of ~7.8 km/s mean impacts release catastrophic kinetic energy — a 10 cm fragment carries 500 kJ.
            </div>
          </div>
          <div class="mission-stat reveal reveal-delay-3">
            <div class="mission-stat-num" style="color:var(--red);">∞</div>
            <div class="mission-stat-label">
              <strong>Self-sustaining cascade threshold</strong>
              Above critical density, collisions generate more debris than drag removes — cascade is irreversible.
            </div>
          </div>
        </div>
      </div>

      <div class="mission-visual reveal reveal-delay-2">
        <!-- Animated globe with orbit paths -->
        <div class="mission-globe">
          <div class="globe-glow"></div>
          <div class="globe-grid-line globe-lat" style="--t:20%"></div>
          <div class="globe-grid-line globe-lat" style="--t:40%"></div>
          <div class="globe-grid-line globe-lat" style="--t:60%"></div>
          <div class="globe-grid-line globe-lat" style="--t:80%"></div>
          <div class="globe-grid-line globe-lon" style="--l:25%"></div>
          <div class="globe-grid-line globe-lon" style="--l:50%"></div>
          <div class="globe-grid-line globe-lon" style="--l:75%"></div>
        </div>
        <div class="orbit-path orbit-path-1">
          <div class="orb-sat" style="background:#4a9eff;color:#4a9eff;"></div>
        </div>
        <div class="orbit-path orbit-path-2">
          <div class="orb-sat" style="background:#34d399;color:#34d399;top:calc(50% - 4px);left:-4px;"></div>
        </div>
        <div class="orbit-path orbit-path-3">
          <div class="orb-sat" style="background:#f59e0b;color:#f59e0b;top:auto;bottom:-4px;left:calc(50% - 4px);"></div>
        </div>

        <!-- Regime labels -->
        <div style="position:absolute;top:30px;right:20px;display:flex;flex-direction:column;gap:8px;text-align:right;">
          <div style="font-family:var(--mono);font-size:9px;letter-spacing:1.5px;color:#4a9eff;">LEO &lt;2000 km</div>
          <div style="font-family:var(--mono);font-size:9px;letter-spacing:1.5px;color:#34d399;">MEO 2–35k km</div>
          <div style="font-family:var(--mono);font-size:9px;letter-spacing:1.5px;color:#f59e0b;">GEO 35,786 km</div>
        </div>
      </div>
    </div>
  </div>
</section>

<div class="section-divider"></div>

<!-- SSA -->
<section id="ssa">
  <div class="section-wrap">
    <div class="ssa-header reveal">
      <div class="section-label">// Space Situational Awareness</div>
      <h2 class="section-title">Knowing where <em>everything</em> is<br>and where it's going</h2>
      <p class="section-body">
        Space Situational Awareness (SSA) is the capacity to observe, understand, and predict the
        physical location of natural and man-made objects in orbit — and assess the potential
        impact of space weather, debris events, and close approaches.
      </p>
    </div>

    <div class="ssa-pillars reveal">
      <div class="ssa-pillar" style="--pillar-color:#4a9eff;">
        <span class="ssa-pillar-icon">📡</span>
        <div class="ssa-pillar-title">Surveillance &amp; Tracking</div>
        <p class="ssa-pillar-body">
          Ground-based radars and optical telescopes continuously track objects larger than 10 cm.
          The US Space Surveillance Network maintains a catalog of over 27,000 objects,
          updating TLEs every few hours as objects drift from predicted paths.
        </p>
        <span class="ssa-pillar-tag">Radar + Optical</span>
      </div>

      <div class="ssa-pillar" style="--pillar-color:#34d399;">
        <span class="ssa-pillar-icon">🔬</span>
        <div class="ssa-pillar-title">Conjunction Analysis</div>
        <p class="ssa-pillar-body">
          Screening all possible object pairs for close approaches (conjunctions) within a
          prediction window. Probability of Collision (Pc) estimates using covariance matrices
          from measured position errors drive operational go/no-go decisions.
        </p>
        <span class="ssa-pillar-tag" style="background:rgba(52,211,153,0.08);color:#34d399;border-color:rgba(52,211,153,0.2);">Pc Estimation</span>
      </div>

      <div class="ssa-pillar" style="--pillar-color:#f59e0b;">
        <span class="ssa-pillar-icon">🛡</span>
        <div class="ssa-pillar-title">Threat Mitigation</div>
        <p class="ssa-pillar-body">
          When Pc exceeds thresholds (typically 1×10⁻⁴ for crewed vehicles), operators plan
          avoidance maneuvers. Active Debris Removal (ADR) technologies aim to deorbit legacy
          rocket bodies before they collide and fragment.
        </p>
        <span class="ssa-pillar-tag" style="background:rgba(245,158,11,0.08);color:#f59e0b;border-color:rgba(245,158,11,0.2);">ADR + Maneuver</span>
      </div>
    </div>

    <div style="margin-top:48px;padding:32px;background:var(--panel);border:1px solid var(--border);border-radius:8px;display:grid;grid-template-columns:repeat(4,1fr);gap:0;background:var(--ink2);" class="reveal">
      <div style="padding:0 28px;border-right:1px solid var(--border);text-align:center;">
        <div style="font-family:var(--serif);font-size:32px;color:var(--accent);margin-bottom:6px;">27k+</div>
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;">Tracked Objects</div>
      </div>
      <div style="padding:0 28px;border-right:1px solid var(--border);text-align:center;">
        <div style="font-family:var(--serif);font-size:32px;color:var(--amber);margin-bottom:6px;">500k</div>
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;">Objects &gt;1 cm</div>
      </div>
      <div style="padding:0 28px;border-right:1px solid var(--border);text-align:center;">
        <div style="font-family:var(--serif);font-size:32px;color:var(--red);margin-bottom:6px;">130M</div>
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;">Objects &gt;1 mm</div>
      </div>
      <div style="padding:0 28px;text-align:center;">
        <div style="font-family:var(--serif);font-size:32px;color:#a78bfa;margin-bottom:6px;">10 cm</div>
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;">Lethal Threshold</div>
      </div>
    </div>
  </div>
</section>

<div class="section-divider"></div>

<!-- KESSLER -->
<section id="kessler">
  <div class="section-wrap">
    <div class="kessler-inner">
      <div>
        <div class="section-label reveal">// Kessler Syndrome</div>
        <h2 class="section-title reveal">The cascade that<br><em>never stops</em></h2>
        <p class="section-body reveal" style="margin-bottom:48px;">
          In 1978, NASA scientist Donald Kessler described a scenario so alarming it now bears his name.
          At critical orbital density, debris from one collision generates enough new fragments to
          trigger another — indefinitely. The math is unforgiving.
        </p>

        <div class="kessler-cascade">
          <div class="cascade-step reveal" style="--step-color:#4a9eff;">
            <div class="cascade-num">01</div>
            <div>
              <div class="cascade-title">Collision Event</div>
              <p class="cascade-body">Two objects — active satellite, defunct payload, rocket body fragment — occupy the same volume within close approach geometry. At orbital velocities, the collision is hypervelocity: 7–15 km/s relative speed.</p>
            </div>
          </div>
          <div class="cascade-step reveal reveal-delay-1" style="--step-color:#34d399;">
            <div class="cascade-num">02</div>
            <div>
              <div class="cascade-title">Fragmentation Cloud</div>
              <p class="cascade-body">The NASA Standard Breakup Model predicts fragment count N(Lc) = 6·d⁰·⁵·Lc⁻¹·⁶. A 1 m collision generates ~6,000 trackable fragments, hundreds of thousands of lethal-but-invisible sub-centimeter debris.</p>
            </div>
          </div>
          <div class="cascade-step reveal reveal-delay-2" style="--step-color:#f59e0b;">
            <div class="cascade-num">03</div>
            <div>
              <div class="cascade-title">Density Increase</div>
              <p class="cascade-body">Fragments spread through a ±340 km altitude band. Local object density n increases in the affected shell. Collision rate scales as n² — doubling density quadruples the collision probability.</p>
            </div>
          </div>
          <div class="cascade-step reveal reveal-delay-3" style="--step-color:#f87171;">
            <div class="cascade-num">04</div>
            <div>
              <div class="cascade-title">Runaway Cascade</div>
              <p class="cascade-body">Above critical density n_c, new collisions outpace atmospheric drag removal. The population grows unbounded. Studies find the 750–900 km shell may already be unstable — even with a complete launch moratorium.</p>
            </div>
          </div>
        </div>
      </div>

      <div class="kessler-data">
        <div class="kd-card reveal" style="--card-tint:rgba(248,113,113,0.04);">
          <div class="kd-label">Critical Population Threshold</div>
          <div class="kd-value" style="color:var(--red);">n_c</div>
          <div class="kd-desc">Critical density where collision generation rate exceeds orbital decay rate. Likely already exceeded in the 750–900 km shell.</div>
          <div class="kd-bar"><div class="kd-bar-fill" style="width:88%;--bar-color:var(--red);"></div></div>
          <div style="font-family:var(--mono);font-size:9px;color:var(--muted);margin-top:6px;letter-spacing:1px;">CURRENT DENSITY ≈ 88% OF ESTIMATED n_c</div>
        </div>

        <div class="kd-card reveal reveal-delay-1">
          <div class="kd-label">Historical Events</div>
          <div style="display:flex;flex-direction:column;gap:10px;margin-top:8px;">
            <div style="display:flex;justify-content:space-between;font-size:12px;padding:8px 12px;background:rgba(248,113,113,0.06);border-left:2px solid var(--red);border-radius:2px;">
              <span style="color:var(--text);font-family:var(--mono);font-size:11px;">Fengyun-1C ASAT</span>
              <span style="color:var(--red);font-family:var(--mono);font-size:11px;">2007</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:12px;padding:8px 12px;background:rgba(245,158,11,0.06);border-left:2px solid var(--amber);border-radius:2px;">
              <span style="color:var(--text);font-family:var(--mono);font-size:11px;">Iridium 33 × Cosmos 2251</span>
              <span style="color:var(--amber);font-family:var(--mono);font-size:11px;">2009</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:12px;padding:8px 12px;background:rgba(248,113,113,0.06);border-left:2px solid var(--red);border-radius:2px;">
              <span style="color:var(--text);font-family:var(--mono);font-size:11px;">Kosmos 1408 ASAT</span>
              <span style="color:var(--red);font-family:var(--mono);font-size:11px;">2021</span>
            </div>
          </div>
        </div>

        <div class="kd-card reveal reveal-delay-2" style="--card-tint:rgba(52,211,153,0.03);">
          <div class="kd-label">ADR Target to Stabilize LEO</div>
          <div class="kd-value" style="color:var(--green);">5–10</div>
          <div class="kd-desc">Rocket body removals per year needed (in critical 750–900 km shell) to arrest population growth. Each removal costs $50–200M.</div>
          <div class="kd-bar"><div class="kd-bar-fill" style="width:12%;--bar-color:var(--green);"></div></div>
          <div style="font-family:var(--mono);font-size:9px;color:var(--muted);margin-top:6px;letter-spacing:1px;">CURRENT REMOVAL RATE ≈ 0 PER YEAR</div>
        </div>
      </div>
    </div>
  </div>
</section>

<div class="section-divider"></div>

<!-- DEEP DIVE CHAPTERS -->
<section id="learn">
  <div class="section-wrap">
    <div class="learn-header">
      <div class="reveal">
        <div class="section-label">// Technical Deep Dives</div>
        <h2 class="section-title">The physics behind<br><em>every orbit</em></h2>
        <p class="section-body">
          Four comprehensive chapters covering the mathematics, algorithms, and engineering principles
          that power modern Space Situational Awareness — from Kepler to Kessler.
        </p>
      </div>
    </div>

    <div class="chapters-grid">
      <!-- Chapter 01 -->
      <a href="/education/orbital-mechanics" class="chapter-card reveal" style="--ch-color:#4a9eff;">
        <div class="chapter-card-accent"></div>
        <div class="chapter-card-body">
          <div class="chapter-number">Chapter 01 — Foundations</div>
          <h3 class="chapter-title">Orbital Mechanics &amp; the Two-Body Problem</h3>
          <p class="chapter-desc">
            From Newton's universal gravitation to Kepler's three laws, vis-viva equation,
            orbital elements, TLE format, and the SGP4 propagator that powers every conjunction
            screening system on Earth.
          </p>
          <div class="chapter-topics">
            <span class="topic-pill">Kepler's Laws</span>
            <span class="topic-pill">Six Orbital Elements</span>
            <span class="topic-pill">TLE Format</span>
            <span class="topic-pill">SGP4 Model</span>
            <span class="topic-pill">Vis-Viva Equation</span>
          </div>
        </div>
        <div class="chapter-footer">
          <span>~25 min read · 12 equations</span>
          <span class="chapter-read-link">Read chapter</span>
        </div>
      </a>

      <!-- Chapter 02 -->
      <a href="/education/collision-prediction" class="chapter-card reveal reveal-delay-1" style="--ch-color:#34d399;">
        <div class="chapter-card-accent"></div>
        <div class="chapter-card-body">
          <div class="chapter-number">Chapter 02 — Collision Analysis</div>
          <h3 class="chapter-title">Conjunction Prediction &amp; Probability of Collision</h3>
          <p class="chapter-desc">
            How operators screen 350 million possible object pairs daily, compute Time of Closest
            Approach, model covariance ellipsoids, and apply the Foster-Alfano method to estimate
            whether a maneuver is warranted.
          </p>
          <div class="chapter-topics">
            <span class="topic-pill">TCA Algorithm</span>
            <span class="topic-pill">Foster-Alfano Pc</span>
            <span class="topic-pill">CCSDS CDM</span>
            <span class="topic-pill">Covariance Matrix</span>
            <span class="topic-pill">CW Maneuver</span>
          </div>
        </div>
        <div class="chapter-footer">
          <span>~30 min read · 18 equations</span>
          <span class="chapter-read-link">Read chapter</span>
        </div>
      </a>

      <!-- Chapter 03 -->
      <a href="/education/perturbations" class="chapter-card reveal reveal-delay-2" style="--ch-color:#f59e0b;">
        <div class="chapter-card-accent"></div>
        <div class="chapter-card-body">
          <div class="chapter-number">Chapter 03 — Perturbation Theory</div>
          <h3 class="chapter-title">Why Real Orbits Deviate from Kepler</h3>
          <p class="chapter-desc">
            Earth's oblateness (J₂ = 1.08263×10⁻³), atmospheric drag, solar radiation pressure,
            and luni-solar gravity all bend real orbits away from ideal ellipses — and drive
            sun-synchronous design, station-keeping budgets, and TLE accuracy decay.
          </p>
          <div class="chapter-topics">
            <span class="topic-pill">J₂ Oblateness</span>
            <span class="topic-pill">Atmospheric Drag</span>
            <span class="topic-pill">Solar Rad. Pressure</span>
            <span class="topic-pill">RAAN Precession</span>
            <span class="topic-pill">TLE Age Error</span>
          </div>
        </div>
        <div class="chapter-footer">
          <span>~28 min read · 15 equations</span>
          <span class="chapter-read-link">Read chapter</span>
        </div>
      </a>

      <!-- Chapter 04 -->
      <a href="/education/debris-modeling" class="chapter-card reveal reveal-delay-3" style="--ch-color:#f87171;">
        <div class="chapter-card-accent"></div>
        <div class="chapter-card-body">
          <div class="chapter-number">Chapter 04 — Debris Physics</div>
          <h3 class="chapter-title">Debris Modeling &amp; the Kessler Cascade</h3>
          <p class="chapter-desc">
            The NASA Standard Breakup Model, power-law fragment distributions, historical events
            from Fengyun-1C to Iridium-Cosmos, cascade threshold mathematics, Active Debris Removal
            technologies, and IADC mitigation guidelines.
          </p>
          <div class="chapter-topics">
            <span class="topic-pill">NASA SBM</span>
            <span class="topic-pill">Fragment Velocity</span>
            <span class="topic-pill">Cascade Physics</span>
            <span class="topic-pill">ADR Technologies</span>
            <span class="topic-pill">IADC Guidelines</span>
          </div>
        </div>
        <div class="chapter-footer">
          <span>~32 min read · 10 equations</span>
          <span class="chapter-read-link">Read chapter</span>
        </div>
      </a>
    </div>
  </div>
</section>

<div class="section-divider"></div>

<!-- SIMULATION -->
<section id="simulation">
  <div class="section-wrap">
    <div class="sim-header reveal">
      <div class="section-label">// Live Simulation Platform</div>
      <h2 class="section-title">See the math <em>in motion</em></h2>
      <p class="section-body">
        The VectraSpace dashboard runs real SGP4 propagation on live TLE data, screens
        every orbit pair for conjunctions, and visualizes the results on a photorealistic
        CesiumJS globe — all in your browser.
      </p>
    </div>

    <div class="sim-grid">
      <div class="sim-card reveal">
        <span class="sim-card-tag">SGP4 / SDP4</span>
        <div class="sim-card-icon">⚡</div>
        <div class="sim-card-title">Live Propagation</div>
        <p class="sim-card-body">NumPy-vectorized SGP4 propagates thousands of satellites simultaneously across a 12–72 hour window at 1-minute resolution. Regime-specific filters for LEO, MEO, and GEO.</p>
        <div class="sim-card-stat">Step size: <span>60 s</span> · Batch: <span>50 sats</span></div>
      </div>
      <div class="sim-card reveal reveal-delay-1">
        <span class="sim-card-tag green">Conjunction</span>
        <div class="sim-card-icon">🎯</div>
        <div class="sim-card-title">Conjunction Screening</div>
        <p class="sim-card-body">Ellipsoid pre-filter eliminates 95%+ of pairs before refinement. Bounded golden-section search finds exact TCA. Foster-Alfano Pc with real CDM covariance when Space-Track credentials are set.</p>
        <div class="sim-card-stat">Filter rate: <span>~95%</span> · Pc method: <span>Foster-Alfano</span></div>
      </div>
      <div class="sim-card reveal reveal-delay-2">
        <span class="sim-card-tag amber">Debris</span>
        <div class="sim-card-icon">💥</div>
        <div class="sim-card-title">Fragmentation Model</div>
        <p class="sim-card-body">Simulate a collision or explosion using the NASA Standard Breakup Model. Lognormal velocity distributions, isotropic ejection directions, and real conjunction screening of the resulting debris cloud.</p>
        <div class="sim-card-stat">Max fragments: <span>200</span> · Lc range: <span>1–50 cm</span></div>
      </div>
      <div class="sim-card reveal">
        <span class="sim-card-tag">CCSDS CDM</span>
        <div class="sim-card-icon">📄</div>
        <div class="sim-card-title">CDM Export</div>
        <p class="sim-card-body">Standards-compliant Conjunction Data Messages (CCSDS 508.0-B-1) generated per event. Individual download or bulk ZIP. Includes Clohessy-Wiltshire minimum-ΔV maneuver advisory for each conjunction.</p>
        <div class="sim-card-stat">Format: <span>CCSDS 508.0</span> · Maneuver: <span>CW Linear</span></div>
      </div>
      <div class="sim-card reveal reveal-delay-1">
        <span class="sim-card-tag red">Alerting</span>
        <div class="sim-card-icon">🔔</div>
        <div class="sim-card-title">Real-time Alerts</div>
        <p class="sim-card-body">Threshold-based alert routing: email (Gmail, SendGrid, SES, Postmark), Pushover mobile push, and HTTP webhooks. Per-user Pc threshold and miss-distance configuration. Styled HTML email with full conjunction data.</p>
        <div class="sim-card-stat">Channels: <span>4 email + Pushover + webhook</span></div>
      </div>
      <div class="sim-card reveal reveal-delay-2">
        <span class="sim-card-tag">CesiumJS</span>
        <div class="sim-card-icon">🌐</div>
        <div class="sim-card-title">3D Globe Visualization</div>
        <p class="sim-card-body">Photorealistic Cesium World Terrain + Imagery, animated orbital tracks, conjunction markers, time-scrubbing, and adjustable simulation speed. Click any object for satellite info powered by the Anthropic API.</p>
        <div class="sim-card-stat">Engine: <span>CesiumJS 1.114</span> · Mode: <span>WebGL 2</span></div>
      </div>
    </div>

    <!-- Terminal -->
    <div class="sim-terminal reveal" style="margin-top:40px;">
      <div class="sim-terminal-bar">
        <div class="terminal-dots"><span class="td-r"></span><span class="td-y"></span><span class="td-g"></span></div>
        <div class="terminal-title">VectraSpace v11 — Orbital Scan</div>
      </div>
      <div class="sim-terminal-body">
        <span class="tl"><span class="tp">$ </span><span class="tc">python vectraspace.py</span></span>
        <span class="tl"><span class="to">[INFO] Loading environment from .env</span></span>
        <span class="tl"><span class="tok">✓ Space-Track authenticated — 4,812 TLEs downloaded</span></span>
        <span class="tl"><span class="to">  LEO: <span class="tv">3,204</span> · MEO: <span class="tv">891</span> · GEO: <span class="tv">717</span></span></span>
        <span class="tl"> </span>
        <span class="tl"><span class="to">Propagating 170 satellites — 12h @ 1 min resolution</span></span>
        <span class="tl"><span class="tok">✓ Vectorized propagation complete (1,240 timesteps × 170 sats)</span></span>
        <span class="tl"><span class="to">Screening 14,365 pairs — ellipsoid pre-filter active</span></span>
        <span class="tl"><span class="tok">✓ 13,642 pairs rejected (94.9%) — 723 refined</span></span>
        <span class="tl"> </span>
        <span class="tl"><span class="tw">⚠ CONJUNCTION DETECTED</span></span>
        <span class="tl"><span class="to">  STARLINK-4521 ↔ COSMOS-1408 DEB  [LEO/LEO]</span></span>
        <span class="tl"><span class="to">  Miss dist: <span class="tv">3.214 km</span> · Pc: <span class="tw">4.1e-04</span> · TCA: <span class="tv">+2h 14m</span></span></span>
        <span class="tl"><span class="to">  Δv advisory: <span class="tv">0.082 m/s</span> radial · <span class="tv">0.341 m/s</span> transverse</span></span>
        <span class="tl"> </span>
        <span class="tl"><span class="tok">✓ 3 conjunctions found · CDMs generated · Alerts dispatched</span></span>
        <span class="tl"><span class="tp">$ </span><span class="cursor-blink"></span></span>
      </div>
    </div>
  </div>
</section>

<div class="section-divider"></div>

<!-- DATA METRICS -->
<section id="data" style="padding:80px 0;">
  <div class="section-wrap">
    <div class="data-metrics reveal">
      <div class="data-metric">
        <div class="data-metric-glyph">∞</div>
        <span class="data-metric-val" id="count-1">0</span>
        <div class="data-metric-label">Tracked Objects in Catalog</div>
      </div>
      <div class="data-metric c2">
        <div class="data-metric-glyph">⌬</div>
        <span class="data-metric-val" id="count-2">0</span>
        <div class="data-metric-label">Conjunction Screens per Day (global)</div>
      </div>
      <div class="data-metric c3">
        <div class="data-metric-glyph">◎</div>
        <span class="data-metric-val" id="count-3">0</span>
        <div class="data-metric-label">Years to Self-Clear Above 800 km</div>
      </div>
      <div class="data-metric c4">
        <div class="data-metric-glyph">✦</div>
        <span class="data-metric-val" id="count-4">0</span>
        <div class="data-metric-label">kJ Energy: 10 cm Fragment at 10 km/s</div>
      </div>
    </div>
  </div>
</section>

<div class="section-divider"></div>

<!-- CTA -->
<section id="cta">
  <div class="section-wrap">
    <div class="cta-box reveal">
      <div class="cta-glow"></div>
      <div class="cta-eyebrow">⬡ Start Exploring</div>
      <h2 class="cta-title">The cosmos doesn't wait.<br><em>Neither should your education.</em></h2>
      <p class="cta-body">
        Dive into the physics chapters, run a live conjunction scan against 4,000+ active satellites,
        or simulate a debris fragmentation event — all backed by the same math used by real SSA operators.
      </p>
      <div class="cta-buttons">
        <a href="/education/orbital-mechanics" class="btn-primary-hero">Begin Chapter 01</a>
        <a href="/dashboard" class="btn-secondary-hero">Open Live Dashboard</a>
      </div>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer>
  <div class="footer-brand">Vectra<em>Space</em></div>
  <ul class="footer-links">
    <li><a href="/education/orbital-mechanics">Orbital Mechanics</a></li>
    <li><a href="/education/collision-prediction">Collision Prediction</a></li>
    <li><a href="/education/perturbations">Perturbations</a></li>
    <li><a href="/education/debris-modeling">Debris Modeling</a></li>
    <li><a href="/dashboard">Dashboard</a></li>
  </ul>
  <div class="footer-copy">© 2026 VectraSpace · Educational Orbital Platform</div>
</footer>

<script>
// ── STARFIELD ────────────────────────────────────────────────
(function() {
  const container = document.getElementById('starfield');
  for (let i = 0; i < 220; i++) {
    const star = document.createElement('div');
    star.className = 'star';
    const size = Math.random() * 1.8 + 0.4;
    star.style.cssText = `
      width:${size}px; height:${size}px;
      left:${Math.random()*100}%; top:${Math.random()*100}%;
      --a1:${(Math.random()*0.5+0.2).toFixed(2)};
      --a2:${(Math.random()*0.1+0.03).toFixed(2)};
      --dur:${(Math.random()*5+3).toFixed(1)}s;
      --delay:-${(Math.random()*8).toFixed(1)}s;
    `;
    container.appendChild(star);
  }
})();

// ── NAV SCROLL ────────────────────────────────────────────────
const nav = document.getElementById('nav');
window.addEventListener('scroll', () => {
  nav.classList.toggle('scrolled', window.scrollY > 30);
}, { passive: true });

// ── REVEAL ON SCROLL ──────────────────────────────────────────
const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('visible'); });
}, { threshold: 0.08 });
document.querySelectorAll('.reveal').forEach(el => revealObserver.observe(el));

// ── COUNTER ANIMATION ─────────────────────────────────────────
const counters = [
  { id: 'count-1', target: 27000, suffix: '+', format: n => n >= 1000 ? Math.round(n/1000)*1000 : n },
  { id: 'count-2', target: 350, suffix: 'M', format: n => Math.round(n) },
  { id: 'count-3', target: 100, suffix: '+', format: n => Math.round(n) },
  { id: 'count-4', target: 500, suffix: ' kJ', format: n => Math.round(n) },
];
let countersStarted = false;
const counterObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting && !countersStarted) {
      countersStarted = true;
      counters.forEach(({ id, target, suffix, format }) => {
        const el = document.getElementById(id);
        const start = performance.now();
        const dur = 1800;
        function step(now) {
          const t = Math.min((now - start) / dur, 1);
          const ease = 1 - Math.pow(1 - t, 3);
          el.textContent = format(target * ease) + suffix;
          if (t < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      });
    }
  });
}, { threshold: 0.4 });
const metricsEl = document.querySelector('.data-metrics');
if (metricsEl) counterObserver.observe(metricsEl);
</script>
</body>
</html>

"""

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 7 — REST API + SSE RUN ENDPOINT                      ║
# ╚══════════════════════════════════════════════════════════════╝

RESEARCH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VectraSpace — Research Data Portal</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700&family=Exo+2:wght@300;400;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
:root {
  --bg:     #030508;
  --panel:  #070f18;
  --border: #0d2137;
  --accent: #00d4ff;
  --green:  #00ff88;
  --red:    #ff4444;
  --muted:  #3a5a75;
  --text:   #c8dff0;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'Exo 2', sans-serif; min-height: 100vh; }
a { color: var(--accent); }

/* ── HEADER ── */
#header {
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  padding: 20px 40px;
  display: flex; align-items: center; justify-content: space-between;
}
.logo { font-family: 'Orbitron', sans-serif; font-size: 11px; letter-spacing: 4px; color: var(--accent); }
.header-links { display: flex; gap: 20px; font-family: 'Share Tech Mono', monospace; font-size: 10px; letter-spacing: 1px; }
.header-links a { color: var(--muted); text-decoration: none; transition: color 0.2s; }
.header-links a:hover { color: var(--accent); }

/* ── HERO ── */
#hero { padding: 48px 40px 32px; max-width: 1100px; margin: 0 auto; }
#hero h1 { font-family: 'Orbitron', sans-serif; font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 8px; }
#hero p { color: var(--muted); font-size: 13px; line-height: 1.7; max-width: 680px; }
.badge { display: inline-block; background: rgba(0,212,255,0.08); color: var(--accent);
         border: 1px solid rgba(0,212,255,0.3); border-radius: 3px;
         font-family: 'Share Tech Mono', monospace; font-size: 9px;
         letter-spacing: 2px; padding: 3px 8px; margin-right: 8px; }

/* ── MAIN GRID ── */
#main { max-width: 1100px; margin: 0 auto; padding: 0 40px 60px; }
.section { margin-bottom: 40px; }
.section-title {
  font-family: 'Share Tech Mono', monospace; font-size: 10px;
  letter-spacing: 3px; color: var(--accent); text-transform: uppercase;
  margin-bottom: 16px; padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
}
.card {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 6px; padding: 24px;
}
.charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.chart-card { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 20px; }
.chart-title { font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 2px; color: var(--muted); margin-bottom: 14px; }

/* ── STATS ROW ── */
.stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }
.stat-card { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 18px 20px; }
.stat-val { font-family: 'Orbitron', sans-serif; font-size: 26px; color: var(--accent); margin-bottom: 4px; }
.stat-lbl { font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 2px; color: var(--muted); text-transform: uppercase; }

/* ── TABLE ── */
.tbl-wrap { overflow-x: auto; border-radius: 6px; border: 1px solid var(--border); }
table { width: 100%; border-collapse: collapse; font-family: 'Share Tech Mono', monospace; font-size: 10px; }
thead th { background: #060d16; color: var(--muted); letter-spacing: 1px; text-transform: uppercase;
           padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
tbody tr { border-bottom: 1px solid rgba(13,33,55,0.6); transition: background 0.15s; }
tbody tr:hover { background: rgba(0,212,255,0.04); }
tbody td { padding: 9px 14px; color: var(--text); white-space: nowrap; }
.pc-high { color: var(--red); } .pc-med { color: #ffaa44; } .pc-low { color: var(--green); }
.empty-row td { text-align: center; color: var(--muted); padding: 32px; letter-spacing: 2px; }

/* ── EXPORT BUTTONS ── */
.export-row { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 16px; }
.export-btn {
  font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 2px;
  text-transform: uppercase; padding: 8px 16px;
  border: 1px solid var(--accent); border-radius: 3px;
  color: var(--accent); background: transparent; cursor: pointer;
  text-decoration: none; display: inline-block; transition: all 0.2s;
}
.export-btn:hover { background: rgba(0,212,255,0.1); }
.export-btn.green { border-color: var(--green); color: var(--green); }
.export-btn.green:hover { background: rgba(0,255,136,0.08); }

/* ── LOADING ── */
.loading { color: var(--muted); font-family: 'Share Tech Mono', monospace; font-size: 10px;
           letter-spacing: 2px; padding: 32px; text-align: center; }

@media (max-width: 700px) {
  #header { padding: 16px 20px; }
  #hero, #main { padding-left: 20px; padding-right: 20px; }
  .charts-grid, .stats-row { grid-template-columns: 1fr; }
}
</style>

<script defer src="https://cloud.umami.is/script.js" data-website-id="4e12fc04-8b26-4e42-8b69-0700a95c7d30"></script>
</head>
<body>

<div id="header">
  <div>
    <div class="logo">VectraSpace // Research Portal</div>
    <div style="font-size:10px;color:var(--muted);margin-top:4px;font-family:'Share Tech Mono',monospace;">
      Public Data Access — No Authentication Required
    </div>
  </div>
  <div class="header-links">
    <a href="/">← Landing</a>
    <a href="/dashboard">Mission Control</a>
  </div>
</div>

<div id="hero">
  <div style="margin-bottom:12px;">
    <span class="badge">OPEN DATA</span>
    <span class="badge" style="color:var(--green);border-color:rgba(0,255,136,0.3);background:rgba(0,255,136,0.05);">UTD CSS COLLABORATION</span>
  </div>
  <h1>Orbital Conjunction Research Portal</h1>
  <p>Real-time and historical conjunction data from VectraSpace's SGP4 propagation engine. All data is derived from public TLE catalogs via CelesTrak. Probability of collision estimates use the Alfriend-Akella covariance model. For research inquiries contact <a href="mailto:trumanheaston@gmail.com">trumanheaston@gmail.com</a>.</p>
</div>

<div id="main">

  <!-- Stats -->
  <div class="stats-row" id="stats-row">
    <div class="stat-card"><div class="stat-val" id="stat-total-conj">—</div><div class="stat-lbl">Total Conjunctions</div></div>
    <div class="stat-card"><div class="stat-val" id="stat-high-risk">—</div><div class="stat-lbl">High Risk (Pc &gt; 1e-4)</div></div>
    <div class="stat-card"><div class="stat-val" id="stat-sats">—</div><div class="stat-lbl">Satellites Tracked</div></div>
    <div class="stat-card"><div class="stat-val" id="stat-last-scan">—</div><div class="stat-lbl">Last Scan</div></div>
  </div>

  <!-- Charts -->
  <div class="section">
    <div class="section-title">Orbital Analysis</div>
    <div class="charts-grid">
      <div class="chart-card">
        <div class="chart-title">PROBABILITY OF COLLISION DISTRIBUTION</div>
        <canvas id="chart-pc" height="200"></canvas>
      </div>
      <div class="chart-card">
        <div class="chart-title">MISS DISTANCE DISTRIBUTION (KM)</div>
        <canvas id="chart-dist" height="200"></canvas>
      </div>
      <div class="chart-card">
        <div class="chart-title">CONJUNCTIONS OVER TIME</div>
        <canvas id="chart-time" height="200"></canvas>
      </div>
      <div class="chart-card">
        <div class="chart-title">RELATIVE VELOCITY AT TCA (KM/S)</div>
        <canvas id="chart-vel" height="200"></canvas>
      </div>
    </div>
  </div>

  <!-- Conjunction Table -->
  <div class="section">
    <div class="section-title">
      <span>Conjunction Events</span>
      <div class="export-row" style="margin-top:0;">
        <a class="export-btn" onclick="exportCSV()">↓ Export CSV</a>
        <a class="export-btn green" onclick="exportJSON()">↓ Export JSON</a>
      </div>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Object 1</th>
            <th>Object 2</th>
            <th>TCA (UTC)</th>
            <th>Miss Distance (km)</th>
            <th>Pc Estimate</th>
            <th>Rel. Velocity (km/s)</th>
            <th>CDM</th>
          </tr>
        </thead>
        <tbody id="conj-tbody">
          <tr class="empty-row"><td colspan="8"><div class="loading">LOADING CONJUNCTION DATA...</div></td></tr>
        </tbody>
      </table>
    </div>
    <div style="margin-top:10px;font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);">
      Data refreshes on each scan execution. All times UTC. Pc values are estimates — not certified for operational use.
    </div>
  </div>

  <!-- TLE Export -->
  <div class="section">
    <div class="section-title">TLE Data Export</div>
    <div class="card">
      <div style="font-size:12px;color:var(--muted);margin-bottom:16px;line-height:1.7;">
        Export the current TLE catalog used in the last scan. Data sourced from CelesTrak active satellite catalog.
        Format conforms to NORAD two-line element set standard (BSTAR drag term, epoch, mean motion, eccentricity).
      </div>
      <div class="export-row">
        <a class="export-btn" href="/research/tle.json" download>↓ TLE Export (JSON)</a>
        <a class="export-btn green" href="/research/tle.csv" download>↓ TLE Export (CSV)</a>
      </div>
    </div>
  </div>

  <!-- Methodology -->
  <div class="section">
    <div class="section-title">Methodology</div>
    <div class="card" style="display:grid;grid-template-columns:1fr 1fr;gap:32px;">
      <div>
        <div style="font-family:'Share Tech Mono',monospace;font-size:9px;letter-spacing:2px;color:var(--accent);margin-bottom:10px;">PROPAGATION</div>
        <div style="font-size:12px;color:var(--muted);line-height:1.8;">
          SGP4/SDP4 orbital propagation via the <strong style="color:var(--text);">Skyfield</strong> library.
          Positions computed at 1-minute intervals over a 24-hour window.
          Vectorized chunk-based screening using NumPy for O(n²) pair comparisons.
        </div>
      </div>
      <div>
        <div style="font-family:'Share Tech Mono',monospace;font-size:9px;letter-spacing:2px;color:var(--accent);margin-bottom:10px;">COLLISION PROBABILITY</div>
        <div style="font-size:12px;color:var(--muted);line-height:1.8;">
          Pc estimates use the <strong style="color:var(--text);">Alfriend-Akella</strong> method with 
          combined error covariance ellipsoids (1σ along-track: 100m, cross-track: 20m, radial: 20m).
          Refined via golden-section search for TCA.
        </div>
      </div>
      <div>
        <div style="font-family:'Share Tech Mono',monospace;font-size:9px;letter-spacing:2px;color:var(--accent);margin-bottom:10px;">DATA SOURCES</div>
        <div style="font-size:12px;color:var(--muted);line-height:1.8;">
          TLE data from <strong style="color:var(--text);">CelesTrak</strong> active satellite catalog.
          Optional Space-Track.org integration for additional orbital elements.
          Conjunction data messages (CDM) generated per CCSDS 508.0-B-1 standard.
        </div>
      </div>
      <div>
        <div style="font-family:'Share Tech Mono',monospace;font-size:9px;letter-spacing:2px;color:var(--accent);margin-bottom:10px;">LIMITATIONS</div>
        <div style="font-size:12px;color:var(--muted);line-height:1.8;">
          TLE accuracy degrades over time. Covariance values are assumed, not measured.
          Pc values should be treated as <strong style="color:var(--text);">screening indicators</strong> only.
          Not validated for operational conjunction assessment.
        </div>
      </div>
    </div>
  </div>

</div>

<script>
let conjData = [];

const CHART_DEFAULTS = {
  color: '#00d4ff',
  plugins: { legend: { display: false } },
  scales: {
    x: { ticks: { color: '#3a5a75', font: { family: 'Share Tech Mono', size: 9 } }, grid: { color: '#0d2137' } },
    y: { ticks: { color: '#3a5a75', font: { family: 'Share Tech Mono', size: 9 } }, grid: { color: '#0d2137' } }
  }
};

async function loadData() {
  try {
    const res = await fetch('/conjunctions');
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    conjData = data.conjunctions || data || [];
    renderStats(conjData);
    renderTable(conjData);
    renderCharts(conjData);
  } catch(e) {
    document.getElementById('conj-tbody').innerHTML =
      '<tr class="empty-row"><td colspan="8">No conjunction data available — run a scan first.</td></tr>';
  }
}

function renderStats(data) {
  document.getElementById('stat-total-conj').textContent = data.length;
  const highRisk = data.filter(c => (c.pc_estimate || c.pc || 0) > 1e-4).length;
  document.getElementById('stat-high-risk').textContent = highRisk;
  const sats = new Set();
  data.forEach(c => { sats.add(c.sat1||c.name1||c.object1); sats.add(c.sat2||c.name2||c.object2); });
  document.getElementById('stat-sats').textContent = sats.size;
  if (data.length > 0) {
    const times = data.map(c => c.tca_utc || c.time || c.epoch).filter(Boolean);
    if (times.length) document.getElementById('stat-last-scan').textContent = times[0].slice(0,10);
  }
}

function pcClass(pc) {
  if (!pc || pc < 1e-6) return 'pc-low';
  if (pc < 1e-4) return 'pc-med';
  return 'pc-high';
}

function fmtPc(pc) {
  if (!pc || pc === 0) return '<1e-8';
  return pc.toExponential(2);
}

function renderTable(data) {
  const tbody = document.getElementById('conj-tbody');
  if (!data.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">No conjunctions recorded yet.</td></tr>';
    return;
  }
  tbody.innerHTML = data.slice(0,200).map((c,i) => {
    const pc = c.pc_estimate ?? c.pc ?? 0;
    const dist = (c.min_dist_km ?? c.miss_distance ?? 0).toFixed(3);
    const vel = (c.v_rel ?? c.relative_velocity ?? 0).toFixed(2);
    const tca = c.tca_utc ?? c.time ?? '—';
    const s1 = c.sat1 ?? c.name1 ?? c.object1 ?? '—';
    const s2 = c.sat2 ?? c.name2 ?? c.object2 ?? '—';
    const cdmLink = c.cdm_index !== undefined
      ? `<a href="/cdm/${c.cdm_index}" style="color:var(--accent);font-size:9px;">↓ CDM</a>`
      : '—';
    return `<tr>
      <td style="color:var(--muted)">${i+1}</td>
      <td>${s1}</td><td>${s2}</td>
      <td style="color:var(--muted)">${tca}</td>
      <td>${dist}</td>
      <td class="${pcClass(pc)}">${fmtPc(pc)}</td>
      <td>${vel}</td>
      <td>${cdmLink}</td>
    </tr>`;
  }).join('');
}

function renderCharts(data) {
  if (!data.length) return;

  // Pc distribution
  const pcBins = [0,0,0,0,0]; // <1e-8, 1e-8..1e-6, 1e-6..1e-4, 1e-4..1e-2, >1e-2
  const pcLabels = ['<1e-8','1e-8 to\n1e-6','1e-6 to\n1e-4','1e-4 to\n1e-2','>1e-2'];
  data.forEach(c => {
    const pc = c.pc_estimate ?? c.pc ?? 0;
    if (pc < 1e-8) pcBins[0]++;
    else if (pc < 1e-6) pcBins[1]++;
    else if (pc < 1e-4) pcBins[2]++;
    else if (pc < 1e-2) pcBins[3]++;
    else pcBins[4]++;
  });
  new Chart(document.getElementById('chart-pc'), {
    type: 'bar',
    data: { labels: pcLabels, datasets: [{ data: pcBins,
      backgroundColor: ['#00ff8844','#44aaff44','#ffaa4444','#ff666644','#ff444444'],
      borderColor:      ['#00ff88',  '#44aaff',  '#ffaa44',  '#ff6666',  '#ff4444'],
      borderWidth: 1 }]},
    options: { ...CHART_DEFAULTS, responsive: true }
  });

  // Miss distance histogram
  const distBins = new Array(10).fill(0);
  const maxDist = Math.max(...data.map(c => c.min_dist_km ?? 0), 10);
  data.forEach(c => {
    const d = c.min_dist_km ?? 0;
    const bin = Math.min(Math.floor(d / maxDist * 10), 9);
    distBins[bin]++;
  });
  const distLabels = distBins.map((_,i) => `${(i*maxDist/10).toFixed(0)}-${((i+1)*maxDist/10).toFixed(0)}`);
  new Chart(document.getElementById('chart-dist'), {
    type: 'bar',
    data: { labels: distLabels, datasets: [{ data: distBins,
      backgroundColor: '#00d4ff22', borderColor: '#00d4ff', borderWidth: 1 }]},
    options: { ...CHART_DEFAULTS, responsive: true }
  });

  // Conjunctions over time
  const timeCounts = {};
  data.forEach(c => {
    const t = (c.tca_utc ?? c.time ?? '').slice(0,10);
    if (t) timeCounts[t] = (timeCounts[t]||0)+1;
  });
  const timeKeys = Object.keys(timeCounts).sort();
  new Chart(document.getElementById('chart-time'), {
    type: 'line',
    data: { labels: timeKeys, datasets: [{ data: timeKeys.map(k=>timeCounts[k]),
      borderColor: '#00ff88', backgroundColor: '#00ff8811', fill: true,
      tension: 0.3, pointRadius: 3 }]},
    options: { ...CHART_DEFAULTS, responsive: true }
  });

  // Relative velocity histogram
  const velBins = new Array(10).fill(0);
  const maxVel = Math.max(...data.map(c => c.v_rel ?? 0), 15);
  data.forEach(c => {
    const v = c.v_rel ?? 0;
    const bin = Math.min(Math.floor(v / maxVel * 10), 9);
    velBins[bin]++;
  });
  const velLabels = velBins.map((_,i) => `${(i*maxVel/10).toFixed(1)}-${((i+1)*maxVel/10).toFixed(1)}`);
  new Chart(document.getElementById('chart-vel'), {
    type: 'bar',
    data: { labels: velLabels, datasets: [{ data: velBins,
      backgroundColor: '#aa88ff22', borderColor: '#aa88ff', borderWidth: 1 }]},
    options: { ...CHART_DEFAULTS, responsive: true }
  });
}

function exportCSV() {
  if (!conjData.length) return;
  const hdr = 'index,object1,object2,tca_utc,miss_dist_km,pc_estimate,rel_velocity_kms\n';
  const rows = conjData.map((c,i) => [
    i+1,
    c.sat1 ?? c.name1 ?? '', c.sat2 ?? c.name2 ?? '',
    c.tca_utc ?? c.time ?? '',
    (c.min_dist_km ?? 0).toFixed(4),
    (c.pc_estimate ?? c.pc ?? 0).toExponential(4),
    (c.v_rel ?? 0).toFixed(4)
  ].join(',')).join('\n');
  const blob = new Blob([hdr+rows], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `vectraspace_conjunctions_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
}

function exportJSON() {
  if (!conjData.length) return;
  const blob = new Blob([JSON.stringify(conjData, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `vectraspace_conjunctions_${new Date().toISOString().slice(0,10)}.json`;
  a.click();
}

loadData();
</script>
</body>
</html>"""

ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VectraSpace — Admin</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@700;900&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #030508; --panel: #07101a; --border: #0d2137;
    --accent: #00d4ff; --accent2: #ff4444; --accent3: #00ff88;
    --text: #c8dff0; --muted: #3a5a75;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'DM Sans', sans-serif;
         min-height: 100vh; }

  /* ── NAV ── */
  .admin-nav {
    background: var(--panel); border-bottom: 1px solid var(--border);
    padding: 0 32px; height: 56px; display: flex; align-items: center;
    justify-content: space-between; position: sticky; top: 0; z-index: 10;
  }
  .admin-nav-logo { font-family: 'Orbitron', sans-serif; font-size: 13px;
    font-weight: 700; color: #fff; letter-spacing: 2px; text-decoration: none; }
  .admin-nav-logo span { color: var(--accent); }
  .admin-nav-badge { font-family: 'Share Tech Mono', monospace; font-size: 9px;
    letter-spacing: 3px; color: var(--accent2); background: rgba(255,68,68,0.1);
    border: 1px solid rgba(255,68,68,0.3); padding: 3px 10px; border-radius: 2px;
    text-transform: uppercase; }
  .admin-nav-links { display: flex; gap: 20px; align-items: center; }
  .admin-nav-links a { font-family: 'Share Tech Mono', monospace; font-size: 10px;
    letter-spacing: 1px; color: var(--muted); text-decoration: none;
    text-transform: uppercase; transition: color 0.2s; }
  .admin-nav-links a:hover { color: var(--accent); }

  /* ── LAYOUT ── */
  .admin-wrap { max-width: 1280px; margin: 0 auto; padding: 32px 24px; }

  /* ── STAT CARDS ── */
  .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
    margin-bottom: 32px; }
  .stat-card { background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 24px 20px; position: relative; overflow: hidden;
    transition: border-color 0.2s; }
  .stat-card:hover { border-color: var(--accent); }
  .stat-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0;
    height: 2px; background: var(--accent); transform: scaleX(0);
    transition: transform 0.3s; }
  .stat-card:hover::before { transform: scaleX(1); }
  .stat-card.c2::before { background: var(--accent3); }
  .stat-card.c3::before { background: #ffaa44; }
  .stat-card.c4::before { background: #aa66ff; }
  .stat-label { font-family: 'Share Tech Mono', monospace; font-size: 8px;
    letter-spacing: 3px; color: var(--muted); text-transform: uppercase;
    margin-bottom: 10px; }
  .stat-num { font-family: 'Orbitron', sans-serif; font-size: 36px; font-weight: 900;
    color: var(--accent); line-height: 1; margin-bottom: 4px; }
  .stat-card.c2 .stat-num { color: var(--accent3); }
  .stat-card.c3 .stat-num { color: #ffaa44; }
  .stat-card.c4 .stat-num { color: #aa66ff; }
  .stat-sub { font-size: 11px; color: var(--muted); }

  /* ── SECTION HEADERS ── */
  .section-hdr { display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 14px; margin-top: 32px; }
  .section-hdr h2 { font-family: 'Share Tech Mono', monospace; font-size: 10px;
    letter-spacing: 3px; color: var(--accent); text-transform: uppercase; }
  .section-hdr .refresh-btn { background: transparent; border: 1px solid var(--border);
    border-radius: 3px; color: var(--muted); font-family: 'Share Tech Mono', monospace;
    font-size: 9px; padding: 4px 10px; cursor: pointer; letter-spacing: 1px;
    transition: all 0.2s; }
  .section-hdr .refresh-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* ── CHARTS ROW ── */
  .charts-row { display: grid; grid-template-columns: 2fr 1fr; gap: 12px;
    margin-bottom: 12px; }
  .chart-card { background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 20px; }
  .chart-title { font-family: 'Share Tech Mono', monospace; font-size: 9px;
    letter-spacing: 2px; color: var(--muted); text-transform: uppercase;
    margin-bottom: 16px; }
  .chart-wrap { position: relative; height: 180px; }

  /* ── USERS TABLE ── */
  .table-wrap { background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; overflow: hidden; margin-bottom: 12px; }
  .data-table { width: 100%; border-collapse: collapse; }
  .data-table th { background: #040a10; font-family: 'Share Tech Mono', monospace;
    font-size: 8px; letter-spacing: 2px; color: var(--muted); text-transform: uppercase;
    padding: 10px 16px; text-align: left; border-bottom: 1px solid var(--border); }
  .data-table td { padding: 10px 16px; font-size: 12px; border-bottom: 1px solid #0a1520;
    color: var(--text); transition: background 0.15s; }
  .data-table tr:last-child td { border-bottom: none; }
  .data-table tr:hover td { background: #0a1520; }
  .data-table .td-mono { font-family: 'Share Tech Mono', monospace; font-size: 11px; }
  .role-badge { font-family: 'Share Tech Mono', monospace; font-size: 8px;
    letter-spacing: 1px; padding: 2px 8px; border-radius: 2px; text-transform: uppercase; }
  .role-admin { background: rgba(255,68,68,0.12); color: var(--accent2);
    border: 1px solid rgba(255,68,68,0.3); }
  .role-operator { background: rgba(0,212,255,0.08); color: var(--accent);
    border: 1px solid rgba(0,212,255,0.25); }
  .status-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    margin-right: 6px; }
  .status-ok { background: var(--accent3); box-shadow: 0 0 4px var(--accent3); }
  .status-pending { background: #ffaa44; }

  /* ── SCANS TABLE ── */
  .dist-crit { color: var(--accent2); font-weight: 700; }
  .dist-warn { color: #ffaa44; }
  .dist-ok   { color: var(--accent3); }

  /* ── ANALYTICS EMBED ── */
  .analytics-card { background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 24px; margin-bottom: 12px; }
  .analytics-placeholder { text-align: center; padding: 40px 20px; }
  .analytics-placeholder .icon { font-size: 32px; margin-bottom: 12px; }
  .analytics-placeholder p { font-family: 'Share Tech Mono', monospace; font-size: 10px;
    letter-spacing: 2px; color: var(--muted); text-transform: uppercase; margin-bottom: 8px; }
  .analytics-placeholder a { color: var(--accent); font-size: 11px; }
  .umami-script-box { background: #040a10; border: 1px solid var(--border);
    border-radius: 4px; padding: 12px 16px; margin-top: 16px; font-family: 'Share Tech Mono', monospace;
    font-size: 10px; color: var(--accent3); word-break: break-all; text-align: left;
    cursor: pointer; transition: border-color 0.2s; }
  .umami-script-box:hover { border-color: var(--accent); }
  .umami-script-box::before { content: '// Click to copy'; display: block;
    font-size: 8px; color: var(--muted); letter-spacing: 2px; margin-bottom: 6px; }

  /* ── EMPTY STATE ── */
  .empty { text-align: center; padding: 32px; font-family: 'Share Tech Mono', monospace;
    font-size: 10px; color: var(--muted); letter-spacing: 2px; text-transform: uppercase; }

  /* ── RESPONSIVE ── */
  @media (max-width: 900px) {
    .stat-grid { grid-template-columns: repeat(2, 1fr); }
    .charts-row { grid-template-columns: 1fr; }
    .admin-wrap { padding: 20px 16px; }
    .data-table td, .data-table th { padding: 8px 12px; }
  }
  @media (max-width: 480px) {
    .stat-grid { grid-template-columns: repeat(2, 1fr); }
    .admin-nav { padding: 0 16px; }
    .admin-nav-logo { font-size: 11px; }
  }
</style>

<script defer src="https://cloud.umami.is/script.js" data-website-id="4e12fc04-8b26-4e42-8b69-0700a95c7d30"></script>
</head>
<body>

<nav class="admin-nav">
  <a href="/" class="admin-nav-logo">VECTRA<span>SPACE</span></a>
  <span class="admin-nav-badge">⬡ Admin Console</span>
  <div class="admin-nav-links">
    <a href="/dashboard">Dashboard</a>
    <a href="/preferences">Prefs</a>
    <a href="/logout">Sign Out</a>
  </div>
</nav>

<div class="admin-wrap">

  <!-- ── STAT CARDS ── -->
  <div class="stat-grid" id="stat-grid">
    <div class="stat-card"><div class="stat-label">Total Users</div><div class="stat-num" id="stat-users">—</div><div class="stat-sub">registered accounts</div></div>
    <div class="stat-card c2"><div class="stat-label">Total Scans</div><div class="stat-num" id="stat-scans">—</div><div class="stat-sub">pipeline runs</div></div>
    <div class="stat-card c3"><div class="stat-label">Conjunctions Found</div><div class="stat-num" id="stat-conj">—</div><div class="stat-sub">all time</div></div>
    <div class="stat-card c4"><div class="stat-label">New Users (7d)</div><div class="stat-num" id="stat-new">—</div><div class="stat-sub">last 7 days</div></div>
  </div>

  <!-- ── CHARTS ── -->
  <div class="section-hdr">
    <h2>Activity</h2>
    <button class="refresh-btn" onclick="loadAdmin()">↺ Refresh</button>
  </div>
  <div class="charts-row">
    <div class="chart-card">
      <div class="chart-title">Scans per Day (30d)</div>
      <div class="chart-wrap"><canvas id="chart-scans"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="chart-title">Conjunctions by Regime</div>
      <div class="chart-wrap"><canvas id="chart-regimes"></canvas></div>
    </div>
  </div>

  <!-- ── USERS TABLE ── -->
  <div class="section-hdr" style="margin-top:28px;">
    <h2>Registered Users</h2>
    <span id="users-count" style="font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:2px;"></span>
  </div>
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th>Username</th>
          <th>Email</th>
          <th>Role</th>
          <th>Status</th>
          <th>Joined</th>
          <th>Scans</th>
        </tr>
      </thead>
      <tbody id="users-tbody">
        <tr><td colspan="6" class="empty">Loading...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- ── RECENT SCANS TABLE ── -->
  <div class="section-hdr">
    <h2>Recent Conjunction Events</h2>
    <span id="scans-count" style="font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:2px;"></span>
  </div>
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th>Time (UTC)</th>
          <th>User</th>
          <th>Sat 1</th>
          <th>Sat 2</th>
          <th>Regimes</th>
          <th>Miss Dist</th>
          <th>Pc</th>
        </tr>
      </thead>
      <tbody id="scans-tbody">
        <tr><td colspan="7" class="empty">Loading...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- ── ANALYTICS ── -->
  <div class="section-hdr">
    <h2>Website Analytics</h2>
  </div>
  <div class="analytics-card">
    <div id="analytics-section">
      <div class="analytics-placeholder">
        <div class="icon">📊</div>
        <p>Umami Analytics Not Configured</p>
        <p style="font-size:10px;color:var(--text);opacity:0.6;margin:8px 0 16px;font-family:DM Sans,sans-serif;letter-spacing:0;">
          Add free website analytics in 2 minutes. Tracks visits, pageviews, countries, devices — with no cookies or GDPR issues.
        </p>
        <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">
          <a href="https://cloud.umami.is/signup" target="_blank"
             style="font-family:'Share Tech Mono',monospace;font-size:10px;letter-spacing:2px;
                    padding:10px 20px;border:1px solid var(--accent);border-radius:3px;
                    color:var(--accent);text-decoration:none;text-transform:uppercase;
                    transition:all 0.2s;"
             onmouseover="this.style.background='rgba(0,212,255,0.1)'"
             onmouseout="this.style.background='transparent'">
            → Create Free Umami Account
          </a>
          <a href="https://analytics.google.com" target="_blank"
             style="font-family:'Share Tech Mono',monospace;font-size:10px;letter-spacing:2px;
                    padding:10px 20px;border:1px solid var(--border);border-radius:3px;
                    color:var(--muted);text-decoration:none;text-transform:uppercase;
                    transition:all 0.2s;"
             onmouseover="this.style.background='rgba(255,255,255,0.03)'"
             onmouseout="this.style.background='transparent'">
            → Use Google Analytics
          </a>
        </div>
        <div class="umami-script-box" onclick="copyUmamiInstructions()" id="umami-box">
Once you have your Umami script tag, add UMAMI_SCRIPT_URL and UMAMI_WEBSITE_ID to your Render environment variables, then redeploy. VectraSpace will inject it automatically into every page.
        </div>
      </div>
    </div>
  </div>

</div><!-- /admin-wrap -->

<script>
let chartScans = null;
let chartRegimes = null;

async function loadAdmin() {
  try {
    const res = await fetch('/admin/data');
    if (res.status === 403) {
      document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:Share Tech Mono,monospace;color:#ff4444;letter-spacing:3px;">ACCESS DENIED — ADMIN ONLY</div>';
      return;
    }
    const d = await res.json();

    // Stat cards
    document.getElementById('stat-users').textContent  = d.total_users;
    document.getElementById('stat-scans').textContent  = d.total_scan_runs;
    document.getElementById('stat-conj').textContent   = d.total_conjunctions;
    document.getElementById('stat-new').textContent    = d.new_users_7d;

    // Users table
    document.getElementById('users-count').textContent = d.users.length + ' TOTAL';
    const tbody = document.getElementById('users-tbody');
    if (!d.users.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No users yet</td></tr>';
    } else {
      tbody.innerHTML = d.users.map(u => {
        const joined = u.created_at ? u.created_at.slice(0,10) : '—';
        const roleClass = u.role === 'admin' ? 'role-admin' : 'role-operator';
        const statusDot = u.approved !== false
          ? '<span class="status-dot status-ok"></span>Active'
          : '<span class="status-dot status-pending"></span>Pending';
        return `<tr>
          <td class="td-mono">${u.username}</td>
          <td style="color:var(--muted);font-size:11px;">${u.email || '—'}</td>
          <td><span class="role-badge ${roleClass}">${u.role}</span></td>
          <td style="font-size:11px;">${statusDot}</td>
          <td class="td-mono" style="color:var(--muted);">${joined}</td>
          <td class="td-mono" style="color:var(--accent);">${u.scan_count || 0}</td>
        </tr>`;
      }).join('');
    }

    // Recent conjunctions table
    document.getElementById('scans-count').textContent = d.recent_conjunctions.length + ' RECENT';
    const stbody = document.getElementById('scans-tbody');
    if (!d.recent_conjunctions.length) {
      stbody.innerHTML = '<tr><td colspan="7" class="empty">No scans yet</td></tr>';
    } else {
      stbody.innerHTML = d.recent_conjunctions.map(c => {
        const distClass = c.min_dist_km < 1 ? 'dist-crit' : c.min_dist_km < 5 ? 'dist-warn' : 'dist-ok';
        const t = (c.run_time || '').slice(0,16).replace('T',' ');
        return `<tr>
          <td class="td-mono" style="color:var(--muted);font-size:10px;">${t}</td>
          <td class="td-mono" style="color:var(--accent);">${c.user_id || 'anon'}</td>
          <td class="td-mono">${c.sat1}</td>
          <td class="td-mono">${c.sat2}</td>
          <td style="font-size:10px;color:var(--muted);">${c.regime1}/${c.regime2}</td>
          <td class="td-mono ${distClass}">${Number(c.min_dist_km).toFixed(2)} km</td>
          <td class="td-mono" style="color:#ffaa44;">${Number(c.pc_estimate).toExponential(1)}</td>
        </tr>`;
      }).join('');
    }

    // Charts
    const scanCtx = document.getElementById('chart-scans').getContext('2d');
    if (chartScans) chartScans.destroy();
    chartScans = new Chart(scanCtx, {
      type: 'bar',
      data: {
        labels: d.daily_scans.map(x => x.day).reverse(),
        datasets: [{
          label: 'Scan Runs',
          data: d.daily_scans.map(x => x.count).reverse(),
          backgroundColor: 'rgba(0,212,255,0.25)',
          borderColor: '#00d4ff',
          borderWidth: 1,
          borderRadius: 2,
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#3a5a75', font: { size: 8, family: 'Share Tech Mono' }, maxTicksLimit: 10 }, grid: { color: '#0d2137' } },
          y: { ticks: { color: '#3a5a75', font: { size: 8 } }, grid: { color: '#0d2137' }, beginAtZero: true }
        }
      }
    });

    const regCtx = document.getElementById('chart-regimes').getContext('2d');
    if (chartRegimes) chartRegimes.destroy();
    chartRegimes = new Chart(regCtx, {
      type: 'doughnut',
      data: {
        labels: d.regime_breakdown.map(x => x.pair),
        datasets: [{
          data: d.regime_breakdown.map(x => x.count),
          backgroundColor: ['#4da6ff','#ff6b6b','#00ff88','#ffaa44','#aa66ff','#00d4ff'],
          borderColor: '#07101a', borderWidth: 2,
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { color: '#3a5a75', font: { family: 'Share Tech Mono', size: 8 }, boxWidth: 10, padding: 8 } } }
      }
    });

  } catch(e) {
    console.error('Admin load failed:', e);
  }
}

function copyUmamiInstructions() {
  const text = 'UMAMI_SCRIPT_URL=https://cloud.umami.is/script.js\nUMEAMI_WEBSITE_ID=your-website-id-here';
  navigator.clipboard.writeText(text).then(() => {
    const box = document.getElementById('umami-box');
    box.style.borderColor = 'var(--accent3)';
    setTimeout(() => box.style.borderColor = '', 1500);
  });
}

loadAdmin();
</script>
</body>
</html>"""

def build_api(cfg: Config):
    if not HAS_FASTAPI:
        log.warning("FastAPI not installed — API disabled. pip install fastapi uvicorn")
        return None

    import asyncio
    import json as _json
    import math
    import concurrent.futures
    from fastapi import FastAPI, Request, Depends
    from fastapi.responses import (JSONResponse, HTMLResponse,
                                   StreamingResponse, PlainTextResponse,
                                   RedirectResponse)

    # ── Background auto-scan ─────────────────────────────────────────────────
    _scan_state: dict = {"time": 0, "running": False, "count": 0}
    AUTO_SCAN_INTERVAL_H = 6

    async def _auto_scan_loop():
        import asyncio as _aio, functools as _fc
        await _aio.sleep(90)  # 90s grace after startup
        while True:
            if not _scan_state["running"]:
                try:
                    _scan_state["running"] = True
                    log.info("[auto-scan] Running scheduled scan...")
                    loop = _aio.get_event_loop()
                    result = await loop.run_in_executor(
                        None, _fc.partial(_run_pipeline, cfg,
                                          run_mode="scheduled", user_id="__auto__"))
                    _scan_state["count"] = len(result.get("conjunctions", []))
                    _scan_state["time"]  = time.time()
                    log.info(f"[auto-scan] Done — {_scan_state['count']} conjunctions")
                except Exception as _e:
                    log.warning(f"[auto-scan] Error: {_e}")
                finally:
                    _scan_state["running"] = False
            await _aio.sleep(AUTO_SCAN_INTERVAL_H * 3600)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app):
        import asyncio as _aio
        task = _aio.create_task(_auto_scan_loop())
        log.info("[startup] Auto-scan task started (every 6h)")
        yield
        task.cancel()

    app = FastAPI(
        title="VectraSpace API",
        description="VectraSpace v11 — Orbital Safety Platform",
        version="11.0",
        lifespan=_lifespan,
    )

    # ── CORS ─────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # ── Security headers ──────────────────────────────────────────
    class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            return response
    app.add_middleware(_SecurityHeadersMiddleware)

    # ── Global IP-based rate limiter ──────────────────────────────
    _ip_hits: dict = {}
    _IP_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))

    class _RateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            ip = (request.client.host or "0.0.0.0")
            now = time.time()
            _ip_hits[ip] = [t for t in _ip_hits.get(ip, []) if now - t < 60]
            if len(_ip_hits[ip]) >= _IP_LIMIT:
                return JSONResponse({"detail": "Rate limit exceeded — try again shortly."}, status_code=429)
            _ip_hits[ip].append(now)
            return await call_next(request)
    app.add_middleware(_RateLimitMiddleware)

    # ── In-memory demo result cache (public/unauthenticated view) ─
    app._demo_result = None   # most recent public (user_id=None) run
    app._user_results = {}    # username -> last result

    # ── Helper: get current user from cookie ──────────────────
    def _get_user(request: Request) -> Optional[dict]:
        return get_current_user_from_request(request, cfg)

    # ── Landing page (public marketing page) ─────────────────
    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request):
        # If user already has a valid session, redirect to dashboard
        user = get_current_user_from_request(request, cfg)
        if user:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/dashboard", status_code=302)
        return HTMLResponse(content=LANDING_HTML)

    @app.get("/welcome", response_class=HTMLResponse)
    def landing_welcome():
        """Always shows landing page — used by dashboard Home button."""
        return HTMLResponse(content=LANDING_HTML)

    @app.get("/research", response_class=HTMLResponse)
    def research_page():
        """Public research data portal — no auth required."""
        return HTMLResponse(content=RESEARCH_HTML)

    @app.get("/research/tle.json")
    def research_tle_json():
        """Export current TLE catalog as JSON."""
        import json as _j
        tle_path = Path("catalog.json")
        if tle_path.exists():
            try:
                return JSONResponse(_j.loads(tle_path.read_text()))
            except Exception:
                pass
        return JSONResponse({"error": "No TLE data available — run a scan first."}, status_code=404)

    @app.get("/research/tle.csv")
    def research_tle_csv():
        """Export current TLE catalog as CSV."""
        from fastapi.responses import PlainTextResponse
        import json as _j
        tle_path = Path("catalog.json")
        if tle_path.exists():
            try:
                data = _j.loads(tle_path.read_text())
                lines = ["name,line1,line2"]
                for entry in (data if isinstance(data, list) else []):
                    name  = str(entry.get("name","")).replace(",","")
                    line1 = str(entry.get("line1","")).replace(",","")
                    line2 = str(entry.get("line2","")).replace(",","")
                    lines.append(f"{name},{line1},{line2}")
                return PlainTextResponse("\n".join(lines), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=vectraspace_tle.csv"})
            except Exception as e:
                pass
        return PlainTextResponse("No TLE data available", status_code=404)

    # ── Dashboard UI ──────────────────────────────────────────
    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse(content=get_dashboard_html())

    # ── /me — current user info ───────────────────────────────
    @app.get("/me")
    def me(request: Request):
        user = _get_user(request)
        if not user:
            return JSONResponse({"authenticated": False}, status_code=401)
        return JSONResponse({"authenticated": True, "username": user["username"], "role": user["role"]})

    @app.get("/tle-status")
    def tle_status():
        """Return TLE catalog freshness info."""
        import os as _os
        catalog_path = Path("catalog.json")
        if not catalog_path.exists():
            return JSONResponse({"fresh": False, "age_hours": None, "count": 0,
                                 "message": "No TLE data — run a scan"})
        try:
            age_s = time.time() - catalog_path.stat().st_mtime
            age_h = age_s / 3600
            data = json.loads(catalog_path.read_text())
            count = len(data) if isinstance(data, list) else 0
            fresh = age_h < 24
            return JSONResponse({
                "fresh": fresh,
                "age_hours": round(age_h, 1),
                "count": count,
                "message": f"{count} sats · updated {round(age_h,1)}h ago" if fresh
                           else f"Stale ({round(age_h,1)}h old) — rescan recommended"
            })
        except Exception as e:
            return JSONResponse({"fresh": False, "age_hours": None, "count": 0,
                                 "message": "TLE status unavailable"})

    @app.get("/scan-status")
    def scan_status():
        """Return last auto-scan time."""
        last    = _scan_state.get("time", 0)
        running = _scan_state.get("running", False)
        count   = _scan_state.get("count", 0)
        return JSONResponse({
            "last_scan":   last,
            "running":     running,
            "count":       count,
            "age_minutes": round((time.time() - last) / 60, 1) if last else None
        })

    # ── /preferences GET/POST ─────────────────────────────────
    @app.get("/preferences", response_class=HTMLResponse)
    def preferences_page(request: Request):
        user = _get_user(request)
        if not user:
            return RedirectResponse(url="/login")
        prefs = _get_user_prefs(user["username"], cfg)
        html = PREFERENCES_HTML.replace("{USERNAME}", user["username"]) \
            .replace("{EMAIL}", prefs.get("email") or "") \
                        .replace("{PUSHOVER_KEY}", prefs.get("pushover_key") or "") \
            .replace("{PC_THRESH}", str(prefs.get("pc_alert_threshold", 1e-4))) \
            .replace("{ALERT_KM}", str(prefs.get("collision_alert_km", 10.0))) \
            .replace("{MESSAGE}", "")
        return HTMLResponse(html)

    @app.post("/preferences", response_class=HTMLResponse)
    async def preferences_save(request: Request):
        user = _get_user(request)
        if not user:
            return RedirectResponse(url="/login")
        form = await request.form()
        prefs = {
            "email": str(form.get("email", "")).strip(),
            "phone": str(form.get("phone", "")).strip(),
            "pushover_key": str(form.get("pushover_key", "")).strip(),
            "pc_alert_threshold": float(form.get("pc_alert_threshold", 1e-4) or 1e-4),
            "collision_alert_km": float(form.get("collision_alert_km", 10.0) or 10.0),
        }
        _save_user_prefs(user["username"], prefs, cfg)
        html = PREFERENCES_HTML.replace("{USERNAME}", user["username"]) \
            .replace("{EMAIL}", prefs["email"]) \
                        .replace("{PUSHOVER_KEY}", prefs["pushover_key"]) \
            .replace("{PC_THRESH}", str(prefs["pc_alert_threshold"])) \
            .replace("{ALERT_KM}", str(prefs["collision_alert_km"])) \
            .replace("{MESSAGE}", '<div class="ok">✓ Preferences saved.</div>')
        return HTMLResponse(html)

    # ── /demo-results — latest public scan for unauthenticated users ─
    @app.get("/demo-results")
    def demo_results():
        """Returns the most recent public scan (user_id IS NULL) for demo mode."""
        if app._demo_result:
            return JSONResponse(app._demo_result)
        # Try to load from DB
        try:
            con = sqlite3.connect(cfg.db_path)
            latest = con.execute(
                "SELECT run_time FROM conjunctions WHERE user_id IS NULL ORDER BY run_time DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return JSONResponse({}, status_code=404)
            # Return empty tracks + conjunctions from DB for demo
            rows = con.execute(
                "SELECT sat1,sat2,regime1,regime2,min_dist_km,time_min,pc_estimate FROM conjunctions WHERE run_time=? AND user_id IS NULL",
                (latest[0],)
            ).fetchall()
            conj_json = [{"sat1":r[0],"sat2":r[1],"regime1":r[2],"regime2":r[3],
                          "min_dist_km":r[4],"time_min":r[5],"pc_estimate":r[6],
                          "covariance_source":"assumed","debris":False,"maneuver":None,
                          "midpoint":[0,0,400000]} for r in rows]
            return JSONResponse({"tracks": [], "conjunctions": conj_json})
        except Exception as e:
            log.warning(f"demo-results error: {e}")
            return JSONResponse({}, status_code=404)

    # ── SSE Run Endpoint ──────────────────────────────────────
    @app.get("/run")
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
        user = _get_user(request)

        async def event_stream():
            def send(text, type_="log"):
                return f"data: {_json.dumps({'type': type_, 'text': text})}\n\n"

            if not user:
                yield f"data: {_json.dumps({'type': 'auth_error', 'text': 'Authentication required'})}\n\n"
                return

            if not _check_run_rate_limit(user["username"]):
                yield f"data: {_json.dumps({'type': 'rate_limit', 'text': 'Max 1 scan per 5 minutes. Please wait.'})}\n\n"
                return

            try:
                # Load saved user preferences to augment alert config
                user_prefs = _get_user_prefs(user["username"], cfg)

                run_cfg = Config(
                    num_leo=num_leo,
                    num_meo=num_meo,
                    num_geo=num_geo,
                    time_window_hours=time_window_hours,
                    collision_alert_km=collision_alert_km,
                    refine_threshold_km=refine_threshold_km,
                    pc_alert_threshold=pc_alert_threshold,
                    alert_email_to=alert_email or user_prefs.get("email") or cfg.alert_email_to,
                    alert_email_from=cfg.alert_email_from,
                    alert_smtp_host=cfg.alert_smtp_host,
                    pushover_token=cfg.pushover_token,
                    pushover_user_key=pushover_user_key or user_prefs.get("pushover_key") or cfg.pushover_user_key,
                )

                def send_progress(pct: int, msg: str):
                    return f"data: {_json.dumps({'type': 'progress', 'pct': pct, 'text': msg})}\n\n"

                yield send_progress(5, "Fetching covariance data from Space-Track...")
                await asyncio.sleep(0)
                cov_cache_result = {}
                loop = asyncio.get_event_loop()
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    try:
                        cov_cache_result = await loop.run_in_executor(pool, lambda: fetch_covariance_cache(run_cfg))
                    except Exception:
                        pass

                yield send_progress(15, "Starting orbital scan pipeline...")
                await asyncio.sleep(0)

                import logging as _logging
                sse_logs = []
                class SSEHandler(_logging.Handler):
                    def emit(self, record):
                        sse_logs.append(self.format(record))

                sse_handler = SSEHandler()
                sse_handler.setFormatter(_logging.Formatter("%(message)s"))
                _logging.getLogger("VectraSpace").addHandler(sse_handler)

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = loop.run_in_executor(
                        pool,
                        lambda: _run_pipeline(run_cfg, covariance_cache=cov_cache_result,
                                               run_mode="interactive",
                                               user_id=user["username"],
                                               user_prefs=user_prefs)
                    )

                    pct_steps = [30, 45, 60, 72, 82]
                    pct_msgs  = ["Propagating LEO satellites...", "Propagating MEO satellites...",
                                 "Propagating GEO satellites...", "Screening conjunction pairs...",
                                 "Estimating collision probabilities..."]
                    step_idx = 0
                    last_step_t = asyncio.get_event_loop().time()
                    while not future.done():
                        while sse_logs:
                            yield send(sse_logs.pop(0))
                        now_t = asyncio.get_event_loop().time()
                        if step_idx < len(pct_steps) and now_t - last_step_t >= 4.0:
                            yield send_progress(pct_steps[step_idx], pct_msgs[step_idx])
                            step_idx += 1
                            last_step_t = now_t
                        await asyncio.sleep(0.3)

                    while sse_logs:
                        yield send(sse_logs.pop(0))

                    result = await future

                _logging.getLogger("VectraSpace").removeHandler(sse_handler)

                # Serialize tracks for Cesium
                tracks_json = []
                for t in result["tracks"]:
                    step = max(1, len(t.positions) // 120)
                    geo_positions = []
                    for pos in t.positions[::step]:
                        x, y, z = pos[0], pos[1], pos[2]
                        r = math.sqrt(x**2 + y**2 + z**2)
                        lat = math.degrees(math.asin(z / r))
                        lon = math.degrees(math.atan2(y, x))
                        alt = (r - 6371) * 1000
                        geo_positions.append([lon, lat, alt])
                    tracks_json.append({
                        "name": t.name,
                        "regime": t.regime,
                        "positions": geo_positions,
                        "geodetic": True,
                    })

                conj_json = []
                for c in result["conjunctions"]:
                    t1 = next((t for t in result["tracks"] if t.name == c.sat1), None)
                    t2 = next((t for t in result["tracks"] if t.name == c.sat2), None)
                    if t1 and t2:
                        idx = int(np.argmin(np.abs(t1.times_min - c.time_min)))
                        mx = (t1.positions[idx] + t2.positions[idx]) / 2
                        x, y, z = mx[0], mx[1], mx[2]
                        r = math.sqrt(x**2 + y**2 + z**2)
                        lat = math.degrees(math.asin(z / r))
                        lon = math.degrees(math.atan2(y, x))
                        alt = (r - 6371) * 1000
                        mid = [lon, lat, alt]
                    else:
                        mid = [0, 0, 400000]
                    maneuver_data = None
                    if c.maneuver:
                        m = c.maneuver
                        maneuver_data = {
                            "delta_v_rtn": m.delta_v_rtn,
                            "delta_v_magnitude": m.delta_v_magnitude,
                            "burn_epoch_offset_min": m.burn_epoch_offset_min,
                            "safe_dist_achieved_km": m.safe_dist_achieved_km,
                            "method": m.method,
                            "advisory_note": m.advisory_note,
                            "feasible": m.feasible,
                        }
                    conj_json.append({
                        "sat1": c.sat1, "sat2": c.sat2,
                        "regime1": c.regime1, "regime2": c.regime2,
                        "min_dist_km": c.min_dist_km,
                        "time_min": c.time_min,
                        "pc_estimate": c.pc_estimate,
                        "covariance_source": c.covariance_source,
                        "debris": c.debris,
                        "maneuver": maneuver_data,
                        "midpoint": mid,
                    })

                # Store for demo view (public, user_id=None means anonymous/headless)
                serialized = {"tracks": tracks_json, "conjunctions": conj_json}
                app._user_results[user["username"]] = result
                # Also update demo cache (latest authenticated scan visible as demo)
                app._demo_result = serialized
                app._last_result = result

                yield send_progress(98, f"Scan complete — {len(result['conjunctions'])} conjunction(s) found")
                payload = _json.dumps({"type": "done", "data": serialized})
                yield f"data: {payload}\n\n"

            except Exception as e:
                log.error(f"Scan pipeline error: {e}", exc_info=True)
                yield f"data: {_json.dumps({'type': 'error', 'text': str(e)})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ── Historical results ────────────────────────────────────
    @app.get("/conjunctions")
    def get_conjunctions(request: Request):
        user = _get_user(request)
        con = sqlite3.connect(cfg.db_path)
        if user:
            rows = con.execute(
                "SELECT * FROM conjunctions WHERE user_id=? ORDER BY run_time DESC LIMIT 200",
                (user["username"],)
            ).fetchall()
        else:
            # Demo: show public/anonymous runs
            rows = con.execute(
                "SELECT * FROM conjunctions WHERE user_id IS NULL ORDER BY run_time DESC LIMIT 50"
            ).fetchall()
        cols = ["id","run_time","sat1","sat2","regime1","regime2","min_dist_km","time_min","pc_estimate","user_id"]
        return JSONResponse([dict(zip(cols, r)) for r in rows])

    @app.get("/history")
    def get_history(request: Request):
        """Returns trend data for historical dashboard charts."""
        user = _get_user(request)
        con = sqlite3.connect(cfg.db_path)

        if user:
            uid_filter = "WHERE user_id=?"
            uid_param = (user["username"],)
        else:
            uid_filter = "WHERE user_id IS NULL"
            uid_param = ()

        daily = con.execute(f"""
            SELECT substr(run_time,1,10) as day, COUNT(*) as count
            FROM conjunctions {uid_filter} GROUP BY day ORDER BY day DESC LIMIT 30
        """, uid_param).fetchall()

        pairs = con.execute(f"""
            SELECT sat1, sat2, COUNT(*) as count, MIN(min_dist_km) as closest
            FROM conjunctions {uid_filter} GROUP BY sat1, sat2 ORDER BY count DESC LIMIT 10
        """, uid_param).fetchall()

        regimes = con.execute(f"""
            SELECT regime1 || '/' || regime2 as pair, COUNT(*) as count
            FROM conjunctions {uid_filter} GROUP BY pair ORDER BY count DESC
        """, uid_param).fetchall()

        return JSONResponse({
            "daily": [{"day": r[0], "count": r[1]} for r in daily],
            "top_pairs": [{"sat1": r[0], "sat2": r[1], "count": r[2], "closest": r[3]} for r in pairs],
            "regimes": [{"pair": r[0], "count": r[1]} for r in regimes],
        })

    # ── CDM endpoints ─────────────────────────────────────────
    @app.get("/cdm/{idx}")
    def download_cdm(idx: int, request: Request):
        user = _get_user(request)
        con = sqlite3.connect(cfg.db_path)
        if user:
            rows = con.execute(
                "SELECT * FROM conjunctions WHERE user_id=? ORDER BY run_time DESC LIMIT 50",
                (user["username"],)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM conjunctions WHERE user_id IS NULL ORDER BY run_time DESC LIMIT 50"
            ).fetchall()
        cols = ["id","run_time","sat1","sat2","regime1","regime2","min_dist_km","time_min","pc_estimate","user_id"]
        if idx >= len(rows):
            return PlainTextResponse("Not found", status_code=404)
        r = dict(zip(cols, rows[idx]))
        c = Conjunction(sat1=r["sat1"], sat2=r["sat2"],
                        regime1=r["regime1"], regime2=r["regime2"],
                        min_dist_km=r["min_dist_km"], time_min=r["time_min"],
                        pc_estimate=r["pc_estimate"])
        cdm_text = generate_cdm(c, r["run_time"])
        fname = f"VS_CDM_{r['sat1'][:8].replace(' ','_')}_{r['sat2'][:8].replace(' ','_')}.cdm"
        return PlainTextResponse(cdm_text, headers={
            "Content-Disposition": f'attachment; filename="{fname}"'
        })

    @app.get("/cdm/zip/all")
    def download_all_cdms(request: Request):
        import io, zipfile
        user = _get_user(request)
        con = sqlite3.connect(cfg.db_path)
        if user:
            latest = con.execute(
                "SELECT run_time FROM conjunctions WHERE user_id=? ORDER BY run_time DESC LIMIT 1",
                (user["username"],)
            ).fetchone()
            if not latest:
                return JSONResponse({"error": "No conjunctions in database"}, status_code=404)
            rows = con.execute(
                "SELECT * FROM conjunctions WHERE run_time=? AND user_id=?",
                (latest[0], user["username"])
            ).fetchall()
        else:
            latest = con.execute(
                "SELECT run_time FROM conjunctions WHERE user_id IS NULL ORDER BY run_time DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return JSONResponse({"error": "No public conjunctions in database"}, status_code=404)
            rows = con.execute(
                "SELECT * FROM conjunctions WHERE run_time=? AND user_id IS NULL",
                (latest[0],)
            ).fetchall()

        cols = ["id","run_time","sat1","sat2","regime1","regime2","min_dist_km","time_min","pc_estimate","user_id"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, row in enumerate(rows):
                r = dict(zip(cols, row))
                c = Conjunction(sat1=r["sat1"], sat2=r["sat2"],
                                regime1=r["regime1"], regime2=r["regime2"],
                                min_dist_km=r["min_dist_km"], time_min=r["time_min"],
                                pc_estimate=r["pc_estimate"])
                cdm_text = generate_cdm(c, r["run_time"])
                s1 = str(r["sat1"] or "UNK")[:8].replace(" ","_")
                s2 = str(r["sat2"] or "UNK")[:8].replace(" ","_")
                zf.writestr(f"CDM_{i+1:03d}_{s1}_{s2}.cdm", cdm_text)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="VectraSpace_CDMs.zip"'
        })

    # ── SEC-01 + PERF-01: Server-side satellite info endpoint ────
    @app.get("/sat-info/{sat_name}")
    async def satellite_info(sat_name: str):
        """
        SEC-01: Anthropic API called server-side — key never reaches the browser.
        PERF-01: Blocking requests.post runs in a thread executor so it never
                 stalls the async event loop under concurrent load.
        Requires ANTHROPIC_API_KEY env var.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            log.warning("ANTHROPIC_API_KEY not set — returning basic info")
            clean = sat_name.strip().upper()
            return JSONResponse({
                "fullName": clean,
                "noradId": None,
                "country": "Unknown",
                "launchDate": None,
                "launchSite": None,
                "orbitType": "Unknown",
                "periodMin": None,
                "inclinationDeg": None,
                "apogeeKm": None,
                "perigeeKm": None,
                "rcsSize": None,
                "operationalStatus": "Unknown",
                "owner": "Unknown",
                "objectType": "UNKNOWN",
                "missionType": "Unknown",
                "note": "Set ANTHROPIC_API_KEY for detailed satellite information.",
                "celestrak_url": f"https://celestrak.org/satcat/records.php?NAME={sat_name}",
            })

        import asyncio
        from functools import partial

        def _call_anthropic_sync(name: str, key: str) -> dict:
            """Runs in a thread — safe to block here."""
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 800,
                    "system": (
                        "You are a satellite data formatter. Given a satellite name, "
                        "return ONLY a JSON object (no markdown, no explanation) with these fields "
                        "extracted or inferred: fullName, noradId, country, launchDate, launchSite, "
                        "orbitType, periodMin, inclinationDeg, apogeeKm, perigeeKm, rcsSize, "
                        "operationalStatus, owner, objectType, missionType. "
                        "missionType must be one of: Communications, Earth Observation, Navigation, "
                        "Scientific, Military, Weather, Technology Demo, Human Spaceflight, "
                        "Space Station, Debris, or Unknown. Use null for missing fields."
                    ),
                    "messages": [{"role": "user",
                                  "content": f"Satellite name: {name}. Return the JSON object."}]
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            text = "".join(
                block.get("text", "") for block in data.get("content", [])
                if block.get("type") == "text"
            )
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)

        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None, partial(_call_anthropic_sync, sat_name, api_key)
            )
            return JSONResponse(info)
        except json.JSONDecodeError:
            log.warning(f"Anthropic returned non-JSON for {sat_name}")
            return JSONResponse({"error": "Could not parse satellite data"}, status_code=502)
        except Exception as e:
            log.warning(f"sat-info error for {sat_name}: {e}")
            return JSONResponse({"error": str(e)}, status_code=502)

    # ── Auth routes ───────────────────────────────────────────
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, error: str = ""):
        err_html    = f'<div class="err">⚠ {error}</div>' if error else ""
        signup_link = '<a href="/signup">Create account</a>' if SIGNUP_OPEN else '<a href="mailto:trumanheaston@gmail.com">Request access</a>'
        next_url    = str(request.query_params.get("next", "")).strip()
        action      = f"/login?next={next_url}" if next_url else "/login"
        return HTMLResponse(
            LOGIN_HTML.replace("{ERROR}", err_html)
                      .replace("{SIGNUP_LINK}", signup_link)
                      .replace('action="/login"', f'action="{action}"')
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request):
        signup_link = '<a href="/signup">Create account</a>' if SIGNUP_OPEN else '<a href="mailto:trumanheaston@gmail.com">Request access</a>'

        def _login_page_with_err(msg: str) -> HTMLResponse:
            return HTMLResponse(
                LOGIN_HTML.replace("{ERROR}", f'<div class="err">{msg}</div>')
                          .replace("{SIGNUP_LINK}", signup_link)
            )

        # On Render (and most PaaS), all traffic comes through a proxy.
        # Use X-Forwarded-For to get the real client IP; fall back to host.
        xff = request.headers.get("X-Forwarded-For", "")
        client_ip = xff.split(",")[0].strip() if xff else (request.client.host or "0.0.0.0")
        if not _check_login_rate_limit(client_ip):
            return _login_page_with_err("Too many login attempts. Try again in 60s.")
        form = await request.form()
        username = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", "")).strip()
        users = _load_users(cfg)
        # Try exact match first, then lowercase fallback
        user = users.get(username) or users.get(username.lower())
        if not user or not _verify_password(password, user.get("password_hash", "")):
            return _login_page_with_err("Invalid username or password.")
        if not user.get("approved", True):
            return _login_page_with_err("Account pending approval. Contact trumanheaston@gmail.com.")
        next_url = str(request.query_params.get("next", "") or "/dashboard").strip()
        if not next_url.startswith("/"):
            next_url = "/dashboard"
        token = _make_session_cookie(username, user.get("role", "operator"), cfg.session_secret)
        resp = RedirectResponse(url=next_url, status_code=303)
        resp.set_cookie("vs_session", token, httponly=True, samesite="lax", path="/", max_age=2592000)
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse(url="/login")
        resp.delete_cookie("vs_session")
        return resp

    # ── AUTH-01: Self-service signup ──────────────────────────
    # Controlled by SIGNUP_OPEN env var: "true" = open, anything else = closed.
    # Set SIGNUP_OPEN=true in .env to enable public registration.
    SIGNUP_OPEN = os.environ.get("SIGNUP_OPEN", "true").lower() == "true"

    _SIGNUP_FORM = """
    <form method="post" action="/signup">
      <label>Username</label>
      <input type="text" name="username" required minlength="3" maxlength="32"
             placeholder="3–32 chars, lowercase" autocomplete="username">
      <label>Email</label>
      <input type="email" name="email" required placeholder="you@example.com"
             autocomplete="email">
      <div class="hint">Used for password resets and conjunction alerts</div>
      <label>Password</label>
      <input type="password" name="password" required minlength="8"
             autocomplete="new-password" placeholder="Min 8 characters">
      <label>Confirm Password</label>
      <input type="password" name="confirm" required minlength="8"
             autocomplete="new-password" placeholder="Repeat password">
      <div class="pw-rules">
        Min 8 chars · Use a mix of letters, numbers, and symbols for best security
      </div>
      <button type="submit">Create Account</button>
    </form>"""

    @app.get("/signup", response_class=HTMLResponse)
    def signup_page():
        if not SIGNUP_OPEN:
            return HTMLResponse(SIGNUP_CLOSED_HTML)
        return HTMLResponse(
            SIGNUP_HTML.replace("{MESSAGE}", "").replace("{FORM}", _SIGNUP_FORM)
                       .replace("{SIGNUP_LINK}", "")
        )

    @app.post("/signup", response_class=HTMLResponse)
    async def signup_submit(request: Request):
        if not SIGNUP_OPEN:
            return HTMLResponse(SIGNUP_CLOSED_HTML, status_code=403)
        form = await request.form()
        username = str(form.get("username", "")).strip().lower()
        email    = str(form.get("email",    "")).strip().lower()
        pw       = str(form.get("password", "")).strip()
        confirm  = str(form.get("confirm",  "")).strip()
        if pw != confirm:
            err = '<div class="err">⚠ Passwords do not match.</div>'
            return HTMLResponse(SIGNUP_HTML.replace("{MESSAGE}", err).replace("{FORM}", _SIGNUP_FORM))
        ok, errmsg = _register_user(username, email, pw, cfg, approved=True)
        if not ok:
            err = f'<div class="err">⚠ {errmsg}</div>'
            return HTMLResponse(SIGNUP_HTML.replace("{MESSAGE}", err).replace("{FORM}", _SIGNUP_FORM))
        # Auto-login after successful registration
        token = _make_session_cookie(username, "operator", cfg.session_secret)
        resp = RedirectResponse(url="/dashboard", status_code=303)
        resp.set_cookie("vs_session", token, httponly=True, samesite="lax", path="/", max_age=2592000)
        log.info(f"New self-signup: '{username}' <{email}>")
        return resp

    # ── AUTH-02: Forgot / reset password ─────────────────────
    # Flow: user submits username → we email a signed 1-hour token link →
    # user clicks link → enters new password → done. No admin needed.

    def _send_reset_email(username: str, token: str, cfg: Config):
        """Send a password reset link to the user's registered email."""
        email = _get_user_email(username, cfg)
        if not email:
            log.warning(f"Password reset requested for '{username}' but no email on file")
            return
        base_url = os.environ.get("VECTRASPACE_BASE_URL", "http://localhost:8000").rstrip("/")
        reset_url = f"{base_url}/reset-password?token={token}"
        html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#050a0f;color:#c8dff0;font-family:sans-serif;padding:40px;">
<div style="max-width:480px;margin:0 auto;background:#090f17;border:1px solid #0d2137;
            border-radius:8px;padding:36px;">
  <div style="font-size:10px;color:#00d4ff;letter-spacing:4px;margin-bottom:8px;">
    VectraSpace // Mission Control
  </div>
  <h2 style="color:#fff;margin-bottom:6px;">Password Reset</h2>
  <p style="font-size:12px;color:#4a6a85;margin-bottom:20px;">
    A reset was requested for account <strong style="color:#c8dff0;">{username}</strong>.
    This link expires in <strong style="color:#00d4ff;">1 hour</strong>.
  </p>
  <a href="{reset_url}"
     style="display:block;padding:12px;background:transparent;border:1px solid #00d4ff;
            border-radius:4px;color:#00d4ff;text-align:center;text-decoration:none;
            font-family:monospace;letter-spacing:2px;font-size:12px;">
    RESET MY PASSWORD
  </a>
  <p style="font-size:10px;color:#4a6a85;margin-top:16px;">
    If you didn't request this, ignore this email — your password won't change.
  </p>
  <p style="font-size:10px;color:#2a4a65;margin-top:8px;">
    Or paste this URL: {reset_url}
  </p>
</div>
</body></html>"""
        _send_email(
            subject="[VectraSpace] Password Reset Link",
            html_body=html_body,
            to_addr=email,
            cfg=cfg,
            plain_body=f"Reset your VectraSpace password: {reset_url}\nExpires in 1 hour.",
        )
        log.info(f"Password reset email sent to {email} for user '{username}'")

    _FORGOT_FORM = """
    <form method="post" action="/forgot-password">
      <label>Username</label>
      <input type="text" name="username" required placeholder="Your username"
             autocomplete="username">
      <div class="hint">We'll send a reset link to your registered email address</div>
      <button type="submit">Send Reset Link</button>
    </form>"""

    @app.get("/forgot-password", response_class=HTMLResponse)
    def forgot_password_page():
        return HTMLResponse(
            FORGOT_PASSWORD_HTML.replace("{MESSAGE}", "").replace("{FORM}", _FORGOT_FORM)
        )

    @app.post("/forgot-password", response_class=HTMLResponse)
    async def forgot_password_submit(request: Request):
        form = await request.form()
        username = str(form.get("username", "")).strip().lower()
        # Always show success — never reveal whether a username exists
        success_msg = '<div class="ok">✓ If that username exists and has an email on file, a reset link has been sent. Check your inbox.</div>'
        users = _load_users(cfg)
        if username in users:
            token = _make_reset_token(username, cfg.session_secret)
            import asyncio
            asyncio.get_event_loop().run_in_executor(
                None, lambda: _send_reset_email(username, token, cfg)
            )
        return HTMLResponse(
            FORGOT_PASSWORD_HTML.replace("{MESSAGE}", success_msg).replace("{FORM}", "")
        )

    _RESET_FORM_TPL = """
    <form method="post" action="/reset-password">
      <input type="hidden" name="token" value="{TOKEN}">
      <label>New Password</label>
      <input type="password" name="password" required minlength="8"
             autocomplete="new-password" placeholder="Min 8 characters">
      <label>Confirm New Password</label>
      <input type="password" name="confirm" required minlength="8"
             autocomplete="new-password" placeholder="Repeat password">
      <div class="pw-rules">Min 8 chars · Use a mix of letters, numbers, and symbols</div>
      <button type="submit">Set New Password</button>
    </form>"""

    @app.get("/reset-password", response_class=HTMLResponse)
    def reset_password_page(token: str = ""):
        username = _verify_reset_token(token, cfg.session_secret)
        if not username:
            err = '<div class="err">⚠ This reset link is invalid or has expired. <a href="/forgot-password" style="color:#00d4ff;">Request a new one.</a></div>'
            return HTMLResponse(RESET_PASSWORD_HTML.replace("{MESSAGE}", err).replace("{FORM}", ""))
        form_html = _RESET_FORM_TPL.replace("{TOKEN}", token)
        sub = f'<div class="sub" style="margin-bottom:16px;">Resetting password for <strong>{username}</strong></div>'
        return HTMLResponse(
            RESET_PASSWORD_HTML.replace("{MESSAGE}", sub).replace("{FORM}", form_html)
        )

    @app.post("/reset-password", response_class=HTMLResponse)
    async def reset_password_submit(request: Request):
        form = await request.form()
        token   = str(form.get("token",    "")).strip()
        pw      = str(form.get("password", "")).strip()
        confirm = str(form.get("confirm",  "")).strip()
        username = _verify_reset_token(token, cfg.session_secret)
        if not username:
            err = '<div class="err">⚠ Link expired or invalid. <a href="/forgot-password" style="color:#00d4ff;">Request a new one.</a></div>'
            return HTMLResponse(RESET_PASSWORD_HTML.replace("{MESSAGE}", err).replace("{FORM}", ""))
        if pw != confirm:
            form_html = _RESET_FORM_TPL.replace("{TOKEN}", token)
            err = '<div class="err">⚠ Passwords do not match.</div>'
            return HTMLResponse(RESET_PASSWORD_HTML.replace("{MESSAGE}", err).replace("{FORM}", form_html))
        if len(pw) < 8:
            form_html = _RESET_FORM_TPL.replace("{TOKEN}", token)
            err = '<div class="err">⚠ Password must be at least 8 characters.</div>'
            return HTMLResponse(RESET_PASSWORD_HTML.replace("{MESSAGE}", err).replace("{FORM}", form_html))
        ok = _update_password(username, pw, cfg)
        if not ok:
            err = '<div class="err">⚠ Could not update password. Contact trumanheaston@gmail.com.</div>'
            return HTMLResponse(RESET_PASSWORD_HTML.replace("{MESSAGE}", err).replace("{FORM}", ""))
        log.info(f"Password successfully reset for user '{username}' via email token")
        # Auto-login after successful reset
        token_cookie = _make_session_cookie(username, "operator", cfg.session_secret)
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("vs_session", token_cookie, httponly=True, samesite="lax", path="/", max_age=2592000)
        return resp

    # ── Authenticated change-password (for logged-in users in prefs) ──
    _CHANGE_PW_FORM = """
    <form method="post" action="/change-password">
      <label>Current Password</label>
      <input type="password" name="current" required autocomplete="current-password">
      <label>New Password</label>
      <input type="password" name="password" required minlength="8"
             autocomplete="new-password" placeholder="Min 8 characters">
      <label>Confirm New Password</label>
      <input type="password" name="confirm" required minlength="8"
             autocomplete="new-password">
      <div class="pw-rules">Min 8 chars · Use a mix of letters, numbers, and symbols</div>
      <button type="submit">Update Password</button>
    </form>"""

    @app.get("/change-password", response_class=HTMLResponse)
    def change_password_page(request: Request):
        user = get_current_user_from_request(request, cfg)
        if not user:
            return RedirectResponse(url="/login")
        return HTMLResponse(
            RESET_PASSWORD_HTML
                .replace("<h1>Set New Password</h1>", "<h1>Change Password</h1>")
                .replace("<div class=\"sub\">Choose a strong password for your account</div>", f'<div class="sub">Logged in as {user["username"]}</div>')
                .replace("{MESSAGE}", "").replace("{FORM}", _CHANGE_PW_FORM)
        )

    @app.post("/change-password", response_class=HTMLResponse)
    async def change_password_submit(request: Request):
        user = get_current_user_from_request(request, cfg)
        if not user:
            return RedirectResponse(url="/login")
        form = await request.form()
        current = str(form.get("current",  "")).strip()
        pw      = str(form.get("password", "")).strip()
        confirm = str(form.get("confirm",  "")).strip()
        users = _load_users(cfg)
        u = users.get(user["username"])

        def _err(msg):
            return HTMLResponse(
                RESET_PASSWORD_HTML
                    .replace("<h1>Set New Password</h1>", "<h1>Change Password</h1>")
                    .replace("<div class=\"sub\">Choose a strong password for your account</div>", f'<div class="sub">Logged in as {user["username"]}</div>')
                    .replace("{MESSAGE}", f'<div class="err">⚠ {msg}</div>')
                    .replace("{FORM}", _CHANGE_PW_FORM)
            )

        if not u or not _verify_password(current, u.get("password_hash", "")):
            return _err("Current password is incorrect.")
        if pw != confirm:
            return _err("New passwords do not match.")
        if len(pw) < 8:
            return _err("Password must be at least 8 characters.")
        _update_password(user["username"], pw, cfg)
        return RedirectResponse(url="/preferences?saved=password", status_code=303)

    PUBLIC_PATHS = {"/login", "/health", "/demo-results", "/signup",
                    "/forgot-password", "/reset-password", "/", "/welcome",
                    "/research", "/research/tle.json", "/research/tle.csv",
                    "/admin", "/admin/data", "/feedback", "/tle-status", "/scan-status"}

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path in PUBLIC_PATHS or path.startswith("/static"):
                return await call_next(request)
            if path in ("/", "/dashboard", "/admin", "/admin/data") or path.startswith("/sat-info/") or path.startswith("/cdm"):
                return await call_next(request)
            if path == "/preferences":
                token = request.cookies.get("vs_session", "")
                try:
                    _verify_session_cookie(token, cfg.session_secret)
                except Exception:
                    return RedirectResponse(url="/login")
            return await call_next(request)

    app.add_middleware(AuthMiddleware)
    log.info("Auth middleware enabled")

    # ── Debris simulation endpoint ────────────────────────────
    @app.get("/debris/simulate")
    async def simulate_debris(
        request: Request,
        sat_name: str = "",
        event_type: str = "COLLISION",
        n_debris: int = 50,
    ):
        import math as _math
        last = getattr(app, "_last_result", None)
        if not last:
            return JSONResponse({"error": "No scan results available. Run a scan first."}, status_code=400)

        all_tracks = last.get("tracks", [])
        ts = last.get("ts")
        if ts is None:
            return JSONResponse({"error": "No timescale available."}, status_code=400)

        parent = next((t for t in all_tracks if t.name == sat_name), None)
        if not parent:
            return JSONResponse({"error": f"Satellite '{sat_name}' not found in last scan."}, status_code=404)

        debris_list = generate_debris_cloud(parent, event_type, n_debris, ts)

        tracks_json = []
        for t in debris_list:
            step = max(1, len(t.positions) // 120)
            geo = []
            for pos in t.positions[::step]:
                x, y, z = pos[0], pos[1], pos[2]
                r = _math.sqrt(x**2 + y**2 + z**2)
                lat = _math.degrees(_math.asin(z / r))
                lon = _math.degrees(_math.atan2(y, x))
                alt = (r - 6371) * 1000
                geo.append([lon, lat, alt])
            tracks_json.append({"name": t.name, "regime": t.regime, "positions": geo})

        debris_conjunctions = check_conjunctions(debris_list + all_tracks, CFG, ts)
        debris_conj_only = [c for c in debris_conjunctions if c.debris]

        conj_json = [{"sat1": c.sat1, "sat2": c.sat2,
                      "regime1": c.regime1, "regime2": c.regime2,
                      "min_dist_km": c.min_dist_km, "time_min": c.time_min,
                      "pc_estimate": c.pc_estimate, "covariance_source": c.covariance_source,
                      "debris": True, "maneuver": None, "midpoint": [0,0,400000]}
                     for c in debris_conj_only]

        return JSONResponse({"debris_tracks": tracks_json, "conjunctions": conj_json})

    # FEEDBACK + ADMIN VERIFY ROUTES

    @app.post("/feedback")
    async def submit_feedback(request: Request):
        """Save feedback to feedback.json and optionally email it."""
        import datetime as _dt
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        fb_type = str(body.get("type", "other"))[:50]
        message = str(body.get("message", "")).strip()[:2000]
        email   = str(body.get("email", "")).strip()[:200]
        user    = str(body.get("user", "anonymous"))[:100]
        url     = str(body.get("url", ""))[:200]
        ua      = str(body.get("ua", ""))[:200]

        if not message:
            return JSONResponse({"error": "empty message"}, status_code=400)

        entry = {
            "id":        __import__('uuid').uuid4().hex[:8],
            "timestamp": _dt.datetime.utcnow().isoformat(),
            "type":      fb_type,
            "message":   message,
            "email":     email,
            "user":      user,
            "url":       url,
            "ua":        ua,
        }

        # Append to feedback.json
        fb_path = Path(cfg.db_path).parent / "feedback.json"
        try:
            existing = json.loads(fb_path.read_text()) if fb_path.exists() else []
        except Exception:
            existing = []
        existing.append(entry)
        fb_path.write_text(json.dumps(existing, indent=2))

        # Email notification to admin
        try:
            _send_email(
                subject=f"[VectraSpace Feedback] {fb_type.upper()} from {user}",
                html_body=f"""<div style="font-family:monospace;background:#050a0f;color:#c8dff0;padding:24px;">
<h3 style="color:#00d4ff;">New Feedback — {fb_type.upper()}</h3>
<p><strong>From:</strong> {user} ({email or 'no email'})</p>
<p><strong>Time:</strong> {entry['timestamp']}</p>
<p><strong>Message:</strong></p>
<pre style="background:#090f17;padding:12px;border-radius:4px;border-left:2px solid #00d4ff;white-space:pre-wrap;">{message}</pre>
<p style="color:#4a6a85;font-size:11px;">UA: {ua}</p>
</div>""",
                plain_body=("Feedback (" + fb_type + ") from " + user + ":\n\n" + message + "\n\nEmail: " + email),
                cfg=cfg,
            )
        except Exception as e:
            log.warning(f"Could not email feedback notification: {e}")

        log.info(f"Feedback received: [{fb_type}] from {user}")
        return JSONResponse({"ok": True, "id": entry["id"]})

    # /admin/verify removed — admin access via login only

    # ADMIN ROUTES

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request):
        """Admin console — requires admin role login."""
        user = get_current_user_from_request(request, cfg)
        if not user or user.get("role") != "admin":
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/login?next=/admin", status_code=303)
        token = os.environ.get("CESIUM_ION_TOKEN", "")
        html = ADMIN_HTML.replace("__CESIUM_TOKEN__", token)
        return HTMLResponse(html)

    @app.get("/admin/data")
    def admin_data(request: Request):
        """JSON endpoint — returns all admin stats."""
        user = get_current_user_from_request(request, cfg)
        if not user or user.get("role") != "admin":
            return JSONResponse({"error": "forbidden"}, status_code=403)

        import datetime as _dt

        # ── Users ────────────────────────────────────────────────────
        all_users = list(_load_users(cfg).values())
        now = _dt.datetime.utcnow()
        week_ago = (now - _dt.timedelta(days=7)).isoformat()
        new_7d = sum(
            1 for u in all_users
            if u.get("created_at", "0") >= week_ago
        )

        # Per-user scan counts
        try:
            with sqlite3.connect(cfg.db_path) as con:
                scan_counts_raw = con.execute(
                    "SELECT user_id, COUNT(*) FROM conjunctions GROUP BY user_id"
                ).fetchall()
            scan_by_user = {r[0]: r[1] for r in scan_counts_raw}
        except Exception:
            scan_by_user = {}

        users_out = []
        for u in sorted(all_users, key=lambda x: x.get("created_at", ""), reverse=True):
            users_out.append({
                "username":   u.get("username", ""),
                "email":      u.get("email", ""),
                "role":       u.get("role", "operator"),
                "approved":   u.get("approved", True),
                "created_at": u.get("created_at", ""),
                "scan_count": scan_by_user.get(u.get("username", ""), 0),
            })

        # ── Conjunction DB stats ──────────────────────────────────────
        try:
            with sqlite3.connect(cfg.db_path) as con:
                total_conj = con.execute("SELECT COUNT(*) FROM conjunctions").fetchone()[0]

                # Unique scan runs (distinct run_time values)
                total_runs = con.execute(
                    "SELECT COUNT(DISTINCT run_time) FROM conjunctions"
                ).fetchone()[0]

                # Recent 50 events
                recent = con.execute(
                    """SELECT run_time, user_id, sat1, sat2, regime1, regime2,
                              min_dist_km, pc_estimate
                       FROM conjunctions
                       ORDER BY id DESC LIMIT 50"""
                ).fetchall()
                recent_out = [
                    {
                        "run_time": r[0], "user_id": r[1] or "anon",
                        "sat1": r[2], "sat2": r[3],
                        "regime1": r[4], "regime2": r[5],
                        "min_dist_km": r[6], "pc_estimate": r[7],
                    }
                    for r in recent
                ]

                # Daily scan runs (last 30 days)
                thirty_ago = (now - _dt.timedelta(days=30)).date().isoformat()
                daily_raw = con.execute(
                    """SELECT DATE(run_time) as day, COUNT(DISTINCT run_time) as cnt
                       FROM conjunctions
                       WHERE DATE(run_time) >= ?
                       GROUP BY day ORDER BY day DESC""",
                    (thirty_ago,)
                ).fetchall()
                daily_out = [{"day": r[0], "count": r[1]} for r in daily_raw]

                # Regime breakdown
                regime_raw = con.execute(
                    """SELECT regime1 || '/' || regime2 as pair, COUNT(*) as cnt
                       FROM conjunctions
                       GROUP BY pair ORDER BY cnt DESC LIMIT 6"""
                ).fetchall()
                regime_out = [{"pair": r[0], "count": r[1]} for r in regime_raw]

        except Exception as e:
            total_conj = 0; total_runs = 0
            recent_out = []; daily_out = []; regime_out = []

        return {
            "total_users":        len(all_users),
            "total_scan_runs":    total_runs,
            "total_conjunctions": total_conj,
            "new_users_7d":       new_7d,
            "users":              users_out,
            "recent_conjunctions": recent_out,
            "daily_scans":        daily_out,
            "regime_breakdown":   regime_out,
        }

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "time": datetime.datetime.utcnow().isoformat(),
            "version": "v11",
            "product": "VectraSpace — Orbital Safety Platform",
        }

    return app

def _run_pipeline(cfg: Config, covariance_cache: Optional[dict] = None,
                  run_mode: str = "interactive",
                  user_id: Optional[str] = None,
                  user_prefs: Optional[dict] = None) -> dict:
    """Run the full detection pipeline and return tracks + conjunctions."""
    t_start = time.time()

    satellites, ts = fetch_tles(cfg)
    buckets = filter_by_regime(satellites, ts)

    regime_counts = {
        'LEO': cfg.num_leo,
        'MEO': cfg.num_meo,
        'GEO': cfg.num_geo,
    }

    all_tracks = []
    for regime, sats in buckets.items():
        count = regime_counts.get(regime, cfg.num_satellites_per_regime)
        subset = sats[:count]
        tracks, _ = propagate_satellites(subset, regime, cfg, ts)
        all_tracks.extend(tracks)
        log.info(f"  {regime}: {len(tracks)} tracks propagated")

    log.info(f"Total tracks: {len(all_tracks)}")
    conjunctions = check_conjunctions(all_tracks, cfg, ts, covariance_cache=covariance_cache)

    con = init_db(cfg)
    run_time = datetime.datetime.utcnow().isoformat()
    log_conjunctions_to_db(conjunctions, con, run_time, user_id=user_id)

    duration = time.time() - t_start

    # Use user-specific prefs for alerts if provided
    send_alerts(conjunctions, cfg, total_sats=len(all_tracks), user_prefs=user_prefs)
    send_propagation_complete(len(all_tracks), conjunctions, duration, cfg, user_prefs=user_prefs)

    return {"tracks": all_tracks, "conjunctions": conjunctions,
            "run_time": run_time, "ts": ts}

# ╔══════════════════════════════════════════════════════════════╗
# ║  MAIN                                                        ║
# ╚══════════════════════════════════════════════════════════════╝

# ── Module-level app init (for uvicorn vectraspace:app on Render/cloud) ──────
def _init_app():
    """Build the FastAPI app and run startup tasks. Called at import time."""
    import os as _os
    from pathlib import Path as _Path

    # Auto-create admin user from env vars on every startup.
    # ADMIN_PASS is required. Falls back to ADMIN_PASSCODE if ADMIN_PASS not set.
    _admin_user = _os.environ.get("ADMIN_USER", "admin").strip().lower()
    _admin_pass = (
        _os.environ.get("ADMIN_PASS", "").strip() or
        _os.environ.get("ADMIN_PASSCODE", "").strip()
    )
    if not _admin_pass:
        _admin_pass = "VectraSpace2526"  # last-resort default
        log.warning("[startup] No ADMIN_PASS set — using default. Set ADMIN_PASS in env!")
    try:
        init_db(CFG)  # ensure tables exist before loading users
        existing = _load_users(CFG)
        if _admin_user not in existing:
            create_user(_admin_user, _admin_pass, "admin", cfg=CFG)
            log.info(f"[startup] Created admin user '{_admin_user}'")
        else:
            if existing[_admin_user].get("role") != "admin":
                existing[_admin_user]["role"] = "admin"
                _save_users(existing, CFG)
                log.info(f"[startup] Fixed role for '{_admin_user}' -> admin")
            else:
                log.info(f"[startup] Admin user '{_admin_user}' OK")
    except Exception as e:
        log.warning(f"[startup] Could not init admin user: {e}")

    # Build and return the FastAPI app (init_db already called above)
    return build_api(CFG)

app = _init_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="VectraSpace v11 — Orbital Safety Platform",
        epilog="Environment variables: CESIUM_ION_TOKEN, ANTHROPIC_API_KEY, SESSION_SECRET, "
               "ALERT_EMAIL_FROM, ALERT_EMAIL_TO, ALERT_SMTP_PASS, ALERT_PHONE, "
               "PUSHOVER_TOKEN, PUSHOVER_USER_KEY, SPACETRACK_USER, SPACETRACK_PASS"
    )
    parser.add_argument("--headless", action="store_true",
                        help="Run pipeline once without web server (for scheduled tasks)")
    parser.add_argument("--create-user", nargs=3, metavar=("USERNAME", "PASSWORD", "ROLE"),
                        help="Add a user to users.json (roles: admin, operator)")
    parser.add_argument("--gen-task-xml", action="store_true",
                        help="Generate Windows Task Scheduler XML for scheduled runs")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't auto-open browser on startup")
    args = parser.parse_args()

    if args.create_user:
        username, password, role = args.create_user
        create_user(username, password, role, cfg=CFG)
        sys.exit(0)

    if args.gen_task_xml:
        import shutil
        py_exe = shutil.which("python") or sys.executable
        script = str(Path(__file__).resolve())
        generate_task_xml(py_exe, script, interval_hours=6, output_path="VectraSpace_Task.xml")
        print("Task XML written to VectraSpace_Task.xml")
        sys.exit(0)

    if args.headless:
        run_headless(CFG)
    else:
        if not HAS_FASTAPI:
            print("ERROR: FastAPI required for VectraSpace v11.")
            print("Run: pip install fastapi uvicorn")
            sys.exit(1)

        # Validate critical environment variables
        if not os.environ.get("SESSION_SECRET"):
            log.warning("SESSION_SECRET not set — using random secret (sessions won't survive restart)")
        if not os.environ.get("CESIUM_ION_TOKEN"):
            log.warning("CESIUM_ION_TOKEN not set — globe rendering may be limited")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            log.warning("ANTHROPIC_API_KEY not set — satellite info modal will show CelesTrak fallback")

        # Ensure DB is initialized on startup
        init_db(CFG)

        # ── Auto-create admin from env vars (for Render/cloud deploys) ───────
        # Set ADMIN_USER + ADMIN_PASS in environment to skip the --create-user step.
        # Only runs if users.json doesn't exist yet — safe to leave set permanently.
        _admin_user = os.environ.get("ADMIN_USER", "").strip().lower()
        _admin_pass = os.environ.get("ADMIN_PASS", "").strip()
        if _admin_user and _admin_pass:
            existing = _load_users(CFG)
            if _admin_user not in existing:
                create_user(_admin_user, _admin_pass, "admin", cfg=CFG)
                log.info(f"Auto-created admin user '{_admin_user}' from ADMIN_USER/ADMIN_PASS")
            else:
                # Always ensure role is admin
                if existing[_admin_user].get("role") != "admin":
                    existing[_admin_user]["role"] = "admin"
                    _save_users(existing, CFG)
                    log.info(f"Corrected role for '{_admin_user}' to admin")
                else:
                    log.info(f"Admin user '{_admin_user}' exists — OK")

        import webbrowser, threading
        port = int(os.environ.get("PORT", args.port))
        base_url = os.environ.get("VECTRASPACE_BASE_URL", f"http://localhost:{port}").rstrip("/")
        url = base_url
        log.info("=" * 60)
        log.info("VectraSpace v11 — Orbital Safety Platform")
        log.info(f"Dashboard: {url}/dashboard")
        log.info(f"Landing:   {url}/")
        log.info(f"API docs:  {url}/docs")
        log.info("=" * 60)

        api = build_api(CFG)

        if not args.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()

        port = int(os.environ.get("PORT", args.port))
        uvicorn.run(api, host=args.host, port=port)
