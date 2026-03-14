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
  @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0b0b0b; --panel: #111; --border: #222; --border2: #2a2a2a;
    --text: #e8e8e8; --muted: #666; --dim: #333;
    --accent: #e8ff00; --danger: #ff3b3b; --ok: #00d67a;
    --mono: 'IBM Plex Mono', monospace; --sans: 'Syne', sans-serif;
  }
  body { background: var(--bg); color: var(--text); font-family: var(--sans); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }
  .card { width: 100%; max-width: 380px; }
  .wordmark { font-family: var(--mono); font-size: 11px; letter-spacing: 0.15em; color: var(--muted); text-transform: uppercase; margin-bottom: 32px; }
  .wordmark em { color: var(--accent); font-style: normal; }
  h1 { font-family: var(--sans); font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 6px; }
  .sub { font-family: var(--mono); font-size: 10px; color: var(--muted); letter-spacing: 0.06em; margin-bottom: 28px; }
  label { display: block; font-family: var(--mono); font-size: 9px; letter-spacing: 0.12em; color: var(--muted); text-transform: uppercase; margin-bottom: 5px; }
  input[type=text], input[type=email], input[type=password] {
    width: 100%; background: var(--panel); border: 1px solid var(--border2);
    border-radius: 2px; color: var(--text); font-family: var(--mono);
    font-size: 13px; padding: 10px 12px; outline: none; transition: border-color 0.15s;
    margin-bottom: 16px;
  }
  input:focus { border-color: var(--accent); }
  button[type=submit], .btn {
    width: 100%; padding: 11px; background: var(--accent); border: none; border-radius: 2px;
    color: var(--bg); font-family: var(--mono); font-size: 11px; font-weight: 500;
    letter-spacing: 0.12em; text-transform: uppercase; cursor: pointer; transition: background 0.15s;
  }
  button[type=submit]:hover, .btn:hover { background: #f5ff4d; }
  .err { padding: 10px 12px; background: rgba(255,59,59,0.08); border: 1px solid rgba(255,59,59,0.3); border-radius: 2px; font-family: var(--mono); font-size: 10px; color: var(--danger); margin-bottom: 16px; letter-spacing: 0.04em; }
  .nav { margin-top: 22px; text-align: center; font-family: var(--mono); font-size: 10px; color: var(--muted); letter-spacing: 0.06em; }
  .nav a { color: var(--text); text-decoration: none; transition: color 0.15s; }
  .nav a:hover { color: var(--accent); }
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
<script src="https://accounts.google.com/gsi/client" async defer></script>
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

<style>
  @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');
  :root {
    --bg:       #0b0b0b;
    --panel:    #111111;
    --border:   #222222;
    --border2:  #2e2e2e;
    --text:     #e8e8e8;
    --muted:    #666666;
    --dim:      #333333;
    --accent:   #e8ff00;
    --danger:   #ff3b3b;
    --ok:       #00d67a;
    --warn:     #ff8c00;
    --panel-w:  320px;
    --mono:     'IBM Plex Mono', monospace;
    --sans:     'Syne', sans-serif;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 100%; height: 100%; overflow: hidden; background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 13px; }

  #app { display: flex; height: 100vh; }

  /* ── SIDEBAR ── */
  #sidebar {
    width: var(--panel-w);
    min-width: var(--panel-w);
    background: var(--panel);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    z-index: 10;
    transition: width 0.2s ease, min-width 0.2s ease;
  }
  #sidebar.collapsed { width: 44px; min-width: 44px; }
  #sidebar.collapsed .sidebar-collapsible { display: none; }
  #sidebar.collapsed #sidebar-toggle-btn { border-left: none; }

  #sidebar-toggle-btn {
    background: transparent;
    border: none;
    border-top: 1px solid var(--border);
    color: var(--dim);
    font-family: var(--mono);
    font-size: 16px;
    line-height: 1;
    padding: 12px;
    cursor: pointer;
    width: 100%;
    text-align: center;
    transition: color 0.15s, background 0.15s;
    flex-shrink: 0;
  }
  #sidebar-toggle-btn:hover { color: var(--text); background: var(--border); }

  #globe-container { flex: 1; position: relative; }
  #cesiumContainer { width: 100%; height: 100%; }

  /* ── SIDEBAR HEADER ── */
  #header {
    padding: 18px 16px 14px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  #header .wordmark {
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.15em;
    color: var(--accent);
    text-transform: uppercase;
    margin-bottom: 6px;
  }
  #header h1 {
    font-family: var(--sans);
    font-size: 15px;
    font-weight: 700;
    color: #fff;
    letter-spacing: -0.01em;
    line-height: 1.2;
    margin-bottom: 2px;
  }
  #header .build {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--muted);
    letter-spacing: 0.08em;
  }
  .header-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px;
  }
  .home-link {
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.1em;
    color: var(--muted);
    text-decoration: none;
    padding: 4px 8px;
    border: 1px solid var(--border2);
    border-radius: 2px;
    transition: all 0.15s;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .home-link:hover { color: var(--text); border-color: var(--dim); }

  /* ── USER BAR ── */
  #user-bar {
    padding: 7px 16px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    font-family: var(--mono);
    font-size: 9px;
    color: var(--muted);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-shrink: 0;
  }
  #user-bar .user-name { color: var(--accent); font-weight: 500; }
  #user-bar a { color: var(--muted); text-decoration: none; letter-spacing: 0.08em; }
  #user-bar a:hover { color: var(--text); }

  /* ── SCROLL AREA ── */
  #scroll { flex: 1; overflow-y: auto; padding: 14px 16px; }
  #scroll::-webkit-scrollbar { width: 3px; }
  #scroll::-webkit-scrollbar-thumb { background: var(--border2); }

  /* ── SECTIONS ── */
  .section { margin-bottom: 22px; }
  .section-title {
    font-family: var(--mono);
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 0.18em;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 10px;
    padding-bottom: 7px;
    border-bottom: 1px solid var(--border);
  }

  /* ── FIELDS ── */
  .field { margin-bottom: 10px; }
  .field label {
    display: block;
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.1em;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 4px;
  }
  .field input {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border2);
    border-radius: 3px;
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    padding: 6px 10px;
    outline: none;
    transition: border-color 0.15s;
  }
  .field input:focus { border-color: var(--accent); }
  .field .hint {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--dim);
    margin-top: 3px;
    letter-spacing: 0.05em;
  }

  /* ── REGIME GRID ── */
  .regime-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; }
  .regime-cell input { text-align: center; }
  .regime-label {
    font-family: var(--mono);
    font-size: 8px;
    letter-spacing: 0.1em;
    color: var(--muted);
    text-align: center;
    margin-bottom: 3px;
    text-transform: uppercase;
  }

  /* ── RUN BUTTON ── */
  #run-btn {
    width: 100%;
    padding: 11px 0;
    background: var(--accent);
    border: none;
    border-radius: 3px;
    color: var(--bg);
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
    transition: background 0.15s, opacity 0.15s;
    display: block;
  }
  #run-btn:hover { background: #f5ff4d; }
  #run-btn:disabled { opacity: 0.35; cursor: not-allowed; background: var(--accent); }
  #run-btn.running { background: var(--dim); color: var(--ok); border: 1px solid var(--ok); }

  #run-locked-msg {
    width: 100%;
    padding: 10px;
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 3px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    text-align: center;
  }

  /* ── FIRST RUN TIP ── */
  #first-run-tip {
    margin-top: 8px;
    padding: 10px 12px;
    background: rgba(232,255,0,0.04);
    border: 1px solid rgba(232,255,0,0.2);
    border-radius: 3px;
    font-family: var(--mono);
    font-size: 9px;
    color: var(--accent);
    line-height: 1.7;
    letter-spacing: 0.04em;
  }
  #first-run-tip .tip-sub { color: var(--muted); }
  #first-run-tip button {
    background: transparent; border: none;
    color: var(--muted); font-family: var(--mono);
    font-size: 9px; cursor: pointer; margin-top: 6px;
    letter-spacing: 0.08em; padding: 0;
  }
  #first-run-tip button:hover { color: var(--text); }

  /* ── DEMO BANNER ── */
  #demo-banner {
    margin-bottom: 12px;
    padding: 8px 10px;
    background: rgba(255,140,0,0.06);
    border: 1px solid rgba(255,140,0,0.25);
    border-radius: 3px;
    font-family: var(--mono);
    font-size: 9px;
    color: var(--warn);
    letter-spacing: 0.06em;
    line-height: 1.6;
  }
  #demo-banner a { color: var(--accent); text-decoration: none; }

  /* ── LOG PANEL ── */
  #log-panel {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 3px;
    height: 120px;
    overflow-y: auto;
    padding: 8px 10px;
    font-family: var(--mono);
    font-size: 10px;
    line-height: 1.65;
  }
  #log-panel::-webkit-scrollbar { width: 3px; }
  #log-panel::-webkit-scrollbar-thumb { background: var(--border2); }
  .log-line         { color: var(--muted); }
  .log-line.info    { color: #7ab8d4; }
  .log-line.ok      { color: var(--ok); }
  .log-line.warn    { color: var(--warn); }
  .log-line.error   { color: var(--danger); }

  /* ── CONJUNCTION CARDS ── */
  #results-list { display: flex; flex-direction: column; gap: 5px; }
  .conj-card {
    background: var(--bg);
    border: 1px solid var(--border);
    border-left: 2px solid var(--danger);
    border-radius: 3px;
    padding: 9px 10px;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
  }
  .conj-card:hover { border-color: var(--text); background: #161616; }
  .conj-card .sats { font-size: 11px; font-weight: 700; color: #fff; margin-bottom: 4px; font-family: var(--sans); }
  .conj-card .meta { display: flex; gap: 10px; font-family: var(--mono); font-size: 9px; }
  .conj-card .dist { color: var(--danger); font-weight: 500; }
  .conj-card .pc   { color: var(--warn); }
  .conj-card .time { color: var(--muted); }
  #no-results { color: var(--muted); font-family: var(--mono); font-size: 10px; text-align: center; padding: 20px 0; letter-spacing: 0.08em; }

  /* ── TOP PAIRS ── */
  #top-pairs-list .tp-row {
    display: flex; justify-content: space-between;
    padding: 5px 0; border-bottom: 1px solid var(--border);
    font-size: 9px; font-family: var(--mono); gap: 6px;
  }
  #top-pairs-list .tp-row .tp-sats { color: var(--text); flex: 1; }
  #top-pairs-list .tp-row .tp-count { color: var(--accent); }
  #top-pairs-list .tp-row .tp-dist  { color: var(--danger); }

  /* ── TLE FRESHNESS ── */
  #tle-freshness-bar {
    padding: 7px 16px;
    border-top: 1px solid var(--border);
    background: var(--bg);
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.06em;
    display: flex;
    align-items: center;
    gap: 7px;
    color: var(--muted);
    flex-shrink: 0;
  }
  #tle-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }

  /* ── STATUS BAR ── */
  #status-bar {
    padding: 8px 16px;
    border-top: 1px solid var(--border);
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.06em;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }
  #status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--dim); flex-shrink: 0; }
  #status-dot.ready   { background: var(--ok); }
  #status-dot.running { background: var(--accent); animation: blink 1s infinite; }
  #status-dot.error   { background: var(--danger); }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }
  #status-text { color: var(--muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ── RISK SLIDER ── */
  #risk-slider-wrap { margin: 4px 0; }
  #risk-track { position: relative; padding-bottom: 20px; }
  #risk-track input[type=range] {
    width: 100%; -webkit-appearance: none; appearance: none;
    height: 3px; border-radius: 1px; outline: none;
    background: linear-gradient(to right, var(--ok), var(--warn), var(--danger));
    cursor: pointer;
  }
  #risk-track input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; width: 13px; height: 13px;
    border-radius: 50%; background: var(--accent);
    border: 2px solid var(--bg); cursor: pointer;
  }
  #risk-labels { display: flex; justify-content: space-between;
    font-size: 8px; color: var(--muted); font-family: var(--mono);
    letter-spacing: 0.06em; margin-top: 4px; }
  #risk-display { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
  #risk-name    { font-family: var(--mono); font-size: 10px; font-weight: 500; color: var(--ok); }
  #risk-pc-val  { font-family: var(--mono); font-size: 9px; color: var(--muted); }

  /* ── GLOBE OVERLAYS ── */
  #globe-header {
    position: absolute; top: 16px; left: 16px;
    background: rgba(11,11,11,0.88);
    border: 1px solid var(--border2);
    border-radius: 3px;
    padding: 7px 12px;
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.14em;
    color: var(--accent);
    backdrop-filter: blur(6px);
    pointer-events: none;
    text-transform: uppercase;
  }
  #sat-counter {
    position: absolute; top: 16px; right: 16px;
    background: rgba(11,11,11,0.88);
    border: 1px solid var(--border2);
    border-radius: 3px;
    padding: 7px 12px;
    font-family: var(--mono);
    font-size: 9px;
    color: var(--muted);
    backdrop-filter: blur(6px);
    pointer-events: none;
    text-align: right;
  }
  #sat-counter span { color: var(--text); font-size: 20px; font-weight: 700; font-family: var(--sans); display: block; line-height: 1.1; }

  /* ── TOOLTIP ── */
  #tooltip {
    position: absolute;
    background: rgba(11,11,11,0.96);
    border: 1px solid var(--border2);
    border-radius: 3px;
    padding: 12px 14px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--text);
    pointer-events: all;
    display: none;
    max-width: 250px;
    backdrop-filter: blur(8px);
    z-index: 100;
  }
  #tooltip .tt-title { color: var(--danger); font-size: 11px; font-weight: 700; margin-bottom: 7px; font-family: var(--sans); }
  #tooltip .tt-row { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 3px; }
  #tooltip .tt-key { color: var(--muted); }
  #tooltip .tt-val { color: #fff; font-weight: 500; }
  #tooltip .tt-link { color: var(--accent); cursor: pointer; font-size: 9px; margin-top: 8px; display: block; text-align: center; letter-spacing: 0.08em; text-decoration: underline; }

  /* ── GLOBE CONTROLS ── */
  #globe-controls {
    position: absolute; bottom: 24px; left: 50%; transform: translateX(-50%);
    display: flex; gap: 4px; align-items: center;
    background: rgba(11,11,11,0.92);
    border: 1px solid var(--border2);
    border-radius: 3px;
    padding: 6px 10px;
    backdrop-filter: blur(8px);
    z-index: 10;
  }
  .ctrl-btn {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 2px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.08em;
    padding: 5px 9px;
    cursor: pointer;
    transition: all 0.12s;
    white-space: nowrap;
    text-transform: uppercase;
  }
  .ctrl-btn:hover { color: var(--text); border-color: var(--border2); }
  .ctrl-btn.active       { color: var(--accent); border-color: var(--accent); background: rgba(232,255,0,0.05); }
  .ctrl-btn.active-green { color: var(--ok); border-color: var(--ok); background: rgba(0,214,122,0.05); }
  .ctrl-divider { width: 1px; height: 18px; background: var(--border); margin: 0 3px; }
  #speed-label { font-family: var(--mono); font-size: 9px; color: var(--muted); letter-spacing: 0.06em; }

  /* ── SAT MODAL ── */
  #sat-modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.75); z-index: 200;
    align-items: center; justify-content: center;
    backdrop-filter: blur(4px);
  }
  #sat-modal-overlay.open { display: flex; }
  #sat-modal {
    background: var(--panel); border: 1px solid var(--border2);
    border-radius: 4px; width: 500px; max-height: 80vh;
    overflow-y: auto; box-shadow: 0 24px 80px rgba(0,0,0,0.6);
  }
  #sat-modal::-webkit-scrollbar { width: 3px; }
  #sat-modal::-webkit-scrollbar-thumb { background: var(--border2); }
  #sat-modal-header {
    padding: 16px 18px 14px; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: flex-start;
    background: var(--bg);
  }
  #sat-modal-header h2 { font-size: 14px; font-weight: 700; color: #fff; font-family: var(--sans); }
  #sat-modal-header .badge { font-family: var(--mono); font-size: 9px; color: var(--accent); letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 3px; }
  #sat-modal-close {
    background: transparent; border: 1px solid var(--border2);
    border-radius: 2px; color: var(--muted); cursor: pointer;
    font-size: 14px; padding: 2px 8px; transition: all 0.12s; font-family: var(--mono);
  }
  #sat-modal-close:hover { border-color: var(--danger); color: var(--danger); }
  #sat-modal-body { padding: 14px 18px; }
  .sat-field { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 11px; }
  .sat-field:last-child { border-bottom: none; }
  .sat-field .sf-key { color: var(--muted); font-family: var(--mono); font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase; }
  .sat-field .sf-val { color: #fff; font-weight: 600; text-align: right; max-width: 60%; font-family: var(--mono); }
  #sat-modal-loading { text-align: center; padding: 28px; color: var(--muted); font-family: var(--mono); font-size: 10px; letter-spacing: 0.1em; }
  #sat-modal-error   { padding: 20px; color: var(--danger); font-family: var(--mono); font-size: 10px; text-align: center; }

  /* ── CESIUM INIT OVERLAY ── */
  #cesium-init-overlay {
    position: absolute; inset: 0; z-index: 50;
    background: var(--bg);
    display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 20px;
    pointer-events: none;
  }

  /* ── HAMBURGER (mobile) ── */
  #hamburger {
    display: none; position: fixed; top: 12px; left: 12px; z-index: 200;
    background: rgba(11,11,11,0.92); border: 1px solid var(--border2); border-radius: 3px;
    width: 40px; height: 40px; flex-direction: column;
    align-items: center; justify-content: center; gap: 5px;
    cursor: pointer; backdrop-filter: blur(8px); transition: border-color 0.15s;
  }
  #hamburger:hover { border-color: var(--accent); }
  #hamburger span { display: block; width: 18px; height: 1.5px; background: var(--text); border-radius: 1px; transition: all 0.22s; }
  #hamburger.open span:nth-child(1) { transform: translateY(6.5px) rotate(45deg); }
  #hamburger.open span:nth-child(2) { opacity: 0; transform: scaleX(0); }
  #hamburger.open span:nth-child(3) { transform: translateY(-6.5px) rotate(-45deg); }

  #sidebar-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.65); z-index: 150; backdrop-filter: blur(2px);
  }

  @media (max-width: 768px) {
    #hamburger { display: flex; }
    #sidebar-overlay.active { display: block; }
    #sidebar {
      position: fixed; top: 0; left: 0; height: 100vh; z-index: 160;
      transform: translateX(-100%); transition: transform 0.28s ease;
      width: min(320px, 92vw) !important; min-width: unset !important;
      box-shadow: 4px 0 40px rgba(0,0,0,0.8);
    }
    #sidebar.open { transform: translateX(0); }
    #globe-container, #app { width: 100vw; height: 100vh; height: 100dvh; }
    #globe-header { top: 12px; left: 60px; font-size: 8px; padding: 5px 9px; }
    #sat-counter  { top: 12px; right: 12px; font-size: 8px; padding: 5px 9px; }
    #sat-counter span { font-size: 14px; }
    #globe-controls { bottom: max(16px, env(safe-area-inset-bottom,16px)); left: 8px; right: 8px; transform: none; overflow-x: auto; flex-wrap: nowrap; min-height: 40px; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
    #globe-controls::-webkit-scrollbar { display: none; }
    .ctrl-btn { padding: 10px 14px; flex-shrink: 0; min-height: 44px; font-size: 11px; }
    #tooltip { left: 8px !important; right: 8px !important; top: auto !important; bottom: 72px; max-width: none; }
    #sat-modal-overlay { align-items: flex-end; }
    #sat-modal { width: 100%; border-radius: 8px 8px 0 0; max-height: 85vh; }
    #log-panel { height: 90px; }
    #status-bar { padding: 6px 12px; }
    #run-btn { padding: 14px 0; font-size: 13px; }
    #scroll { padding: 16px 14px; }
    .section-title { font-size: 10px; }
    .field input { padding: 9px 12px; font-size: 13px; }
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
      <div class="header-row">
        <a href="/welcome" class="wordmark" style="cursor:pointer;text-decoration:none;">VECTRA<em>SPACE</em></a>
        <a href="/welcome" class="home-link">← Home</a>
      </div>
      <h1>Mission Control</h1>
      <div class="build">Orbital Safety Platform · v11</div>
    </div>
    <!-- User bar: populated by /me endpoint -->
    <div id="user-bar">
      <span id="user-label">Loading...</span>
      <span id="user-actions"></span>
    </div>
    <div id="google-signin-bar" style="display:none;padding:8px 16px;border-bottom:1px solid var(--border);background:var(--bg);">
      <div id="g_id_onload"
           data-client_id="__GOOGLE_CLIENT_ID__"
           data-context="signin"
           data-ux_mode="popup"
           data-callback="onGoogleSignIn"
           data-auto_prompt="false">
      </div>
      <div class="g_id_signin"
           data-type="standard"
           data-shape="rectangular"
           data-theme="filled_black"
           data-text="signin_with"
           data-size="medium"
           data-logo_alignment="left"
           data-width="100%">
      </div>
      <div style="font-family:var(--mono);font-size:9px;color:var(--dim);margin-top:6px;letter-spacing:.05em;">
        Or <a href="/login" style="color:var(--muted);text-decoration:underline;">sign in with username</a>
        · <a href="/signup" style="color:var(--muted);text-decoration:underline;">create account</a>
      </div>
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
        <div class="regime-grid">
          <div>
            <div class="regime-label">LEO ≤300</div>
            <input type="number" id="num_leo" placeholder="100" min="1" max="300" class="regime-cell">
          </div>
          <div>
            <div class="regime-label">MEO ≤100</div>
            <input type="number" id="num_meo" placeholder="50" min="1" max="100" class="regime-cell">
          </div>
          <div>
            <div class="regime-label">GEO ≤50</div>
            <input type="number" id="num_geo" placeholder="20" min="1" max="50" class="regime-cell">
          </div>
        </div>
        <div class="field hint" style="margin-top:6px;font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:.05em;">
          Server limits: LEO 300 · MEO 100 · GEO 50
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
        background:var(--bg);
        display:flex;flex-direction:column;
        align-items:center;justify-content:center;gap:20px;
        pointer-events:none;">
      <div style="font-family:var(--mono);font-size:11px;font-weight:700;
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

async function onGoogleSignIn(credentialResponse) {
  fetch('/auth/google', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({credential: credentialResponse.credential})
  }).then(r => r.json()).then(d => {
    if (d.ok) { initUserState(); }
    else { addLog('Google sign-in failed: ' + (d.error || 'Unknown error'), 'error'); }
  }).catch(() => addLog('Google sign-in request failed', 'error'));
}

function initUserState() {
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
    const gbarHide = document.getElementById('google-signin-bar'); if (gbarHide) gbarHide.style.display = 'none';
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
    const gbar = document.getElementById('google-signin-bar'); if (gbar) gbar.style.display = 'block';
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

LANDING_HTML  = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="VectraSpace — Real-time orbital conjunction detection. SGP4 propagation, covariance-based Pc estimation, automated alerting.">
<title>VectraSpace — Orbital Safety Platform</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script defer src="https://cloud.umami.is/script.js" data-website-id="4e12fc04-8b26-4e42-8b69-0700a95c7d30"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0a0a;--surface:#111;--border:#222;--border2:#2c2c2c;
  --text:#ebebeb;--muted:#606060;--dim:#303030;
  --accent:#e2ff00;--ok:#00d26a;--danger:#ff3535;--warn:#ff8800;
  --mono:'IBM Plex Mono',monospace;--sans:'Syne',sans-serif;
}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:15px;line-height:1.5;overflow-x:hidden}
a{color:inherit;text-decoration:none}
::selection{background:var(--accent);color:var(--bg)}

/* NAV */
nav{position:fixed;top:0;left:0;right:0;z-index:999;height:54px;padding:0 40px;
    display:flex;align-items:center;justify-content:space-between;
    border-bottom:1px solid transparent;transition:background .2s,border-color .2s}
nav.on{background:rgba(10,10,10,.97);border-bottom-color:var(--border);backdrop-filter:blur(10px)}
.logo{font-family:var(--mono);font-size:12px;letter-spacing:.14em;color:#fff;font-weight:500}
.logo em{color:var(--accent);font-style:normal}
.nav-mid{display:flex;gap:30px;list-style:none}
.nav-mid a{font-family:var(--mono);font-size:10px;letter-spacing:.1em;color:var(--muted);
           text-transform:uppercase;transition:color .15s}
.nav-mid a:hover{color:var(--text)}
.nav-end{display:flex;gap:8px}
.nb{font-family:var(--mono);font-size:10px;letter-spacing:.1em;padding:7px 16px;
    border-radius:2px;font-weight:500;transition:all .15s;cursor:pointer;text-transform:uppercase}
.nb-o{color:var(--muted);border:1px solid var(--border2);background:transparent}
.nb-o:hover{color:var(--text);border-color:var(--dim)}
.nb-s{color:var(--bg);background:var(--text);border:1px solid var(--text)}
.nb-s:hover{background:var(--accent);border-color:var(--accent)}
@media(max-width:768px){.nav-mid{display:none}nav{padding:0 20px}}

/* LAYOUT */
.w{max-width:1120px;margin:0 auto;padding:0 40px}
@media(max-width:640px){.w{padding:0 20px}}
.rule{height:1px;background:var(--border)}

/* HERO */
#hero{min-height:100vh;display:flex;align-items:center;padding:110px 0 80px}
.hero-wrap{max-width:780px}
.htag{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);
      font-size:10px;letter-spacing:.14em;color:var(--ok);text-transform:uppercase;margin-bottom:32px}
.hdot{width:5px;height:5px;border-radius:50%;background:var(--ok);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
h1{font-family:var(--sans);font-weight:800;font-size:clamp(50px,8.5vw,90px);
   line-height:.95;letter-spacing:-.03em;color:#fff;margin-bottom:28px}
h1 .lo{color:var(--muted);font-weight:400}
.hdesc{font-size:17px;font-weight:400;color:var(--muted);line-height:1.75;
       max-width:540px;margin-bottom:40px}
.hact{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:64px}
.bp{font-family:var(--mono);font-size:11px;font-weight:500;letter-spacing:.1em;
    padding:12px 26px;border-radius:2px;text-transform:uppercase;
    background:var(--accent);color:var(--bg);border:1px solid var(--accent);transition:all .15s;cursor:pointer}
.bp:hover{background:#eeff44;border-color:#eeff44}
.bs{font-family:var(--mono);font-size:11px;letter-spacing:.1em;padding:12px 26px;
    border-radius:2px;text-transform:uppercase;background:transparent;
    color:var(--muted);border:1px solid var(--border2);transition:all .15s;cursor:pointer}
.bs:hover{color:var(--text);border-color:var(--dim)}
.hstats{display:grid;grid-template-columns:repeat(4,1fr);
        border-top:1px solid var(--border);padding-top:32px}
.hs{padding-right:24px}
.hs+.hs{padding-left:24px;border-left:1px solid var(--border)}
.sn{font-family:var(--sans);font-size:28px;font-weight:700;letter-spacing:-.02em;
    color:#fff;line-height:1;margin-bottom:5px}
.sl{font-family:var(--mono);font-size:9px;letter-spacing:.1em;color:var(--muted);text-transform:uppercase}
@media(max-width:600px){
  .hstats{grid-template-columns:1fr 1fr;gap:20px}
  .hs+.hs{border-left:none;padding-left:0}
  .hs:nth-child(3),.hs:nth-child(4){border-top:1px solid var(--border);padding-top:20px}
}

/* STATUS STRIP */
.sstrip{border-top:1px solid var(--border);border-bottom:1px solid var(--border);
        padding:10px 40px;background:var(--surface);
        display:flex;align-items:center;gap:24px;overflow-x:auto;white-space:nowrap}
.si{display:flex;align-items:center;gap:7px;flex-shrink:0}
.sdot{width:5px;height:5px;border-radius:50%}
.sdot.ok{background:var(--ok)}
.slb{font-family:var(--mono);font-size:9px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase}
.sv{font-family:var(--mono);font-size:9px;color:var(--text)}
.sr{width:1px;height:12px;background:var(--border2);flex-shrink:0}

/* SECTIONS */
section.blk{padding:88px 0}
.sec-lbl{font-family:var(--mono);font-size:9px;letter-spacing:.2em;color:var(--muted);
         text-transform:uppercase;margin-bottom:16px}
h2{font-family:var(--sans);font-size:clamp(28px,4vw,46px);font-weight:700;
   letter-spacing:-.025em;line-height:1.05;margin-bottom:16px}
.sec-body{font-size:16px;font-weight:400;color:var(--muted);line-height:1.75;max-width:500px}

/* PIPELINE */
.pipe{display:grid;grid-template-columns:repeat(4,1fr);
      border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-top:48px}
.ps{padding:28px 22px;border-right:1px solid var(--border);background:var(--surface);transition:background .15s}
.ps:last-child{border-right:none}
.ps:hover{background:#161616}
.pn{font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:.1em;margin-bottom:12px;text-transform:uppercase}
.pt{font-family:var(--sans);font-size:14px;font-weight:700;color:#fff;margin-bottom:7px}
.pb{font-family:var(--mono);font-size:11px;color:var(--muted);line-height:1.7}
@media(max-width:760px){.pipe{grid-template-columns:1fr 1fr}.ps{border-bottom:1px solid var(--border)}}
@media(max-width:400px){.pipe{grid-template-columns:1fr}}

/* FEATURE GRID */
.fgrid{display:grid;grid-template-columns:1fr 1fr;
       border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-top:48px}
.fi{padding:30px 26px;border-right:1px solid var(--border);border-bottom:1px solid var(--border);
    background:var(--surface);transition:background .15s}
.fi:hover{background:#161616}
.fi:nth-child(2n){border-right:none}
.fi:nth-last-child(-n+2){border-bottom:none}
.ftag{font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:.12em;
      margin-bottom:11px;text-transform:uppercase}
.ft{font-family:var(--sans);font-size:14px;font-weight:700;color:#fff;margin-bottom:7px}
.fb{font-family:var(--mono);font-size:11px;color:var(--muted);line-height:1.7}
@media(max-width:600px){.fgrid{grid-template-columns:1fr}.fi{border-right:none}}

/* PERFORMANCE */
.pgrid{display:grid;grid-template-columns:repeat(3,1fr);
       border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-top:48px}
.pc{padding:34px 26px;border-right:1px solid var(--border);background:var(--surface)}
.pc:last-child{border-right:none}
.pn2{font-family:var(--sans);font-size:52px;font-weight:800;letter-spacing:-.04em;
     color:#fff;line-height:1;margin-bottom:6px}
.pu{font-size:22px;font-weight:400;color:var(--muted)}
.pl{font-family:var(--sans);font-size:13px;font-weight:600;color:var(--text);margin-bottom:5px}
.pd{font-family:var(--mono);font-size:10px;color:var(--muted);line-height:1.6}
@media(max-width:600px){.pgrid{grid-template-columns:1fr}.pc{border-right:none;border-bottom:1px solid var(--border)}.pc:last-child{border-bottom:none}}

/* ACCESS */
.agrid{display:grid;grid-template-columns:repeat(3,1fr);
       border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-top:48px}
.ac{padding:30px 24px;border-right:1px solid var(--border);background:var(--surface)}
.ac:last-child{border-right:none}
.ac.feat{background:var(--text)}
.ac.feat .at,.ac.feat .apr,.ac.feat .adesc{color:var(--bg)}
.ac.feat .afeat{color:#333;border-color:#d0d0d0}
.ac.feat .afeat::before{color:#888}
.ac.feat .acta{background:var(--bg);color:var(--text);border-color:var(--bg)}
.ac.feat .acta:hover{background:#1a1a1a}
.at{font-family:var(--mono);font-size:9px;letter-spacing:.15em;color:var(--muted);
    text-transform:uppercase;margin-bottom:16px}
.apr{font-family:var(--sans);font-size:34px;font-weight:700;letter-spacing:-.02em;
     color:#fff;line-height:1;margin-bottom:4px}
.apr span{font-size:13px;font-weight:400;color:var(--muted)}
.adesc{font-family:var(--mono);font-size:10px;color:var(--muted);margin-bottom:20px;line-height:1.65}
.afeats{list-style:none;margin-bottom:22px}
.afeat{font-family:var(--mono);font-size:10px;color:var(--muted);padding:8px 0;
       border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:8px;line-height:1.4}
.afeat::before{content:"→";color:var(--dim);flex-shrink:0}
.acta{display:block;text-align:center;font-family:var(--mono);font-size:10px;font-weight:500;
      letter-spacing:.1em;padding:10px 16px;border-radius:2px;text-transform:uppercase;
      border:1px solid var(--border2);color:var(--muted);transition:all .15s;cursor:pointer}
.acta:hover{color:var(--text);border-color:var(--dim)}
@media(max-width:720px){.agrid{grid-template-columns:1fr}.ac{border-right:none;border-bottom:1px solid var(--border)}.ac:last-child{border-bottom:none}}

/* RESEARCH */
.rblock{border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-top:48px;background:var(--surface)}
.rh{padding:24px 26px;border-bottom:1px solid var(--border);background:var(--bg);
    display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.rh h3{font-family:var(--sans);font-size:15px;font-weight:700;color:#fff}
.rtag{font-family:var(--mono);font-size:9px;letter-spacing:.12em;color:var(--ok);
      text-transform:uppercase;border:1px solid rgba(0,210,106,.25);padding:4px 10px;border-radius:2px}
.rbody{display:grid;grid-template-columns:1fr 1fr 1fr}
.ri{padding:22px 26px;border-right:1px solid var(--border)}
.ri:last-child{border-right:none}
.rl{font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px}
.rv{font-family:var(--mono);font-size:11px;color:var(--muted);line-height:1.65}
@media(max-width:600px){.rbody{grid-template-columns:1fr}.ri{border-right:none;border-bottom:1px solid var(--border)}.ri:last-child{border-bottom:none}}

/* ADMIN STRIP */
.astrip{border:1px solid var(--border);border-radius:2px;padding:26px;background:var(--surface);
        margin-top:20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px}
.astrip h3{font-family:var(--sans);font-size:15px;font-weight:700;color:#fff;margin-bottom:3px}
.astrip p{font-family:var(--mono);font-size:10px;color:var(--muted)}

/* FOOTER */
footer{border-top:1px solid var(--border);padding:32px 40px}
.fi2{max-width:1120px;margin:0 auto;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px}
.flogo{font-family:var(--mono);font-size:11px;color:var(--dim);letter-spacing:.1em}
.flinks{display:flex;gap:20px}
.flinks a{font-family:var(--mono);font-size:10px;color:var(--dim);letter-spacing:.08em;transition:color .15s}
.flinks a:hover{color:var(--text)}
.flegal{width:100%;max-width:1120px;margin:14px auto 0;padding-top:14px;
        border-top:1px solid var(--border);font-family:var(--mono);font-size:9px;
        color:var(--dim);line-height:1.7;letter-spacing:.04em}

/* REVEAL */
.rv2{opacity:0;transform:translateY(16px);transition:opacity .5s ease,transform .5s ease}
.rv2.on{opacity:1;transform:none}
</style>
</head>
<body>

<nav id="nav">
  <a href="#" class="logo">VECTRA<em>SPACE</em></a>
  <ul class="nav-mid">
    <li><a href="#pipeline">Pipeline</a></li>
    <li><a href="#capabilities">Capabilities</a></li>
    <li><a href="#performance">Performance</a></li>
    <li><a href="#access">Access</a></li>
  </ul>
  <div class="nav-end">
    <a href="/login" class="nb nb-o">Sign In</a>
    <a href="/signup" class="nb nb-s">Get Access</a>
  </div>
</nav>

<section id="hero">
  <div class="w">
    <div class="hero-wrap rv2">
      <div class="htag"><span class="hdot"></span>System Operational — v11.0</div>
      <h1>Orbital<br><span class="lo">safety</span><br>infrastructure.</h1>
      <p class="hdesc">Real-time conjunction detection across LEO, MEO, and GEO. Vectorized SGP4 propagation, Pc estimation, CDM generation, and multi-channel alerting — autonomous, every six hours.</p>
      <div class="hact">
        <a href="/dashboard" class="bp">Open Dashboard</a>
        <a href="/research" class="bs">Research Portal</a>
        <a href="#pipeline" class="bs">How It Works</a>
      </div>
      <div class="hstats">
        <div class="hs"><div class="sn">6h</div><div class="sl">Autonomous scan cycle</div></div>
        <div class="hs"><div class="sn">3</div><div class="sl">Orbital regimes</div></div>
        <div class="hs"><div class="sn">50×</div><div class="sl">Vectorized propagation</div></div>
        <div class="hs"><div class="sn">4</div><div class="sl">Alert channels</div></div>
      </div>
    </div>
  </div>
</section>

<div class="sstrip">
  <div class="si"><span class="sdot ok"></span><span class="slb">System</span><span class="sv">Nominal</span></div>
  <div class="sr"></div>
  <div class="si"><span class="slb">TLE Source</span><span class="sv">CelesTrak + Space-Track</span></div>
  <div class="sr"></div>
  <div class="si"><span class="slb">Pc Method</span><span class="sv">Alfriend-Akella</span></div>
  <div class="sr"></div>
  <div class="si"><span class="slb">CDM Format</span><span class="sv">CCSDS 508.0-B-1</span></div>
  <div class="sr"></div>
  <div class="si"><span class="slb">Scan Interval</span><span class="sv">6 h autonomous</span></div>
  <div class="sr"></div>
  <div class="si"><span class="slb">Research Portal</span><span class="sv">Public Access</span></div>
</div>

<div class="rule"></div>

<section class="blk" id="pipeline">
  <div class="w">
    <div class="rv2"><div class="sec-lbl">01 — Detection Pipeline</div>
    <h2>From TLE to threat<br>assessment — automatically.</h2>
    <p class="sec-body">VectraSpace ingests live orbital elements, propagates the full catalog, screens every pair, and delivers reports without operator intervention.</p></div>
    <div class="pipe rv2">
      <div class="ps"><div class="pn">01 — Ingest</div><div class="pt">TLE Acquisition</div><div class="pb">Live two-line elements from CelesTrak and Space-Track. Active catalog refreshed before each scan cycle.</div></div>
      <div class="ps"><div class="pn">02 — Propagate</div><div class="pt">SGP4 Propagation</div><div class="pb">Vectorized NumPy propagation at 1-minute intervals over a 24-hour window. 50× faster than sequential methods.</div></div>
      <div class="ps"><div class="pn">03 — Screen</div><div class="pt">Conjunction Detection</div><div class="pb">Chunk-based pair screening across all tracked objects. Sub-threshold pairs refined with golden-section TCA search.</div></div>
      <div class="ps"><div class="pn">04 — Assess</div><div class="pt">Pc Estimation &amp; Reporting</div><div class="pb">Alfriend-Akella probability of collision. CCSDS-compliant CDM output with multi-channel alerting.</div></div>
    </div>
  </div>
</section>

<div class="rule"></div>

<section class="blk" id="capabilities">
  <div class="w">
    <div class="rv2"><div class="sec-lbl">02 — Capabilities</div>
    <h2>Every component built<br>for operational use.</h2>
    <p class="sec-body">Purpose-built for satellite operators — not repurposed from research tools.</p></div>
    <div class="fgrid rv2">
      <div class="fi"><div class="ftag">Detection</div><div class="ft">Real-Time Conjunction Screening</div><div class="fb">Full active catalog across LEO, MEO, and GEO. Configurable miss-distance thresholds, time windows, and Pc alert levels per user.</div></div>
      <div class="fi"><div class="ftag">Visualization</div><div class="ft">3D Orbital Globe</div><div class="fb">Cesium Ion photorealistic globe with live satellite positions, conjunction markers, AI-powered satellite identification, and orbit trails.</div></div>
      <div class="fi"><div class="ftag">Alerting</div><div class="ft">Multi-Channel Notifications</div><div class="fb">SMTP email, SMS, Slack webhook, and Pushover. Per-user threshold controls. Fired automatically each scan cycle.</div></div>
      <div class="fi"><div class="ftag">Reporting</div><div class="ft">CCSDS CDM Generation</div><div class="fb">Conjunction Data Messages per CCSDS 508.0-B-1. Downloadable per event with full metadata, covariance, and Pc fields.</div></div>
      <div class="fi"><div class="ftag">Automation</div><div class="ft">Autonomous 6-Hour Cycles</div><div class="fb">Background task runs every six hours without operator action. Dashboard shows current state immediately on load.</div></div>
      <div class="fi"><div class="ftag">Research</div><div class="ft">Open Research Portal</div><div class="fb">Public data portal with conjunction history, Pc distributions, miss distance analytics, and CSV/JSON/TLE exports. No login required.</div></div>
    </div>
  </div>
</section>

<div class="rule"></div>

<section class="blk" id="performance">
  <div class="w">
    <div class="rv2"><div class="sec-lbl">03 — Performance</div>
    <h2>Numbers that matter<br>in orbit.</h2>
    <p class="sec-body">The same physics as major SSA providers. No $500K enterprise contract required.</p></div>
    <div class="pgrid rv2">
      <div class="pc"><div class="pn2">50<span class="pu">×</span></div><div class="pl">Propagation speedup</div><div class="pd">Vectorized NumPy SGP4 vs.<br>sequential per-satellite iteration</div></div>
      <div class="pc"><div class="pn2">1<span class="pu">min</span></div><div class="pl">Propagation resolution</div><div class="pd">1-minute timestep across<br>full 24-hour screening window</div></div>
      <div class="pc"><div class="pn2">6<span class="pu">h</span></div><div class="pl">Autonomous scan interval</div><div class="pd">Background task — no operator<br>action required between cycles</div></div>
    </div>
  </div>
</section>

<div class="rule"></div>

<section class="blk" id="access">
  <div class="w">
    <div class="rv2"><div class="sec-lbl">04 — Access</div>
    <h2>Scaled for every<br>tier of operator.</h2>
    <p class="sec-body">From independent researchers to commercial satellite operators.</p></div>
    <div class="agrid rv2">
      <div class="ac"><div class="at">Researcher</div><div class="apr">Free</div><div class="adesc">Open access to conjunction data, exports, analytics. No account required.</div><ul class="afeats"><li class="afeat">Conjunction history &amp; charts</li><li class="afeat">CSV &amp; JSON export</li><li class="afeat">TLE catalog download</li><li class="afeat">Pc distribution analytics</li></ul><a href="/research" class="acta">Open Research Portal</a></div>
      <div class="ac feat"><div class="at">Operator</div><div class="apr">$299<span>/month</span></div><div class="adesc">Full platform. Run scans, receive alerts, download CDMs, configure thresholds.</div><ul class="afeats"><li class="afeat">Unlimited scan execution</li><li class="afeat">Multi-channel alerting</li><li class="afeat">CDM download per event</li><li class="afeat">3D globe + satellite info</li><li class="afeat">Per-user threshold controls</li></ul><a href="/signup" class="acta">Get Access</a></div>
      <div class="ac"><div class="at">Enterprise</div><div class="apr">Contact</div><div class="adesc">Dedicated instances, SLA guarantees, API access, white-label deployment.</div><ul class="afeats"><li class="afeat">REST API access</li><li class="afeat">Custom scan windows</li><li class="afeat">SLA guarantee</li><li class="afeat">White-label deployment</li><li class="afeat">Priority support</li></ul><a href="mailto:trumanheaston@gmail.com" class="acta">Contact</a></div>
    </div>
    <div class="astrip rv2">
      <div><h3>Platform Administration</h3><p>User management, scan analytics, system health — admin login required.</p></div>
      <a href="/login?next=/admin" class="bs">Admin Console</a>
    </div>
  </div>
</section>

<div class="rule"></div>

<section class="blk" id="research-section">
  <div class="w">
    <div class="rv2"><div class="sec-lbl">05 — Research</div>
    <h2>Open data for<br>the research community.</h2>
    <p class="sec-body">All conjunction data is publicly accessible in standard formats for academic integration.</p></div>
    <div class="rblock rv2">
      <div class="rh"><h3>Research Data Portal</h3><span class="rtag">Public · No Login Required</span></div>
      <div class="rbody">
        <div class="ri"><div class="rl">Exports</div><div class="rv">CSV and JSON conjunction datasets with full Pc, miss distance, relative velocity, and TCA fields</div></div>
        <div class="ri"><div class="rl">TLE Catalog</div><div class="rv">Full TLE catalog export in JSON and CSV from each autonomous scan cycle</div></div>
        <div class="ri"><div class="rl">Analytics</div><div class="rv">Pc distributions, miss distance histograms, relative velocity profiles, temporal charts</div></div>
      </div>
    </div>
    <div style="margin-top:18px"><a href="/research" class="bp">Open Research Portal</a></div>
  </div>
</section>

<div class="rule"></div>

<footer>
  <div class="fi2">
    <div class="flogo">VECTRASPACE · V11.0</div>
    <div class="flinks">
      <a href="/dashboard">Dashboard</a>
      <a href="/research">Research</a>
      <a href="/login">Sign In</a>
      <a href="/signup">Get Access</a>
      <a href="mailto:trumanheaston@gmail.com">Contact</a>
    </div>
    <div class="flegal">VectraSpace is an independent orbital safety platform. TLE data sourced from CelesTrak and Space-Track. Pc values are screening estimates only and should not be used as the sole basis for operational decisions. © 2026 VectraSpace.</div>
  </div>
</footer>

<script>
window.addEventListener('scroll',()=>{
  document.getElementById('nav').classList.toggle('on',scrollY>40);
});
const obs=new IntersectionObserver(es=>{
  es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('on');obs.unobserve(e.target);}});
},{threshold:.07});
document.querySelectorAll('.rv2').forEach(el=>obs.observe(el));
</script>
</body>
</html>
"""

RESEARCH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VectraSpace — Research Portal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script defer src="https://cloud.umami.is/script.js" data-website-id="4e12fc04-8b26-4e42-8b69-0700a95c7d30"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0a0a;--surface:#111;--border:#222;--border2:#2c2c2c;
  --text:#ebebeb;--muted:#606060;--dim:#303030;
  --accent:#e2ff00;--ok:#00d26a;--danger:#ff3535;--warn:#ff8800;
  --mono:'IBM Plex Mono',monospace;--sans:'Syne',sans-serif;
}
html,body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh}
a{color:inherit;text-decoration:none}
::selection{background:var(--accent);color:var(--bg)}
nav{height:54px;padding:0 32px;display:flex;align-items:center;justify-content:space-between;
    border-bottom:1px solid var(--border);background:var(--bg)}
.logo{font-family:var(--mono);font-size:12px;letter-spacing:.14em;color:#fff}
.logo em{color:var(--accent);font-style:normal}
.nav-right{display:flex;gap:12px;align-items:center}
.nb{font-family:var(--mono);font-size:10px;letter-spacing:.1em;padding:6px 14px;
    border-radius:2px;transition:all .15s;cursor:pointer;text-transform:uppercase}
.nb-o{color:var(--muted);border:1px solid var(--border2);background:transparent}
.nb-o:hover{color:var(--text);border-color:var(--dim)}
.nb-s{color:var(--bg);background:var(--text);border:1px solid var(--text)}
.nb-s:hover{background:var(--accent);border-color:var(--accent)}
.page{max-width:1080px;margin:0 auto;padding:48px 32px}
@media(max-width:640px){.page{padding:24px 16px}}
.page-header{margin-bottom:40px;padding-bottom:28px;border-bottom:1px solid var(--border)}
.page-tag{font-family:var(--mono);font-size:9px;letter-spacing:.18em;color:var(--ok);
          text-transform:uppercase;margin-bottom:12px;display:flex;align-items:center;gap:6px}
.tag-dot{width:5px;height:5px;border-radius:50%;background:var(--ok)}
h1{font-family:var(--sans);font-size:clamp(28px,5vw,42px);font-weight:800;
   letter-spacing:-.02em;color:#fff;margin-bottom:10px}
.page-desc{font-family:var(--mono);font-size:12px;color:var(--muted);line-height:1.7;max-width:560px}
.stat-row{display:grid;grid-template-columns:repeat(4,1fr);
          border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-bottom:32px}
.stat-cell{padding:20px 22px;border-right:1px solid var(--border);background:var(--surface)}
.stat-cell:last-child{border-right:none}
.stat-num{font-family:var(--sans);font-size:26px;font-weight:700;color:#fff;
          letter-spacing:-.02em;margin-bottom:4px}
.stat-lbl{font-family:var(--mono);font-size:9px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase}
@media(max-width:600px){.stat-row{grid-template-columns:1fr 1fr}.stat-cell{border-bottom:1px solid var(--border)}}
.export-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:32px}
.dl-btn{font-family:var(--mono);font-size:10px;letter-spacing:.1em;padding:9px 18px;
        border-radius:2px;cursor:pointer;transition:all .15s;text-transform:uppercase;
        border:1px solid var(--border2);color:var(--muted);background:var(--surface)}
.dl-btn:hover{color:var(--text);border-color:var(--dim)}
.dl-btn.primary{background:var(--accent);color:var(--bg);border-color:var(--accent)}
.dl-btn.primary:hover{background:#eeff44;border-color:#eeff44}
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;
            border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-bottom:32px;
            background:var(--border)}
.chart-box{padding:24px;background:var(--surface)}
.chart-title{font-family:var(--mono);font-size:10px;letter-spacing:.12em;color:var(--muted);
             text-transform:uppercase;margin-bottom:16px}
canvas{max-height:220px}
@media(max-width:640px){.chart-grid{grid-template-columns:1fr}}
.table-wrap{border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-bottom:32px}
.table-head{padding:12px 18px;background:var(--bg);border-bottom:1px solid var(--border);
            display:flex;justify-content:space-between;align-items:center}
.table-head h3{font-family:var(--mono);font-size:10px;letter-spacing:.12em;color:var(--muted);
               text-transform:uppercase}
.table-head .cnt{font-family:var(--mono);font-size:10px;color:var(--accent)}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11px}
th{padding:10px 16px;text-align:left;font-size:9px;letter-spacing:.1em;color:var(--muted);
   border-bottom:1px solid var(--border);background:var(--bg);text-transform:uppercase;font-weight:500}
td{padding:10px 16px;border-bottom:1px solid var(--border);color:var(--text)}
tr:last-child td{border-bottom:none}
tr:hover td{background:#161616}
.td-danger{color:var(--danger);font-weight:500}
.td-warn{color:var(--warn)}
.td-ok{color:var(--ok)}
.empty-state{padding:40px;text-align:center;font-family:var(--mono);font-size:11px;color:var(--muted)}
.loading{padding:40px;text-align:center;font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.1em}
</style>
</head>
<body>
<nav>
  <a href="/welcome" class="logo">VECTRA<em>SPACE</em></a>
  <div class="nav-right">
    <a href="/dashboard" class="nb nb-o">Dashboard</a>
    <a href="/login" class="nb nb-s">Sign In</a>
  </div>
</nav>
<div class="page">
  <div class="page-header">
    <div class="page-tag"><span class="tag-dot"></span>Public Access — No Login Required</div>
    <h1>Research Data Portal</h1>
    <p class="page-desc">Conjunction history, Pc distributions, TLE exports, and orbital analytics generated from live scan cycles. All data is publicly accessible for academic and research use.</p>
  </div>
  <div class="stat-row">
    <div class="stat-cell"><div class="stat-num" id="rs-total">—</div><div class="stat-lbl">Total Conjunctions</div></div>
    <div class="stat-cell"><div class="stat-num" id="rs-critical">—</div><div class="stat-lbl">Critical (&lt;1 km)</div></div>
    <div class="stat-cell"><div class="stat-num" id="rs-scans">—</div><div class="stat-lbl">Scan Cycles</div></div>
    <div class="stat-cell"><div class="stat-num" id="rs-sats">—</div><div class="stat-lbl">TLE Catalog Size</div></div>
  </div>
  <div class="export-row">
    <a href="/conjunctions?format=csv" class="dl-btn primary" download>↓ Conjunctions CSV</a>
    <a href="/conjunctions?format=json" class="dl-btn" download>↓ Conjunctions JSON</a>
    <a href="/research/tle.csv" class="dl-btn" download>↓ TLE Catalog CSV</a>
    <a href="/research/tle.json" class="dl-btn">↓ TLE Catalog JSON</a>
  </div>
  <div class="chart-grid">
    <div class="chart-box"><div class="chart-title">Miss Distance Distribution</div><canvas id="chart-dist"></canvas></div>
    <div class="chart-box"><div class="chart-title">Collision Probability (Pc) Distribution</div><canvas id="chart-pc"></canvas></div>
    <div class="chart-box"><div class="chart-title">Relative Velocity Distribution</div><canvas id="chart-vel"></canvas></div>
    <div class="chart-box"><div class="chart-title">Conjunctions Over Time</div><canvas id="chart-time"></canvas></div>
  </div>
  <div class="table-wrap">
    <div class="table-head">
      <h3>Recent Conjunctions</h3>
      <span class="cnt" id="conj-count-lbl"></span>
    </div>
    <div id="table-body"><div class="loading">LOADING DATA...</div></div>
  </div>
</div>
<script>
const CHART_OPTS = {
  responsive:true, maintainAspectRatio:true,
  plugins:{legend:{display:false}},
  scales:{
    x:{grid:{color:'#1e1e1e'},ticks:{color:'#606060',font:{family:'IBM Plex Mono',size:9}}},
    y:{grid:{color:'#1e1e1e'},ticks:{color:'#606060',font:{family:'IBM Plex Mono',size:9}}}
  }
};
function makeChart(id, type, labels, data, color) {
  const el = document.getElementById(id);
  if (!el) return;
  new Chart(el, {type, data:{labels,datasets:[{data, backgroundColor: color+'33', borderColor: color, borderWidth:1.5, pointRadius:2}]}, options:CHART_OPTS});
}

async function load() {
  try {
    const [conjRes, tleRes] = await Promise.all([
      fetch('/conjunctions').then(r => r.json()),
      fetch('/research/tle.json').then(r => r.json()).catch(() => ({count:0}))
    ]);
    const conjs = conjRes.conjunctions || conjRes || [];
    document.getElementById('rs-total').textContent = conjs.length;
    document.getElementById('rs-sats').textContent = tleRes.count || '—';
    const critical = conjs.filter(c => (c.min_dist_km||999) < 1).length;
    document.getElementById('rs-critical').textContent = critical;
    const runs = new Set(conjs.map(c => c.run_time)).size;
    document.getElementById('rs-scans').textContent = runs || '—';
    document.getElementById('conj-count-lbl').textContent = conjs.length + ' events';

    // Charts
    const dists = conjs.map(c => parseFloat(c.min_dist_km)||0).sort((a,b)=>a-b);
    const distBuckets = [0,1,2,5,10,20,50,100];
    const distCounts = distBuckets.map((b,i) => dists.filter(d => d >= b && d < (distBuckets[i+1]||Infinity)).length);
    makeChart('chart-dist','bar',distBuckets.map((b,i) => b+'–'+(distBuckets[i+1]||'∞')+' km'), distCounts,'#e2ff00');

    const pcs = conjs.map(c => c.pc_estimate||0).filter(p=>p>0);
    const pcBuckets = [1e-6,1e-5,1e-4,1e-3,1e-2,0.1];
    const pcCounts = pcBuckets.map((b,i)=>pcs.filter(p=>p>=b&&p<(pcBuckets[i+1]||Infinity)).length);
    makeChart('chart-pc','bar',pcBuckets.map(b=>b.toExponential(0)),pcCounts,'#ff3535');

    const vels = conjs.map(c => parseFloat(c.relative_velocity_km_s)||0).filter(v=>v>0);
    const velBuckets = [0,2,4,6,8,10,15];
    const velCounts = velBuckets.map((b,i)=>vels.filter(v=>v>=b&&v<(velBuckets[i+1]||Infinity)).length);
    makeChart('chart-vel','bar',velBuckets.map((b,i)=>b+'–'+(velBuckets[i+1]||'∞')+' km/s'),velCounts,'#00d26a');

    const byRun = {};
    conjs.forEach(c => { const k = (c.run_time||'').slice(0,10); byRun[k] = (byRun[k]||0)+1; });
    const rKeys = Object.keys(byRun).sort().slice(-14);
    makeChart('chart-time','line',rKeys,rKeys.map(k=>byRun[k]),'#e2ff00');

    // Table
    if (!conjs.length) {
      document.getElementById('table-body').innerHTML = '<div class="empty-state">No conjunction data available — run a scan first.</div>';
      return;
    }
    const sorted = [...conjs].sort((a,b) => (a.min_dist_km||999) - (b.min_dist_km||999));
    let html = '<table><thead><tr><th>Satellites</th><th>Miss Distance</th><th>Pc Estimate</th><th>Rel. Velocity</th><th>TCA</th></tr></thead><tbody>';
    sorted.slice(0,100).forEach(c => {
      const d = parseFloat(c.min_dist_km)||0;
      const dc = d < 1 ? 'td-danger' : d < 5 ? 'td-warn' : 'td-ok';
      const pc = c.pc_estimate ? c.pc_estimate.toExponential(2) : '—';
      const vel = c.relative_velocity_km_s ? parseFloat(c.relative_velocity_km_s).toFixed(2)+' km/s' : '—';
      const tca = (c.tca||'').replace('T',' ').slice(0,16);
      html += `<tr><td>${c.sat1||''} × ${c.sat2||''}</td><td class="${dc}">${d.toFixed(2)} km</td><td>${pc}</td><td>${vel}</td><td>${tca}</td></tr>`;
    });
    html += '</tbody></table>';
    document.getElementById('table-body').innerHTML = html;
  } catch(e) {
    document.getElementById('table-body').innerHTML = '<div class="empty-state">Failed to load data. Is the server running?</div>';
  }
}
load();
</script>
</body>
</html>
"""

ADMIN_HTML    = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VectraSpace — Admin Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0a0a;--surface:#111;--border:#222;--border2:#2c2c2c;
  --text:#ebebeb;--muted:#606060;--dim:#303030;
  --accent:#e2ff00;--ok:#00d26a;--danger:#ff3535;--warn:#ff8800;
  --mono:'IBM Plex Mono',monospace;--sans:'Syne',sans-serif;
}
html,body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh}
a{color:inherit;text-decoration:none}
nav{height:54px;padding:0 32px;display:flex;align-items:center;justify-content:space-between;
    border-bottom:1px solid var(--border);background:var(--bg)}
.logo{font-family:var(--mono);font-size:12px;letter-spacing:.14em;color:#fff}
.logo em{color:var(--accent);font-style:normal}
.nav-right{display:flex;gap:12px;align-items:center}
.nb{font-family:var(--mono);font-size:10px;letter-spacing:.1em;padding:6px 14px;
    border-radius:2px;transition:all .15s;cursor:pointer;text-transform:uppercase}
.nb-o{color:var(--muted);border:1px solid var(--border2);background:transparent}
.nb-o:hover{color:var(--text);border-color:var(--dim)}
.nb-danger{color:var(--danger);border:1px solid rgba(255,53,53,.3);background:transparent}
.nb-danger:hover{background:rgba(255,53,53,.08)}
.page{max-width:1080px;margin:0 auto;padding:40px 32px}
@media(max-width:640px){.page{padding:20px 16px}}
h1{font-family:var(--sans);font-size:clamp(22px,4vw,32px);font-weight:800;
   letter-spacing:-.02em;color:#fff;margin-bottom:6px}
.admin-tag{font-family:var(--mono);font-size:9px;letter-spacing:.18em;color:var(--accent);
           text-transform:uppercase;margin-bottom:24px}
.stat-row{display:grid;grid-template-columns:repeat(3,1fr);
          border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-bottom:28px}
.stat-cell{padding:20px 22px;border-right:1px solid var(--border);background:var(--surface)}
.stat-cell:last-child{border-right:none}
.stat-num{font-family:var(--sans);font-size:26px;font-weight:700;color:#fff;letter-spacing:-.02em;margin-bottom:4px}
.stat-lbl{font-family:var(--mono);font-size:9px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase}
@media(max-width:500px){.stat-row{grid-template-columns:1fr}.stat-cell{border-right:none;border-bottom:1px solid var(--border)}.stat-cell:last-child{border-bottom:none}}
.section-hdr{font-family:var(--mono);font-size:9px;letter-spacing:.18em;color:var(--muted);
             text-transform:uppercase;padding-bottom:10px;border-bottom:1px solid var(--border);
             margin-bottom:16px;margin-top:32px}
.table-wrap{border:1px solid var(--border);border-radius:2px;overflow:hidden;margin-bottom:28px}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11px}
th{padding:10px 16px;text-align:left;font-size:9px;letter-spacing:.1em;color:var(--muted);
   border-bottom:1px solid var(--border);background:var(--bg);text-transform:uppercase;font-weight:500}
td{padding:10px 16px;border-bottom:1px solid var(--border);color:var(--text)}
tr:last-child td{border-bottom:none}
.role-admin{color:var(--accent);font-weight:500}
.role-op{color:var(--ok)}
.del-btn{font-family:var(--mono);font-size:9px;letter-spacing:.08em;padding:4px 10px;
         border-radius:2px;cursor:pointer;transition:all .15s;text-transform:uppercase;
         border:1px solid rgba(255,53,53,.3);color:var(--danger);background:transparent}
.del-btn:hover{background:rgba(255,53,53,.08)}
.run-row{display:flex;gap:1px;background:var(--border);border:1px solid var(--border);
         border-radius:2px;overflow:hidden;margin-bottom:8px}
.run-cell{padding:10px 16px;background:var(--surface);flex:1;font-family:var(--mono);font-size:11px}
.run-time{color:var(--muted);font-size:9px}
.run-count{color:var(--accent)}
.toast{position:fixed;bottom:24px;right:24px;font-family:var(--mono);font-size:11px;
       padding:10px 16px;border-radius:2px;background:var(--surface);border:1px solid var(--border);
       color:var(--text);display:none;z-index:999;letter-spacing:.06em}
.toast.ok{border-color:var(--ok);color:var(--ok)}
.toast.err{border-color:var(--danger);color:var(--danger)}
</style>
</head>
<body>
<nav>
  <a href="/welcome" class="logo">VECTRA<em>SPACE</em></a>
  <div class="nav-right">
    <a href="/dashboard" class="nb nb-o">Dashboard</a>
    <a href="/logout" class="nb nb-danger">Sign Out</a>
  </div>
</nav>
<div class="page">
  <div class="admin-tag">Admin Console</div>
  <h1>Platform Administration</h1>
  <div class="stat-row">
    <div class="stat-cell"><div class="stat-num" id="a-scans">—</div><div class="stat-lbl">Total Conjunctions</div></div>
    <div class="stat-cell"><div class="stat-num" id="a-users">—</div><div class="stat-lbl">Registered Users</div></div>
    <div class="stat-cell"><div class="stat-num" id="a-runs">—</div><div class="stat-lbl">Scan Runs</div></div>
  </div>
  <div class="section-hdr">Recent Scan History</div>
  <div id="runs-list"><div style="font-family:var(--mono);font-size:11px;color:var(--muted);padding:12px 0">Loading...</div></div>
  <div class="section-hdr">User Management</div>
  <div class="table-wrap">
    <table><thead><tr><th>Username</th><th>Role</th><th>Email</th><th>Actions</th></tr></thead>
    <tbody id="users-table"></tbody></table>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
function toast(msg, type='ok') {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'toast '+type; t.style.display='block';
  setTimeout(() => { t.style.display='none'; }, 3000);
}
async function load() {
  try {
    const d = await fetch('/admin/data').then(r => r.json());
    document.getElementById('a-scans').textContent = d.total_scans ?? '—';
    document.getElementById('a-users').textContent = d.user_count ?? '—';
    document.getElementById('a-runs').textContent = d.recent_runs ? d.recent_runs.length : '—';
    const runsList = document.getElementById('runs-list');
    if (d.recent_runs && d.recent_runs.length) {
      runsList.innerHTML = d.recent_runs.map(r =>
        `<div class="run-row"><div class="run-cell"><span class="run-time">${r.time||''}</span></div><div class="run-cell"><span class="run-count">${r.count} conjunctions</span></div></div>`
      ).join('');
    } else {
      runsList.innerHTML = '<div style="font-family:var(--mono);font-size:11px;color:var(--muted);padding:12px 0">No scan history yet.</div>';
    }
    const tbody = document.getElementById('users-table');
    if (d.users && d.users.length) {
      tbody.innerHTML = d.users.map(u =>
        `<tr><td>${u.username}</td><td class="${u.role==='admin'?'role-admin':'role-op'}">${u.role}</td><td>${u.email||'—'}</td>
         <td><button class="del-btn" onclick="deleteUser('${u.username}')">Delete</button></td></tr>`
      ).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:20px">No users found.</td></tr>';
    }
  } catch(e) {
    console.error(e);
  }
}
async function deleteUser(username) {
  if (!confirm('Delete user "' + username + '"?')) return;
  try {
    const r = await fetch('/admin/users/' + encodeURIComponent(username), {method:'DELETE'});
    const d = await r.json();
    if (d.ok) { toast('User deleted'); load(); }
    else toast(d.error || 'Error', 'err');
  } catch(e) { toast('Request failed', 'err'); }
}
load();
</script>
</body>
</html>
"""


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

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _lifespan(_app):
        import asyncio as _aio
        task = _aio.create_task(_auto_scan_loop())
        log.info("[startup] Auto-scan background task started")
        yield
        task.cancel()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app):
        import asyncio as _aio
        task = _aio.create_task(_auto_scan_loop())
        log.info("[startup] Auto-scan background task started (every 6h)")
        yield
        task.cancel()

    app = FastAPI(
        title="VectraSpace API",
        description="VectraSpace v11 — Orbital Safety Platform",
        version="11.0",
        lifespan=_lifespan,
    )
   
    # ── In-memory demo result cache (public/unauthenticated view) ─
    app._demo_result = None   # most recent public (user_id=None) run
    app._user_results = {}    # username -> last result

    # ── Helper: get current user from cookie ──────────────────
    def _get_user(request: Request) -> Optional[dict]:
        return get_current_user_from_request(request, cfg)

    # ── Dashboard UI ──────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse(content=get_dashboard_html())

    # ── /me — current user info ───────────────────────────────
    @app.get("/me")
    def me(request: Request):
        user = _get_user(request)
        if not user:
            return JSONResponse({"authenticated": False}, status_code=401)
        return JSONResponse({"authenticated": True, "username": user["username"], "role": user["role"]})

    # ── /preferences GET/POST ─────────────────────────────────
    @app.get("/preferences", response_class=HTMLResponse)
    def preferences_page(request: Request):
        user = _get_user(request)
        if not user:
            return RedirectResponse(url="/login")
        prefs = _get_user_prefs(user["username"], cfg)
        html = PREFERENCES_HTML.replace("{USERNAME}", user["username"]) \
            .replace("{EMAIL}", prefs.get("email") or "") \
            .replace("{PHONE}", prefs.get("phone") or "") \
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
            .replace("{PHONE}", prefs["phone"]) \
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
        alert_phone: Optional[str] = None,
        pushover_user_key: Optional[str] = None,
    ):
        user = _get_user(request)

        async def event_stream():
            def send(text, type_="log"):
                return f"data: {_json.dumps({'type': type_, 'text': text})}\n\n"

            # MULTI-02: Require login for /run
            if not user:
                yield f"data: {_json.dumps({'type': 'auth_error', 'text': 'Authentication required'})}\n\n"
                return

            # MULTI-04: Per-user rate limit (1 scan per 5 min)
            if not _check_run_rate_limit(user["username"]):
                yield f"data: {_json.dumps({'type': 'rate_limit', 'text': 'Max 1 scan per 5 minutes. Please wait.'})}\n\n"
                return

            try:
                # ── Hard limits to protect server resources ──
                MAX_LEO, MAX_MEO, MAX_GEO = 300, 100, 50
                if num_leo > MAX_LEO or num_meo > MAX_MEO or num_geo > MAX_GEO:
                    yield send(
                        f"Satellite count exceeds server limits "
                        f"(LEO ≤ {MAX_LEO}, MEO ≤ {MAX_MEO}, GEO ≤ {MAX_GEO}). "
                        f"Your request: LEO={num_leo}, MEO={num_meo}, GEO={num_geo}. "
                        f"Please reduce and retry.",
                        "error"
                    )
                    return
                num_leo  = max(1, min(num_leo,  MAX_LEO))
                num_meo  = max(1, min(num_meo,  MAX_MEO))
                num_geo  = max(1, min(num_geo,  MAX_GEO))

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
                    alert_phone=alert_phone or user_prefs.get("phone") or cfg.alert_phone,
                    alert_email_from=cfg.alert_email_from,
                    alert_smtp_host=cfg.alert_smtp_host,
                    pushover_token=cfg.pushover_token,
                    pushover_user_key=pushover_user_key or user_prefs.get("pushover_key") or cfg.pushover_user_key,
                )

                yield send("Fetching covariance data from Space-Track...")
                await asyncio.sleep(0)
                cov_cache_result = {}
                loop = asyncio.get_event_loop()
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    try:
                        cov_cache_result = await loop.run_in_executor(pool, lambda: fetch_covariance_cache(run_cfg))
                    except Exception:
                        pass

                yield send("Starting scan...")
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
                    # MULTI-03: Tag conjunctions with user_id
                    future = loop.run_in_executor(
                        pool,
                        lambda: _run_pipeline(run_cfg, covariance_cache=cov_cache_result,
                                               run_mode="interactive",
                                               user_id=user["username"],
                                               user_prefs=user_prefs)
                    )

                    while not future.done():
                        while sse_logs:
                            yield send(sse_logs.pop(0))
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
            # MULTI-03: Authenticated users see their own history
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
            log.warning("ANTHROPIC_API_KEY not set — returning CelesTrak fallback")
            return JSONResponse({
                "error": "Satellite info service unavailable",
                "celestrak_url": f"https://celestrak.org/satcat/records.php?NAME={sat_name}",
            }, status_code=503)

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
                        "operationalStatus, owner, objectType. Use null for missing fields."
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
    def login_page(error: str = ""):
        err_html    = f'<div class="err">⚠ {error}</div>' if error else ""
        signup_link = '<a href="/signup">Create account</a>' if SIGNUP_OPEN else '<a href="mailto:support@vectraspace.com">Request access</a>'
        return HTMLResponse(
            LOGIN_HTML.replace("{ERROR}", err_html)
                      .replace("{SIGNUP_LINK}", signup_link)
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request):
        signup_link = '<a href="/signup">Create account</a>' if SIGNUP_OPEN else '<a href="mailto:support@vectraspace.com">Request access</a>'

        def _login_page_with_err(msg: str) -> HTMLResponse:
            return HTMLResponse(
                LOGIN_HTML.replace("{ERROR}", f'<div class="err">{msg}</div>')
                          .replace("{SIGNUP_LINK}", signup_link)
            )

        client_ip = request.client.host or "0.0.0.0"
        if not _check_login_rate_limit(client_ip):
            return _login_page_with_err("Too many login attempts. Try again in 60s.")
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", "")).strip()
        users = _load_users(cfg)
        user = users.get(username)
        if not user or not _verify_password(password, user.get("password_hash", "")):
            return _login_page_with_err("Invalid username or password.")
        if not user.get("approved", True):
            return _login_page_with_err("Account pending approval. Contact support@vectraspace.com.")
        token = _make_session_cookie(username, user.get("role", "operator"), cfg.session_secret)
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("vs_session", token, httponly=True, samesite="lax", max_age=28800)
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse(url="/login")
        resp.delete_cookie("vs_session")
        return resp

    # ── AUTH-01: Self-service signup ──────────────────────────
    # Controlled by SIGNUP_OPEN env var: "true" = open, anything else = closed.
    # Set SIGNUP_OPEN=true in .env to enable public registration.
    SIGNUP_OPEN = os.environ.get("SIGNUP_OPEN", "false").lower() == "true"

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
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("vs_session", token, httponly=True, samesite="lax", max_age=28800)
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
            err = '<div class="err">⚠ Could not update password. Contact support@vectraspace.com.</div>'
            return HTMLResponse(RESET_PASSWORD_HTML.replace("{MESSAGE}", err).replace("{FORM}", ""))
        log.info(f"Password successfully reset for user '{username}' via email token")
        # Auto-login after successful reset
        token_cookie = _make_session_cookie(username, "operator", cfg.session_secret)
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("vs_session", token_cookie, httponly=True, samesite="lax", max_age=28800)
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

    from starlette.middleware.base import BaseHTTPMiddleware
    PUBLIC_PATHS = {"/login", "/health", "/demo-results", "/signup",
                    "/forgot-password", "/reset-password", "/", "/welcome",
                    "/research", "/research/tle.json", "/research/tle.csv",
                    "/admin", "/admin/data", "/feedback",
                    "/tle-status", "/scan-status"}

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path in PUBLIC_PATHS or path.startswith("/static"):
                return await call_next(request)
            if path == "/" or path.startswith("/sat-info/") or path.startswith("/cdm"):
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

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "time": datetime.datetime.utcnow().isoformat(),
            "version": "v11",
            "product": "VectraSpace — Orbital Safety Platform",
        }

    @app.post("/auth/google")
    async def google_signin(request: Request):
        """Exchange Google ID token for a VectraSpace session cookie."""
        try:
            import base64 as _b64, json as _j
            body = await request.json()
            credential = body.get("credential", "")
            if not credential:
                return JSONResponse({"error": "No credential"}, status_code=400)
            parts = credential.split(".")
            if len(parts) < 2:
                return JSONResponse({"error": "Invalid token"}, status_code=400)
            payload_b64 = parts[1] + "=="
            try:
                payload = _j.loads(_b64.urlsafe_b64decode(payload_b64).decode())
            except Exception:
                return JSONResponse({"error": "Could not decode token"}, status_code=400)
            email = payload.get("email", "").lower().strip()
            if not email:
                return JSONResponse({"error": "No email in token"}, status_code=400)
            username = email.split("@")[0].replace(".", "_")[:32]
            users = _load_users(cfg)
            if username not in users:
                create_user(username, secrets.token_hex(24), "operator", cfg=cfg)
                log.info(f"[google-auth] Created account for {email}")
            role = _load_users(cfg).get(username, {}).get("role", "operator")
            cookie = _make_session_cookie(username, cfg.session_secret, role=role)
            response = JSONResponse({"ok": True, "username": username})
            response.set_cookie("vs_session", cookie, httponly=True,
                                samesite="lax", secure=False, max_age=86400 * 7)
            return response
        except Exception as e:
            log.warning(f"[google-auth] Error: {e}")
            return JSONResponse({"error": "Authentication failed"}, status_code=400)

    # ── Auto-scan background task ─────────────────────────────
    async def _auto_scan_loop():
        import asyncio as _aio
        await _aio.sleep(90)
        while True:
            if not _scan_state.get("running"):
                _scan_state["running"] = True
                try:
                    import functools
                    loop = _aio.get_event_loop()
                    result = await loop.run_in_executor(
                        None, functools.partial(_run_pipeline, cfg,
                                                run_mode="scheduled", user_id="__auto__"))
                    _scan_state["count"] = len(result.get("conjunctions", []))
                    _scan_state["time"] = time.time()
                    log.info(f"[auto-scan] Done — {_scan_state['count']} conjunctions")
                except Exception as e:
                    log.warning(f"[auto-scan] Error: {e}")
                finally:
                    _scan_state["running"] = False
            await _aio.sleep(6 * 3600)

    # ── Landing / welcome page ─────────────────────────────────
    @app.get("/welcome", response_class=HTMLResponse)
    def welcome_page():
        return HTMLResponse(content=LANDING_HTML)

    # ── Research portal ────────────────────────────────────────
    @app.get("/research", response_class=HTMLResponse)
    def research_page():
        return HTMLResponse(content=RESEARCH_HTML)

    @app.get("/research/tle.json")
    def research_tle_json():
        try:
            import json as _j
            data = _j.loads(Path("catalog.json").read_text())
            return JSONResponse({"count": len(data), "tles": data})
        except Exception:
            return JSONResponse({"count": 0, "tles": []})

    @app.get("/research/tle.csv")
    def research_tle_csv():
        try:
            import json as _j
            data = _j.loads(Path("catalog.json").read_text())
            lines = ["name,line1,line2"]
            for t in data:
                name = t.get("name","").replace(",","")
                l1 = t.get("line1",""); l2 = t.get("line2","")
                lines.append(f"{name},{l1},{l2}")
            from fastapi.responses import PlainTextResponse
            csv_body = "\n".join(lines)
            return PlainTextResponse(csv_body, media_type="text/csv",
                                     headers={"Content-Disposition":"attachment;filename=vectraspace_tles.csv"})
        except Exception:
            return PlainTextResponse("name,line1,line2", media_type="text/csv")

    # ── Admin console ──────────────────────────────────────────
    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request):
        token = request.cookies.get("vs_session", "")
        try:
            sess = _verify_session_cookie(token, cfg.session_secret)
            if sess.get("role") != "admin":
                return RedirectResponse(url="/login?next=/admin", status_code=303)
        except Exception:
            return RedirectResponse(url="/login?next=/admin", status_code=303)
        html = ADMIN_HTML.replace("__CESIUM_TOKEN__", _get_cesium_token())
        return HTMLResponse(content=html)

    @app.get("/admin/data")
    def admin_data(request: Request):
        token = request.cookies.get("vs_session", "")
        try:
            sess = _verify_session_cookie(token, cfg.session_secret)
            if sess.get("role") != "admin":
                return JSONResponse({"error": "Forbidden"}, status_code=403)
        except Exception:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            con = sqlite3.connect(cfg.db_path)
            scans = con.execute("SELECT COUNT(*) FROM conjunctions").fetchone()[0]
            recent = con.execute(
                "SELECT run_time, COUNT(*) as c FROM conjunctions GROUP BY run_time ORDER BY run_time DESC LIMIT 10"
            ).fetchall()
            con.close()
        except Exception:
            scans = 0; recent = []
        users = _load_users(cfg)
        return JSONResponse({
            "total_scans": scans,
            "user_count": len(users),
            "users": [{"username": u, "role": v.get("role","operator"),
                       "email": v.get("email",""), "approved": v.get("approved", True)}
                      for u, v in users.items()],
            "recent_runs": [{"time": r[0], "count": r[1]} for r in recent],
        })

    @app.delete("/admin/users/{username}")
    def admin_delete_user(username: str, request: Request):
        token = request.cookies.get("vs_session", "")
        try:
            sess = _verify_session_cookie(token, cfg.session_secret)
            if sess.get("role") != "admin":
                return JSONResponse({"error": "Forbidden"}, status_code=403)
        except Exception:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        users = _load_users(cfg)
        if username not in users:
            return JSONResponse({"error": "User not found"}, status_code=404)
        if username == sess.get("username"):
            return JSONResponse({"error": "Cannot delete yourself"}, status_code=400)
        del users[username]
        _save_users(users, cfg)
        return JSONResponse({"ok": True})

    # ── TLE freshness + scan status ────────────────────────────
    @app.get("/tle-status")
    def tle_status():
        try:
            import json as _j
            p = Path("catalog.json")
            age_s = time.time() - p.stat().st_mtime
            age_h = round(age_s / 3600, 1)
            data = _j.loads(p.read_text())
            count = len(data)
            fresh = age_h < 24
            return JSONResponse({
                "fresh": fresh, "age_hours": age_h, "count": count,
                "message": f"{count} sats · updated {age_h}h ago" if fresh
                           else f"Stale ({age_h}h old) — rescan recommended"
            })
        except Exception:
            return JSONResponse({"fresh": False, "age_hours": -1, "count": 0,
                                 "message": "No TLE catalog found — run a scan"})

    @app.get("/scan-status")
    def scan_status():
        last = _scan_state.get("time", 0)
        return JSONResponse({
            "last_scan": last,
            "running": _scan_state.get("running", False),
            "count": _scan_state.get("count", 0),
            "age_minutes": round((time.time() - last) / 60, 1) if last else -1,
        })

    # ── Feedback ───────────────────────────────────────────────
    @app.post("/feedback")
    async def submit_feedback(request: Request):
        try:
            body = await request.json()
            fb_type = body.get("type", "general")
            message = body.get("message", "")[:2000]
            user = _get_user(request)
            username = user.get("username", "anonymous") if user else "anonymous"
            entry = {"type": fb_type, "message": message, "username": username,
                     "timestamp": datetime.datetime.utcnow().isoformat()}
            try:
                con = sqlite3.connect(cfg.db_path)
                con.execute("CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, message TEXT, username TEXT, timestamp TEXT)")
                con.execute("INSERT INTO feedback (type,message,username,timestamp) VALUES (?,?,?,?)",
                            (fb_type, message, username, entry["timestamp"]))
                con.commit(); con.close()
            except Exception as _e:
                log.warning(f"Feedback DB error: {_e}")
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

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
    # MULTI-03: Tag with user_id (None = public/headless)
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

# ── Module-level app for uvicorn vectraspace:app ──────────────
_scan_state: dict = {"time": 0, "running": False, "count": 0}

def _init_app():
    """Build FastAPI app and initialize users. Called at module import."""
    import os as _os
    _admin_user = _os.environ.get("ADMIN_USER", "admin").strip().lower()
    _admin_pass = (_os.environ.get("ADMIN_PASS", "").strip() or
                   _os.environ.get("ADMIN_PASSCODE", "").strip() or
                   "VectraSpace2526")
    try:
        init_db(CFG)
        existing = _load_users(CFG)
        if _admin_user not in existing:
            create_user(_admin_user, _admin_pass, "admin", cfg=CFG)
            log.info(f"[startup] Created admin user '{_admin_user}'")
        else:
            if existing[_admin_user].get("role") != "admin":
                existing[_admin_user]["role"] = "admin"
                _save_users(existing, CFG)
            log.info(f"[startup] Admin user '{_admin_user}' OK")
    except Exception as e:
        log.warning(f"[startup] Init error: {e}")
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

        import webbrowser, threading
        url = f"http://localhost:{args.port}"
        log.info("=" * 60)
        log.info("VectraSpace v11 — Orbital Safety Platform")
        log.info(f"Dashboard: {url}")
        log.info(f"API docs:  {url}/docs")
        log.info("=" * 60)

        api = build_api(CFG)

        if not args.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()

        uvicorn.run(api, host=args.host, port=args.port)
