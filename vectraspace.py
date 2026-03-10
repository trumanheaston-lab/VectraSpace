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
    """Clohessy-Wiltshire minimum-<dfn data-term="delta-v">delta-v</dfn> avoidance maneuver."""
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
    --bg:        #080c12;
    --bg2:       #0d1320;
    --bg3:       #111d2e;
    --panel:     #0a1019;
    --border:    rgba(255,255,255,0.07);
    --border2:   rgba(255,255,255,0.13);
    --accent:    #4a9eff;
    --accent2:   #f87171;
    --accent3:   #34d399;
    --text:      #ccd6e0;
    --muted:     #8aaac5;
    --faint:     #2a3d50;
    --serif:     'Instrument Serif', Georgia, serif;
    --mono:      'DM Mono', monospace;
    --sans:      'Outfit', sans-serif;
    --panel-w:   320px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 100%; height: 100%; overflow: hidden; background: var(--bg); color: var(--text); font-family: var(--sans); }

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
    color: var(--faint);
    font-family: var(--mono);
    font-size: 14px;
    padding: 11px;
    cursor: pointer;
    width: 100%;
    text-align: center;
    transition: color 0.2s, background 0.2s;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 42px;
  }
  #sidebar-toggle-btn .toggle-label {
    font-size: 8px; letter-spacing: 1.5px; text-transform: uppercase;
    transition: opacity 0.2s; color: var(--muted);
  }
  #sidebar.collapsed #sidebar-toggle-btn .toggle-label { display: none; }
  #sidebar-toggle-btn:hover { color: var(--accent); background: rgba(74,158,255,0.06); }
  #globe-container { flex: 1; position: relative; transition: flex 0.25s ease; }
  #cesiumContainer { width: 100%; height: 100%; }

  #header {
    padding: 18px 20px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--bg2);
    flex-shrink: 0;
  }
  #header .logo {
    font-family: var(--mono);
    font-size: 8px;
    color: var(--accent);
    letter-spacing: 3px;
    margin-bottom: 8px;
    text-transform: uppercase;
    opacity: 0.7;
  }
  #header .brand {
    display: flex; align-items: baseline; gap: 6px; margin-bottom: 4px;
  }
  #header .brand-name {
    font-family: var(--serif); font-size: 20px; font-style: italic;
    color: #fff; letter-spacing: -0.2px;
  }
  #header .brand-name em { color: var(--accent); font-style: normal; }
  #header .brand-tag {
    font-family: var(--mono); font-size: 8px; letter-spacing: 2px;
    color: var(--faint); text-transform: uppercase;
  }
  #header .sub {
    font-size: 11px;
    color: var(--muted);
    font-family: var(--sans);
    margin-top: 2px;
    line-height: 1.4;
  }
  #user-bar {
    padding: 6px 20px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    font-family: var(--mono);
    font-size: 9px;
    color: var(--muted);
    display: flex;
    justify-content: space-between;
    align-items: center;
    letter-spacing: 0.5px;
  }
  #user-bar .user-name { color: var(--accent); }
  #user-bar a { color: var(--muted); text-decoration: none; font-size: 9px; }
  #user-bar a:hover { color: var(--accent); }

  #scroll { flex: 1; overflow-y: auto; padding: 18px 16px; }
  #scroll::-webkit-scrollbar { width: 3px; }
  #scroll::-webkit-scrollbar-track { background: transparent; }
  #scroll::-webkit-scrollbar-thumb { background: var(--faint); border-radius: 2px; }

  .section { margin-bottom: 22px; }
  .section-title {
    font-family: var(--mono);
    font-size: 8px;
    letter-spacing: 3px;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .section-title::before {
    content: '';
    width: 16px; height: 1px;
    background: var(--accent);
    display: inline-block;
    flex-shrink: 0;
  }

  .field { margin-bottom: 14px; }
  .field label {
    display: block;
    font-family: var(--mono);
    font-size: 9px;
    color: var(--muted);
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 5px;
  }
  .field input {
    width: 100%;
    background: var(--bg3);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-family: var(--mono);
    font-size: 13px;
    padding: 8px 12px;
    outline: none;
    transition: border-color 0.2s;
  }
  .field input:focus { border-color: var(--accent); }
  .field .hint {
    font-size: 9px;
    color: var(--faint);
    margin-top: 4px;
    font-family: var(--mono);
    letter-spacing: 0.5px;
  }

  #run-btn {
    width: 100%;
    padding: 12px;
    background: linear-gradient(135deg, rgba(74,158,255,0.12), rgba(74,158,255,0.04));
    border: 1px solid rgba(74,158,255,0.4);
    border-radius: 7px;
    color: var(--accent);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 2px;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.2s;
    position: relative;
    overflow: hidden;
  }
  #run-btn:hover {
    background: linear-gradient(135deg, rgba(74,158,255,0.22), rgba(74,158,255,0.08));
    border-color: var(--accent);
    box-shadow: 0 0 20px rgba(74,158,255,0.12);
  }
  #run-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  #run-btn.running {
    border-color: var(--accent3);
    color: var(--accent3);
    background: linear-gradient(135deg, rgba(52,211,153,0.08), rgba(52,211,153,0.02));
    animation: pulse-border 1.5s infinite;
  }
  @keyframes pulse-border {
    0%, 100% { box-shadow: 0 0 0 0 rgba(52,211,153,0.2); }
    50% { box-shadow: 0 0 0 6px rgba(52,211,153,0); }
  }

  #run-locked-msg {
    width: 100%;
    padding: 12px;
    background: rgba(74,106,133,0.06);
    border: 1px solid var(--border);
    border-radius: 7px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 1px;
    text-align: center;
  }

  #status-bar {
    padding: 9px 20px;
    border-top: 1px solid var(--border);
    font-family: var(--mono);
    font-size: 9px;
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--bg);
    flex-shrink: 0;
  }
  #status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--faint); flex-shrink: 0; }
  #status-dot.ready { background: var(--accent3); box-shadow: 0 0 6px rgba(52,211,153,0.5); }
  #status-dot.running { background: var(--accent); animation: blink 1s infinite; }
  #status-dot.error { background: var(--accent2); }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
  #status-text { color: var(--muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; letter-spacing: 0.5px; }

  #log-panel {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    height: 140px;
    overflow-y: auto;
    padding: 10px 12px;
    font-family: var(--mono);
    font-size: 10px;
    line-height: 1.7;
  }
  #log-panel::-webkit-scrollbar { width: 3px; }
  #log-panel::-webkit-scrollbar-thumb { background: var(--faint); border-radius: 2px; }
  .log-line { color: var(--faint); }
  .log-line.info { color: #5ba3c9; }
  .log-line.ok { color: var(--accent3); }
  .log-line.warn { color: #f59e0b; }
  .log-line.error { color: var(--accent2); }

  #results-list { display: flex; flex-direction: column; gap: 6px; }
  .conj-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-left: 2px solid var(--accent2);
    border-radius: 6px;
    padding: 10px 12px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .conj-card:hover { border-color: rgba(74,158,255,0.4); background: var(--bg3); }
  .conj-card .sats { font-weight: 600; color: var(--text); font-size: 11px; margin-bottom: 4px; font-family: var(--sans); }
  .conj-card .meta { color: var(--muted); font-family: var(--mono); font-size: 9px; display: flex; gap: 10px; letter-spacing: 0.5px; }
  .conj-card .dist { color: var(--accent2); font-weight: 600; }
  .conj-card .pc   { color: #f59e0b; }
  .conj-card .time { color: var(--muted); }
  #no-results { color: var(--faint); font-family: var(--mono); font-size: 9px; text-align: center; padding: 20px 0; letter-spacing: 1px; }

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
    <button id="sidebar-toggle-btn" onclick="toggleSidebar()" title="Collapse sidebar">◀ <span class="toggle-label">Collapse</span></button>
    <div class="sidebar-collapsible" style="flex:1;display:flex;flex-direction:column;overflow:hidden;">
    <div id="header">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
        <div class="logo">// Mission Control</div>
        <a href="/welcome" style="font-family:var(--mono);font-size:8px;
                           letter-spacing:1.5px;color:var(--muted);text-decoration:none;
                           padding:4px 10px;border:1px solid var(--border);border-radius:4px;
                           text-transform:uppercase;transition:all 0.2s;"
           onmouseover="this.style.color='var(--accent)';this.style.borderColor='rgba(74,158,255,0.4)'"
           onmouseout="this.style.color='var(--muted)';this.style.borderColor='var(--border)'">
          ← Hub
        </a>
      </div>
      <div class="brand">
        <div class="brand-name">Vectra<em>Space</em></div>
        <div class="brand-tag">Platform</div>
      </div>
      <div class="sub">Orbital Safety Dashboard</div>
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
      requestWaterMask: true,           // shows ocean water surface
      requestVertexNormals: true,       // enables normal-mapped terrain lighting
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
    contextOptions: { requestWebgl2: true, allowTextureFilterAnisotropic: true },
    shadows: false,
    orderIndependentTranslucency: false,
  });

  // ── Imagery: high-resolution aerial with enhanced contrast ──────────────
  viewer.imageryLayers.removeAll();
  try {
    const aerial = await Cesium.createWorldImageryAsync({
      style: Cesium.IonWorldImageryStyle.AERIAL
    });
    const layer = viewer.imageryLayers.add(new Cesium.ImageryLayer(aerial, {
      brightness: 1.05,
      contrast: 1.1,
      saturation: 1.15,
      gamma: 0.9,
    }));
  } catch(e) {
    console.warn('World imagery unavailable — using OSM fallback');
    viewer.imageryLayers.add(new Cesium.ImageryLayer(
      new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/', maximumLevel: 18 }),
      { brightness: 1.0, contrast: 1.1 }
    ));
  }

  // ── Scene settings: maximum visual quality ──────────────────────────────
  // Globe appearance
  viewer.scene.globe.enableLighting = true;
  viewer.scene.globe.atmosphereLightIntensity = 15.0;
  viewer.scene.globe.atmosphereRayleighCoefficient = new Cesium.Cartesian3(5.5e-6, 13.0e-6, 28.4e-6);
  viewer.scene.globe.atmosphereMieCoefficient = new Cesium.Cartesian3(21e-6, 21e-6, 21e-6);
  viewer.scene.globe.showGroundAtmosphere = true;
  viewer.scene.globe.depthTestAgainstTerrain = false;
  viewer.scene.globe.maximumScreenSpaceError = 1.5;     // more tiles = sharper
  viewer.scene.globe.tileCacheSize = 200;               // cache more tiles
  viewer.scene.globe.preloadAncestors = true;
  viewer.scene.globe.preloadSiblings = true;
  viewer.scene.globe.translucency.enabled = false;

  // Atmosphere: rich blue scattering
  viewer.scene.atmosphere.brightnessShift = 0.15;
  viewer.scene.atmosphere.hueShift = 0.0;
  viewer.scene.atmosphere.saturationShift = 0.1;
  viewer.scene.skyAtmosphere.show = true;
  viewer.scene.skyAtmosphere.atmosphereLightIntensity = 20.0;
  viewer.scene.skyAtmosphere.atmosphereRayleighCoefficient = new Cesium.Cartesian3(5.5e-6, 13.0e-6, 28.4e-6);

  // Fog: subtle depth
  viewer.scene.fog.enabled = true;
  viewer.scene.fog.density = 0.0001;
  viewer.scene.fog.minimumBrightness = 0.03;

  // Celestial bodies & HDR
  viewer.scene.sun = new Cesium.Sun();
  viewer.scene.moon = new Cesium.Moon();
  viewer.scene.highDynamicRange = true;                 // enable HDR for better contrast
  viewer.scene.postProcessStages.fxaa.enabled = true;  // anti-aliasing

  // Lighting: sun-based directional
  viewer.scene.light = new Cesium.SunLight();

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
  const label = btn.querySelector('.toggle-label');
  // On mobile, use open/close drawer pattern instead of collapse
  if (window.innerWidth <= 768) {
    const isOpen = sidebar.classList.toggle('open');
    document.getElementById('sidebar-overlay').classList.toggle('active', isOpen);
    return;
  }
  const collapsed = sidebar.classList.toggle('collapsed');
  btn.querySelector
  if (label) label.textContent = collapsed ? 'Expand' : 'Collapse';
  btn.innerHTML = collapsed
    ? '▶ <span class="toggle-label">Expand</span>'
    : '◀ <span class="toggle-label">Collapse</span>';
  btn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
  try { localStorage.setItem('vs_sidebar_collapsed', collapsed ? '1' : '0'); } catch(e) {}
}

function initSidebarState() {
  try {
    if (window.innerWidth > 768 && localStorage.getItem('vs_sidebar_collapsed') === '1') {
      const sidebar = document.getElementById('sidebar');
      const btn = document.getElementById('sidebar-toggle-btn');
      sidebar.classList.add('collapsed');
      btn.innerHTML = '▶ <span class="toggle-label">Expand</span>';
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
    let scanDone = false;
    let lastActivity = Date.now();

    // Keepalive watchdog — large scans (10k sats) can take 3+ min.
    // Only fire if we haven't received any message in 4 minutes AND scan isn't done.
    const watchdog = setInterval(() => {
      if (!scanDone && Date.now() - lastActivity > 240000) {
        clearInterval(watchdog);
        evtSource.close();
        setProgress(0, 'Timeout');
        addLog('Scan timed out after 4 minutes — try fewer satellites or a shorter window', 'warn');
        setStatus('Scan timed out', 'error');
        resetBtn();
      }
    }, 15000);

    evtSource.onmessage = (e) => {
      lastActivity = Date.now();
      const msg = JSON.parse(e.data);
      if (msg.type === 'ping') return; // server keepalive — ignore
      if (msg.type === 'log') {
        const level = msg.text.includes('✓') ? 'ok' : msg.text.includes('✗') || msg.text.includes('ERROR') ? 'error' : msg.text.includes('WARNING') ? 'warn' : 'info';
        addLog(msg.text, level);
        setStatus(msg.text.slice(0, 60), 'running');
      } else if (msg.type === 'progress') {
        setProgress(msg.pct, msg.text);
        setStatus(msg.text.slice(0, 60), 'running');
      } else if (msg.type === 'rate_limit') {
        scanDone = true; clearInterval(watchdog); evtSource.close();
        setProgress(0, 'Rate limited');
        addLog('Rate limit: ' + msg.text, 'warn');
        setStatus('Rate limited — wait before next scan', 'error');
        resetBtn();
      } else if (msg.type === 'auth_error') {
        scanDone = true; clearInterval(watchdog); evtSource.close();
        setProgress(0, 'Auth required');
        addLog('Authentication required', 'error');
        setStatus('Please sign in', 'error');
        resetBtn();
      } else if (msg.type === 'done') {
        scanDone = true; clearInterval(watchdog); evtSource.close();
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
        scanDone = true; clearInterval(watchdog); evtSource.close();
        setProgress(0, 'Error');
        addLog('ERROR: ' + msg.text, 'error');
        setStatus('Scan failed — ' + msg.text.slice(0, 80), 'error');
        resetBtn();
      }
    };

    evtSource.onerror = () => {
      // Some browsers fire onerror on normal stream close — ignore if scan completed
      if (scanDone) return;
      clearInterval(watchdog);
      evtSource.close();
      setProgress(0, 'Connection lost');
      addLog('Connection lost — the scan may still be running server-side. Refresh to check results.', 'warn');
      setStatus('Connection dropped', 'error');
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

_EDU_ORBITAL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orbital Mechanics — VectraSpace Learn</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
  --ink:#0a0e14;--ink2:#111720;--ink3:#1a2333;--panel:#131b27;
  --border:rgba(255,255,255,0.08);--border2:rgba(255,255,255,0.14);
  --text:#d4dde8;--muted:#8aaac5;--accent:#3b82f6;--accent-h:#60a5fa;
  --amber:#f59e0b;--red:#ef4444;--green:#10b981;--teal:#14b8a6;--r:8px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--ink);color:var(--text);font-family:'Space Grotesk',sans-serif;line-height:1.6;overflow-x:hidden;}

/* NAV */
nav{position:fixed;top:0;left:0;right:0;z-index:100;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between;background:rgba(10,14,20,0.92);border-bottom:1px solid var(--border);backdrop-filter:blur(16px);}
.nav-brand{display:flex;align-items:center;text-decoration:none;color:#fff;}
.nav-brand-name{font-family:'Instrument Serif',Georgia,serif;font-size:17px;font-style:italic;letter-spacing:-0.2px;}
.nav-brand-name em{color:var(--accent);font-style:normal;}
.nav-back{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:1px;color:var(--muted);text-decoration:none;padding:6px 14px;border:1px solid var(--border);border-radius:4px;transition:all 0.2s;}
.nav-back:hover{color:var(--text);border-color:var(--border2);}
.nav-brand-name{font-family:'Instrument Serif',Georgia,serif;font-size:17px;font-style:italic;letter-spacing:-0.2px;color:#fff;}
.nav-brand-name em{color:var(--accent);font-style:normal;}
.chapter-progress{flex:1;max-width:300px;height:3px;background:rgba(255,255,255,0.06);border-radius:2px;margin:0 40px;overflow:hidden;}
.chapter-progress-fill{height:100%;background:var(--accent);border-radius:2px;width:0%;transition:width 0.1s;}

/* HERO */
.learn-hero{padding:100px 40px 60px;max-width:900px;margin:0 auto;}
.learn-breadcrumb{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:1.5px;color:var(--muted);text-transform:uppercase;margin-bottom:16px;}
.learn-breadcrumb a{color:var(--muted);text-decoration:none;}
.learn-breadcrumb a:hover{color:var(--accent);}
.learn-chapter{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:3px;text-transform:uppercase;color:var(--accent);margin-bottom:10px;}
.learn-title{font-family:'Syne',sans-serif;font-size:clamp(36px,5vw,64px);font-weight:800;letter-spacing:-1.5px;color:#fff;line-height:1.05;margin-bottom:20px;}
.learn-intro{font-size:17px;color:var(--muted);line-height:1.8;max-width:680px;margin-bottom:36px;}
.learn-meta{display:flex;gap:24px;font-family:'Space Mono',monospace;font-size:10px;color:var(--muted);letter-spacing:1px;}
.meta-item{display:flex;align-items:center;gap:6px;}

/* LAYOUT */
.learn-layout{display:grid;grid-template-columns:220px 1fr;gap:0;max-width:1100px;margin:0 auto;padding:0 40px 80px;}
.toc{position:sticky;top:80px;height:fit-content;padding-right:40px;}
.toc-title{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:14px;}
.toc-list{list-style:none;display:flex;flex-direction:column;gap:2px;}
.toc-list a{display:block;font-family:'Space Mono',monospace;font-size:10px;letter-spacing:0.5px;color:var(--muted);text-decoration:none;padding:6px 10px;border-radius:4px;border-left:2px solid transparent;transition:all 0.2s;}
.toc-list a:hover,.toc-list a.active{color:var(--accent);border-left-color:var(--accent);background:rgba(59,130,246,0.06);}
.content{min-width:0;}

/* CONTENT */
.section-block{margin-bottom:64px;}
.section-block h2{font-family:'Syne',sans-serif;font-size:28px;font-weight:700;color:#fff;letter-spacing:-0.5px;margin-bottom:16px;padding-top:16px;}
.section-block h3{font-family:'Syne',sans-serif;font-size:18px;font-weight:700;color:var(--text);margin:28px 0 10px;}
.section-block p{font-size:15px;color:var(--muted);line-height:1.85;margin-bottom:16px;}
.section-block p strong{color:var(--text);font-weight:600;}
.section-block p em{color:var(--accent-h);font-style:normal;}

/* EQUATION BLOCK */
.eq-block{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:var(--r);padding:24px 28px;margin:24px 0;}
.eq-label{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--accent);margin-bottom:10px;}
.eq-main{font-family:'STIX Two Math','Latin Modern Math',Georgia,serif;font-size:17px;color:#fff;letter-spacing:0;margin-bottom:10px;font-style:italic;}
.eq-vars{font-size:13px;color:var(--muted);line-height:1.9;}
.eq-vars code{font-family:'Space Mono',monospace;font-size:11px;color:var(--accent-h);}

/* CALLOUT */
.callout{background:rgba(59,130,246,0.06);border:1px solid rgba(59,130,246,0.2);border-radius:var(--r);padding:20px 24px;margin:24px 0;}
.callout-title{font-family:'Syne',sans-serif;font-size:13px;font-weight:700;color:var(--accent);margin-bottom:6px;}
.callout p{font-size:13px;color:var(--muted);line-height:1.75;margin:0;}
.callout.amber{background:rgba(245,158,11,0.06);border-color:rgba(245,158,11,0.2);}
.callout.amber .callout-title{color:var(--amber);}
.callout.red{background:rgba(239,68,68,0.06);border-color:rgba(239,68,68,0.2);}
.callout.red .callout-title{color:var(--red);}

/* TABLE */
.data-table-wrap{overflow-x:auto;margin:24px 0;border-radius:var(--r);border:1px solid var(--border);}
table{width:100%;border-collapse:collapse;}
thead th{background:rgba(255,255,255,0.04);font-family:'Space Mono',monospace;font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);padding:10px 16px;text-align:left;border-bottom:1px solid var(--border);}
tbody td{font-size:13px;padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.04);color:var(--text);}
tbody tr:last-child td{border-bottom:none;}
tbody td:first-child{font-family:'Space Mono',monospace;font-size:11px;color:var(--accent-h);}

/* DIAGRAM */
.diagram-wrap{background:var(--panel);border:1px solid var(--border);border-radius:var(--r);padding:24px;margin:24px 0;text-align:center;}
.diagram-caption{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-top:12px;}

/* NEXT/PREV */
.chapter-nav{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:80px;padding-top:40px;border-top:1px solid var(--border);}
.chapter-nav-card{padding:20px 24px;background:var(--panel);border:1px solid var(--border);border-radius:var(--r);text-decoration:none;transition:all 0.2s;}
.chapter-nav-card:hover{border-color:var(--border2);transform:translateY(-1px);}
.cnc-dir{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-bottom:6px;}
.cnc-title{font-family:'Syne',sans-serif;font-size:15px;font-weight:700;color:#fff;}
.chapter-nav-card.next{text-align:right;}

@media(max-width:800px){
  .learn-layout{grid-template-columns:1fr;padding:0 20px 60px;}
  .toc{display:none;}
  .learn-hero{padding:80px 20px 40px;}
  nav{padding:0 20px;}
}
</style>
</head>
<body>

<nav>
  <a href="/" class="nav-brand">
    <span class="nav-brand-name">Vectra<em>Space</em></span>
  </a>
  <div class="chapter-progress"><div class="chapter-progress-fill" id="progress-fill"></div></div>
  <div style="display:flex;gap:8px;"><a href="/#deep-dives" class="nav-back">← All Chapters</a><a href="/glossary" class="nav-back">News</a><a href="/calculator" class="nav-back">Calculator</a></div>
</nav>

<div class="learn-hero">
  <div class="learn-breadcrumb"><a href="/">VectraSpace</a> / <a href="/#deep-dives">Learn</a> / Orbital Mechanics</div>
  <div class="learn-chapter">Chapter 01 · Foundations</div>
  <h1 class="learn-title">Orbital Mechanics</h1>
  <p class="learn-intro">From Kepler's laws to <dfn data-term="SGP4">SGP4</dfn> propagation — the classical physics governing every object in Earth orbit. This is the mathematical foundation beneath VectraSpace's entire simulation engine.</p>
  <div class="learn-meta">
    <span class="meta-item">📖 ~15 min read</span>
    <span class="meta-item">🧮 8 equations</span>
    <span class="meta-item">🎯 Intermediate physics</span>
  </div>
</div>

<div class="learn-layout">
  <aside class="toc">
    <div class="toc-title">On This Page</div>
    <ul class="toc-list">
      <li><a href="#two-body">Two-Body Problem</a></li>
      <li><a href="#kepler">Kepler's Laws</a></li>
      <li><a href="#vis-viva"><dfn data-term="vis-viva">Vis-Viva Equation</dfn></a></li>
      <li><a href="#elements">Orbital Elements</a></li>
      <li><a href="#tle">TLE Format</a></li>
      <li><a href="#sgp4">SGP4 Propagation</a></li>
      <li><a href="#frames">Reference Frames</a></li>
      <li><a href="#velocity">Orbital Velocity</a></li>
    </ul>
  </aside>

  <div class="content">

    <div class="section-block" id="two-body">
      <h2>The Two-Body Problem</h2>
      <p>The foundation of orbital mechanics is the idealized <strong>two-body problem</strong>: a small object (satellite) in the gravitational field of a much larger body (Earth). Under this assumption, the only force acting on the satellite is Earth's gravity, and the motion can be described analytically.</p>
      <p>Newton's law of gravitation gives us the equation of motion:</p>
      <div class="eq-block">
        <div class="eq-label">Newton's Gravitational Equation of Motion</div>
        <div class="eq-main">r̈ = −(μ / r³) · r</div>
        <div class="eq-vars">
          <code>r</code> = position vector from Earth's center to satellite<br>
          <code>r̈</code> = second time derivative (acceleration)<br>
          <code>μ = GM</code> = gravitational parameter = 398,600.4418 km³/s²<br>
          <code>r = |r|</code> = scalar distance from Earth's center
        </div>
      </div>
      <p>This differential equation has analytical solutions that trace out <strong>conic sections</strong> — circles, ellipses, parabolas, or hyperbolas — depending on the satellite's total energy. Satellites in stable orbit follow ellipses.</p>
      <div class="callout">
        <div class="callout-title">Why "Two-Body"?</div>
        <p>In reality, many forces act on a satellite (atmospheric drag, solar radiation, Moon's gravity). The two-body problem ignores all of these. It gives us a clean analytical solution — a perfect baseline that perturbation theory then corrects. See Chapter 03 for perturbations.</p>
      </div>
    </div>

    <div class="section-block" id="kepler">
      <h2>Kepler's Three Laws</h2>
      <p>Johannes Kepler (1609–1619) empirically derived three laws from Tycho Brahe's planetary observations. These laws emerge naturally from the two-body problem and remain central to modern astrodynamics.</p>

      <h3>First Law — Elliptical Orbits</h3>
      <p>The orbit of a satellite around Earth is an <strong>ellipse</strong> with Earth's center at one focus. This means the distance between the satellite and Earth varies continuously — minimum at <em>perigee</em>, maximum at <em>apogee</em>.</p>

      <div class="eq-block">
        <div class="eq-label">Orbit Equation (Polar Form)</div>
        <div class="eq-main">r = p / (1 + e·cos θ)</div>
        <div class="eq-vars">
          <code>r</code> = orbital radius at true anomaly θ<br>
          <code>p = a(1 − e²)</code> = semi-latus rectum<br>
          <code>a</code> = semi-major axis<br>
          <code>e</code> = eccentricity (0 = circle, 0–1 = ellipse)<br>
          <code>θ</code> = true anomaly (angle from perigee)
        </div>
      </div>

      <h3>Second Law — Equal Areas</h3>
      <p>A satellite sweeps out <strong>equal areas in equal times</strong>. This is conservation of angular momentum in disguise: a satellite moves faster near perigee (lower altitude) and slower near apogee (higher altitude).</p>

      <div class="eq-block">
        <div class="eq-label">Conservation of Angular Momentum</div>
        <div class="eq-main">h = r × ṙ = √(μ · p) = const</div>
        <div class="eq-vars"><code>h</code> = specific angular momentum vector (constant throughout orbit)</div>
      </div>

      <h3>Third Law — Period Relation</h3>
      <p>The square of the orbital period is proportional to the cube of the semi-major axis. This is why GPS satellites at ~20,200 km orbit once per ~12 hours, while the ISS at ~420 km orbits once per ~92 minutes.</p>

      <div class="eq-block">
        <div class="eq-label">Kepler's Third Law</div>
        <div class="eq-main">T = 2π · √(a³ / μ)</div>
        <div class="eq-vars">
          <code>T</code> = orbital period (seconds)<br>
          <code>a</code> = semi-major axis (km)<br>
          <code>μ</code> = 398,600.4418 km³/s²
        </div>
      </div>

      <div class="data-table-wrap">
        <table>
          <thead><tr><th>Object</th><th>Altitude (km)</th><th>Semi-major axis (km)</th><th>Period</th><th>Velocity (km/s)</th></tr></thead>
          <tbody>
            <tr><td>ISS</td><td>~420</td><td>6,791</td><td>92 min</td><td>7.66</td></tr>
            <tr><td>Starlink</td><td>~550</td><td>6,921</td><td>95.5 min</td><td>7.60</td></tr>
            <tr><td>GPS</td><td>~20,200</td><td>26,571</td><td>11h 58m</td><td>3.87</td></tr>
            <tr><td>GEO (Clarke Belt)</td><td>35,786</td><td>42,164</td><td>23h 56m</td><td>3.07</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="section-block" id="vis-viva">
      <h2>The Vis-Viva Equation</h2>
      <p>The <dfn data-term="vis-viva">vis-viva equation</dfn> is perhaps the single most useful result in orbital mechanics. It relates a satellite's speed at any point in its orbit to its distance from Earth and the orbit's semi-major axis — through conservation of energy.</p>
      <div class="eq-block">
        <div class="eq-label">Vis-Viva Equation</div>
        <div class="eq-main">v² = μ · (2/r − 1/a)</div>
        <div class="eq-vars">
          <code>v</code> = orbital speed at radius r (km/s)<br>
          <code>μ</code> = gravitational parameter (km³/s²)<br>
          <code>r</code> = current distance from Earth's center (km)<br>
          <code>a</code> = semi-major axis of the orbit (km)
        </div>
      </div>
      <p>For a <strong>circular orbit</strong>, <code>r = a</code> everywhere, giving <code>v = √(μ/r)</code>. This is why lower satellites move faster — they're in a deeper gravitational well. A 1 m/s increase in speed at ISS altitude raises the opposite side of the orbit by ~1.75 km.</p>
      <div class="callout amber">
        <div class="callout-title">VectraSpace Application</div>
        <p>The vis-viva equation underlies all delta-v calculations in the maneuver planning module. When a conjunction is detected, the Clohessy-Wiltshire model computes the minimum Δv needed — and vis-viva tells us how that translates to an altitude change.</p>
      </div>
    </div>

    <div class="section-block" id="elements">
      <h2>Classical Orbital Elements</h2>
      <p>Six numbers fully describe any Keplerian orbit. These are the <strong>Classical Orbital Elements (COEs)</strong> — a compact parameterization used in TLE sets and almost every orbital database.</p>
      <div class="data-table-wrap">
        <table>
          <thead><tr><th>Symbol</th><th>Element</th><th>Description</th><th>Range</th></tr></thead>
          <tbody>
            <tr><td>a</td><td>Semi-major axis</td><td>Half the long axis of the ellipse. Determines orbit size and period.</td><td>0 → ∞ km</td></tr>
            <tr><td>e</td><td>Eccentricity</td><td>Shape of orbit. 0 = circle, 0–1 = ellipse, 1 = parabola (escape).</td><td>0 → &lt;1</td></tr>
            <tr><td>i</td><td>Inclination</td><td>Tilt of orbit plane relative to Earth's equatorial plane.</td><td>0° – 180°</td></tr>
            <tr><td>Ω</td><td>RAAN</td><td>Right Ascension of Ascending Node. Rotates orbit plane around polar axis.</td><td>0° – 360°</td></tr>
            <tr><td>ω</td><td>Argument of perigee</td><td>Angle from ascending node to closest approach point.</td><td>0° – 360°</td></tr>
            <tr><td>ν or M</td><td>True / Mean anomaly</td><td>Current position in orbit. True = actual angle; Mean = time-averaged.</td><td>0° – 360°</td></tr>
          </tbody>
        </table>
      </div>
      <p>Converting between mean anomaly M and true anomaly ν requires solving <em>Kepler's Equation</em> — a transcendental equation typically solved iteratively:</p>
      <div class="eq-block">
        <div class="eq-label">Kepler's Equation</div>
        <div class="eq-main">M = E − e · sin(E)</div>
        <div class="eq-vars">
          <code>M</code> = mean anomaly (linear in time: M = n·t, n = mean motion)<br>
          <code>E</code> = eccentric anomaly (solved iteratively via Newton-Raphson)<br>
          <code>e</code> = eccentricity
        </div>
      </div>
    </div>

    <div class="section-block" id="tle">
      <h2>Two-Line Element Sets (TLEs)</h2>
      <p>A TLE is the standard format used by NORAD and CelesTrak to distribute orbital data for tracked space objects. Each TLE encodes the six orbital elements plus perturbation coefficients in exactly 69 characters per line.</p>
      <div class="eq-block" style="font-size:11px;">
        <div class="eq-label">Example TLE — ISS</div>
        <div class="eq-main" style="font-size:12px;line-height:1.8">
          ISS (ZARYA)<br>
          1 25544U 98067A   24001.50000000  .00003456  00000-0  63041-4 0  9992<br>
          2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579431937
        </div>
        <div class="eq-vars">
          Line 1: Satellite number · Classification · Launch year/number · Epoch · Drag term (B*) · Element set number<br>
          Line 2: Inclination · RAAN · Eccentricity (assumed decimal) · Arg of Perigee · Mean Anomaly · Mean Motion (rev/day) · Rev number
        </div>
      </div>
      <p>TLE accuracy degrades over time as unmodeled perturbations accumulate. A fresh LEO TLE is typically accurate to ~1 km; after 7 days it may be off by 10+ km. This is why <strong>VectraSpace refreshes TLEs every 6 hours</strong> from CelesTrak and Space-Track.</p>
    </div>

    <div class="section-block" id="sgp4">
      <h2>SGP4 / SDP4 Propagation</h2>
      <p>The <strong>Simplified General Perturbations 4 (SGP4)</strong> model is the standard algorithm for propagating TLE sets forward in time. It analytically approximates the most significant orbital perturbations — Earth's oblateness (J₂, J₃, J₄), atmospheric drag, and solar/lunar effects (SDP4 for deep-space orbits).</p>
      <p>SGP4 takes a TLE and a time offset Δt, and returns an ECI position and velocity vector. The computation is fast — thousands of satellites can be propagated per second on modern hardware — making it ideal for VectraSpace's vectorized batch processing.</p>
      <div class="callout">
        <div class="callout-title">SGP4 in VectraSpace</div>
        <p>VectraSpace uses the Skyfield Python library's SGP4 implementation, propagating position arrays over 12–72 hour windows at 1-minute resolution. NumPy batching allows all satellites in a regime to be processed simultaneously, achieving 50× speedup over sequential loops.</p>
      </div>
      <div class="callout red">
        <div class="callout-title">Important Limitation</div>
        <p>SGP4 is a <em>mean element</em> theory — it models average perturbations, not instantaneous forces. For high-precision conjunction analysis (Pc &lt; 10⁻⁶), higher-fidelity numerical propagators with real atmospheric density models are required. VectraSpace's results should be treated as <strong>screening-level estimates</strong>, not operationally certified predictions.</p>
      </div>
    </div>

    <div class="section-block" id="frames">
      <h2>Reference Frames</h2>
      <p>Orbital calculations require a clear choice of coordinate system. VectraSpace uses two primary frames:</p>
      <h3>ECI — Earth-Centered Inertial</h3>
      <p>Origin at Earth's center. X-axis points to the vernal equinox; Z-axis to the celestial north pole. <strong>Does not rotate with Earth</strong>. Satellite positions and velocities are expressed in ECI for propagation calculations.</p>
      <h3>RTN — Radial-Transverse-Normal (Hill Frame)</h3>
      <p>A local coordinate frame co-moving with the reference satellite: <em>R</em> (radial, toward/away from Earth), <em>T</em> (transverse, along-track), <em>N</em> (normal, out-of-plane). Delta-v maneuver vectors are expressed in RTN.</p>
      <div class="eq-block">
        <div class="eq-label">RTN Unit Vectors</div>
        <div class="eq-main">R̂ = r/|r|,  N̂ = (r×ṙ)/|r×ṙ|,  T̂ = N̂×R̂</div>
      </div>
    </div>

    <div class="section-block" id="velocity">
      <h2>Circular Orbital Velocity</h2>
      <p>For a circular orbit, the satellite's speed is constant and determined entirely by altitude. This is the regime most LEO satellites operate in:</p>
      <div class="eq-block">
        <div class="eq-label">Circular Orbital Velocity</div>
        <div class="eq-main">v_c = √(μ / r) = √(μ / (R_E + h))</div>
        <div class="eq-vars">
          <code>v_c</code> = circular velocity (km/s)<br>
          <code>R_E</code> = Earth's mean radius = 6,371 km<br>
          <code>h</code> = altitude above surface (km)
        </div>
      </div>
      <p>At ISS altitude (420 km): v ≈ 7.66 km/s. At GEO (35,786 km): v ≈ 3.07 km/s. Two LEO satellites in crossing orbits can have a <strong>relative velocity of up to 15+ km/s</strong> — equivalent to a small car moving 54,000 km/h. A 1 cm aluminum sphere at this speed carries the kinetic energy of a hand grenade.</p>
    </div>


<!-- CHAPTER 1 QUIZ -->
<div class="quiz-section" id="ch1-quiz-wrap" data-storage-key='vs_ch1_done'>
  <div class="quiz-eyebrow">⬡ Knowledge Check</div>
  <div class="quiz-heading">Chapter 01 Quiz</div>
  <div class="quiz-subtitle">Test your understanding of the two-body problem, Kepler's laws, and SGP4 propagation.</div>
  <div id="ch1-quiz"></div>
</div>
<script>

function initQuiz(containerId, questions) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const letters = ['A','B','C','D','E'];

  function render() {
    container.innerHTML = questions.map((q, qi) => `
      <div class="quiz-q" id="qq-${containerId}-${qi}">
        <div class="quiz-q-num">Question ${qi+1} of ${questions.length}</div>
        <div class="quiz-q-text">${q.q}</div>
        <div class="quiz-options">
          ${q.opts.map((o, oi) => `
            <button class="quiz-opt" onclick="quizAnswer('${containerId}',${qi},${oi},${q.ans},${questions.length})"
              id="qo-${containerId}-${qi}-${oi}">
              <span class="quiz-opt-letter">${letters[oi]}</span>${o}
            </button>
          `).join('')}
        </div>
        <div class="quiz-explanation" id="qe-${containerId}-${qi}">${q.explain}</div>
      </div>
    `).join('') + `<div class="quiz-score-wrap" id="qs-${containerId}"></div>`;
  }

  window.quizAnswer = function(cid, qi, chosen, correct, total) {
    const optEls = document.querySelectorAll(`[id^="qo-${cid}-${qi}-"]`);
    optEls.forEach(b => b.disabled = true);
    const qEl = document.getElementById(`qq-${cid}-${qi}`);
    if (qEl.dataset.counted) return;
    qEl.classList.add('answered');
    optEls[correct].classList.add('correct');
    if (chosen !== correct) optEls[chosen].classList.add('wrong');
    const expEl = document.getElementById(`qe-${cid}-${qi}`);
    if (expEl) expEl.classList.add('show');
    qEl.dataset.counted = '1';
    if (chosen === correct) qEl.dataset.correct = '1';
    const allQ = document.querySelectorAll(`#${cid} [id^="qq-${cid}-"]`);
    if ([...allQ].every(q => q.dataset.counted)) {
      const sc = [...allQ].filter(q => q.dataset.correct).length;
      const tot = allQ.length;
      const pct = Math.round(sc / tot * 100);
      const color = pct >= 80 ? 'var(--green,#34d399)' : pct >= 50 ? 'var(--amber,#f59e0b)' : 'var(--red,#ef4444)';
      const grade = pct === 100 ? 'Perfect Score' : pct >= 80 ? 'Excellent' : pct >= 50 ? 'Good Effort' : 'Keep Studying';
      const msg = pct === 100 ? "You've fully mastered this chapter's core concepts and equations." :
                  pct >= 80  ? "Solid understanding — you're ready to move to the next chapter." :
                  pct >= 50  ? "Decent foundation. Review the highlighted explanations and retry." :
                               "Revisit the chapter material before moving on — the explanations above show where to focus.";
      const scoreEl = document.getElementById(`qs-${cid}`);
      scoreEl.style.setProperty('--qs-color', color);
      scoreEl.innerHTML = `
        <div class="quiz-score-top">
          <div class="quiz-score-ring" style="--qs-color:${color}">
            <div class="quiz-score-frac">${sc}/${tot}</div>
            <div class="quiz-score-pct">${pct}%</div>
          </div>
          <div class="quiz-score-right">
            <div class="quiz-score-title">${grade}</div>
            <div class="quiz-score-msg">${msg}</div>
          </div>
        </div>
        <div class="quiz-score-bottom">
          <button class="quiz-retry-btn" onclick="document.getElementById('${cid}-wrap').dispatchEvent(new Event('retry'))">↩ Retry Quiz</button>
        </div>
      `;
      scoreEl.classList.add('show');
      setTimeout(() => scoreEl.scrollIntoView({ behavior:'smooth', block:'nearest' }), 100);
    }
  };

  render();
  const wrap = document.getElementById(`${containerId}-wrap`);
  if (wrap) wrap.addEventListener('retry', () => { render(); wrap.scrollIntoView({ behavior:'smooth', block:'start' }); });
}

initQuiz('ch1-quiz', [
  {
    q: "A satellite orbits at 500 km altitude. Using the vis-viva equation with μ = 398,600 km³/s² and Earth radius 6,371 km, what is its approximate orbital velocity?",
    opts: ["7.61 km/s", "9.8 km/s", "11.2 km/s", "3.07 km/s"],
    ans: 0,
    explain: "r = 6,371 + 500 = 6,871 km. v = √(μ/r) = √(398,600/6,871) ≈ √57.9 ≈ 7.61 km/s for a circular orbit."
  },
  {
    q: "Kepler's Third Law states T² ∝ a³. If satellite A has a semi-major axis of 7,000 km and satellite B has a = 14,000 km, how does their orbital period compare?",
    opts: ["B's period is 2× longer", "B's period is 2√2 × longer", "B's period is 4× longer", "B's period is 8× longer"],
    ans: 1,
    explain: "T ∝ a^(3/2). Ratio = (14000/7000)^(3/2) = 2^(3/2) = 2√2 ≈ 2.83. So B's period is about 2√2 times longer."
  },
  {
    q: "What does the Two-Line Element (TLE) format encode?",
    opts: ["3D position and velocity vectors at epoch", "Keplerian orbital elements and propagation coefficients at epoch", "GPS coordinates updated every minute", "Satellite mass and drag coefficient only"],
    ans: 1,
    explain: "TLEs encode mean Keplerian elements (inclination, RAAN, eccentricity, argument of perigee, mean anomaly, mean motion) plus drag terms at a reference epoch. SGP4 propagates these forward in time."
  },
  {
    q: "In an elliptical orbit, where does a satellite move fastest?",
    opts: ["At apogee (furthest point)", "At perigee (closest point)", "At the semi-major axis crossing", "Speed is constant throughout"],
    ans: 1,
    explain: "By conservation of angular momentum (h = r × v = const), velocity is highest where r is smallest — at perigee. This is also consistent with the vis-viva equation: v² = μ(2/r − 1/a), so v increases as r decreases."
  },
  {
    q: "Why does SGP4 propagation accuracy degrade over time for LEO satellites?",
    opts: ["The satellite's mass changes as it burns fuel", "Unmodeled perturbations (drag, J₂, solar pressure) accumulate, causing position error to grow", "GPS signals become less accurate at higher altitudes", "SGP4 only works for circular orbits"],
    ans: 1,
    explain: "SGP4 models average perturbation effects but cannot capture every atmospheric fluctuation or solar event. Position errors typically grow from ~1 km at epoch to 10+ km after 7 days for LEO satellites."
  }
]);

// ── GLOSSARY TOOLTIPS ─────────────────────────────────────────
(function() {
  const DEFS = {
    'SGP4':'Simplified General Perturbations model 4 — the standard analytical propagator for Earth satellites using TLE data. Models drag (B*), J₂ oblateness, and secular/periodic terms.',
    'TLE':'Two-Line Element Set — standardised format encoding six Keplerian elements at a given epoch. Accuracy decays ~1 km/day for LEO objects.',
    'vis-viva':'v² = μ(2/r − 1/a). Relates orbital speed to current radius, semi-major axis, and gravitational parameter. Underlies all delta-v calculations.',
    'RAAN':'Right Ascension of Ascending Node — angle from the vernal equinox to the ascending node. Drifts westward for prograde LEO orbits due to J₂ oblateness at ~6–7°/day.',
    'J₂':'Dominant Earth oblateness coefficient: J₂ = 1.08263×10⁻³. Causes nodal regression (RAAN drift) and apsidal precession for all satellites.',
    'Kessler':'A self-sustaining cascade of collisions above a critical debris density, proposed by Donald Kessler (NASA, 1978). Potentially irreversible above 800 km.',
    'Pc':'Probability of Collision — the probability two objects physically contact. Computed by integrating the combined position uncertainty PDF over the collision cross-section. Action threshold: 1×10⁻⁴.',
    'TCA':'Time of Closest Approach — the moment of minimum separation between two conjunction objects. Reference epoch for all CDM calculations.',
    'CDM':'Conjunction Data Message — CCSDS standard format for sharing conjunction screening results between space surveillance providers and operators.',
    'RTN':'Radial-Transverse-Normal coordinate frame. R points from Earth through satellite, T is along-track, N is orbit-plane normal. Standard frame for CDM covariance matrices.',
    'covariance':'A 3×3 or 6×6 symmetric matrix encoding position uncertainty and correlations in RTN. Diagonal elements are position variances (σ_R², σ_T², σ_N²).',
    'NASA SBM':'NASA Standard Breakup Model — predicts fragment count N(Lc) = 6·M^0.75·Lc^−1.6 from satellite collisions or explosions. Used in EVOLVE and LEGEND environment models.',
    'B*':'Ballistic coefficient in TLE format — encodes aerodynamic drag sensitivity. Higher B* means more susceptibility to atmospheric drag.',
    'delta-v':'Change in velocity (km/s) required for an orbital maneuver — the fundamental currency of spaceflight. Limited by onboard propellant.',
  };
  const tip = document.createElement('div');
  tip.className = 'gtooltip';
  tip.innerHTML = '<div class="gtooltip-term"></div><div class="gtooltip-def"></div><a class="gtooltip-link" href="/glossary">Space News →</a>';
  document.body.appendChild(tip);
  let hideTimer;
  document.querySelectorAll('dfn[data-term]').forEach(el => {
    const key = el.dataset.term;
    const def = DEFS[key] || '';
    if (!def) return;
    el.addEventListener('mouseenter', e => {
      clearTimeout(hideTimer);
      tip.querySelector('.gtooltip-term').textContent = key;
      tip.querySelector('.gtooltip-def').textContent = def;
      const rect = el.getBoundingClientRect();
      let top = rect.bottom + 8, left = rect.left;
      if (left + 300 > window.innerWidth - 16) left = window.innerWidth - 316;
      if (top + 120 > window.innerHeight - 16) top = rect.top - 130;
      tip.style.top = top + 'px'; tip.style.left = left + 'px';
      tip.classList.add('show');
    });
    el.addEventListener('mouseleave', () => { hideTimer = setTimeout(() => tip.classList.remove('show'), 200); });
  });
})();

</script>

    <div class="chapter-nav">
      <div></div>
      <a href="/education/collision-prediction" class="chapter-nav-card next">
        <div class="cnc-dir">Next Chapter →</div>
        <div class="cnc-title">Collision Prediction</div>
      </a>
    </div>

  </div>
</div>

<script>
// Reading progress bar
const fill = document.getElementById('progress-fill');
window.addEventListener('scroll', () => {
  const h = document.documentElement;
  const pct = (window.scrollY / (h.scrollHeight - h.clientHeight)) * 100;
  fill.style.width = pct + '%';
});

// TOC active highlight
const sections = document.querySelectorAll('.section-block');
const links = document.querySelectorAll('.toc-list a');
const obs = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if(e.isIntersecting) {
      links.forEach(l => l.classList.remove('active'));
      const active = document.querySelector(`.toc-list a[href="#${e.target.id}"]`);
      if(active) active.classList.add('active');
    }
  });
}, { threshold: 0.3 });
sections.forEach(s => obs.observe(s));

<!-- ══════════════════════════════════════════════════════════
     GUIDED TOUR OVERLAY
     ══════════════════════════════════════════════════════════ -->
<style>
/* ── TOUR LAUNCHER ── */
#tour-fab {
  position: fixed; bottom: 28px; right: 28px; z-index: 9000;
  width: 52px; height: 52px; border-radius: 50%;
  background: var(--accent); border: none; cursor: pointer;
  box-shadow: 0 4px 20px rgba(74,158,255,0.5), 0 0 0 0 rgba(74,158,255,0.3);
  display: flex; align-items: center; justify-content: center;
  animation: tour-pulse 3s ease-in-out infinite;
  transition: transform 0.2s, box-shadow 0.2s;
  color: #fff;
}
#tour-fab:hover { transform: scale(1.08); box-shadow: 0 6px 28px rgba(74,158,255,0.7); }
#tour-fab svg { width: 22px; height: 22px; fill: none; stroke: #fff; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
@keyframes tour-pulse {
  0%,100% { box-shadow: 0 4px 20px rgba(74,158,255,0.5), 0 0 0 0 rgba(74,158,255,0.3); }
  50%      { box-shadow: 0 4px 20px rgba(74,158,255,0.5), 0 0 0 12px rgba(74,158,255,0); }
}
#tour-fab-label {
  position: fixed; bottom: 34px; right: 88px; z-index: 9000;
  font-family: var(--mono); font-size: 9px; letter-spacing: 1.5px;
  text-transform: uppercase; color: var(--muted);
  background: var(--panel); border: 1px solid var(--border);
  padding: 6px 12px; border-radius: 4px; white-space: nowrap;
  pointer-events: none;
  opacity: 1; transition: opacity 0.4s;
}

/* ── TOUR BACKDROP ── */
#tour-backdrop {
  display: none; position: fixed; inset: 0; z-index: 8900;
  background: rgba(4,8,16,0.72); backdrop-filter: blur(3px);
}
#tour-backdrop.active { display: block; }

/* ── SPOTLIGHT CUTOUT ── */
#tour-spotlight {
  position: fixed; z-index: 8950; pointer-events: none;
  border-radius: 10px;
  box-shadow: 0 0 0 9999px rgba(4,8,16,0.76);
  transition: all 0.4s cubic-bezier(0.4,0,0.2,1);
  border: 1.5px solid rgba(74,158,255,0.4);
}

/* ── TOUR CARD ── */
#tour-card {
  display: none; position: fixed; z-index: 9100;
  width: 320px;
  background: var(--panel, #0d1320);
  border: 1px solid rgba(74,158,255,0.25);
  border-radius: 14px;
  padding: 0;
  overflow: hidden;
  box-shadow: 0 16px 48px rgba(0,0,0,0.6), 0 0 0 1px rgba(74,158,255,0.12);
  transition: top 0.4s cubic-bezier(0.4,0,0.2,1), left 0.4s cubic-bezier(0.4,0,0.2,1);
}
#tour-card.active { display: block; }
.tc-header {
  background: linear-gradient(135deg, rgba(74,158,255,0.12), rgba(74,158,255,0.04));
  border-bottom: 1px solid rgba(74,158,255,0.12);
  padding: 16px 20px 14px;
  display: flex; align-items: center; justify-content: space-between;
}
.tc-step-badge {
  font-family: var(--mono, monospace); font-size: 8px; letter-spacing: 2px;
  text-transform: uppercase; color: var(--accent, #4a9eff);
  background: rgba(74,158,255,0.1); border: 1px solid rgba(74,158,255,0.2);
  padding: 3px 9px; border-radius: 12px;
}
.tc-close {
  width: 26px; height: 26px; border-radius: 50%;
  background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1);
  color: #8aaac5; cursor: pointer; display: flex; align-items: center;
  justify-content: center; font-size: 14px; transition: all 0.15s; line-height:1;
  font-family: monospace;
}
.tc-close:hover { background: rgba(255,255,255,0.12); color: #fff; }
.tc-body { padding: 18px 20px; }
.tc-icon { font-size: 28px; margin-bottom: 10px; display: block; }
.tc-title {
  font-family: var(--serif, serif); font-size: 18px; font-style: italic;
  color: #fff; margin-bottom: 8px; line-height: 1.25;
}
.tc-title em { color: var(--accent, #4a9eff); font-style: normal; }
.tc-text {
  font-family: var(--sans, sans-serif); font-size: 13px;
  color: var(--muted, #8aaac5); line-height: 1.7;
}
.tc-text strong { color: var(--text, #ccd6e0); }
.tc-footer {
  padding: 14px 20px 16px;
  display: flex; align-items: center; justify-content: space-between;
  border-top: 1px solid rgba(255,255,255,0.06);
}
.tc-dots { display: flex; gap: 5px; }
.tc-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: rgba(255,255,255,0.15); transition: background 0.2s;
}
.tc-dot.active { background: var(--accent, #4a9eff); }
.tc-btns { display: flex; gap: 8px; }
.tc-btn {
  font-family: var(--mono, monospace); font-size: 9px; letter-spacing: 1px;
  text-transform: uppercase; padding: 8px 16px; border-radius: 6px;
  cursor: pointer; border: 1px solid; transition: all 0.15s;
}
.tc-btn-skip {
  background: transparent; border-color: rgba(255,255,255,0.1); color: var(--muted, #8aaac5);
}
.tc-btn-skip:hover { border-color: rgba(255,255,255,0.2); color: var(--text, #ccd6e0); }
.tc-btn-next {
  background: var(--accent, #4a9eff); border-color: var(--accent, #4a9eff); color: #fff;
}
.tc-btn-next:hover { background: #6bb5ff; border-color: #6bb5ff; }
.tc-progress-bar {
  height: 2px; background: rgba(74,158,255,0.15);
  position: relative; overflow: hidden;
}
.tc-progress-fill {
  height: 100%; background: var(--accent, #4a9eff);
  transition: width 0.4s ease;
}
</style>

<!-- Tour Launcher FAB -->
<div id="tour-fab-label">Take the tour</div>
<button id="tour-fab" onclick="tourStart()" aria-label="Start guided tour" title="Take the guided tour">
  <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
</button>

<!-- Tour Backdrop + Spotlight -->
<div id="tour-backdrop" onclick="tourClose()"></div>
<div id="tour-spotlight"></div>

<!-- Tour Card -->
<div id="tour-card">
  <div class="tc-progress-bar"><div class="tc-progress-fill" id="tc-progress"></div></div>
  <div class="tc-header">
    <span class="tc-step-badge" id="tc-badge">Step 1 / 7</span>
    <button class="tc-close" onclick="tourClose()">✕</button>
  </div>
  <div class="tc-body">
    <span class="tc-icon" id="tc-icon">🛰</span>
    <div class="tc-title" id="tc-title">Welcome to <em>VectraSpace</em></div>
    <p class="tc-text" id="tc-text">A two-minute tour of everything the platform can do. Hit Next to start — you can exit anytime.</p>
  </div>
  <div class="tc-footer">
    <div class="tc-dots" id="tc-dots"></div>
    <div class="tc-btns">
      <button class="tc-btn tc-btn-skip" onclick="tourClose()">Skip</button>
      <button class="tc-btn tc-btn-next" id="tc-next" onclick="tourNext()">Start →</button>
    </div>
  </div>
</div>

<script>
(function() {
  // ── TOUR STEPS ───────────────────────────────────────────────
  const STEPS = [
    {
      selector: null, // centre screen intro card
      icon: '🛰',
      title: 'Welcome to <em>VectraSpace</em>',
      text: 'An orbital safety education platform covering real debris events, collision prediction, and Keplerian mechanics. This two-minute tour shows you where everything lives.',
      pos: 'center',
      nextLabel: 'Start →'
    },
    {
      selector: '#hero',
      icon: '⬡',
      title: 'The <em>Mission</em>',
      text: 'Over <strong>27,000 objects</strong> are tracked in orbit right now. VectraSpace helps you understand the physics, risks, and real-world events behind the headline numbers.',
      pos: 'bottom',
      nextLabel: 'Next →'
    },
    {
      selector: '#learn',
      icon: '📖',
      title: 'Four <em>Deep Dives</em>',
      text: 'Work through four self-paced chapters: <strong>Orbital Mechanics</strong>, <strong>Collision Prediction</strong>, <strong>Perturbation Forces</strong>, and <strong>Debris Modeling</strong>. Each has interactive quizzes and simulations.',
      pos: 'top',
      nextLabel: 'Next →'
    },
    {
      selector: '#sim',
      icon: '💥',
      title: 'Interactive <em>Scenarios</em>',
      text: 'Watch the <strong>Iridium-Cosmos collision</strong>, the FY-1C ASAT test, and a Kessler cascade play out in real 3D — with accurate fragment counts and timelines.',
      pos: 'top',
      nextLabel: 'Next →'
    },
    {
      selector: '#satod',
      icon: '🔭',
      title: 'Satellite of the <em>Day</em>',
      text: 'Every day a different real satellite is featured — orbital parameters, launch history, and mission context. Bookmark it and come back tomorrow.',
      pos: 'top',
      nextLabel: 'Next →'
    },
    {
      selector: '#data',
      icon: '📡',
      title: 'Live <em>TLE Feed</em>',
      text: 'The strip below pulls <strong>live Two-Line Element data</strong> from CelesTrak. Every satellite shown is currently in orbit with a real altitude derived from propagated TLEs.',
      pos: 'top',
      nextLabel: 'Next →'
    },
    {
      selector: '#contact',
      icon: '🚀',
      title: 'Ready to <em>Explore</em>?',
      text: 'Head to the <strong>Orbit Explorer</strong> to visualise any Keplerian orbit, try the <strong>Scenarios</strong> for visual impact, or start <strong>Chapter 01</strong> to build your understanding from the ground up.',
      pos: 'top',
      nextLabel: 'Explore →'
    },
  ];

  let step = 0;
  let started = false;

  const backdrop   = document.getElementById('tour-backdrop');
  const spotlight  = document.getElementById('tour-spotlight');
  const card       = document.getElementById('tour-card');
  const badge      = document.getElementById('tc-badge');
  const icon       = document.getElementById('tc-icon');
  const title      = document.getElementById('tc-title');
  const text       = document.getElementById('tc-text');
  const nextBtn    = document.getElementById('tc-next');
  const dots       = document.getElementById('tc-dots');
  const progress   = document.getElementById('tc-progress');
  const fab        = document.getElementById('tour-fab');
  const fabLabel   = document.getElementById('tour-fab-label');

  // Build dots
  STEPS.forEach((_, i) => {
    const d = document.createElement('div');
    d.className = 'tc-dot';
    dots.appendChild(d);
  });

  function showStep(n) {
    const s = STEPS[n];
    const total = STEPS.length;
    // Update content
    badge.textContent = 'Step ' + (n+1) + ' / ' + total;
    icon.textContent  = s.icon;
    title.innerHTML   = s.title;
    text.innerHTML    = s.text;
    nextBtn.textContent = s.nextLabel || 'Next →';
    // Dots
    dots.querySelectorAll('.tc-dot').forEach((d,i) => d.classList.toggle('active', i===n));
    // Progress bar
    progress.style.width = ((n+1)/total*100) + '%';

    // Spotlight + card positioning
    if (s.selector) {
      const el = document.querySelector(s.selector);
      if (el) {
        const r = el.getBoundingClientRect();
        const pad = 12;
        spotlight.style.display = 'block';
        spotlight.style.left   = (r.left - pad) + 'px';
        spotlight.style.top    = (r.top - pad) + 'px';
        spotlight.style.width  = (r.width + pad*2) + 'px';
        spotlight.style.height = (r.height + pad*2) + 'px';
        positionCard(r, s.pos);
      }
    } else {
      // Intro: hide spotlight, centre card
      spotlight.style.display = 'none';
      card.style.top  = '50%';
      card.style.left = '50%';
      card.style.transform = 'translate(-50%,-50%)';
    }
  }

  function positionCard(r, pos) {
    card.style.transform = '';
    const cw = 320, ch = 280;
    const vw = window.innerWidth, vh = window.innerHeight;
    const pad = 16;
    let top, left;

    if (pos === 'bottom') {
      top  = r.bottom + 20;
      left = Math.min(r.left, vw - cw - pad);
    } else { // top
      top  = r.top - ch - 20;
      left = Math.min(r.left, vw - cw - pad);
    }
    // Clamp
    top  = Math.max(pad, Math.min(top,  vh - ch - pad));
    left = Math.max(pad, Math.min(left, vw - cw - pad));

    card.style.top  = top  + 'px';
    card.style.left = left + 'px';
  }

  window.tourStart = function() {
    if (started) return;
    started = true;
    step = 0;
    backdrop.classList.add('active');
    card.classList.add('active');
    fab.style.display = 'none';
    fabLabel.style.opacity = '0';
    showStep(0);
  };

  window.tourNext = function() {
    step++;
    if (step >= STEPS.length) {
      tourClose();
      // Scroll to learn section as final action
      const learnEl = document.getElementById('learn');
      if (learnEl) learnEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    showStep(step);
  };

  window.tourClose = function() {
    backdrop.classList.remove('active');
    spotlight.style.display = 'none';
    card.classList.remove('active');
    fab.style.display = 'flex';
    fabLabel.style.opacity = '1';
    started = false;
    step = 0;
  };

  // Hide label after 6 seconds
  setTimeout(() => {
    if (fabLabel) fabLabel.style.opacity = '0';
  }, 6000);

  // Auto-show tour for first-time visitors
  try {
    if (!localStorage.getItem('vs_tour_seen')) {
      setTimeout(() => {
        try { localStorage.setItem('vs_tour_seen', '1'); } catch(e){}
        tourStart();
      }, 1800);
    }
  } catch(e) {
    // private browsing - show tour anyway after delay
    setTimeout(tourStart, 1800);
  }
})();
</script>

</script>
</body>
</html>

"""

_EDU_COLLISION_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Collision Prediction — VectraSpace Learn</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;700;800&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root{--ink:#0a0e14;--ink2:#111720;--panel:#131b27;--border:rgba(255,255,255,0.08);--border2:rgba(255,255,255,0.14);--text:#d4dde8;--muted:#8aaac5;--accent:#f59e0b;--accent-h:#fbbf24;--blue:#3b82f6;--red:#ef4444;--green:#10b981;--r:8px;}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--ink);color:var(--text);font-family:'Space Grotesk',sans-serif;line-height:1.6;overflow-x:hidden;}
nav{position:fixed;top:0;left:0;right:0;z-index:100;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between;background:rgba(10,14,20,0.92);border-bottom:1px solid var(--border);backdrop-filter:blur(16px);}
.nav-brand{display:flex;align-items:center;text-decoration:none;color:#fff;}
.nav-brand-name{font-family:'Instrument Serif',Georgia,serif;font-size:17px;font-style:italic;letter-spacing:-0.2px;}
.nav-brand-name em{color:var(--accent);font-style:normal;}
.nav-back{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:1px;color:var(--muted);text-decoration:none;padding:6px 14px;border:1px solid var(--border);border-radius:4px;transition:all 0.2s;}
.nav-back:hover{color:var(--text);border-color:var(--border2);}
.chapter-progress{flex:1;max-width:300px;height:3px;background:rgba(255,255,255,0.06);border-radius:2px;margin:0 40px;overflow:hidden;}
.chapter-progress-fill{height:100%;background:var(--accent);border-radius:2px;width:0%;transition:width 0.1s;}
.learn-hero{padding:100px 40px 60px;max-width:900px;margin:0 auto;}
.learn-breadcrumb{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:1.5px;color:var(--muted);text-transform:uppercase;margin-bottom:16px;}
.learn-breadcrumb a{color:var(--muted);text-decoration:none;}
.learn-breadcrumb a:hover{color:var(--blue);}
.learn-chapter{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:3px;text-transform:uppercase;color:var(--accent);margin-bottom:10px;}
.learn-title{font-family:'Syne',sans-serif;font-size:clamp(36px,5vw,64px);font-weight:800;letter-spacing:-1.5px;color:#fff;line-height:1.05;margin-bottom:20px;}
.learn-intro{font-size:17px;color:var(--muted);line-height:1.8;max-width:680px;margin-bottom:36px;}
.learn-meta{display:flex;gap:24px;font-family:'Space Mono',monospace;font-size:10px;color:var(--muted);letter-spacing:1px;}
.learn-layout{display:grid;grid-template-columns:220px 1fr;gap:0;max-width:1100px;margin:0 auto;padding:0 40px 80px;}
.toc{position:sticky;top:80px;height:fit-content;padding-right:40px;}
.toc-title{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:14px;}
.toc-list{list-style:none;display:flex;flex-direction:column;gap:2px;}
.toc-list a{display:block;font-family:'Space Mono',monospace;font-size:10px;letter-spacing:0.5px;color:var(--muted);text-decoration:none;padding:6px 10px;border-radius:4px;border-left:2px solid transparent;transition:all 0.2s;}
.toc-list a:hover,.toc-list a.active{color:var(--accent);border-left-color:var(--accent);background:rgba(245,158,11,0.06);}
.content{min-width:0;}
.section-block{margin-bottom:64px;}
.section-block h2{font-family:'Syne',sans-serif;font-size:28px;font-weight:700;color:#fff;letter-spacing:-0.5px;margin-bottom:16px;padding-top:16px;}
.section-block h3{font-family:'Syne',sans-serif;font-size:18px;font-weight:700;color:var(--text);margin:28px 0 10px;}
.section-block p{font-size:15px;color:var(--muted);line-height:1.85;margin-bottom:16px;}
.section-block p strong{color:var(--text);font-weight:600;}
.section-block p em{color:var(--accent-h);font-style:normal;}
.eq-block{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:var(--r);padding:24px 28px;margin:24px 0;}
.eq-label{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--accent);margin-bottom:10px;}
.eq-main{font-family:'STIX Two Math','Latin Modern Math',Georgia,serif;font-size:17px;color:#fff;letter-spacing:0;margin-bottom:10px;font-style:italic;}
.eq-vars{font-size:13px;color:var(--muted);line-height:1.9;}
.eq-vars code{font-family:'Space Mono',monospace;font-size:11px;color:var(--accent-h);}
.callout{background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.2);border-radius:var(--r);padding:20px 24px;margin:24px 0;}
.callout-title{font-family:'Syne',sans-serif;font-size:13px;font-weight:700;color:var(--accent);margin-bottom:6px;}
.callout p{font-size:13px;color:var(--muted);line-height:1.75;margin:0;}
.callout.blue{background:rgba(59,130,246,0.06);border-color:rgba(59,130,246,0.2);}
.callout.blue .callout-title{color:var(--blue);}
.callout.red{background:rgba(239,68,68,0.06);border-color:rgba(239,68,68,0.2);}
.callout.red .callout-title{color:var(--red);}
.callout.green{background:rgba(16,185,129,0.06);border-color:rgba(16,185,129,0.2);}
.callout.green .callout-title{color:var(--green);}
.data-table-wrap{overflow-x:auto;margin:24px 0;border-radius:var(--r);border:1px solid var(--border);}
table{width:100%;border-collapse:collapse;}
thead th{background:rgba(255,255,255,0.04);font-family:'Space Mono',monospace;font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);padding:10px 16px;text-align:left;border-bottom:1px solid var(--border);}
tbody td{font-size:13px;padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.04);color:var(--text);}
tbody tr:last-child td{border-bottom:none;}
tbody td:first-child{font-family:'Space Mono',monospace;font-size:11px;color:var(--accent-h);}
.risk-scale{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:16px 0;gap:2px;}
.rs-seg{flex:1;border-radius:2px;}
.pc-table{width:100%;}
.chapter-nav{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:80px;padding-top:40px;border-top:1px solid var(--border);}
.chapter-nav-card{padding:20px 24px;background:var(--panel);border:1px solid var(--border);border-radius:var(--r);text-decoration:none;transition:all 0.2s;}
.chapter-nav-card:hover{border-color:var(--border2);transform:translateY(-1px);}
.cnc-dir{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-bottom:6px;}
.cnc-title{font-family:'Syne',sans-serif;font-size:15px;font-weight:700;color:#fff;}
.chapter-nav-card.next{text-align:right;}
@media(max-width:800px){
  .learn-layout{grid-template-columns:1fr;padding:0 20px 60px;}
  .toc{display:none;}
  .learn-hero{padding:80px 20px 40px;}
  nav{padding:0 20px;}
  .content-block{padding:24px 0;}
  .equation-box{padding:16px 14px;overflow-x:auto;}
  .quiz-wrap{padding:24px 20px;}
  .quiz-option{padding:12px 14px;font-size:13px;}
  .learn-hero h1{font-size:clamp(26px,7vw,42px);}
}
</style>
</head>
<body>

<nav>
  <a href="/" class="nav-brand"><span class="nav-brand-name">Vectra<em>Space</em></span></a>
  <div class="chapter-progress"><div class="chapter-progress-fill" id="progress-fill"></div></div>
  <div style="display:flex;gap:8px;"><a href="/#deep-dives" class="nav-back">← All Chapters</a><a href="/glossary" class="nav-back">News</a><a href="/calculator" class="nav-back">Calculator</a></div>
</nav>

<div class="learn-hero">
  <div class="learn-breadcrumb"><a href="/">VectraSpace</a> / <a href="/#deep-dives">Learn</a> / Collision Prediction</div>
  <div class="learn-chapter">Chapter 02 · Risk Analysis</div>
  <h1 class="learn-title">Collision Prediction</h1>
  <p class="learn-intro">How do we calculate the probability that two objects will collide? This chapter covers the mathematics of conjunction analysis — from identifying close approaches to computing Pc and planning avoidance maneuvers.</p>
  <div class="learn-meta">
    <span>📖 ~18 min read</span>
    <span>🧮 10 equations</span>
    <span>🎯 Intermediate–Advanced</span>
  </div>
</div>

<div class="learn-layout">
  <aside class="toc">
    <div class="toc-title">On This Page</div>
    <ul class="toc-list">
      <li><a href="#screening">Conjunction Screening</a></li>
      <li><a href="#tca"><dfn data-term="TCA">Time of Closest Approach</dfn></a></li>
      <li><a href="#covariance">Uncertainty & Covariance</a></li>
      <li><a href="#pc-method">Pc Calculation</a></li>
      <li><a href="#pc-levels">Risk Thresholds</a></li>
      <li><a href="#cdm">CDM Standard</a></li>
      <li><a href="#maneuver">Avoidance Maneuvers</a></li>
      <li><a href="#cw">Clohessy-Wiltshire</a></li>
    </ul>
  </aside>

  <div class="content">

    <div class="section-block" id="screening">
      <h2>Conjunction Screening</h2>
      <p>With 27,000+ tracked objects in orbit, checking every possible pair at every time step would be computationally prohibitive. Conjunction screening uses a <strong>filter cascade</strong> to rapidly eliminate low-risk pairs before expensive calculations.</p>
      <h3>Step 1: Perigee-Apogee Filter</h3>
      <p>Two objects can only collide if their orbits can geometrically intersect. Objects whose apogee-perigee altitude ranges don't overlap are immediately eliminated.</p>
      <h3>Step 2: Ellipsoidal Pre-filter</h3>
      <p>For remaining pairs, compute the minimum distance over the propagation window. Only pairs where this coarse minimum falls within <em>n·σ</em> of the combined position uncertainty are retained for refinement:</p>
      <div class="eq-block">
        <div class="eq-label">Ellipsoidal Overlap Condition</div>
        <div class="eq-main">d_miss ≤ n · √2 · max(σ_a, σ_c, σ_r)</div>
        <div class="eq-vars">
          <code>d_miss</code> = coarse minimum miss distance<br>
          <code>n</code> = sigma multiplier (typically 5σ)<br>
          <code>σ_a, σ_c, σ_r</code> = position uncertainty: along-track, cross-track, radial
        </div>
      </div>
      <p>VectraSpace uses <strong>NumPy-batched distance matrix computation</strong> — all satellite pairs computed simultaneously in chunks, achieving ~50× speedup over sequential iteration. Typically 85–95% of pairs are eliminated at this stage.</p>
    </div>

    <div class="section-block" id="tca">
      <h2>Time of Closest Approach (TCA)</h2>
      <p>After coarse screening, the exact <strong>Time of Closest Approach (TCA)</strong> is found by minimizing the inter-satellite distance as a function of time. VectraSpace uses a bounded golden-section search (Brent's method) within a ±1 minute window around the coarse minimum.</p>
      <div class="eq-block">
        <div class="eq-label">Miss Distance at TCA</div>
        <div class="eq-main">d(t) = |r₁(t) − r₂(t)|<br>TCA = argmin_t d(t)</div>
        <div class="eq-vars">
          <code>r₁(t), r₂(t)</code> = propagated positions of objects 1 and 2 at time t<br>
          Time interpolation uses Hermite polynomials for smooth derivatives
        </div>
      </div>
      <p>Relative velocity at TCA determines collision energy. For LEO-crossing conjunctions, relative speeds of <strong>0–15 km/s</strong> are possible — even a 10 cm fragment at 10 km/s carries 500+ kJ of kinetic energy, catastrophic for any spacecraft.</p>
    </div>

    <div class="section-block" id="covariance">
      <h2>Uncertainty & Covariance</h2>
      <p>We never know a satellite's position exactly. Every TLE has errors — from unmodeled forces, tracking gaps, and atmospheric variability. This uncertainty is quantified by a <strong><dfn data-term="covariance">covariance matrix</dfn></strong> in the RTN frame.</p>
      <div class="eq-block">
        <div class="eq-label">3×3 RTN Covariance Matrix</div>
        <div class="eq-main">
P = [CR_R   CT_R   CN_R]<br>
    [CT_R   CT_T   CN_T]<br>
    [CN_R   CN_T   CN_N]
        </div>
        <div class="eq-vars">
          Diagonal elements: variance in radial (R), transverse (T), normal (N) directions<br>
          Off-diagonal elements: cross-correlations (usually large CT_R for LEO drag errors)<br>
          Position uncertainty ellipsoid: principal axes from eigendecomposition of P
        </div>
      </div>
      <p>When real CDM covariance data is available from Space-Track, VectraSpace uses it. When not, it falls back to <strong>assumed sigma values</strong> — typically σ_along = 500m, σ_cross = 200m, σ_radial = 100m for LEO. The covariance source is flagged in every conjunction report.</p>
      <div class="callout blue">
        <div class="callout-title">Why Covariance Matters</div>
        <p>Two conjunctions with the same 5 km miss distance can have wildly different Pc values — depending on the uncertainty. If position uncertainty is only 100 m (very certain), Pc is near zero. If uncertainty is 10 km (very uncertain), the 5 km miss could represent a high-risk event. Pc collapses miss distance and uncertainty into a single risk metric.</p>
      </div>
    </div>

    <div class="section-block" id="pc-method">
      <h2><dfn data-term="Pc">Probability of Collision</dfn> — Foster-Alfano Method</h2>
      <p>VectraSpace uses the <strong>Foster (1992) / Alfano (1995)</strong> conjunction probability method, which projects the 3D problem onto the 2D collision plane (the plane perpendicular to relative velocity at TCA).</p>
      <p>The combined position PDF (assuming Gaussian) is integrated over a disk of radius <em>R_c</em> — the "hard-body radius," or sum of the two object radii:</p>
      <div class="eq-block">
        <div class="eq-label">2D Collision Probability (Foster-Alfano)</div>
        <div class="eq-main">Pc = (1/2π·σ_x·σ_y) · ∬_D exp[−½·(x²/σ_x² + y²/σ_y²)] dx dy</div>
        <div class="eq-vars">
          Integration domain D: disk of radius R_c centered on predicted miss vector<br>
          <code>σ_x, σ_y</code> = combined 1σ position uncertainty in collision plane<br>
          <code>R_c = r₁ + r₂</code> = combined hard-body radius (typically 5–15 m for intact satellites)<br>
          Numerically evaluated using chi-squared CDF: Pc ≈ 1 − χ²_CDF(x², df=2)
        </div>
      </div>
      <p>This integral has no closed form for arbitrary offset — it is computed numerically in VectraSpace using SciPy's chi-squared CDF as an approximation valid for the typical range of operational Pc values.</p>
      <div class="eq-block">
        <div class="eq-label">VectraSpace Implementation (Simplified)</div>
        <div class="eq-main">σ_c = √[(σ_a² + σ_c² + σ_r²)/3] · √2<br>x = ((d_miss − R_c) / σ_c)²<br>Pc = 1 − χ²_CDF(x, df=3)</div>
      </div>
    </div>

    <div class="section-block" id="pc-levels">
      <h2>Risk Thresholds & Decision Framework</h2>
      <p>Pc alone does not tell an operator what to do. Different organizations apply different thresholds based on risk tolerance, available propellant, and operational context.</p>
      <div class="risk-scale">
        <div class="rs-seg" style="background:#10b981;flex:3"></div>
        <div class="rs-seg" style="background:#f59e0b;flex:2"></div>
        <div class="rs-seg" style="background:#ef4444;flex:1.5"></div>
        <div class="rs-seg" style="background:#7f1d1d;flex:0.5"></div>
      </div>
      <div class="data-table-wrap">
        <table>
          <thead><tr><th>Pc Range</th><th>Risk Level</th><th>Typical Response</th><th>VectraSpace Alert</th></tr></thead>
          <tbody>
            <tr><td>&lt; 1×10⁻⁶</td><td style="color:#10b981">Negligible</td><td>No action required</td><td>No alert</td></tr>
            <tr><td>1×10⁻⁶ – 1×10⁻⁴</td><td style="color:#fbbf24">Low / Watch</td><td>Monitor; gather more data</td><td>Optional</td></tr>
            <tr><td>1×10⁻⁴ – 1×10⁻³</td><td style="color:#f59e0b">Elevated</td><td>Maneuver analysis; prepare burn</td><td>Default threshold</td></tr>
            <tr><td>1×10⁻³ – 1×10⁻²</td><td style="color:#ef4444">High</td><td>Maneuver strongly recommended</td><td>High priority alert</td></tr>
            <tr><td>&gt; 1×10⁻²</td><td style="color:#7f1d1d">Critical</td><td>Emergency maneuver required</td><td>Critical alert</td></tr>
          </tbody>
        </table>
      </div>
      <p>The default VectraSpace alert threshold is <strong>Pc ≥ 1×10⁻⁴</strong> (1 in 10,000) — consistent with NASA and ESA operational screening. Users can adjust this in their preferences down to 1×10⁻⁶ for higher sensitivity or up to 1×10⁻² for reduced noise.</p>
      <div class="callout red">
        <div class="callout-title">The False Alarm Problem</div>
        <p>Most conjunction alerts do not lead to actual collisions. The false positive rate at 1×10⁻⁴ is very high — operators must balance the cost of unnecessary maneuvers (fuel, operational complexity) against the risk of inaction. This is fundamentally a decision theory problem, not just a physics problem.</p>
      </div>
    </div>

    <div class="section-block" id="cdm">
      <h2><dfn data-term="CDM">Conjunction Data Message</dfn>s (CDM)</h2>
      <p>The <strong>CCSDS Conjunction Data Message (CDM)</strong> standard (CCSDS 508.0-B-1) is the international format for communicating conjunction events between agencies, operators, and databases. VectraSpace generates a CDM for every detected conjunction.</p>
      <p>A CDM contains: time of closest approach, miss distance, Pc estimate, Pc method identifier, and full covariance matrices for both objects. It is the interoperability standard for space traffic management worldwide.</p>
      <div class="callout green">
        <div class="callout-title">Download CDMs from VectraSpace</div>
        <p>Every conjunction detected in a VectraSpace scan generates a downloadable CDM file. Individual events can be downloaded from the results panel; the full run can be exported as a ZIP archive. These files follow the CCSDS format and can be imported into other SSA tools.</p>
      </div>
    </div>

    <div class="section-block" id="maneuver">
      <h2>Avoidance Maneuver Planning</h2>
      <p>When Pc exceeds the action threshold, the satellite operator must decide whether and how to maneuver. The goal is to <strong>increase miss distance</strong> sufficiently to bring Pc below threshold, using the minimum propellant (Δv).</p>
      <p>Maneuver planning is complicated by uncertainty in both orbits — a maneuver may be necessary even with low initial Pc if subsequent TLE updates reveal the true trajectory is closer. Conversely, updates may show a previously alarming event was benign.</p>
      <h3>Maneuver Geometry</h3>
      <p>The most efficient avoidance burns are typically in the <strong>transverse (along-track) direction</strong>. An along-track burn changes the orbital period, causing the satellite to arrive at the conjunction point earlier or later — spatially shifting the pass without a large altitude change.</p>
    </div>

    <div class="section-block" id="cw">
      <h2>Clohessy-Wiltshire (Hill's) Equations</h2>
      <p>For satellites in nearby orbits, the <strong>Clohessy-Wiltshire (CW) equations</strong> (also called Hill's equations) describe relative motion in the co-rotating RTN frame. They linearize orbital mechanics around a reference circular orbit, making analytical maneuver solutions tractable.</p>
      <div class="eq-block">
        <div class="eq-label">Clohessy-Wiltshire Equations (Linearized Relative Motion)</div>
        <div class="eq-main">
ẍ − 2n·ẏ − 3n²·x = f_x<br>
ÿ + 2n·ẋ = f_y<br>
z̈ + n²·z = f_z
        </div>
        <div class="eq-vars">
          <code>x, y, z</code> = relative position in radial (x), transverse (y), normal (z)<br>
          <code>n = √(μ/a³)</code> = mean motion of reference orbit<br>
          <code>f_x, f_y, f_z</code> = applied accelerations (thrust)<br>
          Coriolis terms (−2n·ẏ, +2n·ẋ) couple radial and transverse motion
        </div>
      </div>
      <p>VectraSpace uses a simplified CW solution to estimate minimum Δv for each conjunction. The advisory assumes an impulsive burn and linear dynamics — appropriate for initial screening. <strong>All maneuver recommendations require verification with a high-fidelity propagator before execution.</strong></p>
      <div class="eq-block">
        <div class="eq-label">VectraSpace Minimum Δv Estimate</div>
        <div class="eq-main">Δv_T ≈ (d_safe − d_current) / (2 · t_TCA)<br>Δv_R ≈ −(v_rel · r̂) · 0.1</div>
        <div class="eq-vars">
          <code>d_safe</code> = target safe separation distance (default: 50 km)<br>
          <code>d_current</code> = current predicted miss distance<br>
          <code>t_TCA</code> = time until closest approach (seconds)<br>
          Output: [Δv_R, Δv_T, Δv_N] vector in m/s (RTN frame)
        </div>
      </div>
    </div>


<!-- CHAPTER 2 QUIZ -->
<div class="quiz-section" id="ch2-quiz-wrap" data-storage-key='vs_ch2_done'>
  <div class="quiz-eyebrow">⬡ Knowledge Check</div>
  <div class="quiz-heading">Chapter 02 Quiz</div>
  <div class="quiz-subtitle">Test your understanding of conjunction analysis, miss distance, and probability of collision.</div>
  <div id="ch2-quiz"></div>
</div>
<script>

function initQuiz(containerId, questions) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const letters = ['A','B','C','D','E'];

  function render() {
    container.innerHTML = questions.map((q, qi) => `
      <div class="quiz-q" id="qq-${containerId}-${qi}">
        <div class="quiz-q-num">Question ${qi+1} of ${questions.length}</div>
        <div class="quiz-q-text">${q.q}</div>
        <div class="quiz-options">
          ${q.opts.map((o, oi) => `
            <button class="quiz-opt" onclick="quizAnswer('${containerId}',${qi},${oi},${q.ans},${questions.length})"
              id="qo-${containerId}-${qi}-${oi}">
              <span class="quiz-opt-letter">${letters[oi]}</span>${o}
            </button>
          `).join('')}
        </div>
        <div class="quiz-explanation" id="qe-${containerId}-${qi}">${q.explain}</div>
      </div>
    `).join('') + `<div class="quiz-score-wrap" id="qs-${containerId}"></div>`;
  }

  window.quizAnswer = function(cid, qi, chosen, correct, total) {
    const optEls = document.querySelectorAll(`[id^="qo-${cid}-${qi}-"]`);
    optEls.forEach(b => b.disabled = true);
    const qEl = document.getElementById(`qq-${cid}-${qi}`);
    if (qEl.dataset.counted) return;
    qEl.classList.add('answered');
    optEls[correct].classList.add('correct');
    if (chosen !== correct) optEls[chosen].classList.add('wrong');
    const expEl = document.getElementById(`qe-${cid}-${qi}`);
    if (expEl) expEl.classList.add('show');
    qEl.dataset.counted = '1';
    if (chosen === correct) qEl.dataset.correct = '1';
    const allQ = document.querySelectorAll(`#${cid} [id^="qq-${cid}-"]`);
    if ([...allQ].every(q => q.dataset.counted)) {
      const sc = [...allQ].filter(q => q.dataset.correct).length;
      const tot = allQ.length;
      const pct = Math.round(sc / tot * 100);
      const color = pct >= 80 ? 'var(--green,#34d399)' : pct >= 50 ? 'var(--amber,#f59e0b)' : 'var(--red,#ef4444)';
      const grade = pct === 100 ? 'Perfect Score' : pct >= 80 ? 'Excellent' : pct >= 50 ? 'Good Effort' : 'Keep Studying';
      const msg = pct === 100 ? "You've fully mastered this chapter's core concepts and equations." :
                  pct >= 80  ? "Solid understanding — you're ready to move to the next chapter." :
                  pct >= 50  ? "Decent foundation. Review the highlighted explanations and retry." :
                               "Revisit the chapter material before moving on — the explanations above show where to focus.";
      const scoreEl = document.getElementById(`qs-${cid}`);
      scoreEl.style.setProperty('--qs-color', color);
      scoreEl.innerHTML = `
        <div class="quiz-score-top">
          <div class="quiz-score-ring" style="--qs-color:${color}">
            <div class="quiz-score-frac">${sc}/${tot}</div>
            <div class="quiz-score-pct">${pct}%</div>
          </div>
          <div class="quiz-score-right">
            <div class="quiz-score-title">${grade}</div>
            <div class="quiz-score-msg">${msg}</div>
          </div>
        </div>
        <div class="quiz-score-bottom">
          <button class="quiz-retry-btn" onclick="document.getElementById('${cid}-wrap').dispatchEvent(new Event('retry'))">↩ Retry Quiz</button>
        </div>
      `;
      scoreEl.classList.add('show');
      setTimeout(() => scoreEl.scrollIntoView({ behavior:'smooth', block:'nearest' }), 100);
    }
  };

  render();
  const wrap = document.getElementById(`${containerId}-wrap`);
  if (wrap) wrap.addEventListener('retry', () => { render(); wrap.scrollIntoView({ behavior:'smooth', block:'start' }); });
}

initQuiz('ch2-quiz', [
  {
    q: "Two satellites have a miss distance of 3 km at TCA. Satellite 1 has position uncertainty σ = 0.1 km. Satellite 2 has σ = 5 km. Which scenario results in a higher probability of collision (Pc)?",
    opts: ["Both are equal — Pc depends only on miss distance", "Scenario A (σ = 0.1 km) — smaller uncertainty means higher confidence in the close pass", "Scenario B (σ = 5 km) — larger uncertainty spreads the covariance ellipsoid across the miss distance", "Pc is undefined without knowing satellite mass"],
    ans: 2,
    explain: "When uncertainty is large relative to miss distance, the probability mass of the combined covariance ellipsoid overlaps the collision hard-body radius more. Large σ with small miss distance → high Pc. Small σ with 3 km miss distance → Pc near zero (the position is well-known to be safe)."
  },
  {
    q: "What is Time of Closest Approach (TCA)?",
    opts: ["The moment two satellites collide", "The time instant when the distance between two objects reaches its minimum", "The time when two satellites are directly above each other on the ground track", "The average time between conjunction events"],
    ans: 1,
    explain: "TCA is defined as argmin_t |r₁(t) − r₂(t)| — the time at which the inter-satellite distance is minimized. It is the reference point for miss distance and conjunction geometry calculations."
  },
  {
    q: "In the Foster-Alfano Pc method, what does the 2D collision probability integral compute?",
    opts: ["The exact probability of physical contact between two rigid bodies", "The probability that the combined position uncertainty ellipsoid overlaps the combined hard-body radius disk in the conjunction plane", "The probability that either satellite will maneuver within 24 hours", "The kinetic energy released if the two objects collide"],
    ans: 1,
    explain: "Foster-Alfano projects relative position uncertainty onto the conjunction plane (perpendicular to relative velocity) and integrates the 2D Gaussian PDF over a disk of radius equal to the sum of hard-body radii. This gives the probability that the relative position falls within collision distance."
  },
  {
    q: "Conjunction Data Messages (CDMs) use the RTN coordinate frame. What do R, T, and N stand for?",
    opts: ["Right, Tangential, Normal", "Radial, Tangential, Normal (along-track)", "Radial, Transverse, Nodal", "Range, Time, Navigation"],
    ans: 1,
    explain: "RTN is the satellite-centered frame: R (Radial) points from Earth center through the satellite, T (Tangential/Along-track) is in the orbit plane perpendicular to R in the direction of motion, and N (Normal) is perpendicular to the orbit plane. Covariance matrices in CDMs are expressed in this frame."
  },
  {
    q: "What Pc threshold does US Space Command generally use to trigger active conjunction screening and operator notification?",
    opts: ["1 in 10 (10%)", "1 in 1,000 (1e-3)", "1 in 10,000 (1e-4)", "1 in 1,000,000 (1e-6)"],
    ans: 2,
    explain: "A Pc of 1 in 10,000 (1e-4) is a widely used industry threshold for elevated-risk conjunctions requiring operator attention and potential maneuver consideration. Above 1e-3 is considered high-risk."
  }
]);

// ── GLOSSARY TOOLTIPS ─────────────────────────────────────────
(function() {
  const DEFS = {
    'SGP4':'Simplified General Perturbations model 4 — the standard analytical propagator for Earth satellites using TLE data. Models drag (B*), J₂ oblateness, and secular/periodic terms.',
    'TLE':'Two-Line Element Set — standardised format encoding six Keplerian elements at a given epoch. Accuracy decays ~1 km/day for LEO objects.',
    'vis-viva':'v² = μ(2/r − 1/a). Relates orbital speed to current radius, semi-major axis, and gravitational parameter. Underlies all delta-v calculations.',
    'RAAN':'Right Ascension of Ascending Node — angle from the vernal equinox to the ascending node. Drifts westward for prograde LEO orbits due to J₂ oblateness at ~6–7°/day.',
    'J₂':'Dominant Earth oblateness coefficient: J₂ = 1.08263×10⁻³. Causes nodal regression (RAAN drift) and apsidal precession for all satellites.',
    'Kessler':'A self-sustaining cascade of collisions above a critical debris density, proposed by Donald Kessler (NASA, 1978). Potentially irreversible above 800 km.',
    'Pc':'Probability of Collision — the probability two objects physically contact. Computed by integrating the combined position uncertainty PDF over the collision cross-section. Action threshold: 1×10⁻⁴.',
    'TCA':'Time of Closest Approach — the moment of minimum separation between two conjunction objects. Reference epoch for all CDM calculations.',
    'CDM':'Conjunction Data Message — CCSDS standard format for sharing conjunction screening results between space surveillance providers and operators.',
    'RTN':'Radial-Transverse-Normal coordinate frame. R points from Earth through satellite, T is along-track, N is orbit-plane normal. Standard frame for CDM covariance matrices.',
    'covariance':'A 3×3 or 6×6 symmetric matrix encoding position uncertainty and correlations in RTN. Diagonal elements are position variances (σ_R², σ_T², σ_N²).',
    'NASA SBM':'NASA Standard Breakup Model — predicts fragment count N(Lc) = 6·M^0.75·Lc^−1.6 from satellite collisions or explosions. Used in EVOLVE and LEGEND environment models.',
    'B*':'Ballistic coefficient in TLE format — encodes aerodynamic drag sensitivity. Higher B* means more susceptibility to atmospheric drag.',
    'delta-v':'Change in velocity (km/s) required for an orbital maneuver — the fundamental currency of spaceflight. Limited by onboard propellant.',
  };
  const tip = document.createElement('div');
  tip.className = 'gtooltip';
  tip.innerHTML = '<div class="gtooltip-term"></div><div class="gtooltip-def"></div><a class="gtooltip-link" href="/glossary">Space News →</a>';
  document.body.appendChild(tip);
  let hideTimer;
  document.querySelectorAll('dfn[data-term]').forEach(el => {
    const key = el.dataset.term;
    const def = DEFS[key] || '';
    if (!def) return;
    el.addEventListener('mouseenter', e => {
      clearTimeout(hideTimer);
      tip.querySelector('.gtooltip-term').textContent = key;
      tip.querySelector('.gtooltip-def').textContent = def;
      const rect = el.getBoundingClientRect();
      let top = rect.bottom + 8, left = rect.left;
      if (left + 300 > window.innerWidth - 16) left = window.innerWidth - 316;
      if (top + 120 > window.innerHeight - 16) top = rect.top - 130;
      tip.style.top = top + 'px'; tip.style.left = left + 'px';
      tip.classList.add('show');
    });
    el.addEventListener('mouseleave', () => { hideTimer = setTimeout(() => tip.classList.remove('show'), 200); });
  });
})();

</script>

    <div class="chapter-nav">
      <a href="/education/orbital-mechanics" class="chapter-nav-card">
        <div class="cnc-dir">← Previous Chapter</div>
        <div class="cnc-title">Orbital Mechanics</div>
      </a>
      <a href="/education/perturbations" class="chapter-nav-card next">
        <div class="cnc-dir">Next Chapter →</div>
        <div class="cnc-title">Orbital Perturbations</div>
      </a>
    </div>

  </div>
</div>

<script>
const fill = document.getElementById('progress-fill');
window.addEventListener('scroll', () => {
  const h = document.documentElement;
  fill.style.width = ((window.scrollY / (h.scrollHeight - h.clientHeight)) * 100) + '%';
});
const sections = document.querySelectorAll('.section-block');
const links = document.querySelectorAll('.toc-list a');
const obs = new IntersectionObserver(e => {
  e.forEach(en => {
    if(en.isIntersecting){
      links.forEach(l => l.classList.remove('active'));
      const a = document.querySelector(`.toc-list a[href="#${en.target.id}"]`);
      if(a) a.classList.add('active');
    }
  });
}, { threshold: 0.3 });
sections.forEach(s => obs.observe(s));
</script>
</body>
</html>

"""

_EDU_PERTURBATIONS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orbital Perturbations — VectraSpace Deep Dive</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;700;800&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {
  --ink:        #070c14;
  --ink-2:      #0d1520;
  --ink-3:      #111d2b;
  --border:     #1a2e42;
  --border-2:   #243d54;
  --accent:     #3b82f6;
  --accent-glow:rgba(59,130,246,0.18);
  --amber:      #f59e0b;
  --amber-dim:  rgba(245,158,11,0.12);
  --green:      #10b981;
  --green-dim:  rgba(16,185,129,0.10);
  --red:        #ef4444;
  --red-dim:    rgba(239,68,68,0.10);
  --text:       #c9ddef;
  --text-2:     #9dbbd4;
  --text-3:     #6d92ad;
  --mono:       'Space Mono', monospace;
  --math:       'STIX Two Math','Latin Modern Math',Georgia,serif;
  --sans:       'Space Grotesk', sans-serif;
  --display:    'Syne', sans-serif;
  --toc-w:      230px;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; font-size: 16px; }
body {
  background: var(--ink);
  color: var(--text);
  font-family: var(--sans);
  line-height: 1.7;
  overflow-x: hidden;
}

/* ── PROGRESS BAR ── */
#progress-bar {
  position: fixed; top: 0; left: 0; height: 2px; width: 0%;
  background: linear-gradient(90deg, var(--accent), var(--amber));
  z-index: 200; transition: width 0.1s linear;
}

/* ── NAV ── */
nav {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  height: 56px; padding: 0 32px;
  display: flex; align-items: center; justify-content: space-between;
  background: rgba(7,12,20,0.92);
  border-bottom: 1px solid var(--border);
  backdrop-filter: blur(12px);
}
.nav-brand {
  font-family: var(--mono); font-size: 11px; letter-spacing: 3px;
  color: var(--accent); text-transform: uppercase; text-decoration: none;
}
.nav-back {
  font-family: var(--mono); font-size: 10px; letter-spacing: 2px;
  color: var(--text-3); text-decoration: none; text-transform: uppercase;
  transition: color 0.2s;
}
.nav-back:hover { color: var(--accent); }

/* ── HERO ── */
.hero {
  padding: 120px 48px 64px;
  max-width: 900px; margin: 0 auto;
  position: relative;
}
.hero-breadcrumb {
  font-family: var(--mono); font-size: 9px; letter-spacing: 3px;
  color: var(--text-3); text-transform: uppercase; margin-bottom: 16px;
}
.hero-breadcrumb a { color: var(--text-3); text-decoration: none; }
.hero-breadcrumb a:hover { color: var(--accent); }
.chapter-label {
  display: inline-block; font-family: var(--mono); font-size: 9px;
  letter-spacing: 3px; color: var(--amber); text-transform: uppercase;
  background: var(--amber-dim); border: 1px solid rgba(245,158,11,0.25);
  padding: 4px 10px; border-radius: 2px; margin-bottom: 20px;
}
.hero h1 {
  font-family: var(--display); font-size: clamp(36px,5vw,58px);
  font-weight: 800; line-height: 1.1; color: #fff; margin-bottom: 16px;
}
.hero-accent { color: var(--accent); }
.hero-intro {
  font-size: 17px; font-weight: 300; color: var(--text-2); line-height: 1.8;
  max-width: 680px; margin-bottom: 32px;
}
.hero-meta {
  display: flex; gap: 24px; flex-wrap: wrap;
  font-family: var(--mono); font-size: 9px; letter-spacing: 2px;
  color: var(--text-3); text-transform: uppercase;
}
.hero-meta span { display: flex; align-items: center; gap: 6px; }
.hero-meta-dot { width: 4px; height: 4px; background: var(--accent); border-radius: 50%; }

/* ── LAYOUT ── */
.page-wrap {
  max-width: 1140px; margin: 0 auto;
  padding: 48px 48px 120px;
  display: grid;
  grid-template-columns: var(--toc-w) 1fr;
  gap: 64px;
  align-items: start;
}

/* ── TOC ── */
.toc {
  position: sticky; top: 72px;
  background: var(--ink-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
  max-height: calc(100vh - 88px);
  overflow-y: auto;
}
.toc::-webkit-scrollbar { width: 3px; }
.toc::-webkit-scrollbar-thumb { background: var(--border); }
.toc-label {
  font-family: var(--mono); font-size: 8px; letter-spacing: 3px;
  color: var(--text-3); text-transform: uppercase; margin-bottom: 14px;
  padding-bottom: 8px; border-bottom: 1px solid var(--border);
}
.toc-list { list-style: none; display: flex; flex-direction: column; gap: 2px; }
.toc-list a {
  display: block; font-size: 12px; color: var(--text-3);
  text-decoration: none; padding: 5px 8px; border-radius: 4px;
  transition: all 0.2s; border-left: 2px solid transparent;
}
.toc-list a:hover { color: var(--text); background: var(--ink-3); }
.toc-list a.active {
  color: var(--accent); background: var(--accent-glow);
  border-left-color: var(--accent);
}

/* ── CONTENT ── */
.content { min-width: 0; }
.content-section { margin-bottom: 72px; scroll-margin-top: 80px; }
.section-number {
  font-family: var(--mono); font-size: 9px; letter-spacing: 3px;
  color: var(--accent); text-transform: uppercase; margin-bottom: 12px;
}
.content h2 {
  font-family: var(--display); font-size: clamp(22px,3vw,30px);
  font-weight: 700; color: #fff; margin-bottom: 20px; line-height: 1.2;
}
.content h3 {
  font-family: var(--sans); font-size: 16px; font-weight: 600;
  color: var(--text); margin: 28px 0 12px;
}
.content p { margin-bottom: 16px; color: var(--text-2); font-size: 15px; }
.content strong { color: var(--text); font-weight: 600; }

/* ── EQUATION BLOCKS ── */
.eq-block {
  background: var(--ink-2); border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 6px; padding: 20px 24px; margin: 24px 0;
  font-family: var(--mono); font-size: 13px; color: var(--text);
  overflow-x: auto;
}
.eq-block .eq-label {
  font-size: 8px; letter-spacing: 3px; color: var(--text-3);
  text-transform: uppercase; margin-bottom: 10px;
}
.eq-block .eq-main { font-family:var(--math,'STIX Two Math',Georgia,serif); font-size: 17px; color: #fff; margin-bottom: 8px; font-style:italic; }
.eq-block .eq-vars { font-size: 12px; color: var(--text-2); line-height: 1.9; }
.eq-block .eq-var-name { color: var(--amber); }

/* ── CALLOUT BOXES ── */
.callout {
  border-radius: 6px; padding: 16px 20px; margin: 24px 0;
  border-left: 3px solid; font-size: 14px;
}
.callout.info {
  background: rgba(59,130,246,0.07); border-color: var(--accent); color: var(--text);
}
.callout.warning {
  background: var(--amber-dim); border-color: var(--amber); color: var(--text);
}
.callout.danger {
  background: var(--red-dim); border-color: var(--red); color: var(--text);
}
.callout.success {
  background: var(--green-dim); border-color: var(--green); color: var(--text);
}
.callout-label {
  font-family: var(--mono); font-size: 8px; letter-spacing: 3px;
  text-transform: uppercase; margin-bottom: 6px;
  display: block;
}
.callout.info .callout-label { color: var(--accent); }
.callout.warning .callout-label { color: var(--amber); }
.callout.danger .callout-label { color: var(--red); }
.callout.success .callout-label { color: var(--green); }

/* ── DATA TABLE ── */
.data-table-wrap { overflow-x: auto; margin: 24px 0; }
table {
  width: 100%; border-collapse: collapse;
  font-size: 13px; font-family: var(--mono);
}
thead th {
  background: var(--ink-3); color: var(--text-3); font-size: 9px;
  letter-spacing: 2px; text-transform: uppercase; padding: 10px 14px;
  text-align: left; border-bottom: 1px solid var(--border);
}
tbody td { padding: 10px 14px; border-bottom: 1px solid rgba(26,46,66,0.5); color: var(--text-2); }
tbody tr:hover td { background: var(--ink-2); }
.td-accent { color: var(--accent); }
.td-amber  { color: var(--amber); }
.td-green  { color: var(--green); }
.td-red    { color: var(--red); }
.td-white  { color: #fff; font-weight: 600; }

/* ── PERTURBATION DIAGRAM ── */
.pert-diagram {
  background: var(--ink-2); border: 1px solid var(--border);
  border-radius: 8px; padding: 32px; margin: 24px 0; overflow: hidden;
}
.pert-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px;
}
.pert-card {
  background: var(--ink-3); border: 1px solid var(--border);
  border-radius: 6px; padding: 18px; transition: border-color 0.2s;
}
.pert-card:hover { border-color: var(--accent); }
.pert-card-icon { font-size: 24px; margin-bottom: 10px; }
.pert-card-title {
  font-family: var(--mono); font-size: 10px; letter-spacing: 2px;
  color: var(--accent); text-transform: uppercase; margin-bottom: 6px;
}
.pert-card-desc { font-size: 13px; color: var(--text-2); line-height: 1.6; }
.pert-card-mag {
  margin-top: 10px; padding: 6px 8px;
  background: var(--ink); border-radius: 4px;
  font-family: var(--mono); font-size: 11px; color: var(--amber);
}

/* ── J2 VISUALIZER ── */
.j2-vis {
  background: var(--ink-2); border: 1px solid var(--border);
  border-radius: 8px; padding: 24px; margin: 24px 0;
  display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: center;
}
.j2-canvas-wrap { position: relative; height: 200px; }
.j2-canvas-wrap canvas { width: 100%; height: 100%; }
.j2-data { display: flex; flex-direction: column; gap: 12px; }
.j2-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; background: var(--ink-3); border-radius: 4px;
  border-left: 2px solid var(--border);
  font-family: var(--mono); font-size: 11px;
}
.j2-row.active { border-left-color: var(--accent); }
.j2-key { color: var(--text-3); }
.j2-val { color: var(--accent); }

/* ── DRAG CHART ── */
.drag-chart-wrap {
  background: var(--ink-2); border: 1px solid var(--border);
  border-radius: 8px; padding: 24px; margin: 24px 0;
}
.drag-chart-title {
  font-family: var(--mono); font-size: 9px; letter-spacing: 3px;
  color: var(--text-3); text-transform: uppercase; margin-bottom: 16px;
}
.drag-bars { display: flex; flex-direction: column; gap: 10px; }
.drag-bar-row { display: flex; align-items: center; gap: 12px; }
.drag-bar-label { font-family: var(--mono); font-size: 10px; color: var(--text-2); width: 100px; flex-shrink: 0; }
.drag-bar-track { flex: 1; background: var(--ink-3); border-radius: 2px; height: 8px; position: relative; }
.drag-bar-fill { height: 100%; border-radius: 2px; background: var(--accent); transition: width 0.8s ease; }
.drag-bar-val { font-family: var(--mono); font-size: 10px; color: var(--amber); width: 80px; text-align: right; flex-shrink: 0; }

/* ── TLE ACCURACY CHART ── */
.accuracy-chart {
  background: var(--ink-2); border: 1px solid var(--border);
  border-radius: 8px; padding: 24px; margin: 24px 0;
}
.accuracy-chart canvas { width: 100%; height: 180px; }

/* ── CHAPTER NAV ── */
.chapter-nav {
  display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
  margin-top: 80px; padding-top: 40px;
  border-top: 1px solid var(--border);
}
.chapter-nav-card {
  background: var(--ink-2); border: 1px solid var(--border);
  border-radius: 8px; padding: 20px 24px; text-decoration: none;
  transition: all 0.2s; display: block;
}
.chapter-nav-card:hover { border-color: var(--accent); background: var(--ink-3); }
.cnc-dir {
  font-family: var(--mono); font-size: 8px; letter-spacing: 3px;
  color: var(--text-3); text-transform: uppercase; margin-bottom: 6px;
}
.cnc-title { font-family: var(--display); font-size: 16px; font-weight: 700; color: #fff; }
.cnc-sub { font-size: 12px; color: var(--text-3); margin-top: 4px; }
.chapter-nav-card.next { text-align: right; }

/* ── SCROLL REVEAL ── */
.reveal { opacity: 0; transform: translateY(16px); transition: opacity 0.6s ease, transform 0.6s ease; }
.reveal.visible { opacity: 1; transform: none; }

@media (max-width: 900px) {
  .page-wrap { grid-template-columns: 1fr; }
  .toc { display: none; }
  .hero { padding: 100px 24px 48px; }
  .page-wrap { padding: 32px 24px 80px; }
  .pert-grid, .j2-vis { grid-template-columns: 1fr; }
  
/* ─── QUIZ ─────────────────────────────────────────────────── */
.quiz-section {
  margin: 64px 0 0; padding: 48px 0 0;
  border-top: 1px solid var(--border);
}
.quiz-eyebrow {
  font-family: 'Space Mono', monospace; font-size: 9px;
  letter-spacing: 3px; text-transform: uppercase;
  color: var(--accent); margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
}
.quiz-eyebrow::before { content: ''; width: 20px; height: 1px; background: var(--accent); display: inline-block; }
.quiz-heading {
  font-family: 'Syne', sans-serif; font-size: 26px; font-weight: 800;
  color: #fff; letter-spacing: -0.5px; margin-bottom: 6px;
}
.quiz-subtitle { font-size: 14px; color: var(--muted); margin-bottom: 36px; line-height: 1.65; }
.quiz-q {
  background: var(--ink2); border: 1px solid var(--border);
  border-radius: var(--r, 8px); padding: 24px 28px; margin-bottom: 14px; transition: border-color 0.2s;
}
.quiz-q.answered { border-color: var(--border2); }
.quiz-q-num { font-family: 'Space Mono', monospace; font-size: 8px; letter-spacing: 2px; text-transform: uppercase; color: var(--accent); margin-bottom: 10px; }
.quiz-q-text { font-size: 15px; color: var(--text); line-height: 1.65; margin-bottom: 18px; font-weight: 500; }
.quiz-options { display: flex; flex-direction: column; gap: 8px; }
.quiz-opt {
  display: flex; align-items: center; gap: 12px; width: 100%;
  padding: 11px 16px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; transition: all 0.15s; font-size: 13px; font-family: 'Space Grotesk', sans-serif;
  color: var(--muted); background: transparent; text-align: left; line-height: 1.5;
}
.quiz-opt:hover:not(:disabled) { border-color: rgba(59,130,246,0.5); color: var(--text); background: rgba(59,130,246,0.05); }
.quiz-opt-letter {
  width: 24px; height: 24px; min-width: 24px; border-radius: 4px; border: 1px solid var(--border);
  display: flex; align-items: center; justify-content: center;
  font-family: 'Space Mono', monospace; font-size: 9px; flex-shrink: 0; transition: all 0.15s; color: var(--muted);
}
.quiz-opt:hover:not(:disabled) .quiz-opt-letter { border-color: var(--accent); color: var(--accent); }
.quiz-opt.correct { border-color: var(--green,#10b981) !important; color: var(--green,#10b981) !important; background: rgba(16,185,129,0.06) !important; }
.quiz-opt.correct .quiz-opt-letter { background: var(--green,#10b981); border-color: var(--green,#10b981); color: #000 !important; font-weight: 700; }
.quiz-opt.wrong { border-color: var(--red,#ef4444) !important; color: var(--red,#ef4444) !important; background: rgba(239,68,68,0.06) !important; }
.quiz-opt.wrong .quiz-opt-letter { background: var(--red,#ef4444); border-color: var(--red,#ef4444); color: #fff !important; font-weight: 700; }
.quiz-opt:disabled { cursor: default; opacity: 0.9; }
.quiz-explanation { margin-top: 14px; padding: 13px 16px; border-radius: 6px; background: rgba(59,130,246,0.05); border-left: 2px solid var(--accent); font-size: 13px; color: var(--muted); line-height: 1.7; display: none; }
.quiz-explanation.show { display: block; animation: fadeSlideDown 0.2s ease; }
@keyframes fadeSlideDown { from { opacity:0; transform:translateY(-4px); } to { opacity:1; transform:translateY(0); } }
.quiz-score-wrap { margin-top: 28px; border: 1px solid var(--border); border-radius: var(--r,8px); overflow: hidden; display: none; }
.quiz-score-wrap.show { display: block; animation: fadeSlideDown 0.3s ease; }
.quiz-score-top { padding: 32px 36px; display: flex; align-items: center; gap: 28px; background: var(--ink2); border-bottom: 1px solid var(--border); }
.quiz-score-ring { width: 80px; height: 80px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; flex-direction: column; border: 2px solid var(--qs-color,var(--accent)); box-shadow: 0 0 20px color-mix(in srgb, var(--qs-color,var(--accent)) 18%, transparent); }
.quiz-score-frac { font-family: 'Syne', sans-serif; font-size: 22px; font-weight: 800; color: var(--qs-color,var(--accent)); line-height: 1; }
.quiz-score-pct { font-family: 'Space Mono', monospace; font-size: 9px; color: var(--muted); letter-spacing: 1px; }
.quiz-score-right { flex: 1; }
.quiz-score-title { font-family: 'Syne', sans-serif; font-size: 18px; font-weight: 800; color: var(--text); margin-bottom: 4px; }
.quiz-score-msg { font-size: 13px; color: var(--muted); line-height: 1.6; }
.quiz-score-bottom { padding: 16px 36px; background: var(--ink3,var(--ink2)); display: flex; justify-content: flex-end; }
.quiz-retry-btn { padding: 9px 22px; border-radius: 6px; background: transparent; border: 1px solid var(--border); color: var(--muted); font-family: 'Space Mono', monospace; font-size: 9px; letter-spacing: 1.5px; text-transform: uppercase; cursor: pointer; transition: all 0.2s; }
.quiz-retry-btn:hover { border-color: var(--accent); color: var(--accent); }

/* ── GLOSSARY TOOLTIPS ── */
dfn {
  font-style: normal;
  border-bottom: 1px dashed rgba(74,158,255,0.4);
  cursor: help;
  color: inherit;
  transition: color 0.15s, border-color 0.15s;
}
dfn:hover { color: var(--accent,#4a9eff); border-color: var(--accent,#4a9eff); }
.gtooltip {
  position: fixed; z-index: 9999;
  max-width: 300px; pointer-events: none;
  background: #0d1320; border: 1px solid rgba(74,158,255,0.3);
  border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.6);
  padding: 14px 16px; opacity: 0; transform: translateY(4px);
  transition: opacity 0.15s, transform 0.15s;
}
.gtooltip.show { opacity: 1; transform: translateY(0); }
.gtooltip-term { font-family: 'DM Mono','Space Mono',monospace; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; color: #4a9eff; margin-bottom: 6px; }
.gtooltip-def  { font-size: 12px; color: #8aaac5; line-height: 1.6; }
.gtooltip-link { display: block; margin-top: 8px; font-family: 'DM Mono','Space Mono',monospace; font-size: 9px; color: #4a9eff; letter-spacing: 1px; opacity: 0.7; }


/* ── GLOSSARY TOOLTIPS ── */
dfn {
  font-style: normal;
  border-bottom: 1px dashed rgba(74,158,255,0.4);
  cursor: help;
  color: inherit;
  transition: color 0.15s, border-color 0.15s;
}
dfn:hover { color: var(--accent,#4a9eff); border-color: var(--accent,#4a9eff); }
.gtooltip {
  position: fixed; z-index: 9999;
  max-width: 300px; pointer-events: none;
  background: #0d1320; border: 1px solid rgba(74,158,255,0.3);
  border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.6);
  padding: 14px 16px; opacity: 0; transform: translateY(4px);
  transition: opacity 0.15s, transform 0.15s;
}
.gtooltip.show { opacity: 1; transform: translateY(0); }
.gtooltip-term { font-family: 'DM Mono','Space Mono',monospace; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; color: #4a9eff; margin-bottom: 6px; }
.gtooltip-def  { font-size: 12px; color: #8aaac5; line-height: 1.6; }
.gtooltip-link { display: block; margin-top: 8px; font-family: 'DM Mono','Space Mono',monospace; font-size: 9px; color: #4a9eff; letter-spacing: 1px; opacity: 0.7; }

.chapter-nav { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

<div id="progress-bar"></div>

<nav>
  <a href="/" class="nav-brand"><span class="nav-brand-name">Vectra<em>Space</em></span></a>
  <div style="display:flex;gap:8px;"><a href="/#learn" class="nav-back">← All Chapters</a><a href="/glossary" class="nav-back">News</a><a href="/calculator" class="nav-back">Calculator</a></div>
</nav>

<div class="hero">
  <div class="hero-breadcrumb">
    <a href="/">VectraSpace</a> / <a href="/#learn">Chapters</a> / Chapter 03
  </div>
  <span class="chapter-label">Chapter 03</span>
  <h1>Orbital <span class="hero-accent">Perturbations</span></h1>
  <p class="hero-intro">
    Real orbits are never perfect ellipses. Atmospheric drag, Earth's oblate shape, solar radiation pressure,
    and gravitational pulls from the Moon and Sun continuously nudge every satellite off its Keplerian path —
    with consequences ranging from millisecond timing errors to catastrophic reentry.
  </p>
  <div class="hero-meta">
    <span><span class="hero-meta-dot"></span>30 min read</span>
    <span><span class="hero-meta-dot"></span>Intermediate · Advanced</span>
    <span><span class="hero-meta-dot"></span>Physics · Astrodynamics</span>
  </div>
</div>

<div class="page-wrap">

  <!-- TOC -->
  <aside>
    <nav class="toc">
      <div class="toc-label">Contents</div>
      <ul class="toc-list">
        <li><a href="#why-matter">Why Perturbations Matter</a></li>
        <li><a href="#j2-oblateness">J₂ Oblateness</a></li>
        <li><a href="#nodal-regression">Nodal Regression</a></li>
        <li><a href="#apsidal-precession">Apsidal Precession</a></li>
        <li><a href="#atmospheric-drag">Atmospheric Drag</a></li>
        <li><a href="#ballistic-coeff">Ballistic Coefficient</a></li>
        <li><a href="#solar-radiation">Solar Radiation Pressure</a></li>
        <li><a href="#luni-solar">Luni-Solar Gravity</a></li>
        <li><a href="#tle-accuracy">TLE Accuracy & Decay</a></li>
        <li><a href="#sgp4-model">SGP4 Perturbation Model</a></li>
        <li><a href="#ops-consequences">Operational Consequences</a></li>
      </ul>
    </nav>
  </aside>

  <!-- Content -->
  <article class="content">

    <!-- WHY PERTURBATIONS MATTER -->
    <section id="why-matter" class="content-section reveal">
      <div class="section-number">// 01</div>
      <h2>Why Perturbations Matter for SSA</h2>
      <p>
        In introductory orbital mechanics, we solve the <strong>two-body problem</strong>: a point mass orbiting another
        under pure Newtonian gravity. The solution — a perfect conic section — holds forever. Real satellites
        inhabit a messier universe.
      </p>
      <p>
        Earth is not a perfect sphere. It has mass concentrations, an atmosphere that extends hundreds of kilometers,
        and sits in a solar system full of other gravitating bodies. Each effect introduces small accelerations
        that, over hours and days, accumulate into position errors measured in kilometers.
      </p>

      <div class="pert-diagram">
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:3px;color:var(--text-3);text-transform:uppercase;margin-bottom:20px;">Four Major Perturbation Sources</div>
        <div class="pert-grid">
          <div class="pert-card">
            <div class="pert-card-icon">🌍</div>
            <div class="pert-card-title">J₂ Oblateness</div>
            <div class="pert-card-desc">Earth's equatorial bulge exerts a stronger gravitational pull on low-inclination orbits, causing the orbital plane to precess.</div>
            <div class="pert-card-mag">LEO: ~7°/day RAAN drift</div>
          </div>
          <div class="pert-card">
            <div class="pert-card-icon">🌬️</div>
            <div class="pert-card-title">Atmospheric Drag</div>
            <div class="pert-card-desc">Residual air molecules below ~1000 km exert a retarding force, bleeding orbital energy and lowering the orbit over time.</div>
            <div class="pert-card-mag">ISS: ~2 km/day altitude loss</div>
          </div>
          <div class="pert-card">
            <div class="pert-card-icon">☀️</div>
            <div class="pert-card-title">Solar Radiation Pressure</div>
            <div class="pert-card-desc">Photons carry momentum. Large, lightweight satellites (solar panels, balloon payloads) feel significant radiation pressure perturbations.</div>
            <div class="pert-card-mag">4.56 μN/m² at 1 AU</div>
          </div>
          <div class="pert-card">
            <div class="pert-card-icon">🌙</div>
            <div class="pert-card-title">Luni-Solar Gravity</div>
            <div class="pert-card-desc">Moon and Sun third-body perturbations dominate at GEO and HEO where Earth's gravity weakens relative to their influence.</div>
            <div class="pert-card-mag">GEO: ~0.75°/yr inclination growth</div>
          </div>
        </div>
      </div>

      <p>
        For Space Situational Awareness, perturbations drive two critical concerns. First, they mean that
        a TLE propagated forward in time becomes less accurate every hour — the longer the prediction horizon,
        the larger the position uncertainty. Second, some perturbations accumulate secularly, permanently
        changing orbital elements rather than oscillating around a mean value.
      </p>
    </section>

    <!-- J2 OBLATENESS -->
    <section id="j2-oblateness" class="content-section reveal">
      <div class="section-number">// 02</div>
      <h2>J₂: Earth's Equatorial Bulge</h2>
      <p>
        The dominant non-spherical gravitational term is the <strong>J₂ coefficient</strong>, which captures
        Earth's oblateness: the equatorial radius (6,378 km) exceeds the polar radius (6,357 km) by about 21 km.
        This equatorial bulge creates a gravitational potential that varies with latitude.
      </p>

      <div class="eq-block">
        <div class="eq-label">Gravitational Potential with J₂</div>
        <div class="eq-main">U = −(μ/r)·[1 − J₂·(R⊕/r)²·(3sin²φ − 1)/2]</div>
        <div class="eq-vars">
          <span class="eq-var-name">J₂</span> = 1.08263 × 10⁻³ (dimensionless oblateness coefficient)<br>
          <span class="eq-var-name">R⊕</span> = 6,378.137 km (Earth equatorial radius)<br>
          <span class="eq-var-name">φ</span> = geocentric latitude<br>
          <span class="eq-var-name">r</span> = radial distance from Earth center
        </div>
      </div>

      <p>
        The J₂ term produces three distinct effects on Keplerian orbital elements. Two are <strong>secular</strong>
        (they grow linearly with time, never reversing). One is <strong>periodic</strong> (it oscillates with the
        orbital period and averages to zero over many revolutions).
      </p>

      <div class="j2-vis">
        <div>
          <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--text-3);margin-bottom:14px;">J₂ SECULAR EFFECTS</div>
          <div class="j2-data">
            <div class="j2-row active">
              <span class="j2-key">RAAN Regression (Ω̇)</span>
              <span class="j2-val">Secular ↓</span>
            </div>
            <div class="j2-row active">
              <span class="j2-key">Apsidal Precession (ω̇)</span>
              <span class="j2-val">Secular ↑/↓</span>
            </div>
            <div class="j2-row">
              <span class="j2-key">Semi-major axis (ȧ)</span>
              <span class="j2-val">Periodic only</span>
            </div>
            <div class="j2-row">
              <span class="j2-key">Eccentricity (ė)</span>
              <span class="j2-val">Periodic only</span>
            </div>
            <div class="j2-row">
              <span class="j2-key">Inclination (i̇)</span>
              <span class="j2-val">Periodic only</span>
            </div>
          </div>
        </div>
        <div>
          <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--text-3);margin-bottom:14px;">RAAN DRIFT RATE (°/day)</div>
          <canvas id="j2-canvas" height="200"></canvas>
        </div>
      </div>
    </section>

    <!-- NODAL REGRESSION -->
    <section id="nodal-regression" class="content-section reveal">
      <div class="section-number">// 03</div>
      <h2>Nodal Regression: The Drifting Orbital Plane</h2>
      <p>
        The most practically significant J₂ effect is <strong>right ascension of the ascending node (RAAN)
        regression</strong>. The orbital plane slowly rotates around Earth's polar axis like a spinning top:
        prograde for low-inclination orbits, retrograde for high-inclination orbits.
      </p>

      <div class="eq-block">
        <div class="eq-label">RAAN Secular Drift Rate</div>
        <div class="eq-main">dΩ/dt = −(3/2)·n·J₂·(R⊕/p)²·cos(i)</div>
        <div class="eq-vars">
          <span class="eq-var-name">n</span> = mean motion (rad/s)<br>
          <span class="eq-var-name">p</span> = semi-latus rectum = a(1 − e²)<br>
          <span class="eq-var-name">i</span> = orbital inclination<br>
          <span class="eq-var-name">cos(i) = 0</span> → zero drift at i = 90° (polar orbit)<br>
          <span class="eq-var-name">cos(i) &lt; 0</span> → prograde drift at i &gt; 90° (retrograde orbits)
        </div>
      </div>

      <div class="data-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Satellite / Orbit</th>
              <th>Altitude</th>
              <th>Inclination</th>
              <th>RAAN Drift</th>
              <th>Application</th>
            </tr>
          </thead>
          <tbody>
            <tr><td class="td-white">ISS</td><td>~420 km</td><td>51.6°</td><td class="td-amber">−6.0°/day</td><td class="td-accent">Human spaceflight</td></tr>
            <tr><td class="td-white">Starlink LEO</td><td>~550 km</td><td>53°</td><td class="td-amber">−6.4°/day</td><td class="td-accent">Broadband internet</td></tr>
            <tr><td class="td-white">Sun-Sync (SSO)</td><td>~600 km</td><td>97.8°</td><td class="td-green">+0.9856°/day</td><td class="td-accent">Earth observation</td></tr>
            <tr><td class="td-white">GPS (MEO)</td><td>~20,200 km</td><td>55°</td><td class="td-amber">−0.04°/day</td><td class="td-accent">Navigation</td></tr>
            <tr><td class="td-white">GEO</td><td>35,786 km</td><td>0.1°</td><td class="td-red">−0.013°/day</td><td class="td-accent">Communications</td></tr>
          </tbody>
        </table>
      </div>

      <div class="callout success">
        <span class="callout-label">Sun-Synchronous Orbits</span>
        At inclination ≈ 97–98°, the J₂-driven RAAN drift rate of +0.9856°/day exactly matches
        Earth's orbital rate around the Sun. This keeps the orbital plane fixed relative to the Sun,
        ensuring consistent lighting for Earth observation — a critical engineering feature exploited
        by Landsat, Sentinel, and hundreds of optical imaging satellites.
      </div>
    </section>

    <!-- APSIDAL PRECESSION -->
    <section id="apsidal-precession" class="content-section reveal">
      <div class="section-number">// 04</div>
      <h2>Apsidal Precession: The Rotating Ellipse</h2>
      <p>
        J₂ also causes the argument of perigee ω to drift — the ellipse slowly rotates within its
        orbital plane. The rate depends strongly on inclination, and at two <strong>critical inclinations</strong>
        the drift stops entirely.
      </p>

      <div class="eq-block">
        <div class="eq-label">Apsidal Precession Rate</div>
        <div class="eq-main">dω/dt = (3/4)·n·J₂·(R⊕/p)²·(5cos²i − 1)</div>
        <div class="eq-vars">
          <span class="eq-var-name">5cos²i − 1 = 0</span> when cos(i) = 1/√5<br>
          <span class="eq-var-name">i = 63.43°</span> or <span class="eq-var-name">i = 116.57°</span> → zero apsidal drift<br>
          These are the <strong>Molniya critical inclinations</strong>
        </div>
      </div>

      <div class="callout warning">
        <span class="callout-label">Molniya & Tundra Orbits</span>
        Russian engineers discovered that highly elliptical orbits (HEO) at exactly 63.43° inclination
        keep their apogee fixed over the northern hemisphere indefinitely — J₂ apsidal precession is
        exactly zero. Molniya communication satellites exploit this to provide 6–8 hours of high-elevation
        coverage over Russia per orbit, where geostationary geometry is poor.
      </div>
    </section>

    <!-- ATMOSPHERIC DRAG -->
    <section id="atmospheric-drag" class="content-section reveal">
      <div class="section-number">// 05</div>
      <h2>Atmospheric Drag: The Orbit Killer</h2>
      <p>
        Below approximately 1,000 km, residual atmospheric molecules collide with satellites, removing
        kinetic energy. Counterintuitively, this energy loss causes the satellite to <strong>speed up</strong>:
        losing energy causes it to drop to a lower orbit with higher velocity per vis-viva. The orbit
        spirals inward, shrinking both apogee and perigee.
      </p>

      <div class="eq-block">
        <div class="eq-label">Drag Acceleration</div>
        <div class="eq-main">a_drag = −(1/2)·(C_D · A / m)·ρ·v²</div>
        <div class="eq-vars">
          <span class="eq-var-name">C_D</span> = drag coefficient (~2.2 for satellites in free molecular flow)<br>
          <span class="eq-var-name">A/m</span> = area-to-mass ratio (m²/kg) — critical parameter<br>
          <span class="eq-var-name">ρ(h)</span> = atmospheric density at altitude h (kg/m³)<br>
          <span class="eq-var-name">v</span> = orbital velocity relative to atmosphere (~7.7 km/s at 400 km)
        </div>
      </div>

      <div class="drag-chart-wrap">
        <div class="drag-chart-title">Atmospheric Density by Altitude (Exponential Scale)</div>
        <div class="drag-bars">
          <div class="drag-bar-row">
            <div class="drag-bar-label">200 km</div>
            <div class="drag-bar-track"><div class="drag-bar-fill" style="width:100%;background:#ef4444;"></div></div>
            <div class="drag-bar-val">2.5 × 10⁻¹⁰</div>
          </div>
          <div class="drag-bar-row">
            <div class="drag-bar-label">400 km (ISS)</div>
            <div class="drag-bar-track"><div class="drag-bar-fill" style="width:58%;background:#f59e0b;"></div></div>
            <div class="drag-bar-val">3.7 × 10⁻¹²</div>
          </div>
          <div class="drag-bar-row">
            <div class="drag-bar-label">550 km (SL)</div>
            <div class="drag-bar-track"><div class="drag-bar-fill" style="width:35%;background:#3b82f6;"></div></div>
            <div class="drag-bar-val">7.9 × 10⁻¹³</div>
          </div>
          <div class="drag-bar-row">
            <div class="drag-bar-label">800 km</div>
            <div class="drag-bar-track"><div class="drag-bar-fill" style="width:18%;background:#3b82f6;"></div></div>
            <div class="drag-bar-val">4.5 × 10⁻¹⁴</div>
          </div>
          <div class="drag-bar-row">
            <div class="drag-bar-label">1,000 km</div>
            <div class="drag-bar-track"><div class="drag-bar-fill" style="width:8%;background:#10b981;"></div></div>
            <div class="drag-bar-val">3.6 × 10⁻¹⁵</div>
          </div>
        </div>
        <div style="margin-top:12px;font-family:var(--mono);font-size:9px;color:var(--text-3);">
          * kg/m³ — density varies by factor ~2–4× with solar activity (F10.7 solar flux index)
        </div>
      </div>

      <h3>Solar Cycle Effects</h3>
      <p>
        Atmospheric density is not constant. During solar maximum, extreme ultraviolet radiation heats
        and expands the upper atmosphere, increasing density at a given altitude by up to <strong>4×</strong>
        compared to solar minimum. This variability is parameterized by the <strong>F10.7 solar flux index</strong>
        (measured in solar flux units, SFU) and the geomagnetic Kp index.
      </p>

      <div class="callout danger">
        <span class="callout-label">Solar Activity Impact on Starlink</span>
        In February 2022, a geomagnetic storm following a solar event increased atmospheric density
        at 210 km by 20–50%. Forty-nine of the 49 newly-launched Starlink satellites, still in their
        low parking orbit, experienced drag levels 50% higher than predicted and re-entered
        within days. This event highlighted how space weather directly determines satellite lifetimes.
      </div>
    </section>

    <!-- BALLISTIC COEFFICIENT -->
    <section id="ballistic-coeff" class="content-section reveal">
      <div class="section-number">// 06</div>
      <h2>Ballistic Coefficient &amp; the BSTAR Term</h2>
      <p>
        The <strong>ballistic coefficient</strong> β = m/(C_D · A) (kg/m²) summarizes how strongly a satellite
        resists atmospheric drag. A high ballistic coefficient — dense, compact objects — experiences
        less drag per unit mass than large, lightweight ones.
      </p>

      <div class="eq-block">
        <div class="eq-label">Orbital Decay Rate (Circular Orbit Approximation)</div>
        <div class="eq-main">da/dt ≈ −(C_D · A / m)·ρ·v·a = −ρ·v·a/β</div>
        <div class="eq-vars">
          <span class="eq-var-name">β = m/(C_D·A)</span> = ballistic coefficient (kg/m²)<br>
          Higher β → slower orbital decay<br>
          <span class="eq-var-name">ISS β</span> ≈ 120 kg/m² | <span class="eq-var-name">CubeSat β</span> ≈ 10–30 kg/m²
        </div>
      </div>

      <p>
        In the TLE format, atmospheric drag is encoded in the <strong>BSTAR drag term</strong> (units of 1/Earth radii).
        SGP4 uses this value to propagate the secular decay of mean motion over time. When BSTAR is unavailable
        or unreliable, VectraSpace falls back to a standard assumed value based on orbital regime and estimated
        satellite type.
      </p>

      <div class="callout info">
        <span class="callout-label">VectraSpace Implementation</span>
        VectraSpace uses the BSTAR value from each satellite's TLE when computing 12-hour propagation
        windows. For debris objects — which often have poorly-determined BSTAR values — position
        uncertainty grows fastest in the along-track direction. The covariance matrix assigned to debris
        objects uses σ_along = 500 m vs. σ_along = 100 m for well-tracked active satellites.
      </div>
    </section>

    <!-- SOLAR RADIATION PRESSURE -->
    <section id="solar-radiation" class="content-section reveal">
      <div class="section-number">// 07</div>
      <h2>Solar Radiation Pressure</h2>
      <p>
        Photons carry momentum: p = E/c. When sunlight strikes a satellite surface, radiation pressure
        imparts a small but continuous force. At Earth's distance of 1 AU, the solar radiation flux is
        approximately 1,361 W/m², producing a radiation pressure of <strong>4.56 μN/m²</strong>.
      </p>

      <div class="eq-block">
        <div class="eq-label">Solar Radiation Acceleration</div>
        <div class="eq-main">a_SRP = −ν · (P_⊙ / c) · (A/m) · C_r · (r_⊙/|r_⊙|)</div>
        <div class="eq-vars">
          <span class="eq-var-name">ν</span> = shadow function (0 in eclipse, 1 in sunlight)<br>
          <span class="eq-var-name">P_⊙</span> = solar radiation flux ≈ 1361 W/m² at 1 AU<br>
          <span class="eq-var-name">C_r</span> = radiation pressure coefficient (1 for absorption, 2 for perfect reflection)<br>
          <span class="eq-var-name">A/m</span> = area-to-mass ratio (m²/kg) — same parameter as drag!
        </div>
      </div>

      <p>
        SRP is negligible for dense LEO satellites (few mm/s² per year) but becomes significant for
        objects with high area-to-mass ratios: <strong>solar sail technology demonstrators</strong>,
        balloon payloads, and large solar-panel-dominated GEO satellites. At GEO where drag is absent,
        SRP is the dominant non-gravitational perturbation, responsible for the characteristic
        "resonant eccentricity pumping" that slowly increases GEO eccentricity.
      </p>

      <div class="callout warning">
        <span class="callout-label">Debris SRP Complication</span>
        Tumbling debris objects present a varying cross-section to the Sun with unknown orientation.
        The effective A/m ratio changes as the object rotates. This makes long-term SRP modeling
        highly uncertain for defunct satellites and rocket bodies, contributing to the rapid
        growth of position uncertainty in their TLE propagations.
      </div>
    </section>

    <!-- LUNI-SOLAR -->
    <section id="luni-solar" class="content-section reveal">
      <div class="section-number">// 08</div>
      <h2>Luni-Solar Third-Body Perturbations</h2>
      <p>
        The Moon and Sun exert gravitational forces on every Earth-orbiting satellite. The <strong>differential
        force</strong> across the satellite's orbit — the deviation from perfect parallel attraction — is
        the perturbation. For a satellite at radius r orbiting Earth, the third-body acceleration varies
        as (m_3 / r_3³) · r, where r_3 is the distance to the perturbing body.
      </p>

      <div class="eq-block">
        <div class="eq-label">Third-Body Perturbation (Simplified)</div>
        <div class="eq-main">a_3b = μ₃ · [(r_3 − r)/|r_3 − r|³ − r_3/|r_3|³]</div>
        <div class="eq-vars">
          <span class="eq-var-name">μ_Moon</span> = 4,902.8 km³/s² (Moon's gravitational parameter)<br>
          <span class="eq-var-name">μ_Sun</span> = 1.327 × 10¹¹ km³/s² (Sun's gravitational parameter)<br>
          For GEO (~42,000 km radius): luni-solar effects produce ~0.75°/year inclination oscillation
        </div>
      </div>

      <div class="data-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Orbit Regime</th>
              <th>Dominant Perturbation</th>
              <th>Effect on TLE Age</th>
              <th>Typical Position Error at 24h</th>
            </tr>
          </thead>
          <tbody>
            <tr><td class="td-white">LEO &lt; 500 km</td><td class="td-red">Atmospheric Drag</td><td class="td-amber">Hours–days</td><td class="td-red">&gt;10 km</td></tr>
            <tr><td class="td-white">LEO 500–800 km</td><td class="td-amber">J₂ + Drag</td><td class="td-amber">1–3 days</td><td class="td-amber">1–5 km</td></tr>
            <tr><td class="td-white">MEO (GPS ~20k km)</td><td class="td-accent">J₂ + Luni-Solar</td><td>Days–weeks</td><td class="td-green">&lt;1 km</td></tr>
            <tr><td class="td-white">GEO (36k km)</td><td class="td-accent">Luni-Solar + SRP</td><td>Weeks</td><td class="td-green">100–500 m</td></tr>
          </tbody>
        </table>
      </div>

      <p>
        The luni-solar perturbations at GEO are strong enough to require active station-keeping to maintain
        geostationary position. Without north-south station-keeping burns, GEO satellites develop inclinations
        of up to 15° over a 26-year period. "Graveyard" GEO orbits for retired satellites slowly develop
        inclined, eccentric paths that create conjunction risk with operational satellites.
      </p>
    </section>

    <!-- TLE ACCURACY -->
    <section id="tle-accuracy" class="content-section reveal">
      <div class="section-number">// 09</div>
      <h2>TLE Accuracy &amp; Prediction Horizon</h2>
      <p>
        A Two-Line Element set is a snapshot of mean orbital elements at a specific epoch. As time passes,
        perturbations accumulate and the TLE prediction diverges from the true position. The rate of
        divergence defines the <strong>effective TLE age</strong> beyond which the element set is unreliable
        for conjunction screening.
      </p>

      <div class="accuracy-chart">
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--text-3);margin-bottom:16px;">TLE POSITION ERROR GROWTH (REPRESENTATIVE)</div>
        <canvas id="accuracy-canvas"></canvas>
        <div style="margin-top:10px;font-family:var(--mono);font-size:9px;color:var(--text-3);">
          * LEO object at 400 km · Standard deviation grows roughly as σ ≈ σ₀ + k·t (along-track dominates)
        </div>
      </div>

      <p>
        Error growth is fastest in the <strong>along-track direction</strong> because perturbations that
        change orbital period — drag, J₂ — create systematic timing errors that accumulate indefinitely.
        Cross-track and radial errors grow more slowly and are dominated by J₂ periodic effects.
        This asymmetry is reflected in the elongated covariance ellipsoids used in Pc calculation.
      </p>

      <div class="callout info">
        <span class="callout-label">VectraSpace TLE Management</span>
        VectraSpace caches TLEs for up to 6 hours (configurable). Beyond this, a fresh fetch
        is triggered before each scan. For conjunction prediction requiring high accuracy,
        operator-uploaded custom element sets can override the cached TLEs for specific objects
        of interest. Fresh element sets reduce screening false-alarm rates significantly.
      </div>
    </section>

    <!-- SGP4 MODEL -->
    <section id="sgp4-model" class="content-section reveal">
      <div class="section-number">// 10</div>
      <h2>SGP4: The Perturbation Propagator</h2>
      <p>
        The <strong>Simplified General Perturbations 4 (SGP4)</strong> model, developed at NORAD in the 1970s
        and refined since, is the standard analytic propagator for TLE-based orbit determination.
        It captures the dominant perturbation effects through closed-form algebraic equations rather
        than numerical integration, enabling fast propagation of thousands of objects.
      </p>

      <h3>Physical Effects in SGP4</h3>
      <p>SGP4 models the following perturbations analytically:</p>

      <div class="data-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Effect</th>
              <th>Modeling Approach</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            <tr><td class="td-white">J₂, J₃, J₄ geopotential</td><td>Secular + short-period terms</td><td class="td-green">Good</td></tr>
            <tr><td class="td-white">Atmospheric drag (BSTAR)</td><td>Power-law density model, secular ṅ</td><td class="td-amber">Moderate (solar-cycle dependent)</td></tr>
            <tr><td class="td-white">SRP</td><td>Not modeled in basic SGP4</td><td class="td-red">Absent (use SDP4 for deep space)</td></tr>
            <tr><td class="td-white">Luni-solar (SDP4)</td><td>Simplified lunisolar terms for T > 225 min</td><td class="td-amber">Approximate</td></tr>
            <tr><td class="td-white">Higher harmonics (J₅+)</td><td>Not modeled</td><td class="td-red">Absent</td></tr>
          </tbody>
        </table>
      </div>

      <p>
        SGP4 achieves position accuracies of roughly <strong>1–3 km at epoch</strong>, degrading to
        tens of kilometers over days for LEO objects. For precise applications — rendezvous, precise
        reentry prediction, high-accuracy conjunction assessment — numerical integrators (like
        <strong>RK4/RK89</strong> with a full force model including up to J₇₀ harmonics and atmospheric
        density tables) are required.
      </p>

      <div class="callout info">
        <span class="callout-label">VectraSpace uses Skyfield's SGP4</span>
        VectraSpace propagates all satellites using the Skyfield Python library's SGP4/SDP4
        implementation, which conforms to the 2006 Vallado/Crawford/Hujsak revision of the
        model. The SDP4 extension is automatically applied for satellites with orbital periods
        greater than 225 minutes (semi-synchronous and higher orbits). All propagation results
        are expressed in the ECI (Earth-Centered Inertial) J2000 frame.
      </div>
    </section>

    <!-- OPERATIONAL CONSEQUENCES -->
    <section id="ops-consequences" class="content-section reveal">
      <div class="section-number">// 11</div>
      <h2>Operational Consequences for SSA</h2>
      <p>
        Understanding perturbations is not merely academic for Space Situational Awareness — it directly
        determines how far ahead conjunction screens are meaningful, how wide safety margins must be,
        and which objects pose the highest long-term risk.
      </p>

      <h3>The 5σ Screening Challenge</h3>
      <p>
        Conjunction screening typically evaluates pairs whose miss distance falls within 5σ of the combined
        position uncertainty ellipsoid. As TLE age increases, σ grows, meaning the 5σ envelope balloons
        until nearly every object pair triggers a candidate event — swamping operators with false alarms.
        This drives the requirement for frequent TLE updates (daily or better) for active conjunction
        assessment.
      </p>

      <h3>Debris Population Growth</h3>
      <p>
        Perturbations also shape long-term debris population dynamics. Atmospheric drag naturally removes
        debris below ~600 km within years to decades — a self-cleaning mechanism. Above 800 km, the
        clearing timescale exceeds centuries. J₂ RAAN regression spreads debris clouds around orbital
        shells, while luni-solar perturbations slowly perturb debris orbits at higher altitudes, sometimes
        pumping eccentricity enough to force objects through crowded lower shells.
      </p>

      <div class="callout danger">
        <span class="callout-label">The Reentry Timing Problem</span>
        Predicting exactly when and where a decaying satellite will reenter is extremely difficult.
        The primary uncertainty is atmospheric density, which varies with solar activity on timescales
        from minutes to years. Even 24 hours before reentry, the predicted landing ellipse spans
        thousands of kilometers along-track. Only within the final orbit can reentry location be
        predicted to within ~500 km — and most objects survive only minutes of atmospheric passage.
      </div>


<!-- CHAPTER 3 QUIZ -->
<div class="quiz-section" id="ch3-quiz-wrap" data-storage-key='vs_ch3_done'>
  <div class="quiz-eyebrow">⬡ Knowledge Check</div>
  <div class="quiz-heading">Chapter 03 Quiz</div>
  <div class="quiz-subtitle">Test your understanding of J₂, atmospheric drag, and solar radiation pressure.</div>
  <div id="ch3-quiz"></div>
</div>
<script>

function initQuiz(containerId, questions) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const letters = ['A','B','C','D','E'];

  function render() {
    container.innerHTML = questions.map((q, qi) => `
      <div class="quiz-q" id="qq-${containerId}-${qi}">
        <div class="quiz-q-num">Question ${qi+1} of ${questions.length}</div>
        <div class="quiz-q-text">${q.q}</div>
        <div class="quiz-options">
          ${q.opts.map((o, oi) => `
            <button class="quiz-opt" onclick="quizAnswer('${containerId}',${qi},${oi},${q.ans},${questions.length})"
              id="qo-${containerId}-${qi}-${oi}">
              <span class="quiz-opt-letter">${letters[oi]}</span>${o}
            </button>
          `).join('')}
        </div>
        <div class="quiz-explanation" id="qe-${containerId}-${qi}">${q.explain}</div>
      </div>
    `).join('') + `<div class="quiz-score-wrap" id="qs-${containerId}"></div>`;
  }

  window.quizAnswer = function(cid, qi, chosen, correct, total) {
    const optEls = document.querySelectorAll(`[id^="qo-${cid}-${qi}-"]`);
    optEls.forEach(b => b.disabled = true);
    const qEl = document.getElementById(`qq-${cid}-${qi}`);
    if (qEl.dataset.counted) return;
    qEl.classList.add('answered');
    optEls[correct].classList.add('correct');
    if (chosen !== correct) optEls[chosen].classList.add('wrong');
    const expEl = document.getElementById(`qe-${cid}-${qi}`);
    if (expEl) expEl.classList.add('show');
    qEl.dataset.counted = '1';
    if (chosen === correct) qEl.dataset.correct = '1';
    const allQ = document.querySelectorAll(`#${cid} [id^="qq-${cid}-"]`);
    if ([...allQ].every(q => q.dataset.counted)) {
      const sc = [...allQ].filter(q => q.dataset.correct).length;
      const tot = allQ.length;
      const pct = Math.round(sc / tot * 100);
      const color = pct >= 80 ? 'var(--green,#34d399)' : pct >= 50 ? 'var(--amber,#f59e0b)' : 'var(--red,#ef4444)';
      const grade = pct === 100 ? 'Perfect Score' : pct >= 80 ? 'Excellent' : pct >= 50 ? 'Good Effort' : 'Keep Studying';
      const msg = pct === 100 ? "You've fully mastered this chapter's core concepts and equations." :
                  pct >= 80  ? "Solid understanding — you're ready to move to the next chapter." :
                  pct >= 50  ? "Decent foundation. Review the highlighted explanations and retry." :
                               "Revisit the chapter material before moving on — the explanations above show where to focus.";
      const scoreEl = document.getElementById(`qs-${cid}`);
      scoreEl.style.setProperty('--qs-color', color);
      scoreEl.innerHTML = `
        <div class="quiz-score-top">
          <div class="quiz-score-ring" style="--qs-color:${color}">
            <div class="quiz-score-frac">${sc}/${tot}</div>
            <div class="quiz-score-pct">${pct}%</div>
          </div>
          <div class="quiz-score-right">
            <div class="quiz-score-title">${grade}</div>
            <div class="quiz-score-msg">${msg}</div>
          </div>
        </div>
        <div class="quiz-score-bottom">
          <button class="quiz-retry-btn" onclick="document.getElementById('${cid}-wrap').dispatchEvent(new Event('retry'))">↩ Retry Quiz</button>
        </div>
      `;
      scoreEl.classList.add('show');
      setTimeout(() => scoreEl.scrollIntoView({ behavior:'smooth', block:'nearest' }), 100);
    }
  };

  render();
  const wrap = document.getElementById(`${containerId}-wrap`);
  if (wrap) wrap.addEventListener('retry', () => { render(); wrap.scrollIntoView({ behavior:'smooth', block:'start' }); });
}

initQuiz('ch3-quiz', [
  {
    q: "The J₂ perturbation causes nodal regression — a drift of the Right Ascension of the Ascending Node (RAAN). For a prograde LEO orbit at ~500 km, which direction does RAAN drift?",
    opts: ["Eastward (increasing RAAN)", "Westward (decreasing RAAN)", "It oscillates with no net drift", "RAAN does not change — only argument of perigee drifts"],
    ans: 1,
    explain: "For prograde orbits (inclination < 90°), J₂ causes the orbit plane to precess westward, so RAAN decreases over time at a rate of roughly −6 to −7 degrees per day at LEO altitudes. Sun-synchronous orbits at ~97° are designed to precess at exactly +0.9856°/day to match Earth's solar angle."
  },
  {
    q: "Atmospheric drag causes a satellite's semi-major axis to decrease. What paradoxically happens to its orbital speed?",
    opts: ["Speed decreases as the satellite loses energy", "Speed increases because orbital velocity scales as √(μ/r) — lower orbit = faster speed", "Speed stays constant — only altitude changes", "Speed oscillates as drag varies with solar activity"],
    ans: 1,
    explain: "This is the 'drag paradox': drag removes orbital energy, causing the satellite to fall to a lower orbit. But lower circular orbits have *higher* velocity (v = √(μ/r)). The satellite ends up moving faster, but at a lower altitude with shorter period."
  },
  {
    q: "Solar Radiation Pressure (SRP) has the greatest effect on which type of satellite?",
    opts: ["High-mass, low-area satellites (like spent rocket bodies)", "Low-mass, high-area satellites (like thin-film solar sails)", "Satellites in geosynchronous orbit regardless of mass", "Satellites with metallic surfaces that reflect sunlight"],
    ans: 1,
    explain: "SRP force = P · Cr · (A/m) where A/m is the area-to-mass ratio. Satellites with high A/m (thin, large area relative to mass — like solar sails or flat-panel satellites) experience the largest SRP acceleration."
  },
  {
    q: "What is a Sun-synchronous orbit, and what perturbation makes it possible?",
    opts: ["An orbit that keeps the satellite in permanent sunlight, enabled by high altitude", "An orbit whose RAAN precesses at the same rate as Earth orbits the Sun (~0.99°/day), enabled by <dfn data-term="J₂">J₂ oblateness</dfn>", "An orbit that tracks the Sun by using onboard thrusters", "A geostationary orbit over the equator that appears fixed to the Sun"],
    ans: 1,
    explain: "At a specific inclination (~97–98° for LEO), the J₂ nodal regression rate exactly matches Earth's orbital rate around the Sun. This means the orbit plane maintains a fixed angle to the Sun — allowing consistent lighting conditions for every pass over a target area."
  },
  {
    q: "The International Space Station requires regular 'reboosts' — what perturbation makes these necessary?",
    opts: ["Solar radiation pressure pushes it outward", "Atmospheric drag at ~420 km altitude removes orbital energy, causing ~2 km/day altitude loss", "J₂ oblateness degrades the orbit over time", "Lunar gravity gradually lowers the orbit"],
    ans: 1,
    explain: "At 420 km altitude, atmospheric density is still non-negligible, especially during solar maximum when the upper atmosphere expands. The ISS loses roughly 1–2 km of altitude per day and must be reboosted periodically using visiting spacecraft or its own thrusters."
  }
]);

// ── GLOSSARY TOOLTIPS ─────────────────────────────────────────
(function() {
  const DEFS = {
    'SGP4':'Simplified General Perturbations model 4 — the standard analytical propagator for Earth satellites using TLE data. Models drag (B*), J₂ oblateness, and secular/periodic terms.',
    'TLE':'Two-Line Element Set — standardised format encoding six Keplerian elements at a given epoch. Accuracy decays ~1 km/day for LEO objects.',
    'vis-viva':'v² = μ(2/r − 1/a). Relates orbital speed to current radius, semi-major axis, and gravitational parameter. Underlies all delta-v calculations.',
    'RAAN':'Right Ascension of Ascending Node — angle from vernal equinox to ascending node. Drifts westward in prograde LEO orbits due to J₂ at ~6–7°/day.',
    'J₂':'Dominant Earth oblateness coefficient: J₂ = 1.08263×10⁻³. Causes nodal regression (RAAN drift) and apsidal precession for all satellites.',
    'Kessler':'A self-sustaining cascade of collisions above a critical debris density, proposed by Donald Kessler (NASA, 1978). Potentially irreversible above 800 km.',
    'Pc':'Probability of Collision — the probability two objects physically contact. Computed by integrating the combined uncertainty PDF over the collision cross-section. Action threshold: 1×10⁻⁴.',
    'TCA':'Time of Closest Approach — the moment of minimum separation between two conjunction objects. Reference epoch for all CDM calculations.',
    'CDM':'Conjunction Data Message — CCSDS standard format for sharing conjunction results between space surveillance providers and operators.',
    'NASA SBM':'NASA Standard Breakup Model — predicts fragment count N(Lc) = 6·M^0.75·Lc^−1.6 from satellite collisions or explosions.',
    'covariance':'A 3×3 or 6×6 symmetric matrix encoding position uncertainty and correlations in RTN. Diagonal elements are variances (σ_R², σ_T², σ_N²).',
    'delta-v':'Change in velocity (km/s) required for an orbital maneuver — the fundamental currency of spaceflight, limited by onboard propellant.',
  };
  const tip = document.createElement('div');
  tip.className = 'gtooltip';
  tip.innerHTML = '<div class="gtooltip-term"></div><div class="gtooltip-def"></div><a class="gtooltip-link" href="/glossary">Space News →</a>';
  document.body.appendChild(tip);
  let hideTimer;
  document.querySelectorAll('dfn[data-term]').forEach(el => {
    const key = el.dataset.term;
    const def = DEFS[key] || '';
    if (!def) return;
    el.addEventListener('mouseenter', () => {
      clearTimeout(hideTimer);
      tip.querySelector('.gtooltip-term').textContent = key;
      tip.querySelector('.gtooltip-def').textContent = def;
      const rect = el.getBoundingClientRect();
      let top = rect.bottom + 8, left = rect.left;
      if (left + 300 > window.innerWidth - 16) left = window.innerWidth - 316;
      if (top + 140 > window.innerHeight - 16) top = rect.top - 150;
      tip.style.top = top + 'px'; tip.style.left = left + 'px';
      tip.classList.add('show');
    });
    el.addEventListener('mouseleave', () => { hideTimer = setTimeout(() => tip.classList.remove('show'), 200); });
  });
})();

</script>

      <!-- Chapter nav -->
      <div class="chapter-nav">
        <a href="/education/collision-prediction" class="chapter-nav-card">
          <div class="cnc-dir">← Previous</div>
          <div class="cnc-title">Chapter 02</div>
          <div class="cnc-sub">Collision Prediction &amp; Pc Methods</div>
        </a>
        <a href="/education/debris-modeling" class="chapter-nav-card next">
          <div class="cnc-dir">Next →</div>
          <div class="cnc-title">Chapter 04</div>
          <div class="cnc-sub">Debris Modeling &amp; Kessler Cascade</div>
        </a>
      </div>
    </section>

  </article>
</div>

<script>
// Progress bar
const bar = document.getElementById('progress-bar');
window.addEventListener('scroll', () => {
  const pct = (window.scrollY / (document.body.scrollHeight - window.innerHeight)) * 100;
  bar.style.width = pct + '%';
});

// Scroll reveal
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('visible'); });
}, { threshold: 0.1 });
document.querySelectorAll('.reveal').forEach(el => observer.observe(el));

// TOC active highlight
const sections = document.querySelectorAll('.content-section');
const tocLinks = document.querySelectorAll('.toc-list a');
const tocObserver = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      const id = e.target.id;
      tocLinks.forEach(a => {
        a.classList.toggle('active', a.getAttribute('href') === '#' + id);
      });
    }
  });
}, { threshold: 0.3 });
sections.forEach(s => tocObserver.observe(s));

// J2 canvas — RAAN drift by inclination
const j2Canvas = document.getElementById('j2-canvas');
if (j2Canvas) {
  const ctx = j2Canvas.getContext('2d');
  const W = j2Canvas.parentElement.offsetWidth || 300;
  const H = 200;
  j2Canvas.width = W; j2Canvas.height = H;
  const pad = { top: 16, right: 16, bottom: 32, left: 48 };
  const incs = [];
  const rates = [];
  for (let i = 0; i <= 180; i += 2) {
    incs.push(i);
    const n = 0.001078; // ~LEO mean motion rad/s
    const J2 = 1.08263e-3;
    const Re = 6378.137;
    const p = 6928 * (1 - 0.001**2); // ~500km LEO
    const rate = -(3/2) * n * J2 * (Re/p)**2 * Math.cos(i * Math.PI/180);
    rates.push(rate * (180/Math.PI) * 86400); // deg/day
  }
  const minR = Math.min(...rates); const maxR = Math.max(...rates);
  const scaleX = (inc) => pad.left + (inc / 180) * (W - pad.left - pad.right);
  const scaleY = (r) => pad.top + ((maxR - r) / (maxR - minR)) * (H - pad.top - pad.bottom);

  ctx.strokeStyle = '#1a2e42'; ctx.lineWidth = 1;
  for (let g = -7; g <= 7; g += 3.5) {
    ctx.beginPath();
    ctx.moveTo(pad.left, scaleY(g)); ctx.lineTo(W - pad.right, scaleY(g));
    ctx.stroke();
    ctx.fillStyle = '#4a6a85'; ctx.font = '9px Space Mono';
    ctx.fillText(g.toFixed(1), 2, scaleY(g) + 3);
  }
  // Zero line
  ctx.strokeStyle = '#243d54'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad.left, scaleY(0)); ctx.lineTo(W-pad.right, scaleY(0)); ctx.stroke();

  // 90° vertical
  ctx.strokeStyle = '#10b981'; ctx.lineWidth = 1; ctx.setLineDash([3,3]);
  ctx.beginPath(); ctx.moveTo(scaleX(90), pad.top); ctx.lineTo(scaleX(90), H-pad.bottom); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#10b981'; ctx.font = '8px Space Mono';
  ctx.fillText('90°', scaleX(90)+3, pad.top+10);

  // Curve
  ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 2;
  ctx.beginPath();
  incs.forEach((inc, i) => {
    i === 0 ? ctx.moveTo(scaleX(inc), scaleY(rates[i])) : ctx.lineTo(scaleX(inc), scaleY(rates[i]));
  });
  ctx.stroke();

  // X axis labels
  ctx.fillStyle = '#4a6a85'; ctx.font = '8px Space Mono';
  [0,30,60,90,120,150,180].forEach(inc => {
    ctx.fillText(inc+'°', scaleX(inc)-6, H-6);
  });
}

// TLE Accuracy canvas
const accCanvas = document.getElementById('accuracy-canvas');
if (accCanvas) {
  const ctx = accCanvas.getContext('2d');
  const W = accCanvas.parentElement.offsetWidth || 600;
  const H = 180;
  accCanvas.width = W; accCanvas.height = H;
  const pad = { top: 12, right: 16, bottom: 32, left: 56 };

  const days = Array.from({length: 21}, (_, i) => i);
  const along = days.map(d => 0.5 + 1.2 * d);      // km
  const cross  = days.map(d => 0.1 + 0.08 * d);
  const radial = days.map(d => 0.05 + 0.04 * d);
  const maxV = Math.max(...along);
  const scaleX = d => pad.left + (d / 20) * (W - pad.left - pad.right);
  const scaleY = v => pad.top + ((maxV - v) / maxV) * (H - pad.top - pad.bottom);

  ctx.strokeStyle = '#1a2e42'; ctx.lineWidth = 1;
  [0, 6, 12, 18, 24].forEach(km => {
    if (km > maxV) return;
    ctx.beginPath(); ctx.moveTo(pad.left, scaleY(km)); ctx.lineTo(W-pad.right, scaleY(km)); ctx.stroke();
    ctx.fillStyle = '#4a6a85'; ctx.font = '9px Space Mono';
    ctx.fillText(km+'km', 2, scaleY(km)+3);
  });

  const drawLine = (data, color, label) => {
    ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.beginPath();
    days.forEach((d, i) => {
      i === 0 ? ctx.moveTo(scaleX(d), scaleY(data[i])) : ctx.lineTo(scaleX(d), scaleY(data[i]));
    });
    ctx.stroke();
  };

  drawLine(along, '#ef4444', 'Along-track');
  drawLine(cross, '#3b82f6', 'Cross-track');
  drawLine(radial, '#10b981', 'Radial');

  // Legend
  [[along,'#ef4444','Along-track'],[cross,'#3b82f6','Cross-track'],[radial,'#10b981','Radial']].forEach(([,c,l],i) => {
    ctx.fillStyle = c; ctx.fillRect(pad.left + i*120, H-10, 14, 3);
    ctx.fillStyle = '#7a9bb5'; ctx.font = '9px Space Mono';
    ctx.fillText(l, pad.left + i*120 + 18, H-6);
  });

  // X axis
  ctx.fillStyle = '#4a6a85'; ctx.font = '9px Space Mono';
  [0,5,10,15,20].forEach(d => ctx.fillText(d+'d', scaleX(d)-8, H-4));
}
</script>
</body>
</html>

"""

_EDU_DEBRIS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Debris Modeling &amp; Kessler Cascade — VectraSpace Deep Dive</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;700;800&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {
  --ink:        #070c14;
  --ink-2:      #0d1520;
  --ink-3:      #111d2b;
  --border:     #1a2e42;
  --border-2:   #243d54;
  --accent:     #3b82f6;
  --accent-glow:rgba(59,130,246,0.18);
  --amber:      #f59e0b;
  --amber-dim:  rgba(245,158,11,0.12);
  --green:      #10b981;
  --green-dim:  rgba(16,185,129,0.10);
  --red:        #ef4444;
  --red-dim:    rgba(239,68,68,0.10);
  --text:       #c9ddef;
  --text-2:     #9dbbd4;
  --text-3:     #6d92ad;
  --mono:       'Space Mono', monospace;
  --math:       'STIX Two Math','Latin Modern Math',Georgia,serif;
  --sans:       'Space Grotesk', sans-serif;
  --display:    'Syne', sans-serif;
  --toc-w:      230px;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body { background: var(--ink); color: var(--text); font-family: var(--sans); line-height: 1.7; overflow-x: hidden; }
#progress-bar { position:fixed;top:0;left:0;height:2px;width:0%;background:linear-gradient(90deg,var(--red),var(--amber));z-index:200;transition:width 0.1s linear; }
nav { position:fixed;top:0;left:0;right:0;z-index:100;height:56px;padding:0 32px;display:flex;align-items:center;justify-content:space-between;background:rgba(7,12,20,0.92);border-bottom:1px solid var(--border);backdrop-filter:blur(12px); }
.nav-brand { text-decoration:none; display:flex; align-items:center; }
.nav-brand-name { font-family:'Instrument Serif',Georgia,serif;font-size:17px;font-style:italic;letter-spacing:-0.2px;color:#fff; }
.nav-brand-name em { color:var(--accent);font-style:normal; }
.nav-back { font-family:var(--mono);font-size:10px;letter-spacing:2px;color:var(--text-3);text-decoration:none;text-transform:uppercase;transition:color 0.2s; }
.nav-back:hover { color:var(--accent); }
.hero { padding:120px 48px 64px;max-width:900px;margin:0 auto; }
.hero-breadcrumb { font-family:var(--mono);font-size:9px;letter-spacing:3px;color:var(--text-3);text-transform:uppercase;margin-bottom:16px; }
.hero-breadcrumb a { color:var(--text-3);text-decoration:none; }
.hero-breadcrumb a:hover { color:var(--accent); }
.chapter-label { display:inline-block;font-family:var(--mono);font-size:9px;letter-spacing:3px;color:var(--red);text-transform:uppercase;background:var(--red-dim);border:1px solid rgba(239,68,68,0.25);padding:4px 10px;border-radius:2px;margin-bottom:20px; }
.hero h1 { font-family:var(--display);font-size:clamp(36px,5vw,58px);font-weight:800;line-height:1.1;color:#fff;margin-bottom:16px; }
.hero-accent { color:var(--red); }
.hero-intro { font-size:17px;font-weight:300;color:var(--text-2);line-height:1.8;max-width:680px;margin-bottom:32px; }
.hero-meta { display:flex;gap:24px;flex-wrap:wrap;font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--text-3);text-transform:uppercase; }
.hero-meta span { display:flex;align-items:center;gap:6px; }
.hero-meta-dot { width:4px;height:4px;background:var(--red);border-radius:50%; }
.page-wrap { max-width:1140px;margin:0 auto;padding:48px 48px 120px;display:grid;grid-template-columns:var(--toc-w) 1fr;gap:64px;align-items:start; }
.toc { position:sticky;top:72px;background:var(--ink-2);border:1px solid var(--border);border-radius:8px;padding:20px;max-height:calc(100vh - 88px);overflow-y:auto; }
.toc::-webkit-scrollbar { width:3px; }
.toc::-webkit-scrollbar-thumb { background:var(--border); }
.toc-label { font-family:var(--mono);font-size:8px;letter-spacing:3px;color:var(--text-3);text-transform:uppercase;margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid var(--border); }
.toc-list { list-style:none;display:flex;flex-direction:column;gap:2px; }
.toc-list a { display:block;font-size:12px;color:var(--text-3);text-decoration:none;padding:5px 8px;border-radius:4px;transition:all 0.2s;border-left:2px solid transparent; }
.toc-list a:hover { color:var(--text);background:var(--ink-3); }
.toc-list a.active { color:var(--accent);background:var(--accent-glow);border-left-color:var(--accent); }
.content { min-width:0; }
.content-section { margin-bottom:72px;scroll-margin-top:80px; }
.section-number { font-family:var(--mono);font-size:9px;letter-spacing:3px;color:var(--red);text-transform:uppercase;margin-bottom:12px; }
.content h2 { font-family:var(--display);font-size:clamp(22px,3vw,30px);font-weight:700;color:#fff;margin-bottom:20px;line-height:1.2; }
.content h3 { font-family:var(--sans);font-size:16px;font-weight:600;color:var(--text);margin:28px 0 12px; }
.content p { margin-bottom:16px;color:var(--text-2);font-size:15px; }
.content strong { color:var(--text);font-weight:600; }
.eq-block { background:var(--ink-2);border:1px solid var(--border);border-left:3px solid var(--red);border-radius:6px;padding:20px 24px;margin:24px 0;font-size:13px;color:var(--text);overflow-x:auto; }
.eq-block .eq-label { font-size:8px;letter-spacing:3px;color:var(--text-3);text-transform:uppercase;margin-bottom:10px; }
.eq-block .eq-main { font-family:var(--math,'STIX Two Math',Georgia,serif);font-size:17px;color:#fff;margin-bottom:8px;font-style:italic; }
.eq-block .eq-vars { font-size:12px;color:var(--text-2);line-height:1.9; }
.eq-block .eq-var-name { color:var(--amber); }
.callout { border-radius:6px;padding:16px 20px;margin:24px 0;border-left:3px solid;font-size:14px; }
.callout.info { background:rgba(59,130,246,0.07);border-color:var(--accent);color:var(--text); }
.callout.warning { background:var(--amber-dim);border-color:var(--amber);color:var(--text); }
.callout.danger { background:var(--red-dim);border-color:var(--red);color:var(--text); }
.callout.success { background:var(--green-dim);border-color:var(--green);color:var(--text); }
.callout-label { font-family:var(--mono);font-size:8px;letter-spacing:3px;text-transform:uppercase;margin-bottom:6px;display:block; }
.callout.info .callout-label { color:var(--accent); }
.callout.warning .callout-label { color:var(--amber); }
.callout.danger .callout-label { color:var(--red); }
.callout.success .callout-label { color:var(--green); }
.data-table-wrap { overflow-x:auto;margin:24px 0; }
table { width:100%;border-collapse:collapse;font-size:13px;font-family:var(--mono); }
thead th { background:var(--ink-3);color:var(--text-3);font-size:9px;letter-spacing:2px;text-transform:uppercase;padding:10px 14px;text-align:left;border-bottom:1px solid var(--border); }
tbody td { padding:10px 14px;border-bottom:1px solid rgba(26,46,66,0.5);color:var(--text-2); }
tbody tr:hover td { background:var(--ink-2); }
.td-accent{color:var(--accent);} .td-amber{color:var(--amber);} .td-green{color:var(--green);} .td-red{color:var(--red);} .td-white{color:#fff;font-weight:600;}

/* CASCADE DIAGRAM */
.cascade-diagram { margin:24px 0; }
.cascade-steps { display:flex;flex-direction:column;gap:0; }
.cascade-step { display:grid;grid-template-columns:60px 1fr;gap:0;position:relative; }
.cascade-step::before { content:'';position:absolute;left:29px;top:60px;bottom:-4px;width:2px;background:linear-gradient(180deg,var(--red),var(--amber));z-index:0; }
.cascade-step:last-child::before { display:none; }
.cascade-num { display:flex;align-items:flex-start;padding-top:16px;justify-content:center;position:relative;z-index:1; }
.cascade-num-inner { width:40px;height:40px;border-radius:50%;background:var(--red-dim);border:2px solid var(--red);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:11px;font-weight:700;color:var(--red); }
.cascade-body { padding:14px 16px 32px; }
.cascade-title { font-family:var(--sans);font-size:15px;font-weight:600;color:#fff;margin-bottom:6px; }
.cascade-text { font-size:13px;color:var(--text-2);line-height:1.7; }
.cascade-stat { display:inline-block;margin-top:8px;font-family:var(--mono);font-size:10px;color:var(--amber);background:var(--amber-dim);padding:3px 8px;border-radius:2px; }

/* POPULATION CHART */
.pop-chart-wrap { background:var(--ink-2);border:1px solid var(--border);border-radius:8px;padding:24px;margin:24px 0; }
.pop-chart-title { font-family:var(--mono);font-size:9px;letter-spacing:3px;color:var(--text-3);text-transform:uppercase;margin-bottom:16px; }
.pop-chart-wrap canvas { width:100%; }

/* FRAGMENT SIZE DIST */
.frag-chart-wrap { background:var(--ink-2);border:1px solid var(--border);border-radius:8px;padding:24px;margin:24px 0; }

/* HISTORICAL EVENTS TIMELINE */
.timeline { margin:24px 0;display:flex;flex-direction:column;gap:0; }
.timeline-item { display:grid;grid-template-columns:100px 1fr;gap:20px;padding:20px 0;border-bottom:1px solid var(--border); }
.timeline-item:last-child { border-bottom:none; }
.timeline-year { font-family:var(--mono);font-size:22px;font-weight:700;color:var(--red);line-height:1; padding-top:2px; }
.timeline-content-title { font-family:var(--sans);font-size:14px;font-weight:600;color:#fff;margin-bottom:4px; }
.timeline-content-body { font-size:13px;color:var(--text-2);line-height:1.6; }
.timeline-content-badge { display:inline-block;margin-top:6px;font-family:var(--mono);font-size:9px;letter-spacing:1px;padding:2px 8px;border-radius:2px; }
.badge-red { background:var(--red-dim);color:var(--red);border:1px solid rgba(239,68,68,0.25); }
.badge-amber { background:var(--amber-dim);color:var(--amber);border:1px solid rgba(245,158,11,0.25); }
.badge-accent { background:rgba(59,130,246,0.1);color:var(--accent);border:1px solid rgba(59,130,246,0.25); }

/* ADR CARDS */
.adr-grid { display:grid;grid-template-columns:repeat(2,1fr);gap:16px;margin:24px 0; }
.adr-card { background:var(--ink-2);border:1px solid var(--border);border-radius:6px;padding:20px;transition:border-color 0.2s; }
.adr-card:hover { border-color:var(--green); }
.adr-icon { font-size:22px;margin-bottom:10px; }
.adr-title { font-family:var(--mono);font-size:10px;letter-spacing:2px;color:var(--green);text-transform:uppercase;margin-bottom:6px; }
.adr-desc { font-size:13px;color:var(--text-2);line-height:1.6; }
.adr-status { margin-top:10px;font-family:var(--mono);font-size:9px;padding:3px 8px;border-radius:2px;display:inline-block; }

/* CHAPTER NAV */
.chapter-nav { display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:80px;padding-top:40px;border-top:1px solid var(--border); }
.chapter-nav-card { background:var(--ink-2);border:1px solid var(--border);border-radius:8px;padding:20px 24px;text-decoration:none;transition:all 0.2s;display:block; }
.chapter-nav-card:hover { border-color:var(--accent);background:var(--ink-3); }
.cnc-dir { font-family:var(--mono);font-size:8px;letter-spacing:3px;color:var(--text-3);text-transform:uppercase;margin-bottom:6px; }
.cnc-title { font-family:var(--display);font-size:16px;font-weight:700;color:#fff; }
.cnc-sub { font-size:12px;color:var(--text-3);margin-top:4px; }
.chapter-nav-card.next { text-align:right; }
.reveal { opacity:0;transform:translateY(16px);transition:opacity 0.6s ease,transform 0.6s ease; }
.reveal.visible { opacity:1;transform:none; }
@media (max-width:900px) {
  .page-wrap { grid-template-columns:1fr; }
  .toc { display:none; }
  .hero { padding:100px 24px 48px; }
  .page-wrap { padding:32px 24px 80px; }
  .adr-grid { grid-template-columns:1fr; }
  
/* ─── QUIZ ─────────────────────────────────────────────────── */
.quiz-section {
  margin: 64px 0 0; padding: 48px 0 0;
  border-top: 1px solid var(--border);
}
.quiz-eyebrow {
  font-family: 'Space Mono', monospace; font-size: 9px;
  letter-spacing: 3px; text-transform: uppercase;
  color: var(--accent); margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
}
.quiz-eyebrow::before { content: ''; width: 20px; height: 1px; background: var(--accent); display: inline-block; }
.quiz-heading {
  font-family: 'Syne', sans-serif; font-size: 26px; font-weight: 800;
  color: #fff; letter-spacing: -0.5px; margin-bottom: 6px;
}
.quiz-subtitle { font-size: 14px; color: var(--muted); margin-bottom: 36px; line-height: 1.65; }
.quiz-q {
  background: var(--ink2); border: 1px solid var(--border);
  border-radius: var(--r, 8px); padding: 24px 28px; margin-bottom: 14px; transition: border-color 0.2s;
}
.quiz-q.answered { border-color: var(--border2); }
.quiz-q-num { font-family: 'Space Mono', monospace; font-size: 8px; letter-spacing: 2px; text-transform: uppercase; color: var(--accent); margin-bottom: 10px; }
.quiz-q-text { font-size: 15px; color: var(--text); line-height: 1.65; margin-bottom: 18px; font-weight: 500; }
.quiz-options { display: flex; flex-direction: column; gap: 8px; }
.quiz-opt {
  display: flex; align-items: center; gap: 12px; width: 100%;
  padding: 11px 16px; border-radius: 6px; border: 1px solid var(--border);
  cursor: pointer; transition: all 0.15s; font-size: 13px; font-family: 'Space Grotesk', sans-serif;
  color: var(--muted); background: transparent; text-align: left; line-height: 1.5;
}
.quiz-opt:hover:not(:disabled) { border-color: rgba(59,130,246,0.5); color: var(--text); background: rgba(59,130,246,0.05); }
.quiz-opt-letter {
  width: 24px; height: 24px; min-width: 24px; border-radius: 4px; border: 1px solid var(--border);
  display: flex; align-items: center; justify-content: center;
  font-family: 'Space Mono', monospace; font-size: 9px; flex-shrink: 0; transition: all 0.15s; color: var(--muted);
}
.quiz-opt:hover:not(:disabled) .quiz-opt-letter { border-color: var(--accent); color: var(--accent); }
.quiz-opt.correct { border-color: var(--green,#10b981) !important; color: var(--green,#10b981) !important; background: rgba(16,185,129,0.06) !important; }
.quiz-opt.correct .quiz-opt-letter { background: var(--green,#10b981); border-color: var(--green,#10b981); color: #000 !important; font-weight: 700; }
.quiz-opt.wrong { border-color: var(--red,#ef4444) !important; color: var(--red,#ef4444) !important; background: rgba(239,68,68,0.06) !important; }
.quiz-opt.wrong .quiz-opt-letter { background: var(--red,#ef4444); border-color: var(--red,#ef4444); color: #fff !important; font-weight: 700; }
.quiz-opt:disabled { cursor: default; opacity: 0.9; }
.quiz-explanation { margin-top: 14px; padding: 13px 16px; border-radius: 6px; background: rgba(59,130,246,0.05); border-left: 2px solid var(--accent); font-size: 13px; color: var(--muted); line-height: 1.7; display: none; }
.quiz-explanation.show { display: block; animation: fadeSlideDown 0.2s ease; }
@keyframes fadeSlideDown { from { opacity:0; transform:translateY(-4px); } to { opacity:1; transform:translateY(0); } }
.quiz-score-wrap { margin-top: 28px; border: 1px solid var(--border); border-radius: var(--r,8px); overflow: hidden; display: none; }
.quiz-score-wrap.show { display: block; animation: fadeSlideDown 0.3s ease; }
.quiz-score-top { padding: 32px 36px; display: flex; align-items: center; gap: 28px; background: var(--ink2); border-bottom: 1px solid var(--border); }
.quiz-score-ring { width: 80px; height: 80px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; flex-direction: column; border: 2px solid var(--qs-color,var(--accent)); box-shadow: 0 0 20px color-mix(in srgb, var(--qs-color,var(--accent)) 18%, transparent); }
.quiz-score-frac { font-family: 'Syne', sans-serif; font-size: 22px; font-weight: 800; color: var(--qs-color,var(--accent)); line-height: 1; }
.quiz-score-pct { font-family: 'Space Mono', monospace; font-size: 9px; color: var(--muted); letter-spacing: 1px; }
.quiz-score-right { flex: 1; }
.quiz-score-title { font-family: 'Syne', sans-serif; font-size: 18px; font-weight: 800; color: var(--text); margin-bottom: 4px; }
.quiz-score-msg { font-size: 13px; color: var(--muted); line-height: 1.6; }
.quiz-score-bottom { padding: 16px 36px; background: var(--ink3,var(--ink2)); display: flex; justify-content: flex-end; }
.quiz-retry-btn { padding: 9px 22px; border-radius: 6px; background: transparent; border: 1px solid var(--border); color: var(--muted); font-family: 'Space Mono', monospace; font-size: 9px; letter-spacing: 1.5px; text-transform: uppercase; cursor: pointer; transition: all 0.2s; }
.quiz-retry-btn:hover { border-color: var(--accent); color: var(--accent); }
.chapter-nav { grid-template-columns:1fr; }
}
</style>
</head>
<body>

<div id="progress-bar"></div>

<nav>
  <a href="/" class="nav-brand"><span class="nav-brand-name">Vectra<em>Space</em></span></a>
  <div style="display:flex;gap:8px;"><a href="/#learn" class="nav-back">← All Chapters</a><a href="/glossary" class="nav-back">News</a><a href="/calculator" class="nav-back">Calculator</a></div>
</nav>

<div class="hero">
  <div class="hero-breadcrumb">
    <a href="/">VectraSpace</a> / <a href="/#learn">Chapters</a> / Chapter 04
  </div>
  <span class="chapter-label">Chapter 04</span>
  <h1>Debris Modeling &amp; <span class="hero-accent">Kessler Cascade</span></h1>
  <p class="hero-intro">
    Every collision in orbit creates thousands of new fragments, each capable of causing further collisions.
    The runaway chain reaction known as <dfn data-term="Kessler">Kessler Syndrome</dfn> could render entire orbital shells
    permanently inaccessible. Understanding its physics — and how to model, predict, and prevent it — is
    the defining challenge of 21st century spaceflight.
  </p>
  <div class="hero-meta">
    <span><span class="hero-meta-dot"></span>35 min read</span>
    <span><span class="hero-meta-dot"></span>Intermediate · Policy</span>
    <span><span class="hero-meta-dot"></span>Orbital Mechanics · Risk</span>
  </div>
</div>

<div class="page-wrap">
  <aside>
    <nav class="toc">
      <div class="toc-label">Contents</div>
      <ul class="toc-list">
        <li><a href="#kessler-defined">Kessler Syndrome Defined</a></li>
        <li><a href="#cascade-physics">Cascade Physics</a></li>
        <li><a href="#population-history">Population History</a></li>
        <li><a href="#critical-density">Critical Density</a></li>
        <li><a href="#sbm-model">NASA Breakup Model (SBM)</a></li>
        <li><a href="#fragment-distribution">Fragment Distributions</a></li>
        <li><a href="#historical-events">Historical Events</a></li>
        <li><a href="#collision-probability">Collision Rate Models</a></li>
        <li><a href="#adr-remediation">Active Debris Removal</a></li>
        <li><a href="#mitigation-guidelines">Mitigation Guidelines</a></li>
        <li><a href="#vectraspace-sim">VectraSpace Simulation</a></li>
      </ul>
    </nav>
  </aside>

  <article class="content">

    <!-- KESSLER DEFINED -->
    <section id="kessler-defined" class="content-section reveal">
      <div class="section-number">// 01</div>
      <h2>Kessler Syndrome: The Runaway Cascade</h2>
      <p>
        In 1978, NASA scientist Donald Kessler and Burton Cour-Palais published a paper describing a
        concerning possibility: if the density of objects in low Earth orbit exceeded a critical threshold,
        collisions would generate debris faster than atmospheric drag could remove it. Each collision
        creates new objects that cause more collisions — a <strong>self-sustaining cascade</strong>
        with no natural end state.
      </p>
      <p>
        The Kessler paper did not predict imminent danger. It projected that this critical density
        might be reached in the early 21st century if debris generation continued unchecked.
        With over 27,000 tracked objects and an estimated 130 million fragments larger than 1 mm,
        many researchers believe we may already be in the early stages of a Kessler cascade
        in certain orbital bands.
      </p>

      <div class="cascade-diagram">
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:3px;color:var(--text-3);text-transform:uppercase;margin-bottom:20px;">The Cascade Mechanism</div>
        <div class="cascade-steps">
          <div class="cascade-step">
            <div class="cascade-num"><div class="cascade-num-inner">01</div></div>
            <div class="cascade-body">
              <div class="cascade-title">Initial Collision or Fragmentation Event</div>
              <div class="cascade-text">Two objects in the same orbital shell collide at hypervelocity (typically 10–15 km/s relative velocity). Even a 10 cm fragment carries kinetic energy equivalent to a hand grenade — enough to destroy a satellite.</div>
              <span class="cascade-stat">Impact energy: ~500 kJ for 10 cm fragment at 10 km/s</span>
            </div>
          </div>
          <div class="cascade-step">
            <div class="cascade-num"><div class="cascade-num-inner">02</div></div>
            <div class="cascade-body">
              <div class="cascade-title">Debris Cloud Generation</div>
              <div class="cascade-text">The collision produces thousands to millions of fragments ranging from mm-scale dust to multi-meter panels. These fragments distribute themselves across a band of inclinations and altitudes centered on the collision point, based on their ejection velocity.</div>
              <span class="cascade-stat">A 1-tonne collision: ~thousands of &gt;1 cm fragments</span>
            </div>
          </div>
          <div class="cascade-step">
            <div class="cascade-num"><div class="cascade-num-inner">03</div></div>
            <div class="cascade-body">
              <div class="cascade-title">Density Increase in the Shell</div>
              <div class="cascade-text">The new fragments spread around their orbital altitude band through J₂ RAAN regression and apsidal precession. Within weeks to months, they are distributed uniformly through the orbital shell, increasing the local object density.</div>
              <span class="cascade-stat">~weeks to full shell distribution via RAAN spreading</span>
            </div>
          </div>
          <div class="cascade-step">
            <div class="cascade-num"><div class="cascade-num-inner">04</div></div>
            <div class="cascade-body">
              <div class="cascade-title">Elevated Collision Rate</div>
              <div class="cascade-text">Higher object density means higher probability of subsequent collisions. If the density exceeds the critical value, new collisions produce more fragments than atmospheric drag removes. The collision rate accelerates, not decelerates — a runaway cascade.</div>
              <span class="cascade-stat">Critical: generation rate &gt; removal rate by drag</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- CASCADE PHYSICS -->
    <section id="cascade-physics" class="content-section reveal">
      <div class="section-number">// 02</div>
      <h2>Cascade Physics: Kinetic Theory in Orbit</h2>
      <p>
        The mathematical treatment of orbital debris population dynamics borrows from
        <strong>kinetic gas theory</strong>. Objects in a given orbital shell can be modeled as
        particles in a box, with their collision rate determined by their number density and
        cross-section-weighted relative velocity — a quantity called the <strong>spatial density</strong>.
      </p>

      <div class="eq-block">
        <div class="eq-label">Collision Rate per Object</div>
        <div class="eq-main">dN_c/dt = n_d · A_c · v_rel</div>
        <div class="eq-vars">
          <span class="eq-var-name">n_d</span> = number density of debris (objects/km³)<br>
          <span class="eq-var-name">A_c</span> = combined cross-sectional area (m²)<br>
          <span class="eq-var-name">v_rel</span> = mean relative collision velocity (~10–15 km/s at 400–800 km)<br>
          <span class="eq-var-name">n_d · A_c · v_rel</span> has units of collisions/year per object
        </div>
      </div>

      <p>
        The <strong>critical density</strong> is reached when the debris fragments produced by a
        single collision (which then add to n_d) eventually cause more collisions than the original
        collision itself replaced. This depends on both the number density and the mass
        distribution of the debris population.
      </p>

      <div class="eq-block">
        <div class="eq-label">Population Evolution (Simplified Two-Species Model)</div>
        <div class="eq-main">dN/dt = S + G(N,D) − L(N) − R(N)</div>
        <div class="eq-vars">
          <span class="eq-var-name">N</span> = number of lethal (≥10 cm) objects in shell<br>
          <span class="eq-var-name">S</span> = launch rate (new satellites added)<br>
          <span class="eq-var-name">G(N,D)</span> = collision-generated fragments from N objects and D debris<br>
          <span class="eq-var-name">L(N)</span> = orbital decay (atmospheric drag removal rate)<br>
          <span class="eq-var-name">R(N)</span> = active remediation removal rate
        </div>
      </div>
    </section>

    <!-- POPULATION HISTORY -->
    <section id="population-history" class="content-section reveal">
      <div class="section-number">// 03</div>
      <h2>Population History: How We Got Here</h2>

      <div class="pop-chart-wrap">
        <div class="pop-chart-title">Tracked Object Count in Earth Orbit (1957–2024)</div>
        <canvas id="pop-canvas" height="200"></canvas>
        <div style="margin-top:10px;font-family:var(--mono);font-size:9px;color:var(--text-3);">
          * USSPACECOM catalog data — objects ≥10 cm in LEO, ≥1 m in GEO · Events marked: ↑ Chinese ASAT test 2007, ↑ Iridium-Cosmos 2009
        </div>
      </div>

      <div class="data-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Object Category</th>
              <th>Tracked (&gt;10 cm)</th>
              <th>Estimated Total (&gt;1 cm)</th>
              <th>Estimated Total (&gt;1 mm)</th>
            </tr>
          </thead>
          <tbody>
            <tr><td class="td-white">Active Satellites</td><td class="td-green">~9,000</td><td class="td-green">~9,000</td><td class="td-green">~9,000</td></tr>
            <tr><td class="td-white">Inactive Satellites</td><td class="td-amber">~5,000</td><td class="td-amber">~5,000</td><td class="td-amber">~5,000</td></tr>
            <tr><td class="td-white">Rocket Bodies</td><td class="td-amber">~2,000</td><td class="td-amber">~2,000</td><td class="td-amber">~2,000</td></tr>
            <tr><td class="td-white">Fragmentation Debris</td><td class="td-red">~14,000</td><td class="td-red">~500,000</td><td class="td-red">~130,000,000</td></tr>
            <tr><td class="td-white">Total</td><td class="td-accent">~30,000</td><td class="td-accent">~516,000</td><td class="td-accent">&gt;130,000,000</td></tr>
          </tbody>
        </table>
      </div>

      <p>
        The vast majority of the hazard comes from debris objects too small to track but large enough
        to be lethal. A 1 cm aluminum sphere at 7.7 km/s carries the kinetic energy of a bowling ball
        dropped from 7 km. A 1 mm particle can damage solar panels and optics. <strong>None of these
        objects appear in the TLE catalog</strong> — their existence is inferred from statistical models
        and in-situ measurements on returned hardware (Space Shuttle windows, Hubble solar panels).
      </p>
    </section>

    <!-- CRITICAL DENSITY -->
    <section id="critical-density" class="content-section reveal">
      <div class="section-number">// 04</div>
      <h2>Critical Density: The Tipping Point</h2>
      <p>
        The critical debris density is not a single number — it depends on altitude (through drag removal
        timescales), the mass distribution of debris, and the assumed breakup model. The classic
        Kessler–Cour-Palais formulation gives a critical spatial density where the collision rate
        equals the drag removal rate.
      </p>

      <div class="eq-block">
        <div class="eq-label">Critical Spatial Density (Kessler 1978)</div>
        <div class="eq-main">n_c = 1 / (A_c · v_rel · τ_d · φ_f)</div>
        <div class="eq-vars">
          <span class="eq-var-name">n_c</span> = critical number density (objects/km³)<br>
          <span class="eq-var-name">τ_d</span> = atmospheric drag decay timescale (years)<br>
          <span class="eq-var-name">φ_f</span> = average number of new lethal fragments per collision<br>
          At 800 km: τ_d ≈ 100 years → <strong>n_c is already exceeded in some shells</strong>
        </div>
      </div>

      <div class="callout danger">
        <span class="callout-label">We May Already Be Past the Threshold</span>
        Multiple independent modeling studies (Liou &amp; Johnson 2006, ESA DRAMA, NASA LEGEND) find that
        even if all launches stopped today, the debris population in the 750–900 km shell would continue
        to grow due to collisions among existing objects. The shell is self-sustaining. This does not
        mean access is immediately impossible — but it does mean active remediation is required to
        prevent long-term collapse of this orbital band.
      </div>
    </section>

    <!-- NASA SBM -->
    <section id="sbm-model" class="content-section reveal">
      <div class="section-number">// 05</div>
      <h2><dfn data-term="NASA SBM">NASA Standard Breakup Model</dfn> (SBM)</h2>
      <p>
        When a collision or explosion occurs in orbit, how many fragments does it create, and what are
        their sizes and velocities? The answer comes from the <strong>NASA Standard Breakup Model</strong>
        (SBM), developed from analysis of on-orbit fragmentations, ground hypervelocity impact tests,
        and recovered debris.
      </p>

      <h3>Fragment Number Distribution</h3>
      <p>
        The SBM predicts that the number of fragments larger than characteristic length L_c follows
        a power-law distribution — a hallmark of fracture mechanics:
      </p>

      <div class="eq-block">
        <div class="eq-label">Fragment Count Distribution (SBM)</div>
        <div class="eq-main">N(L_c) = 6 · d^(0.5) · L_c^(−1.6)</div>
        <div class="eq-vars">
          <span class="eq-var-name">N(L_c)</span> = number of fragments larger than L_c<br>
          <span class="eq-var-name">d</span> = effective diameter of the larger body (m)<br>
          <span class="eq-var-name">L_c</span> = characteristic length (m) — roughly max dimension<br>
          A 1 m × 1 m collision: ~6,000 fragments &gt;10 cm, ~600,000 fragments &gt;1 cm
        </div>
      </div>

      <h3>Fragment Velocity Distribution</h3>
      <p>
        Fragment velocities relative to the parent orbit follow a <strong>lognormal distribution</strong>
        whose parameters depend on the area-to-mass ratio (a surrogate for fragment size and shape):
      </p>

      <div class="eq-block">
        <div class="eq-label">Fragment Velocity Distribution (SBM)</div>
        <div class="eq-main">log₁₀(v) ~ N(μ_v, σ_v)</div>
        <div class="eq-vars">
          <span class="eq-var-name">μ_v</span> = 0.2 · χ + 1.85 (for collision fragments)<br>
          <span class="eq-var-name">σ_v</span> = 0.4 (approximately)<br>
          <span class="eq-var-name">χ</span> = log₁₀(A/m) — log of area-to-mass ratio<br>
          Small high-A/m fragments receive the highest ejection velocities (~hundreds m/s)
        </div>
      </div>
    </section>

    <!-- FRAGMENT DISTRIBUTIONS -->
    <section id="fragment-distribution" class="content-section reveal">
      <div class="section-number">// 06</div>
      <h2>Fragment Size &amp; Velocity Distributions</h2>

      <div class="frag-chart-wrap">
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:3px;color:var(--text-3);text-transform:uppercase;margin-bottom:16px;">Fragment Count vs. Size (SBM Power Law) — Hypothetical 1-Tonne Collision</div>
        <canvas id="frag-canvas" height="200"></canvas>
        <div style="margin-top:10px;font-family:var(--mono);font-size:9px;color:var(--text-3);">
          * Log-log scale · Dashed lines: tracking threshold (10 cm) and lethal threshold (1 cm)
        </div>
      </div>

      <p>
        The power-law distribution means that <strong>vastly more small fragments are created than large ones</strong>:
        roughly 1,000× more 1 cm fragments than 10 cm fragments. This is the core of the problem —
        surveillance systems can track objects down to about 10 cm in LEO, but the most numerous
        hazardous fragments fall below the detection threshold.
      </p>

      <h3>Velocity Spreading and Shell Distribution</h3>
      <p>
        Fragments ejected with velocities of 10–100 m/s from a circular orbit will shift their
        semi-major axis by Δa ≈ ±(2/n) · Δv, where n is mean motion. For LEO at 400 km,
        a 100 m/s ejection velocity shifts altitude by approximately ±340 km, spreading the
        debris cloud through a thick altitude band rather than concentrating it at the parent orbit.
        High-velocity fragments (200+ m/s) may be ejected to orbits that cross multiple
        occupied altitude bands.
      </p>

      <div class="callout warning">
        <span class="callout-label">VectraSpace Debris Simulation</span>
        The VectraSpace debris simulation module implements a simplified version of the SBM lognormal
        fragment velocity distribution. When a fragmentation event is triggered, N_debris synthetic
        fragment objects are generated with ejection velocities sampled from the lognormal model,
        with characteristic length L_c randomly drawn between 1 cm and 50 cm. Their trajectories
        are then propagated using the same SGP4 engine as primary catalog objects, and the resulting
        debris cloud is screened for conjunctions with the existing catalog.
      </div>
    </section>

    <!-- HISTORICAL EVENTS -->
    <section id="historical-events" class="content-section reveal">
      <div class="section-number">// 07</div>
      <h2>Historical Fragmentation Events</h2>
      <p>
        The current debris environment has been shaped by a small number of high-mass fragmentation
        events that together account for a disproportionate share of the hazard.
      </p>

      <div class="timeline">
        <div class="timeline-item">
          <div class="timeline-year">1965–</div>
          <div>
            <div class="timeline-content-title">Propellant Tank Explosions</div>
            <div class="timeline-content-body">Residual propellant in rocket upper stages causes pressure-driven explosions years after launch. Over 200 fragmentation events attributed to this source. The US Delta and Soviet SL-12 families were particularly prolific. Modern mitigation: passivation — venting all remaining propellants and pressurized gases before abandonment.</div>
            <span class="timeline-content-badge badge-amber">Ongoing</span>
          </div>
        </div>
        <div class="timeline-item">
          <div class="timeline-year">2007</div>
          <div>
            <div class="timeline-content-title">Chinese ASAT Test — Fengyun-1C</div>
            <div class="timeline-content-body">China destroyed its own 758 kg weather satellite Fengyun-1C using a direct-ascent kinetic kill vehicle, in a deliberate anti-satellite weapons test. The 865 km altitude generated the largest single debris-generating event in history, producing over 3,000 tracked fragments and an estimated 35,000+ objects ≥1 cm — nearly all above the ISS orbit with decay times of centuries to decades.</div>
            <span class="timeline-content-badge badge-red">~3,500+ tracked fragments</span>
          </div>
        </div>
        <div class="timeline-item">
          <div class="timeline-year">2009</div>
          <div>
            <div class="timeline-content-title">Iridium 33 / Cosmos 2251 Collision</div>
            <div class="timeline-content-body">The first accidental collision between two intact cataloged satellites. The active 560 kg Iridium-33 communications satellite collided with the defunct 950 kg Cosmos-2251 at 789 km altitude, 11.7 km/s relative velocity. Both were completely destroyed, generating ~2,000 tracked fragments and an estimated 100,000+ hazardous objects. The event demonstrated that uncontrolled satellites in crowded orbits are a systemic risk.</div>
            <span class="timeline-content-badge badge-red">First-ever intact satellite collision</span>
          </div>
        </div>
        <div class="timeline-item">
          <div class="timeline-year">2021</div>
          <div>
            <div class="timeline-content-title">Russian ASAT Test — Kosmos 1408</div>
            <div class="timeline-content-body">Russia destroyed its defunct 1,750 kg reconnaissance satellite Kosmos-1408 at 480 km altitude using a direct-ascent weapon, generating over 1,500 tracked fragments. The ISS crew sheltered in their return vehicles as the debris cloud passed through the station's orbital altitude. The event drew international condemnation and prompted US, Japan, and UK unilateral bans on destructive ASAT testing.</div>
            <span class="timeline-content-badge badge-red">ISS crew emergency</span>
            <span class="timeline-content-badge badge-amber" style="margin-left:6px;">International condemnation</span>
          </div>
        </div>
        <div class="timeline-item">
          <div class="timeline-year">2022–</div>
          <div>
            <div class="timeline-content-title">Mega-Constellation Launch Wave</div>
            <div class="timeline-content-body">SpaceX Starlink, OneWeb, and Amazon Kuiper are deploying tens of thousands of satellites into LEO. While each individual satellite poses lower risk (designed for deorbit), the cumulative conjunction rate with existing objects is unprecedented. Close approach frequency between Starlink and other operators has increased dramatically, raising concerns about both collision risk and operator coordination.</div>
            <span class="timeline-content-badge badge-accent">Active monitoring required</span>
          </div>
        </div>
      </div>
    </section>

    <!-- COLLISION PROBABILITY -->
    <section id="collision-probability" class="content-section reveal">
      <div class="section-number">// 08</div>
      <h2>Collision Rate Models: From Fragment to Fleet</h2>
      <p>
        Beyond individual Pc calculations for specific conjunctions, long-term debris environment
        modeling requires predicting the <strong>fleet-wide collision rate</strong> — how many
        collisions per year are expected in a given orbital shell?
      </p>

      <div class="eq-block">
        <div class="eq-label">Flux-Based Collision Rate (Kessler Model)</div>
        <div class="eq-main">F_c = (1/2) · n² · ⟨σ_c · v_rel⟩ · V_shell</div>
        <div class="eq-vars">
          <span class="eq-var-name">n</span> = object spatial density (objects/km³)<br>
          <span class="eq-var-name">⟨σ_c · v_rel⟩</span> = cross-section × velocity, averaged over distribution<br>
          <span class="eq-var-name">V_shell</span> = volume of the orbital shell (km³)<br>
          The n² dependence means doubling the population → quadrupling the collision rate
        </div>
      </div>

      <p>
        The <strong>n² scaling</strong> is the key driver of Kessler Syndrome: a doubling of the
        debris population quadruples the collision rate and therefore quadruples the fragment generation
        rate from those collisions. Below the critical density, the drag removal rate grows only
        linearly with n, so the population remains stable. Above it, generation outpaces removal
        and growth accelerates.
      </p>

      <div class="data-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Altitude Band</th>
              <th>Object Density (obj/km³)</th>
              <th>Drag Decay Time</th>
              <th>Cascade Status</th>
            </tr>
          </thead>
          <tbody>
            <tr><td class="td-white">350–500 km</td><td>~0.0008</td><td>1–5 years</td><td class="td-green">Self-clearing</td></tr>
            <tr><td class="td-white">500–700 km</td><td>~0.003</td><td>10–50 years</td><td class="td-amber">Marginal</td></tr>
            <tr><td class="td-white">750–900 km</td><td>~0.006</td><td>50–200 years</td><td class="td-red">Likely unstable</td></tr>
            <tr><td class="td-white">900–1,200 km</td><td>~0.002</td><td>100–500 years</td><td class="td-amber">Borderline</td></tr>
            <tr><td class="td-white">&gt;1,200 km</td><td>&lt;0.0005</td><td>&gt;500 years</td><td class="td-accent">Low density but permanent</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ADR -->
    <section id="adr-remediation" class="content-section reveal">
      <div class="section-number">// 09</div>
      <h2>Active Debris Removal: The Engineering Challenge</h2>
      <p>
        Passive mitigation (deorbiting satellites within 25 years) slows the growth rate but cannot
        reverse an ongoing cascade. Only <strong>Active Debris Removal (ADR)</strong> — physically
        capturing and deorbiting existing dead objects — can reduce population density in critical
        shells.
      </p>

      <p>
        Studies by ESA, NASA, and JAXA consistently find that removing approximately <strong>5–10
        large intact objects per year</strong> (>1 tonne rocket bodies in 750–900 km altitude) would
        stabilize the debris population. Each large object removed prevents dozens to hundreds of
        future fragmentation fragments.
      </p>

      <div class="adr-grid">
        <div class="adr-card">
          <div class="adr-icon">🦾</div>
          <div class="adr-title">Robotic Grappling</div>
          <div class="adr-desc">A chaser spacecraft matches the rotation rate of the tumbling target and mechanically grasps it, then fires to deorbit. The primary challenge: most targets are not designed to be captured.</div>
          <div class="adr-status" style="background:rgba(16,185,129,0.1);color:#10b981;border:1px solid rgba(16,185,129,0.25);">ClearSpace-1 planned 2026</div>
        </div>
        <div class="adr-card">
          <div class="adr-icon">🕸️</div>
          <div class="adr-title">Harpoon &amp; Net Capture</div>
          <div class="adr-desc">A harpoon or net is fired at the target to entangle it. Demonstrated on RemoveDEBRIS mission (2018). Lower precision required but harder to control the resulting motion.</div>
          <div class="adr-status" style="background:var(--amber-dim);color:var(--amber);border:1px solid rgba(245,158,11,0.25);">Demonstrated in LEO</div>
        </div>
        <div class="adr-card">
          <div class="adr-icon">⚡</div>
          <div class="adr-title">Electrodynamic Tether</div>
          <div class="adr-desc">A conductive tether deployed from the debris object interacts with Earth's magnetic field to generate drag, deorbiting the object over months without a propulsive maneuver.</div>
          <div class="adr-status" style="background:rgba(59,130,246,0.1);color:var(--accent);border:1px solid rgba(59,130,246,0.25);">Research phase</div>
        </div>
        <div class="adr-card">
          <div class="adr-icon">🔆</div>
          <div class="adr-title">Ground-Based Laser</div>
          <div class="adr-desc">A high-power pulsed laser ablates material from the debris surface, imparting a small thrust impulse. Effective for small debris (1–10 cm) but raises dual-use weapons concerns internationally.</div>
          <div class="adr-status" style="background:var(--red-dim);color:var(--red);border:1px solid rgba(239,68,68,0.25);">Politically sensitive</div>
        </div>
      </div>

      <div class="callout warning">
        <span class="callout-label">The ADR Economics Problem</span>
        Each ADR mission to capture a single defunct rocket body costs an estimated $50–200 million.
        To stabilize the 750–900 km shell, 5–10 removals per year over decades are required —
        a $500 million–$2 billion annual commitment with no commercial return. This is why
        international policy frameworks, liability attribution, and government funding mechanisms
        are as important as the engineering solutions.
      </div>
    </section>

    <!-- MITIGATION GUIDELINES -->
    <section id="mitigation-guidelines" class="content-section reveal">
      <div class="section-number">// 10</div>
      <h2>Mitigation Guidelines: Current Norms</h2>
      <p>
        In 2002, the Inter-Agency Space Debris Coordination Committee (IADC) published debris mitigation
        guidelines, which have since been adopted by the UN Committee on the Peaceful Uses of Outer Space
        (COPUOS). The key provisions:
      </p>

      <div class="data-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Guideline</th>
              <th>Requirement</th>
              <th>Compliance Rate</th>
            </tr>
          </thead>
          <tbody>
            <tr><td class="td-white">LEO post-mission disposal</td><td>Deorbit within 25 years</td><td class="td-amber">~70–80% (improving)</td></tr>
            <tr><td class="td-white">GEO graveyard orbit</td><td>Raise ≥300 km above GEO</td><td class="td-green">~75%</td></tr>
            <tr><td class="td-white">Passivation</td><td>Vent propellants and batteries</td><td class="td-amber">Improving</td></tr>
            <tr><td class="td-white">Protected regions</td><td>Minimize time in LEO/GEO</td><td class="td-accent">Varies by mission</td></tr>
            <tr><td class="td-white">Intentional fragmentation</td><td>Prohibited in protected regions</td><td class="td-red">Violated by ASAT tests</td></tr>
          </tbody>
        </table>
      </div>

      <p>
        The 25-year rule is increasingly seen as insufficient. The FCC in 2022 mandated 5-year
        deorbit timelines for new US-licensed LEO satellites. SpaceX Starlink satellites are
        designed to deorbit within 1–3 years. Some researchers advocate for mandatory
        deorbit within 1 orbital cycle — a position not yet reflected in any binding treaty.
      </p>
    </section>

    <!-- VECTRASPACE SIM -->
    <section id="vectraspace-sim" class="content-section reveal">
      <div class="section-number">// 11</div>
      <h2>VectraSpace Debris Simulation Engine</h2>
      <p>
        VectraSpace includes an interactive debris simulation module that lets users explore
        fragmentation dynamics in real time. When a fragmentation event is triggered, the engine:
      </p>

      <div class="data-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Step</th>
              <th>Method</th>
              <th>Parameters</th>
            </tr>
          </thead>
          <tbody>
            <tr><td class="td-white">1. Select parent</td><td>Any tracked satellite from current scan</td><td>Position, velocity, regime</td></tr>
            <tr><td class="td-white">2. Fragment count</td><td>User-specified (10–200)</td><td>Capped for performance</td></tr>
            <tr><td class="td-white">3. Lc distribution</td><td>Uniform(1 cm, 50 cm)</td><td>Simplified SBM</td></tr>
            <tr><td class="td-white">4. Δv sampling</td><td>Log-normal N(μ_v, σ_v = 0.4)</td><td>μ_v from SBM A/m relation</td></tr>
            <tr><td class="td-white">5. Direction</td><td>Uniform on unit sphere</td><td>Isotropic ejection</td></tr>
            <tr><td class="td-white">6. Propagation</td><td>Linear position offset (dt in seconds)</td><td>Simplified (not SGP4 for debris)</td></tr>
            <tr><td class="td-white">7. Conjunction screen</td><td>Same chunked screener as primary scan</td><td>Debris-aware Pc flags</td></tr>
          </tbody>
        </table>
      </div>

      <div class="callout info">
        <span class="callout-label">Educational Accuracy Note</span>
        The VectraSpace debris simulation is designed for educational illustration, not operational
        conjunction prediction. The linearized trajectory model diverges from true SGP4 propagation
        within minutes for realistic ejection velocities. For operational debris cloud analysis,
        agencies use full numerical integration with the complete SBM fragment distribution,
        shape estimation, and individual BSTAR fitting for each fragment as tracking data becomes
        available. The 2009 Iridium-Cosmos cloud took weeks to characterize adequately.
      </div>

      <div class="callout success">
        <span class="callout-label">Try It Live</span>
        The VectraSpace dashboard lets you run a real conjunction scan, select any tracked satellite
        as a parent object, choose COLLISION or EXPLOSION event type, and generate up to 200 synthetic
        debris fragments displayed in real time on the Cesium globe with instant conjunction screening.
        <br><br>
        <strong>→ Access the live platform at the VectraSpace dashboard to explore these models in action.</strong>
      </div>


<!-- CHAPTER 4 QUIZ -->
<div class="quiz-section" id="ch4-quiz-wrap" data-storage-key='vs_ch4_done'>
  <div class="quiz-eyebrow">⬡ Knowledge Check</div>
  <div class="quiz-title">Chapter 04 — Debris Modeling & Kessler Syndrome</div>
  <div class="quiz-subtitle">Test your understanding of the space debris environment, breakup models, and mitigation strategies.</div>
  <div id="ch4-quiz"></div>
</div>
<script>

function initQuiz(containerId, questions) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const letters = ['A','B','C','D','E'];

  function render() {
    container.innerHTML = questions.map((q, qi) => `
      <div class="quiz-q" id="qq-${containerId}-${qi}">
        <div class="quiz-q-num">Question ${qi+1} of ${questions.length}</div>
        <div class="quiz-q-text">${q.q}</div>
        <div class="quiz-options">
          ${q.opts.map((o, oi) => `
            <button class="quiz-opt" onclick="quizAnswer('${containerId}',${qi},${oi},${q.ans},${questions.length})"
              id="qo-${containerId}-${qi}-${oi}">
              <span class="quiz-opt-letter">${letters[oi]}</span>${o}
            </button>
          `).join('')}
        </div>
        <div class="quiz-explanation" id="qe-${containerId}-${qi}">${q.explain}</div>
      </div>
    `).join('') + `<div class="quiz-score-wrap" id="qs-${containerId}"></div>`;
  }

  window.quizAnswer = function(cid, qi, chosen, correct, total) {
    const optEls = document.querySelectorAll(`[id^="qo-${cid}-${qi}-"]`);
    optEls.forEach(b => b.disabled = true);
    const qEl = document.getElementById(`qq-${cid}-${qi}`);
    if (qEl.dataset.counted) return;
    qEl.classList.add('answered');
    optEls[correct].classList.add('correct');
    if (chosen !== correct) optEls[chosen].classList.add('wrong');
    const expEl = document.getElementById(`qe-${cid}-${qi}`);
    if (expEl) expEl.classList.add('show');
    qEl.dataset.counted = '1';
    if (chosen === correct) qEl.dataset.correct = '1';
    const allQ = document.querySelectorAll(`#${cid} [id^="qq-${cid}-"]`);
    if ([...allQ].every(q => q.dataset.counted)) {
      const sc = [...allQ].filter(q => q.dataset.correct).length;
      const tot = allQ.length;
      const pct = Math.round(sc / tot * 100);
      const color = pct >= 80 ? 'var(--green,#34d399)' : pct >= 50 ? 'var(--amber,#f59e0b)' : 'var(--red,#ef4444)';
      const grade = pct === 100 ? 'Perfect Score' : pct >= 80 ? 'Excellent' : pct >= 50 ? 'Good Effort' : 'Keep Studying';
      const msg = pct === 100 ? "You've fully mastered this chapter's core concepts and equations." :
                  pct >= 80  ? "Solid understanding — you're ready to move to the next chapter." :
                  pct >= 50  ? "Decent foundation. Review the highlighted explanations and retry." :
                               "Revisit the chapter material before moving on — the explanations above show where to focus.";
      const scoreEl = document.getElementById(`qs-${cid}`);
      scoreEl.style.setProperty('--qs-color', color);
      scoreEl.innerHTML = `
        <div class="quiz-score-top">
          <div class="quiz-score-ring" style="--qs-color:${color}">
            <div class="quiz-score-frac">${sc}/${tot}</div>
            <div class="quiz-score-pct">${pct}%</div>
          </div>
          <div class="quiz-score-right">
            <div class="quiz-score-title">${grade}</div>
            <div class="quiz-score-msg">${msg}</div>
          </div>
        </div>
        <div class="quiz-score-bottom">
          <button class="quiz-retry-btn" onclick="document.getElementById('${cid}-wrap').dispatchEvent(new Event('retry'))">↩ Retry Quiz</button>
        </div>
      `;
      scoreEl.classList.add('show');
      setTimeout(() => scoreEl.scrollIntoView({ behavior:'smooth', block:'nearest' }), 100);
    }
  };

  render();
  const wrap = document.getElementById(`${containerId}-wrap`);
  if (wrap) wrap.addEventListener('retry', () => { render(); wrap.scrollIntoView({ behavior:'smooth', block:'start' }); });
}

initQuiz('ch4-quiz', [
  {
    q: "What is the Kessler Syndrome, and what makes it irreversible?",
    opts: ["A chain reaction where debris collisions generate more debris faster than atmospheric drag can remove it, making the regime self-sustaining", "A phenomenon where solar activity causes mass satellite failures simultaneously", "A regulatory failure causing overcrowded orbital slots", "A gravity resonance effect that clusters debris in specific altitude bands"],
    ans: 0,
    explain: "Proposed by Donald Kessler in 1978: above a critical debris density, each collision creates a debris cloud that increases collision probability for other objects. When the cascade rate exceeds the removal rate (atmospheric drag), the cascade becomes self-sustaining. Above ~800–1000 km where drag is negligible, this is effectively permanent on human timescales."
  },
  {
    q: "NASA's standard 25-year deorbit rule requires satellites to re-enter within 25 years of end-of-mission. Why 25 years specifically?",
    opts: ["It matches the average satellite operational lifetime", "Modeling shows that compliance rates above ~90% keep LEO collision risk stable below Kessler thresholds at most altitudes", "International treaty signed in 1987 specified this number", "25 years is how long satellites can maintain attitude control"],
    ans: 1,
    explain: "The 25-year rule is an engineering/policy compromise: it's achievable with reasonable fuel reserves while being short enough (per NASA models) to keep the LEO environment stable if broadly followed. Recent proposals suggest tightening this to 5 years given mega-constellation growth."
  },
  {
    q: "The NASA Standard Breakup Model (SBM) predicts the debris cloud from a collision. What are the two primary input parameters?",
    opts: ["Satellite age and orbital altitude", "Impactor mass and relative impact velocity", "Satellite material composition and surface area", "Orbital period and inclination"],
    ans: 1,
    explain: "The SBM (and its derivative EVOLVE/LEGEND models) primarily uses impactor/target mass and relative collision velocity to estimate the number, size distribution, and Δv distribution of fragments generated. This drives debris hazard assessments for conjunction events."
  },
  {
    q: "What characteristic makes debris from the 2007 Chinese ASAT test particularly dangerous and long-lived?",
    opts: ["The debris is at GEO altitude where no atmospheric drag exists", "The test was conducted at ~850 km altitude, where atmospheric drag is minimal and debris will persist for centuries", "The debris was made of titanium which is radar-invisible", "The fragments are too small to track but large enough to be lethal"],
    ans: 1,
    explain: "The FY-1C satellite was destroyed at ~850 km altitude. At this altitude, atmospheric drag is extremely weak — individual fragments will remain in orbit for decades to centuries depending on their ballistic coefficient. The test created ~3,500+ trackable fragments and hundreds of thousands of smaller untrackable debris."
  },
  {
    q: "Active Debris Removal (ADR) is technically challenging. Which physical property of uncontrolled debris objects makes capture especially difficult?",
    opts: ["High electrical charge accumulated from solar radiation", "Tumbling rotation (up to several RPM) with no cooperative grapple points", "Extreme temperatures from thermal cycling", "Random orbit changes from outgassing"],
    ans: 1,
    explain: "Most uncontrolled debris objects (spent rocket bodies, defunct satellites) are tumbling at rates that can reach several RPM. They were not designed with capture interfaces. Matching rotation rates with a chaser spacecraft while grappling a tumbling, non-cooperative target is one of the hardest rendezvous problems in orbital mechanics."
  }
]);

// ── GLOSSARY TOOLTIPS ─────────────────────────────────────────
(function() {
  const DEFS = {
    'SGP4':'Simplified General Perturbations model 4 — the standard analytical propagator for Earth satellites using TLE data. Models drag (B*), J₂ oblateness, and secular/periodic terms.',
    'TLE':'Two-Line Element Set — standardised format encoding six Keplerian elements at a given epoch. Accuracy decays ~1 km/day for LEO objects.',
    'vis-viva':'v² = μ(2/r − 1/a). Relates orbital speed to current radius, semi-major axis, and gravitational parameter. Underlies all delta-v calculations.',
    'RAAN':'Right Ascension of Ascending Node — angle from vernal equinox to ascending node. Drifts westward in prograde LEO orbits due to J₂ at ~6–7°/day.',
    'J₂':'Dominant Earth oblateness coefficient: J₂ = 1.08263×10⁻³. Causes nodal regression (RAAN drift) and apsidal precession for all satellites.',
    'Kessler':'A self-sustaining cascade of collisions above a critical debris density, proposed by Donald Kessler (NASA, 1978). Potentially irreversible above 800 km.',
    'Pc':'Probability of Collision — the probability two objects physically contact. Computed by integrating the combined uncertainty PDF over the collision cross-section. Action threshold: 1×10⁻⁴.',
    'TCA':'Time of Closest Approach — the moment of minimum separation between two conjunction objects. Reference epoch for all CDM calculations.',
    'CDM':'Conjunction Data Message — CCSDS standard format for sharing conjunction results between space surveillance providers and operators.',
    'NASA SBM':'NASA Standard Breakup Model — predicts fragment count N(Lc) = 6·M^0.75·Lc^−1.6 from satellite collisions or explosions.',
    'covariance':'A 3×3 or 6×6 symmetric matrix encoding position uncertainty and correlations in RTN. Diagonal elements are variances (σ_R², σ_T², σ_N²).',
    'delta-v':'Change in velocity (km/s) required for an orbital maneuver — the fundamental currency of spaceflight, limited by onboard propellant.',
  };
  const tip = document.createElement('div');
  tip.className = 'gtooltip';
  tip.innerHTML = '<div class="gtooltip-term"></div><div class="gtooltip-def"></div><a class="gtooltip-link" href="/glossary">Space News →</a>';
  document.body.appendChild(tip);
  let hideTimer;
  document.querySelectorAll('dfn[data-term]').forEach(el => {
    const key = el.dataset.term;
    const def = DEFS[key] || '';
    if (!def) return;
    el.addEventListener('mouseenter', () => {
      clearTimeout(hideTimer);
      tip.querySelector('.gtooltip-term').textContent = key;
      tip.querySelector('.gtooltip-def').textContent = def;
      const rect = el.getBoundingClientRect();
      let top = rect.bottom + 8, left = rect.left;
      if (left + 300 > window.innerWidth - 16) left = window.innerWidth - 316;
      if (top + 140 > window.innerHeight - 16) top = rect.top - 150;
      tip.style.top = top + 'px'; tip.style.left = left + 'px';
      tip.classList.add('show');
    });
    el.addEventListener('mouseleave', () => { hideTimer = setTimeout(() => tip.classList.remove('show'), 200); });
  });
})();

</script>

      <!-- Chapter nav -->
      <div class="chapter-nav">
        <a href="/education/perturbations" class="chapter-nav-card">
          <div class="cnc-dir">← Previous</div>
          <div class="cnc-title">Chapter 03</div>
          <div class="cnc-sub">Orbital Perturbations</div>
        </a>
        <a href="/" class="chapter-nav-card next">
          <div class="cnc-dir">↑ Back to Top</div>
          <div class="cnc-title">Learning Hub</div>
          <div class="cnc-sub">VectraSpace Educational Home</div>
        </a>
      </div>
    </section>

  </article>
</div>

<script>
const bar = document.getElementById('progress-bar');
window.addEventListener('scroll', () => {
  const pct = (window.scrollY / (document.body.scrollHeight - window.innerHeight)) * 100;
  bar.style.width = pct + '%';
});
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('visible'); });
}, { threshold: 0.1 });
document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
const sections = document.querySelectorAll('.content-section');
const tocLinks = document.querySelectorAll('.toc-list a');
const tocObs = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      const id = e.target.id;
      tocLinks.forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#'+id));
    }
  });
}, { threshold: 0.3 });
sections.forEach(s => tocObs.observe(s));

// Population history chart
const popCanvas = document.getElementById('pop-canvas');
if (popCanvas) {
  const ctx = popCanvas.getContext('2d');
  const W = popCanvas.parentElement.offsetWidth || 600;
  const H = 200;
  popCanvas.width = W; popCanvas.height = H;
  const pad = { top:12, right:16, bottom:32, left:56 };
  // Approximate data points
  const data = [
    [1957,1],[1960,50],[1965,400],[1970,900],[1975,2000],[1980,4000],
    [1985,6000],[1990,7500],[1995,8500],[2000,9500],[2005,10500],
    [2007,13000],[2008,13500],[2009,16000],[2010,16200],[2015,17000],
    [2019,19000],[2021,21000],[2022,23000],[2024,28000]
  ];
  const years = data.map(d=>d[0]); const counts = data.map(d=>d[1]);
  const minY=1957, maxY=2024, maxC=30000;
  const sX = y => pad.left + ((y-minY)/(maxY-minY)) * (W-pad.left-pad.right);
  const sY = c => pad.top + ((maxC-c)/maxC) * (H-pad.top-pad.bottom);
  // Grid
  ctx.strokeStyle='#1a2e42'; ctx.lineWidth=1;
  [0,10000,20000,30000].forEach(c => {
    ctx.beginPath(); ctx.moveTo(pad.left,sY(c)); ctx.lineTo(W-pad.right,sY(c)); ctx.stroke();
    ctx.fillStyle='#4a6a85'; ctx.font='9px Space Mono';
    ctx.fillText(c === 0 ? '0' : (c/1000)+'k', 2, sY(c)+3);
  });
  // Event markers
  [[2007,'Fengyun'],[2009,'Iridium']].forEach(([yr,lbl]) => {
    ctx.strokeStyle='rgba(239,68,68,0.4)'; ctx.lineWidth=1; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(sX(yr),pad.top); ctx.lineTo(sX(yr),H-pad.bottom); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle='#ef4444'; ctx.font='8px Space Mono';
    ctx.fillText(lbl, sX(yr)+3, pad.top+12);
  });
  // Curve
  const grad = ctx.createLinearGradient(pad.left, 0, W-pad.right, 0);
  grad.addColorStop(0,'#3b82f6'); grad.addColorStop(0.7,'#f59e0b'); grad.addColorStop(1,'#ef4444');
  ctx.strokeStyle=grad; ctx.lineWidth=2;
  ctx.beginPath();
  data.forEach(([y,c],i) => i===0 ? ctx.moveTo(sX(y),sY(c)) : ctx.lineTo(sX(y),sY(c)));
  ctx.stroke();
  // X labels
  ctx.fillStyle='#4a6a85'; ctx.font='9px Space Mono';
  [1960,1970,1980,1990,2000,2010,2020].forEach(y => ctx.fillText(y, sX(y)-12, H-5));
}

// Fragment count chart
const fragCanvas = document.getElementById('frag-canvas');
if (fragCanvas) {
  const ctx = fragCanvas.getContext('2d');
  const W = fragCanvas.parentElement.offsetWidth || 600;
  const H = 200;
  fragCanvas.width = W; fragCanvas.height = H;
  const pad = { top:12, right:16, bottom:32, left:64 };
  // Log-log: size from 0.1 cm to 100 cm, N from SBM
  const sizes = []; const counts = [];
  for (let lx = -1; lx <= 2; lx += 0.15) {
    const Lc = Math.pow(10, lx) / 100; // meters
    const N = 6 * Math.pow(1.0, 0.5) * Math.pow(Lc, -1.6);
    sizes.push(lx);
    counts.push(Math.log10(Math.max(1, N)));
  }
  const maxC = Math.max(...counts);
  const sX = lx => pad.left + ((lx - (-1)) / 3) * (W-pad.left-pad.right);
  const sY = c => pad.top + ((maxC-c)/maxC) * (H-pad.top-pad.bottom);
  // Grid
  ctx.strokeStyle='#1a2e42'; ctx.lineWidth=1;
  [0,2,4,6,8].forEach(c => {
    ctx.beginPath(); ctx.moveTo(pad.left,sY(c)); ctx.lineTo(W-pad.right,sY(c)); ctx.stroke();
    ctx.fillStyle='#4a6a85'; ctx.font='9px Space Mono';
    ctx.fillText('10^'+c, 2, sY(c)+3);
  });
  // Threshold lines
  [[Math.log10(0.01),'#ef4444','1 cm'],[Math.log10(0.1),'#f59e0b','10 cm']].forEach(([lx,color,lbl]) => {
    ctx.strokeStyle=color+'77'; ctx.lineWidth=1; ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(sX(lx),pad.top); ctx.lineTo(sX(lx),H-pad.bottom); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle=color; ctx.font='8px Space Mono';
    ctx.fillText(lbl, sX(lx)+3, pad.top+10);
  });
  // Curve
  ctx.strokeStyle='#3b82f6'; ctx.lineWidth=2;
  ctx.beginPath();
  sizes.forEach((lx,i) => i===0 ? ctx.moveTo(sX(lx),sY(counts[i])) : ctx.lineTo(sX(lx),sY(counts[i])));
  ctx.stroke();
  // X labels
  ctx.fillStyle='#4a6a85'; ctx.font='9px Space Mono';
  [[Math.log10(0.1),'0.1 cm'],[0,'1 cm'],[1,'10 cm'],[2,'100 cm']].forEach(([lx,lbl]) => {
    ctx.fillText(lbl, sX(lx)-14, H-5);
  });
}
</script>
</body>
</html>

"""

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
  --muted:   #8aaac5;
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
  display: flex; align-items: center; text-decoration: none;
}
.nav-brand-name {
  font-family: var(--serif); font-size: 18px; font-weight: 400;
  color: #fff; letter-spacing: -0.2px; font-style: italic;
}
.nav-brand-name em { color: var(--accent); font-style: normal; }
.nav-links {
  display: flex; gap: 24px; list-style: none; align-items: center;
}
.nav-links a {
  font-family: var(--mono); font-size: 10px; letter-spacing: 0.5px;
  color: var(--muted); text-decoration: none; transition: color 0.2s;
}
.nav-links a:hover { color: var(--text); }
.nav-cta {
  font-family: var(--mono); font-size: 10px; letter-spacing: 1px;
  text-transform: uppercase; padding: 8px 18px;
  border: 1px solid var(--accent); border-radius: 4px;
  color: var(--accent); text-decoration: none;
  transition: all 0.2s; white-space: nowrap;
}
.nav-cta:hover { background: var(--accent); color: var(--ink); }
/* Mobile hamburger */
.nav-hamburger {
  display: none; flex-direction: column; gap: 5px; cursor: pointer;
  padding: 8px; border: 1px solid transparent; border-radius: 4px;
  background: transparent; transition: border-color 0.2s;
}
.nav-hamburger:hover { border-color: var(--border2); }
.nav-hamburger span {
  display: block; width: 20px; height: 1.5px; background: var(--muted);
  border-radius: 2px; transition: all 0.25s;
}
.nav-hamburger.open span:nth-child(1) { transform: translateY(6.5px) rotate(45deg); }
.nav-hamburger.open span:nth-child(2) { opacity: 0; transform: scaleX(0); }
.nav-hamburger.open span:nth-child(3) { transform: translateY(-6.5px) rotate(-45deg); }
/* Mobile drawer */
#mobile-nav {
  display: none; position: fixed; top: 60px; left: 0; right: 0; z-index: 999;
  background: rgba(8,12,18,0.97); border-bottom: 1px solid var(--border);
  backdrop-filter: blur(16px); padding: 20px 24px 28px;
  flex-direction: column; gap: 4px;
  transform: translateY(-8px); opacity: 0;
  transition: transform 0.2s ease, opacity 0.2s ease;
}
#mobile-nav.open { transform: translateY(0); opacity: 1; }
#mobile-nav a {
  font-family: var(--mono); font-size: 13px; letter-spacing: 1px;
  color: var(--muted); text-decoration: none; padding: 12px 0;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  transition: color 0.15s;
}
#mobile-nav a:last-child { border-bottom: none; }
#mobile-nav a:hover { color: var(--text); }
#mobile-nav a.cta-link {
  color: var(--accent); margin-top: 8px; border: 1px solid var(--accent);
  border-radius: 4px; padding: 12px 16px; justify-content: center;
  border-bottom: 1px solid var(--accent);
}

/* ── LIVE TLE TICKER (in-page section version) ── */
#tle-ticker {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 10px; overflow: hidden; height: 44px;
  display: flex; align-items: center; max-width: 1100px; margin: 0 auto;
}
.ticker-label {
  font-family: var(--mono); font-size: 8px; letter-spacing: 2px;
  text-transform: uppercase; color: var(--accent);
  background: rgba(74,158,255,0.08); border-right: 1px solid var(--border);
  padding: 0 16px; height: 100%; display: flex; align-items: center;
  white-space: nowrap; flex-shrink: 0;
}
.ticker-scroll {
  display: flex; overflow: hidden; flex: 1; height: 100%;
}
.ticker-track {
  display: flex; gap: 0; animation: ticker-move 60s linear infinite;
  white-space: nowrap; align-items: center;
}
.ticker-track:hover { animation-play-state: paused; }
@keyframes ticker-move {
  from { transform: translateX(0); }
  to   { transform: translateX(-50%); }
}
.ticker-sat {
  font-family: var(--mono); font-size: 9px; letter-spacing: 0.5px;
  color: var(--muted); padding: 0 18px; border-right: 1px solid var(--border);
  height: 44px; display: flex; align-items: center; gap: 8px;
}
.ticker-sat .t-name { color: var(--text); }
.ticker-sat .t-alt { color: var(--accent); }
.ticker-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--green); flex-shrink: 0; animation: blink-slow 3s infinite; }
@keyframes blink-slow { 0%,100%{opacity:1} 50%{opacity:0.3} }
.ticker-status {
  font-family: var(--mono); font-size: 8px; letter-spacing: 1px;
  color: var(--faint); padding: 0 14px; height: 100%; display: flex; align-items: center;
  white-space: nowrap; flex-shrink: 0; border-left: 1px solid var(--border);
}

/* ── TOOLS STRIP ── */
.tools-strip {
  display: flex; gap: 12px; margin-top: 40px; flex-wrap: wrap;
}
.tool-card {
  flex: 1; min-width: 200px;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 20px 22px;
  text-decoration: none; display: flex; align-items: flex-start; gap: 14px;
  transition: all 0.2s; position: relative; overflow: hidden;
}
.tool-card::before {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(135deg, rgba(74,158,255,0.04) 0%, transparent 60%);
  opacity: 0; transition: opacity 0.2s;
}
.tool-card:hover { border-color: rgba(74,158,255,0.35); transform: translateY(-2px); }
.tool-card:hover::before { opacity: 1; }
.tool-card-icon {
  width: 38px; height: 38px; border-radius: 8px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; font-size: 18px;
  background: rgba(74,158,255,0.1); border: 1px solid rgba(74,158,255,0.2);
}
.tool-card-icon.green  { background: rgba(52,211,153,0.1); border-color: rgba(52,211,153,0.2); }
.tool-card-icon.amber  { background: rgba(245,158,11,0.1); border-color: rgba(245,158,11,0.2); }
.tool-card-icon.purple { background: rgba(167,139,250,0.1); border-color: rgba(167,139,250,0.2); }
.tool-card-body { flex: 1; }
.tool-card-title {
  font-family: var(--sans); font-size: 13px; font-weight: 600;
  color: var(--text); margin-bottom: 3px;
}
.tool-card-desc {
  font-family: var(--mono); font-size: 9px; letter-spacing: 0.3px;
  color: var(--muted); line-height: 1.5;
}

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
  height: 400px; overflow: visible;
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
  top: 50%; left: 50%; transform: translate(-50%, -50%);
}
.orbit-path-1 { --w:296px; border-color: rgba(74,158,255,0.35); animation: orb 8s linear infinite; }
.orbit-path-2 { --w:366px; border-color: rgba(52,211,153,0.22); animation: orb2 12s linear infinite; }
.orbit-path-3 { --w:436px; border-color: rgba(245,158,11,0.18); animation: orb3 18s linear infinite; }
@keyframes orb  { from { transform: translate(-50%,-50%) rotate(0deg); }   to { transform: translate(-50%,-50%) rotate(360deg); } }
@keyframes orb2 { from { transform: translate(-50%,-50%) rotate(45deg); }  to { transform: translate(-50%,-50%) rotate(405deg); } }
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


/* ── CHAPTER PROGRESS TRACKING ── */
.chapter-card { position: relative; }
.chapter-progress-badge {
  position: absolute; top: 14px; right: 14px;
  width: 26px; height: 26px; border-radius: 50%;
  background: var(--green, #34d399); display: none;
  align-items: center; justify-content: center;
  font-size: 12px; z-index: 2;
  box-shadow: 0 0 12px rgba(52,211,153,0.4);
}
.chapter-card.completed .chapter-progress-badge { display: flex; }
.chapter-card.completed .chapter-card-accent { transform: scaleX(1); background: var(--green, #34d399); }
.chapter-card.completed { border-color: rgba(52,211,153,0.2); }

/* Learning progress bar strip */
.learn-progress-strip {
  max-width: 480px; margin: 0 auto 56px;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px 24px;
  display: none;
}
.learn-progress-strip.show { display: block; }
.lps-label {
  font-family: var(--mono); font-size: 9px; letter-spacing: 2px;
  text-transform: uppercase; color: var(--muted); margin-bottom: 10px;
  display: flex; justify-content: space-between;
}
.lps-bar-track {
  height: 4px; background: var(--border); border-radius: 2px; overflow: hidden;
}
.lps-bar-fill {
  height: 100%; border-radius: 2px;
  background: linear-gradient(90deg, var(--accent) 0%, var(--green) 100%);
  transition: width 0.6s cubic-bezier(0.4,0,0.2,1);
}


/* ── SUBSCRIBE SECTION ── */
.subscribe-card {
  background: var(--ink2); border: 1px solid var(--border);
  border-radius: 10px; padding: 48px; overflow: hidden;
  display: grid; grid-template-columns: 1fr 1fr; gap: 48px; align-items: center;
  position: relative;
}
.subscribe-card::before {
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(ellipse at 80% 50%, rgba(74,158,255,0.06) 0%, transparent 70%);
  pointer-events: none;
}
.sub-eyebrow {
  font-family: var(--mono); font-size: 9px; letter-spacing: 3px;
  text-transform: uppercase; color: var(--accent); margin-bottom: 12px;
}
.sub-title {
  font-family: var(--serif); font-size: 34px; font-weight: 400;
  color: #fff; line-height: 1.1; letter-spacing: -0.3px; margin-bottom: 14px;
}
.sub-title em { font-style: italic; color: var(--accent2); }
.sub-body { font-size: 14px; color: var(--muted); line-height: 1.75; margin-bottom: 24px; }
.sub-features { display: flex; flex-direction: column; gap: 8px; }
.sub-feat { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }
.sub-feat-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.sub-form-label {
  font-family: var(--mono); font-size: 9px; letter-spacing: 1.5px;
  text-transform: uppercase; color: var(--muted); margin-bottom: 10px;
}
.sub-input-row { display: flex; gap: 8px; margin-bottom: 10px; }
.sub-input {
  flex: 1; padding: 12px 16px; background: var(--ink3);
  border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); font-family: var(--mono); font-size: 13px;
  outline: none; transition: border-color 0.2s;
}
.sub-input:focus { border-color: var(--accent); }
.sub-input::placeholder { color: var(--faint); }
.sub-btn {
  padding: 12px 22px; background: var(--accent); color: #fff;
  border: none; border-radius: 6px; font-family: var(--mono);
  font-size: 10px; letter-spacing: 1.5px; text-transform: uppercase;
  cursor: pointer; transition: all 0.2s; white-space: nowrap;
}
.sub-btn:hover { background: var(--accent2); transform: translateY(-1px); }
.sub-btn:disabled { opacity: 0.5; cursor: default; transform: none; }
.sub-disclaimer {
  font-family: var(--mono); font-size: 9px; color: var(--faint); letter-spacing: 0.5px;
}
.sub-msg { margin-top: 8px; font-family: var(--mono); font-size: 10px; min-height: 16px; }
.sub-success { text-align: center; padding: 20px; }
.sub-success-icon {
  width: 56px; height: 56px; border-radius: 50%;
  background: rgba(52,211,153,0.12); border: 1px solid rgba(52,211,153,0.3);
  display: flex; align-items: center; justify-content: center;
  font-size: 22px; color: var(--green); margin: 0 auto 14px;
}
.sub-success-title { font-family: var(--serif); font-size: 22px; color: #fff; margin-bottom: 6px; }
.sub-success-body { font-size: 13px; color: var(--muted); line-height: 1.6; }
@media(max-width:760px) {
  .subscribe-card { grid-template-columns: 1fr; gap: 32px; padding: 32px; }
}

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

/* ── SATELLITE OF THE DAY ── */
#satod { padding: 40px 0 80px; }
.satod-card {
  max-width: 900px; margin: 0 auto;
  background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
  overflow: hidden; position: relative;
}
.satod-card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, var(--satod-color, var(--accent)) 0%, transparent 100%);
}
.satod-header {
  padding: 28px 36px 20px; display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; flex-wrap: wrap;
}
.satod-eyebrow {
  font-family: var(--mono); font-size: 9px; letter-spacing: 3px;
  color: var(--green); text-transform: uppercase; margin-bottom: 8px;
  display: flex; align-items: center; gap: 8px;
}
.satod-live-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--green);
  animation: pulse-dot 2s ease-in-out infinite;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.7); }
}
.satod-name {
  font-family: var(--serif); font-size: 28px; color: #fff;
  font-weight: 400; letter-spacing: -0.3px; line-height: 1.2;
}
.satod-type-badge {
  font-family: var(--mono); font-size: 9px; letter-spacing: 2px;
  padding: 5px 12px; border-radius: 20px; border: 1px solid;
  text-transform: uppercase; white-space: nowrap; align-self: flex-start;
  color: var(--satod-color, var(--accent));
  border-color: var(--satod-color, var(--accent));
  background: rgba(74,158,255,0.07);
}
.satod-stats {
  display: grid; grid-template-columns: repeat(3, 1fr);
  border-top: 1px solid var(--border);
}
.satod-stat {
  padding: 24px 28px; border-right: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 4px;
}
.satod-stat:last-child { border-right: none; }
.satod-stat-val {
  font-family: var(--serif); font-size: 32px; color: var(--satod-color, var(--accent));
  line-height: 1; letter-spacing: -0.5px;
}
.satod-stat-unit {
  font-family: var(--mono); font-size: 9px; color: var(--muted);
  letter-spacing: 1px; text-transform: uppercase;
}
.satod-stat-label {
  font-family: var(--mono); font-size: 9px; color: var(--muted);
  letter-spacing: 1px; text-transform: uppercase; margin-top: 2px;
}
.satod-footer {
  padding: 20px 36px; border-top: 1px solid var(--border);
  display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap;
}
.satod-fact-icon { font-size: 18px; flex-shrink: 0; margin-top: 2px; }
.satod-fact {
  font-size: 14px; color: var(--muted); line-height: 1.7; flex: 1;
  font-style: italic;
}
.satod-operator {
  font-family: var(--mono); font-size: 9px; color: var(--faint);
  letter-spacing: 1px; text-transform: uppercase; padding: 20px 36px 0;
}
.satod-loading {
  padding: 60px 36px; text-align: center;
  font-family: var(--mono); font-size: 11px; color: var(--muted); letter-spacing: 1px;
}
@media (max-width: 600px) {
  .satod-stats { grid-template-columns: 1fr; }
  .satod-stat { border-right: none; border-bottom: 1px solid var(--border); }
  .satod-stat:last-child { border-bottom: none; }
  .satod-header { padding: 24px 20px 16px; }
  .satod-footer, .satod-operator { padding-left: 20px; padding-right: 20px; }
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
  padding: 36px 48px 28px;
  display: flex; flex-direction: column; align-items: center; gap: 20px;
}
.footer-top {
  width: 100%; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;
}
.footer-brand { font-family: var(--sans); font-size: 15px; font-weight: 700; color: var(--text); }
.footer-brand em { color: var(--accent); font-style: normal; }
.footer-links {
  display: flex; gap: 24px; list-style: none; flex-wrap: wrap; justify-content: center;
}
.footer-links a {
  font-family: var(--mono); font-size: 10px; letter-spacing: 1px;
  color: var(--muted); text-decoration: none;
  text-transform: uppercase; transition: color 0.2s;
}
.footer-links a:hover { color: var(--text); }
.footer-contact {
  font-family: var(--mono); font-size: 10px; letter-spacing: 0.5px;
  color: var(--muted);
}
.footer-contact a { color: var(--accent); text-decoration: none; }
.footer-copy {
  font-family: var(--mono); font-size: 9px; color: var(--faint);
  letter-spacing: 1px; border-top: 1px solid var(--border);
  width: 100%; padding-top: 16px; text-align: center;
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
  .nav-hamburger { display: flex; }
  .nav-cta { display: none; }
  .section-wrap { padding: 0 24px; }
  .mission-grid, .kessler-inner { grid-template-columns: 1fr; gap: 48px; }
  .ssa-pillars { grid-template-columns: 1fr; }
  .sim-grid { grid-template-columns: 1fr 1fr; }
  .chapters-grid { grid-template-columns: 1fr; }
  .data-metrics { grid-template-columns: repeat(2, 1fr); }
  .mission-visual { height: 280px; }
  footer { flex-direction: column; gap: 20px; text-align: center; }
  .kessler-data { position: static; }
  /* Contact section tablet */
  #contact .container { padding: 0 24px !important; }
  #contact .reveal > div { padding: 48px 40px !important; }
}
@media (max-width: 600px) {
  .sim-grid { grid-template-columns: 1fr; }
  .data-metrics { grid-template-columns: 1fr 1fr; }
  .cta-box { padding: 48px 24px; }
  #hero { padding: 100px 16px 60px; }
  .hero-title { letter-spacing: -1px; }
  .tools-strip { flex-direction: column; }
  .tool-card { min-width: 0; }
  #tle-ticker { display: none; } /* too cramped on small phones */
  /* Contact section mobile */
  #contact { padding: 60px 0 !important; }
  #contact .container { padding: 0 16px !important; }
  #contact .reveal > div { padding: 36px 24px !important; }
  .contact-bio-row {
    flex-direction: column !important;
    gap: 24px !important;
    align-items: center !important;
    text-align: center !important;
  }
  #contact .reveal [style*="font-size:26px"] { font-size: 20px !important; }
  #contact .reveal [style*="font-size:clamp(22px"] { font-size: 20px !important; }
  #contact .reveal [style*="max-width:520px"] { max-width: 100% !important; }
  #contact a[href^="mailto"] {
    width: 100%;
    justify-content: center !important;
    font-size: 10px !important;
    padding: 14px 16px !important;
    box-sizing: border-box;
  }
  /* Footer mobile */
  footer { padding: 32px 20px; gap: 16px; }
  .footer-top { flex-direction: column; align-items: center; text-align: center; }
  .footer-links { flex-wrap: wrap; justify-content: center; gap: 8px 16px; }
  .footer-contact { text-align: center; }
  .footer-copy { font-size: 10px; text-align: center; }
  /* Hero orbit system — hide on very small screens */
  .hero-orbit-system { display: none; }
  /* Chapter cards mobile padding */
  .chapter-card-body { padding: 24px; }
  .chapter-footer { padding: 12px 24px; }
}
</style>
</head>
<body>

<!-- STARFIELD -->
<div id="starfield"></div>

<!-- NAV -->
<nav id="nav">
  <a href="/" class="nav-brand">
    <span class="nav-brand-name">Vectra<em>Space</em></span>
  </a>
  <ul class="nav-links">
    <li><a href="#mission">Mission</a></li>
    <li><a href="#learn">Chapters</a></li>
    <li><a href="/scenarios">Scenarios</a></li>
    <li><a href="/kepler">Orbit Explorer</a></li>
    <li><a href="/glossary">News</a></li>
    <li><a href="/calculator">Calculator</a></li>
    <li><a href="#contact">Contact</a></li>
  </ul>
  <a href="/dashboard" class="nav-cta">Dashboard →</a>
  <button class="nav-hamburger" id="nav-hamburger" onclick="toggleMobileNav()" aria-label="Menu">
    <span></span><span></span><span></span>
  </button>
</nav>
<div id="mobile-nav">
  <a href="#mission">Mission <span>→</span></a>
  <a href="#learn">Chapters <span>→</span></a>
  <a href="/scenarios">Scenarios <span>→</span></a>
  <a href="/kepler">Orbit Explorer <span>→</span></a>
  <a href="/glossary">News <span>→</span></a>
  <a href="/calculator">Calculator <span>→</span></a>
  <a href="#contact">Contact <span>→</span></a>
  <a href="/dashboard" class="cta-link">Open Dashboard →</a>
</div>

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

    <div class="learn-progress-strip" id="learn-progress-strip">
      <div class="lps-label"><span>Your Progress</span><span id="lps-text">0 / 4 Chapters</span></div>
      <div class="lps-bar-track"><div class="lps-bar-fill" id="lps-fill" style="width:0%"></div></div>
    </div>
    <div class="chapters-grid">
      <!-- Chapter 01 -->
      <a href="/education/orbital-mechanics" class="chapter-card reveal" id="chcard-1" style="--ch-color:#4a9eff;">
        <div class="chapter-card-accent"></div>
        <div class="chapter-progress-badge">✓</div>
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
      <a href="/education/collision-prediction" class="chapter-card reveal reveal-delay-1" id="chcard-2" style="--ch-color:#34d399;">
        <div class="chapter-card-accent"></div>
        <div class="chapter-progress-badge">✓</div>
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
      <a href="/education/perturbations" class="chapter-card reveal reveal-delay-2" id="chcard-3" style="--ch-color:#f59e0b;">
        <div class="chapter-card-accent"></div>
        <div class="chapter-progress-badge">✓</div>
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
      <a href="/education/debris-modeling" class="chapter-card reveal reveal-delay-3" id="chcard-4" style="--ch-color:#f87171;">
        <div class="chapter-card-accent"></div>
        <div class="chapter-progress-badge">✓</div>
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

    <!-- Interactive Tools Strip -->
    <div class="tools-strip reveal" style="margin-top:40px;">
      <a href="/scenarios" class="tool-card">
        <div class="tool-card-icon">💥</div>
        <div class="tool-card-body">
          <div class="tool-card-title">Scenario Modules</div>
          <div class="tool-card-desc">Iridium-Cosmos · Kessler · ASAT · Maneuver</div>
        </div>
      </a>
      <a href="/kepler" class="tool-card">
        <div class="tool-card-icon green">🌐</div>
        <div class="tool-card-body">
          <div class="tool-card-title">Orbit Explorer</div>
          <div class="tool-card-desc">Drag sliders to warp orbits in real-time 3D</div>
        </div>
      </a>
      <a href="/calculator" class="tool-card">
        <div class="tool-card-icon amber">⚡</div>
        <div class="tool-card-body">
          <div class="tool-card-title">Impact Calculator</div>
          <div class="tool-card-desc">KE, Pc, fragment counts, Kessler risk</div>
        </div>
      </a>
      <a href="/glossary" class="tool-card">
        <div class="tool-card-icon purple">📖</div>
        <div class="tool-card-body">
          <div class="tool-card-title">Space News</div>
          <div class="tool-card-desc">50+ terms · searchable · deep-link ready</div>
        </div>
      </a>
    </div>

    <!-- Terminal -->
    <div class="sim-terminal reveal" style="margin-top:36px;">
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

<!-- SATELLITE OF THE DAY -->
<section id="satod">
  <div class="section-wrap">
    <div class="section-label reveal" style="margin-bottom:28px;">// Featured Object</div>
    <div class="satod-card reveal" id="satod-card">
      <div class="satod-loading">⌁ Loading today's featured satellite...</div>
    </div>
  </div>
</section>

<script>
(function loadSatOD() {
  fetch('/satellite-of-the-day')
    .then(r => r.json())
    .then(sat => {
      const card = document.getElementById('satod-card');
      const color = sat.color || '#4a9eff';
      card.style.setProperty('--satod-color', color);
      const liveTag = sat.live
        ? '<span class="satod-live-dot"></span> Live Data'
        : 'Estimated Orbital Data';
      card.innerHTML = `
        <div class="satod-header">
          <div>
            <div class="satod-eyebrow">${liveTag} · Satellite of the Day</div>
            <div class="satod-name">${sat.name}</div>
            <div class="satod-operator">${sat.operator || ''}</div>
          </div>
          <div class="satod-type-badge">${sat.type}</div>
        </div>
        <div class="satod-stats">
          <div class="satod-stat">
            <div class="satod-stat-val">${(sat.alt_km||0).toLocaleString()}</div>
            <div class="satod-stat-unit">km</div>
            <div class="satod-stat-label">Current Altitude</div>
          </div>
          <div class="satod-stat">
            <div class="satod-stat-val">${sat.velocity_kms}</div>
            <div class="satod-stat-unit">km/s</div>
            <div class="satod-stat-label">Orbital Velocity</div>
          </div>
          <div class="satod-stat">
            <div class="satod-stat-val">${sat.period_min}</div>
            <div class="satod-stat-unit">min</div>
            <div class="satod-stat-label">Orbital Period</div>
          </div>
        </div>
        <div class="satod-footer">
          <div class="satod-fact-icon">💡</div>
          <div class="satod-fact">${sat.fun_fact}</div>
        </div>
      `;
    })
    .catch(() => {
      const card = document.getElementById('satod-card');
      if (card) card.innerHTML = '<div class="satod-loading">Satellite data unavailable — run a scan to populate the TLE cache.</div>';
    });
})();
</script>

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
  <!-- TLE Ticker embedded in data section -->
  <div style="padding: 0 48px 48px; position:relative; z-index:1;">
    <div id="tle-ticker">
      <div class="ticker-label">⬤ LIVE</div>
      <div class="ticker-scroll">
        <div class="ticker-track" id="ticker-track">
          <span class="ticker-sat"><div class="ticker-dot"></div><span class="t-name">Loading...</span></span>
        </div>
      </div>
      <div class="ticker-status" id="ticker-status">— connecting</div>
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
        <a href="/scenarios" style="font-family:var(--mono);font-size:10px;letter-spacing:1px;text-transform:uppercase;padding:14px 28px;border:1px solid var(--border2);border-radius:6px;color:var(--muted);text-decoration:none;transition:all 0.2s;" onmouseover="this.style.borderColor='var(--accent)';this.style.color='var(--accent)'" onmouseout="this.style.borderColor='var(--border2)';this.style.color='var(--muted)'">Try Scenarios →</a>
        <a href="/dashboard" class="btn-secondary-hero">Open Live Dashboard</a>
      </div>
    </div>
  </div>
</section>

<!-- ABOUT & CONTACT -->

<div class="section-divider"></div>

<!-- SUBSCRIBE -->
<section id="subscribe" style="padding:80px 0;">
  <div class="section-wrap">
    <div class="subscribe-card reveal">
      <div class="sub-left">
        <div class="sub-eyebrow">// Stay Informed</div>
        <h2 class="sub-title">Weekly Conjunction<br><em>Alert Digest</em></h2>
        <p class="sub-body">
          Get a weekly summary of the highest-risk conjunctions detected by VectraSpace —
          top Pc events, new debris cloud alerts, and orbital safety news. No spam, unsubscribe anytime.
        </p>
        <div class="sub-features">
          <div class="sub-feat"><span class="sub-feat-dot" style="background:var(--accent)"></span>Top 5 conjunctions of the week</div>
          <div class="sub-feat"><span class="sub-feat-dot" style="background:var(--green)"></span>New debris event notifications</div>
          <div class="sub-feat"><span class="sub-feat-dot" style="background:var(--amber)"></span>Satellite of the week deep dive</div>
        </div>
      </div>
      <div class="sub-right">
        <div class="sub-form-wrap" id="sub-form-wrap">
          <div class="sub-form-label">Enter your email address</div>
          <div class="sub-input-row">
            <input type="email" class="sub-input" id="sub-email" placeholder="you@example.com" autocomplete="email">
            <button class="sub-btn" id="sub-btn" onclick="submitSubscribe()">Subscribe</button>
          </div>
          <div class="sub-disclaimer">No spam · Sent every Monday · Unsubscribe any time</div>
          <div class="sub-msg" id="sub-msg"></div>
        </div>
        <div class="sub-success" id="sub-success" style="display:none;">
          <div class="sub-success-icon">✓</div>
          <div class="sub-success-title">You're subscribed!</div>
          <div class="sub-success-body">Look out for your first digest next Monday.</div>
        </div>
      </div>
    </div>
  </div>
</section>

<section id="contact" style="padding:100px 0; position:relative; z-index:1;">
  <div class="container" style="max-width:860px; margin:0 auto; padding:0 48px;">
    <div class="reveal" style="background:var(--panel); border:1px solid var(--border); border-radius:16px; overflow:hidden; position:relative;">
      <!-- top accent line -->
      <div style="height:2px; background:linear-gradient(90deg, var(--accent) 0%, var(--green) 50%, transparent 100%);"></div>
      <div style="padding:64px 72px;">
        <!-- eyebrow -->
        <div style="font-family:var(--mono); font-size:10px; letter-spacing:3px; color:var(--green); text-transform:uppercase; margin-bottom:20px;">About the Creator</div>

        <!-- avatar + bio row -->
        <div class="contact-bio-row" style="display:flex; gap:40px; align-items:flex-start; margin-bottom:52px; flex-wrap:wrap;">
          <!-- avatar placeholder -->
          <div style="flex-shrink:0; width:80px; height:80px; border-radius:50%; background:linear-gradient(135deg, var(--accent) 0%, var(--green) 100%); display:flex; align-items:center; justify-content:center; font-family:var(--serif); font-size:32px; color:#fff; letter-spacing:-1px; box-shadow:0 0 0 3px var(--border), 0 0 32px rgba(74,158,255,0.2);">T</div>

          <div style="flex:1; min-width:220px;">
            <div style="font-family:var(--serif); font-size:26px; color:#fff; font-weight:400; margin-bottom:6px; letter-spacing:-0.3px;">Truman Heaston</div>
            <div style="font-family:var(--mono); font-size:10px; letter-spacing:2px; color:var(--accent); text-transform:uppercase; margin-bottom:16px;">Builder · Student · Orbital Mechanics Nerd</div>
            <p style="font-size:15px; color:var(--muted); line-height:1.8; margin:0;">
              Passionate about space, orbital mechanics, and the belief that great education can change the world. VectraSpace started as a personal obsession — I wanted to understand the real math behind satellite conjunction events, so I built the platform I wished existed. The goal is to make high-stakes technical knowledge genuinely engaging, not just accessible.
            </p>
          </div>
        </div>

        <!-- divider -->
        <div style="height:1px; background:var(--border); margin-bottom:52px;"></div>

        <!-- contact heading -->
        <div style="font-family:var(--mono); font-size:10px; letter-spacing:3px; color:var(--green); text-transform:uppercase; margin-bottom:20px;">Get in Touch</div>
        <div style="font-family:var(--serif); font-size:clamp(22px,3vw,34px); color:#fff; font-weight:400; line-height:1.25; margin-bottom:16px; letter-spacing:-0.3px;">Have feedback, questions,<br>or want to collaborate?</div>
        <p style="font-size:15px; color:var(--muted); line-height:1.8; max-width:520px; margin-bottom:36px;">Whether you're a researcher, operator, educator, or fellow student — I'd genuinely love to hear from you. Technical critique, curriculum suggestions, partnership ideas — all of it is welcome.</p>

        <!-- email button -->
        <a href="mailto:trumanheaston@gmail.com"
           style="display:inline-flex; align-items:center; gap:10px; padding:14px 28px;
                  background:var(--accent); color:#fff; border-radius:6px;
                  font-family:var(--mono); font-size:11px; letter-spacing:2px; text-transform:uppercase;
                  text-decoration:none; transition:all 0.2s; font-weight:500;
                  box-shadow:0 4px 24px rgba(74,158,255,0.25);"
           onmouseover="this.style.background='#6ab4ff'; this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 32px rgba(74,158,255,0.4)';"
           onmouseout="this.style.background='var(--accent)'; this.style.transform=''; this.style.boxShadow='0 4px 24px rgba(74,158,255,0.25)';">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
          trumanheaston@gmail.com
        </a>

        <!-- background glow -->
        <div style="position:absolute; bottom:-80px; right:-80px; width:360px; height:360px; background:radial-gradient(ellipse, rgba(74,158,255,0.05) 0%, transparent 70%); pointer-events:none;"></div>
      </div>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer>
  <div class="footer-top">
    <div class="footer-brand">Vectra<em>Space</em></div>
    <ul class="footer-links">
      <li><a href="/education/orbital-mechanics">Orbital Mechanics</a></li>
      <li><a href="/education/collision-prediction">Collision Prediction</a></li>
      <li><a href="/education/perturbations">Perturbations</a></li>
      <li><a href="/education/debris-modeling">Debris Modeling</a></li>
      <li><a href="/dashboard">Dashboard</a></li>
      <li><a href="#contact">Contact</a></li>
    </ul>
    <div class="footer-contact">Built by Truman Heaston · <a href="mailto:trumanheaston@gmail.com">trumanheaston@gmail.com</a></div>
  </div>
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

// ── MOBILE NAV TOGGLE ────────────────────────────────────────
function toggleMobileNav() {
  const drawer = document.getElementById('mobile-nav');
  const btn    = document.getElementById('nav-hamburger');
  const isOpen = drawer.classList.contains('open');
  if (isOpen) {
    drawer.classList.remove('open');
    btn.classList.remove('open');
    setTimeout(() => { drawer.style.display = 'none'; }, 220);
  } else {
    drawer.style.display = 'flex';
    requestAnimationFrame(() => {
      drawer.classList.add('open');
      btn.classList.add('open');
    });
  }
}
document.querySelectorAll('#mobile-nav a').forEach(a => {
  a.addEventListener('click', () => {
    const drawer = document.getElementById('mobile-nav');
    const btn    = document.getElementById('nav-hamburger');
    drawer.classList.remove('open');
    btn.classList.remove('open');
    setTimeout(() => { drawer.style.display = 'none'; }, 220);
  });
});

// ── LIVE TLE TICKER ──────────────────────────────────────────
(async function loadTicker() {
  try {
    const res  = await fetch('/api/live-sats?limit=60&regime=LEO');
    const data = await res.json();
    if (!data.sats || data.sats.length === 0) throw new Error('empty');
    const track = document.getElementById('ticker-track');
    const html  = data.sats.map(s =>
      '<span class="ticker-sat"><div class="ticker-dot"></div>' +
      '<span class="t-name">' + s.name + '</span>' +
      '<span class="t-alt">' + Math.round(s.alt) + ' km</span></span>'
    ).join('');
    track.innerHTML = html + html; // duplicate for seamless loop
    const w = track.scrollWidth / 2;
    track.style.animationDuration = Math.max(30, w / 80) + 's';
    document.getElementById('ticker-status').textContent =
      data.count + ' sats · ' + (data.utc || '').slice(11, 16) + ' UTC';
  } catch(e) {
    // Static fallback so ticker always shows something
    const fallback = [
      'ISS (ZARYA)|418', 'STARLINK-1007|550', 'COSMOS 2251 DEB|789',
      'STARLINK-3004|553', 'SENTINEL-2A|786', 'TERRA|705', 'AQUA|709',
      'LANDSAT 8|705', 'NOAA 18|854', 'METOP-B|817', 'FENGYUN 3D|836',
      'SUOMI NPP|824', 'STARLINK-2488|548', 'ONEWEB-0012|1200',
      'IRIDIUM 33 DEB|782', 'COSMOS 1408 DEB|760',
    ];
    const track = document.getElementById('ticker-track');
    const html  = fallback.map(s => {
      const [name, alt] = s.split('|');
      return '<span class="ticker-sat"><div class="ticker-dot" style="background:var(--faint)"></div>' +
             '<span class="t-name">' + name + '</span>' +
             '<span class="t-alt">' + alt + ' km</span></span>';
    }).join('');
    track.innerHTML = html + html;
    document.getElementById('ticker-status').textContent = 'Sample data';
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


// ── EMAIL SUBSCRIBE ───────────────────────────────────────────
async function submitSubscribe() {
  const emailEl = document.getElementById('sub-email');
  const btn     = document.getElementById('sub-btn');
  const msgEl   = document.getElementById('sub-msg');
  const email   = emailEl.value.trim();
  if (!email || !/^[^@]+@[^@]+\.[^@]+$/.test(email)) {
    msgEl.style.color = 'var(--red)'; msgEl.textContent = 'Please enter a valid email address.'; return;
  }
  btn.disabled = true; btn.textContent = 'Subscribing…'; msgEl.textContent = '';
  try {
    const r = await fetch('/subscribe', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ email }) });
    const data = await r.json();
    if (r.ok) {
      document.getElementById('sub-form-wrap').style.display = 'none';
      document.getElementById('sub-success').style.display = 'block';
    } else {
      msgEl.style.color = 'var(--red)'; msgEl.textContent = data.detail || 'Something went wrong.';
      btn.disabled = false; btn.textContent = 'Subscribe';
    }
  } catch(e) {
    msgEl.style.color = 'var(--red)'; msgEl.textContent = 'Network error — please try again.';
    btn.disabled = false; btn.textContent = 'Subscribe';
  }
}

// ── CHAPTER PROGRESS ──────────────────────────────────────────
(function() {
  const CHAPTERS = [
    { id: 'chcard-1', key: 'vs_ch1_done' },
    { id: 'chcard-2', key: 'vs_ch2_done' },
    { id: 'chcard-3', key: 'vs_ch3_done' },
    { id: 'chcard-4', key: 'vs_ch4_done' },
  ];
  let completed = 0;
  CHAPTERS.forEach(({ id, key }) => {
    try {
      if (localStorage.getItem(key) === '1') {
        const card = document.getElementById(id);
        if (card) { card.classList.add('completed'); completed++; }
      }
    } catch(e) {}
  });
  if (completed > 0) {
    const strip = document.getElementById('learn-progress-strip');
    const fill  = document.getElementById('lps-fill');
    const text  = document.getElementById('lps-text');
    if (strip) strip.classList.add('show');
    if (fill)  setTimeout(() => fill.style.width = (completed/4*100) + '%', 100);
    if (text)  text.textContent = completed + ' / 4 Chapters';
  }
})();

</script>
</body>
</html>

"""

# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 7 — REST API + SSE RUN ENDPOINT                      ║
# ╚══════════════════════════════════════════════════════════════╝

SCENARIOS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>Interactive Scenarios — VectraSpace</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:wght@400;500&family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#080c12;--ink2:#0d1320;--ink3:#111d2e;
  --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.13);
  --text:#ccd6e0;--muted:#8aaac5;--faint:#2a3d50;
  --accent:#4a9eff;--green:#34d399;--amber:#f59e0b;--red:#f87171;--purple:#a78bfa;
  --serif:'Instrument Serif',serif;--mono:'DM Mono',monospace;--sans:'Outfit',sans-serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html,body{background:var(--ink);color:var(--text);font-family:var(--sans);overflow:hidden;width:100%;height:100%;}

#topbar{position:fixed;top:0;left:0;right:0;z-index:100;height:52px;padding:0 20px;display:flex;align-items:center;justify-content:space-between;background:rgba(8,12,18,0.96);border-bottom:1px solid var(--border);backdrop-filter:blur(12px);}
.tb-brand{font-family:var(--serif);font-size:16px;font-style:italic;color:#fff;text-decoration:none;}
.tb-brand em{color:var(--accent);font-style:normal;}
.tb-links{display:flex;gap:4px;}
.tb-link{font-family:var(--mono);font-size:9px;letter-spacing:1px;color:var(--muted);text-decoration:none;padding:5px 12px;border:1px solid transparent;border-radius:4px;transition:all 0.15s;}
.tb-link:hover,.tb-link.active{border-color:var(--border2);color:var(--text);}
.tb-link.active{border-color:rgba(74,158,255,0.4);color:var(--accent);background:rgba(74,158,255,0.06);}

#app{display:flex;height:100vh;padding-top:52px;}
#canvas-wrap{flex:1;position:relative;overflow:hidden;}
#three-canvas{display:block;width:100%;height:100%;}

/* SCENARIO SELECTOR */
#scenario-bar{position:absolute;top:16px;left:50%;transform:translateX(-50%);z-index:50;display:flex;gap:8px;background:rgba(8,12,18,0.9);border:1px solid var(--border);border-radius:8px;padding:8px;}
.sc-btn{font-family:var(--mono);font-size:9px;letter-spacing:1px;padding:7px 16px;border-radius:5px;border:1px solid var(--border);color:var(--muted);background:transparent;cursor:pointer;transition:all 0.15s;text-transform:uppercase;}
.sc-btn:hover{border-color:var(--accent);color:var(--accent);}
.sc-btn.active{background:rgba(74,158,255,0.12);border-color:var(--accent);color:var(--accent);}

/* PLAYBACK BAR */
#playback{position:absolute;bottom:0;left:0;right:0;z-index:50;background:rgba(8,12,18,0.92);border-top:1px solid var(--border);padding:12px 20px 16px;backdrop-filter:blur(8px);}
.pb-top{display:flex;align-items:center;gap:12px;margin-bottom:10px;}
.pb-title{font-family:var(--serif);font-size:16px;font-style:italic;color:#fff;flex:1;}
.pb-time{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:1px;}
.pb-btns{display:flex;gap:8px;}
.pb-btn{width:34px;height:34px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--muted);font-size:14px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all 0.15s;}
.pb-btn:hover{border-color:var(--accent);color:var(--accent);}
.pb-btn.active{background:rgba(74,158,255,0.12);border-color:var(--accent);color:var(--accent);}
#pb-progress{width:100%;height:4px;background:var(--border2);border-radius:2px;cursor:pointer;position:relative;}
#pb-fill{height:100%;background:var(--accent);border-radius:2px;width:0%;transition:width 0.05s linear;pointer-events:none;}
#pb-scrubber{position:absolute;top:50%;transform:translateY(-50%);width:12px;height:12px;border-radius:50%;background:var(--accent);cursor:grab;left:0%;margin-left:-6px;}

/* INFO OVERLAY */
#info-overlay{position:absolute;top:76px;left:20px;z-index:50;max-width:320px;}
.io-card{background:rgba(8,12,18,0.9);border:1px solid var(--border);border-radius:8px;padding:16px 18px;backdrop-filter:blur(8px);margin-bottom:10px;}
.io-eyebrow{font-family:var(--mono);font-size:8px;letter-spacing:2px;text-transform:uppercase;color:var(--accent);margin-bottom:6px;}
.io-title{font-family:var(--serif);font-size:17px;color:#fff;margin-bottom:6px;}
.io-body{font-size:12px;color:var(--muted);line-height:1.65;}
.io-body strong{color:var(--text);}
.io-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px;}
.io-stat{background:var(--ink3);border:1px solid var(--border);border-radius:5px;padding:8px 10px;}
.io-stat-val{font-family:var(--mono);font-size:13px;color:var(--text);}
.io-stat-lbl{font-family:var(--mono);font-size:8px;color:var(--faint);letter-spacing:1px;text-transform:uppercase;margin-top:2px;}

/* FRAGMENT COUNTER */
#frag-counter{position:absolute;top:76px;right:20px;z-index:50;background:rgba(8,12,18,0.9);border:1px solid var(--border);border-radius:8px;padding:14px 18px;backdrop-filter:blur(8px);text-align:center;min-width:120px;}
.fc-val{font-family:var(--serif);font-size:32px;font-style:italic;color:var(--red);line-height:1;}
.fc-lbl{font-family:var(--mono);font-size:8px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-top:4px;}

/* TIMELINE EVENTS */
#timeline-events{position:absolute;bottom:80px;left:20px;right:20px;pointer-events:none;}
.te-badge{position:absolute;transform:translateX(-50%);font-family:var(--mono);font-size:8px;letter-spacing:1px;padding:4px 10px;border-radius:4px;white-space:nowrap;opacity:0;transition:opacity 0.3s;}
.te-badge.show{opacity:1;}

/* MOBILE */
@media(max-width:700px){
  #scenario-bar{top:8px;gap:4px;padding:6px;}
  .sc-btn{font-size:8px;padding:5px 10px;}
  #info-overlay{max-width:200px;}
  .io-card{padding:12px 14px;}
  .tb-link:not(.back-link){display:none;}
}
</style>
</head>
<body>
<div id="topbar">
  <a href="/" class="tb-brand">Vectra<em>Space</em></a>
  <div class="tb-links">
    <a href="/" class="tb-link back-link">← Hub</a>
    <a href="/kepler" class="tb-link">Kepler Explorer</a>
    <a href="/scenarios" class="tb-link active">Scenarios</a>
    <a href="/calculator" class="tb-link">Calculator</a>
    <a href="/glossary" class="tb-link">News</a>
  </div>
</div>

<div id="app">
  <div id="canvas-wrap">
    <canvas id="three-canvas"></canvas>

    <div id="scenario-bar">
      <button class="sc-btn active" onclick="loadScenario('iridium')">Iridium-Cosmos</button>
      <button class="sc-btn" onclick="loadScenario('kessler')">Kessler Cascade</button>
      <button class="sc-btn" onclick="loadScenario('fy1c')">FY-1C ASAT</button>
      <button class="sc-btn" onclick="loadScenario('maneuver')">Avoidance Maneuver</button>
    </div>

    <div id="info-overlay">
      <div class="io-card" id="io-main">
        <div class="io-eyebrow" id="io-eyebrow">Feb 10, 2009 · 789 km</div>
        <div class="io-title" id="io-title">Iridium 33 ↔ Cosmos 2251</div>
        <div class="io-body" id="io-body">The first accidental hypervelocity collision between two intact satellites. Both were destroyed, generating <strong>~2,300 trackable fragments</strong> — many still orbit today.</div>
        <div class="io-stats" id="io-stats">
          <div class="io-stat"><div class="io-stat-val" id="is-v">11.7</div><div class="io-stat-lbl">km/s rel. vel.</div></div>
          <div class="io-stat"><div class="io-stat-val" id="is-alt">789</div><div class="io-stat-lbl">km altitude</div></div>
          <div class="io-stat"><div class="io-stat-val" id="is-m1">560</div><div class="io-stat-lbl">Iridium mass (kg)</div></div>
          <div class="io-stat"><div class="io-stat-val" id="is-m2">900</div><div class="io-stat-lbl">Cosmos mass (kg)</div></div>
        </div>
      </div>
    </div>

    <div id="frag-counter">
      <div class="fc-val" id="fc-val">0</div>
      <div class="fc-lbl" id="fc-lbl">Fragments</div>
    </div>

    <div id="playback">
      <div class="pb-top">
        <div class="pb-title" id="pb-title">Iridium-Cosmos Collision Simulation</div>
        <div class="pb-time" id="pb-time">T+00:00</div>
        <div class="pb-btns">
          <button class="pb-btn" id="btn-restart" onclick="restart()" title="Restart">↺</button>
          <button class="pb-btn active" id="btn-play" onclick="togglePlay()" title="Play/Pause">⏸</button>
          <button class="pb-btn" id="btn-speed" onclick="cycleSpeed()" title="Speed">1×</button>
        </div>
      </div>
      <div id="pb-progress" onclick="scrubTo(event)">
        <div id="pb-fill"></div>
        <div id="pb-scrubber"></div>
      </div>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
// ══ THREE.JS SETUP ════════════════════════════════════════════
const canvas  = document.getElementById('three-canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x080c12, 1);

const scene  = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 800000);

// Earth
const R_E = 6371;
const earthMesh = new THREE.Mesh(
  new THREE.SphereGeometry(R_E, 48, 48),
  new THREE.MeshPhongMaterial({ color: 0x1a3a6a, emissive: 0x0a1a3a, shininess: 5 })
);
scene.add(earthMesh);

// Grid overlay
const gMat = new THREE.LineBasicMaterial({ color: 0x1a3a6a, transparent: true, opacity: 0.3 });
for (let la = -80; la <= 80; la += 20) {
  const pts = [];
  for (let lo = 0; lo <= 360; lo += 6) {
    const r=R_E+5, lR=la*Math.PI/180, oR=lo*Math.PI/180;
    pts.push(new THREE.Vector3(r*Math.cos(lR)*Math.cos(oR), r*Math.sin(lR), r*Math.cos(lR)*Math.sin(oR)));
  }
  scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), gMat));
}
for (let lo = 0; lo < 360; lo += 30) {
  const pts = [];
  for (let la = -90; la <= 90; la += 6) {
    const r=R_E+5, lR=la*Math.PI/180, oR=lo*Math.PI/180;
    pts.push(new THREE.Vector3(r*Math.cos(lR)*Math.cos(oR), r*Math.sin(lR), r*Math.cos(lR)*Math.sin(oR)));
  }
  scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), gMat));
}

// Atmosphere
scene.add(new THREE.Mesh(
  new THREE.SphereGeometry(R_E * 1.04, 32, 32),
  new THREE.MeshPhongMaterial({ color: 0x2a5f9f, transparent: true, opacity: 0.07, side: THREE.FrontSide })
));

// Stars
const sPos = [];
for (let i = 0; i < 2000; i++) sPos.push((Math.random()-.5)*600000, (Math.random()-.5)*600000, (Math.random()-.5)*600000);
const sGeo = new THREE.BufferGeometry();
sGeo.setAttribute('position', new THREE.Float32BufferAttribute(sPos, 3));
scene.add(new THREE.Points(sGeo, new THREE.PointsMaterial({ color: 0xffffff, size: 80, transparent: true, opacity: 0.5 })));

// Lights
scene.add(new THREE.AmbientLight(0x223355, 1.2));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(50000, 20000, 30000);
scene.add(sun);

// ══ ORBIT MATH ═══════════════════════════════════════════════
const MU = 398600.4418;
function orbitPoints(a, e, iDeg, ODeg, wDeg, N=180) {
  const pts=[], iR=iDeg*Math.PI/180, OR=ODeg*Math.PI/180, wR=wDeg*Math.PI/180;
  for (let k=0; k<=N; k++) {
    const nu=(k/N)*2*Math.PI;
    const p=a*(1-e*e), r=p/(1+e*Math.cos(nu));
    const xp=r*Math.cos(nu), yp=r*Math.sin(nu);
    const cosO=Math.cos(OR), sinO=Math.sin(OR), ci=Math.cos(iR), si=Math.sin(iR), cw=Math.cos(wR), sw=Math.sin(wR);
    pts.push(new THREE.Vector3(
      (cosO*cw-sinO*sw*ci)*xp+(-cosO*sw-sinO*cw*ci)*yp,
      (si*sw)*xp+(si*cw)*yp,
      (sinO*cw+cosO*sw*ci)*xp+(-sinO*sw+cosO*cw*ci)*yp
    ));
  }
  return pts;
}
function satPos(a, e, iDeg, ODeg, wDeg, nuDeg) {
  const pts = orbitPoints(a, e, iDeg, ODeg, wDeg, 1);
  // recompute at nuDeg
  const nu=nuDeg*Math.PI/180, iR=iDeg*Math.PI/180, OR=ODeg*Math.PI/180, wR=wDeg*Math.PI/180;
  const p=a*(1-e*e), r=p/(1+e*Math.cos(nu));
  const xp=r*Math.cos(nu), yp=r*Math.sin(nu);
  const cosO=Math.cos(OR), sinO=Math.sin(OR), ci=Math.cos(iR), si=Math.sin(iR), cw=Math.cos(wR), sw=Math.sin(wR);
  return new THREE.Vector3(
    (cosO*cw-sinO*sw*ci)*xp+(-cosO*sw-sinO*cw*ci)*yp,
    (si*sw)*xp+(si*cw)*yp,
    (sinO*cw+cosO*sw*ci)*xp+(-sinO*sw+cosO*cw*ci)*yp
  );
}

// ══ SCENE OBJECTS ═════════════════════════════════════════════
let sceneGroup = new THREE.Group();
scene.add(sceneGroup);
let fragments = [];
let animState = {};
let playing = true, speed = 1, t = 0, tMax = 1;
const speeds = [0.5, 1, 2, 4];
let speedIdx = 1;

function clearScene() {
  sceneGroup.clear();
  fragments = [];
}

function mkLine(pts, color, opacity=1) {
  const mat = new THREE.LineBasicMaterial({ color, transparent: opacity<1, opacity });
  return new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat);
}
function mkSphere(r, color, emissive=0) {
  return new THREE.Mesh(new THREE.SphereGeometry(r,12,12), new THREE.MeshPhongMaterial({ color, emissive }));
}

// ══ SCENARIOS ════════════════════════════════════════════════
const SCENARIOS = {
  iridium: {
    title: 'Iridium-Cosmos Collision Simulation',
    eyebrow: 'Feb 10, 2009 · 789 km',
    heading: 'Iridium 33 ↔ Cosmos 2251',
    body: 'The first accidental hypervelocity collision between two intact satellites. Both were destroyed, generating <strong>~2,300 trackable fragments</strong> — many still orbit today.',
    stats: { v:'11.7', alt:'789', m1:'560', m2:'900', l1:'km/s rel. vel.', l2:'km altitude', l3:'Iridium mass (kg)', l4:'Cosmos mass (kg)' },
    fcLabel: 'Trackable Fragments',
    totalFrags: 2300,
    camera: { pos: [0, 8000, 22000], lookAt: [0, 0, 0] },
    build() {
      clearScene();
      // Iridium orbit (a=7160 km, i=86.4°)
      const iriPts = orbitPoints(7160, 0.001, 86.4, 340, 0);
      sceneGroup.add(mkLine(iriPts, 0x4a9eff, 0.6));
      // Cosmos orbit (a=7159 km, i=74.0°, retrograde)
      const cosPts = orbitPoints(7159, 0.001, 74.0, 155, 0);
      sceneGroup.add(mkLine(cosPts, 0xf87171, 0.6));

      // sat meshes (start positions nu=~270)
      const iriSat = mkSphere(100, 0x4a9eff, 0x103060);
      const cosSat = mkSphere(100, 0xf87171, 0x601010);
      const collPt = satPos(7160, 0.001, 86.4, 340, 0, 270);
      iriSat.position.copy(satPos(7160, 0.001, 86.4, 340, 0, 180));
      cosSat.position.copy(satPos(7159, 0.001, 74.0, 155, 0, 90));
      sceneGroup.add(iriSat); sceneGroup.add(cosSat);

      // Collision point marker (appears at t=0.45)
      const flashGeo = new THREE.SphereGeometry(350, 16, 16);
      const flashMat = new THREE.MeshPhongMaterial({ color: 0xffaa00, emissive: 0xff6600, transparent: true, opacity: 0 });
      const flash = new THREE.Mesh(flashGeo, flashMat);
      flash.position.copy(collPt);
      sceneGroup.add(flash);

      // Build debris particles
      const NFRAG = 400;
      const fragGeo = new THREE.BufferGeometry();
      const fragPos = new Float32Array(NFRAG * 3);
      fragGeo.setAttribute('position', new THREE.BufferAttribute(fragPos, 3));
      const fragMat = new THREE.PointsMaterial({ color: 0xff8844, size: 50, transparent: true, opacity: 0 });
      const fragPoints = new THREE.Points(fragGeo, fragMat);
      sceneGroup.add(fragPoints);

      // Collision ring (expanding ring at impact)
      const ringGeo = new THREE.RingGeometry(100, 160, 32);
      const ringMat = new THREE.MeshBasicMaterial({ color: 0xff8844, transparent: true, opacity: 0, side: THREE.DoubleSide });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.position.copy(collPt);
      ring.lookAt(0, 1, 0);
      sceneGroup.add(ring);

      // Store fragment velocity vectors
      fragments = [];
      for (let k = 0; k < NFRAG; k++) {
        const phi = Math.random() * Math.PI * 2;
        const theta2 = Math.acos(2 * Math.random() - 1);
        const spd = 300 + Math.random() * 2000;  // m/s dispersion
        fragments.push({
          vx: Math.sin(theta2)*Math.cos(phi)*spd,
          vy: Math.cos(theta2)*spd,
          vz: Math.sin(theta2)*Math.sin(phi)*spd,
        });
      }

      animState = {
        iriSat, cosSat, flash, flashMat, fragPoints, fragPos, fragMat, ring, ringMat, ringGeo,
        collPt, NFRAG,
      };
    },
    tick(t) {
      const s = animState;
      if (!s.iriSat) return;
      // Phase 0→0.4: satellites approach
      const approach = Math.min(t / 0.4, 1.0);
      const iriNu  = 180 + approach * 90;
      const cosNu  = 90  + approach * 180;
      s.iriSat.position.copy(satPos(7160, 0.001, 86.4, 340, 0, iriNu));
      s.cosSat.position.copy(satPos(7159, 0.001, 74.0, 155, 0, cosNu));

      // Phase 0.4→0.5: collision flash
      const flashT = Math.max(0, Math.min((t - 0.4) / 0.1, 1));
      s.flashMat.opacity = flashT < 0.5 ? flashT * 2 : (1 - flashT) * 2;

      // Phase 0.45+: fragments expand
      if (t > 0.45) {
        const fT = (t - 0.45) * 3.0;
        s.iriSat.visible = false; s.cosSat.visible = false;
        s.fragMat.opacity = Math.min(fT * 3, 0.8);
        for (let k = 0; k < s.NFRAG; k++) {
          const f = fragments[k];
          s.fragPos[k*3]   = s.collPt.x + f.vx * fT * 0.8;
          s.fragPos[k*3+1] = s.collPt.y + f.vy * fT * 0.8;
          s.fragPos[k*3+2] = s.collPt.z + f.vz * fT * 0.8;
        }
        s.fragPoints.geometry.attributes.position.needsUpdate = true;
        // Ring expand
        s.ring.scale.setScalar(1 + fT * 6);
        s.ringMat.opacity = Math.max(0, 0.5 - fT * 0.5);
      } else {
        s.iriSat.visible = true; s.cosSat.visible = true;
        s.fragMat.opacity = 0;
      }

      // Fragment counter
      const fragCount = t > 0.45 ? Math.round(Math.min((t - 0.45) / 0.4 * 2300, 2300)) : 0;
      document.getElementById('fc-val').textContent = fragCount.toLocaleString();
    }
  },

  kessler: {
    title: 'Kessler Cascade — Runaway Debris Chain',
    eyebrow: 'Hypothetical · 800–1000 km',
    heading: 'The Kessler Syndrome',
    body: 'Each collision generates debris that causes more collisions. Above a critical density, the cascade becomes <strong>self-sustaining and irreversible</strong>. This simulation shows the exponential growth in fragment count.',
    stats: { v:'9–11', alt:'900', m1:'2', m2:'events', l1:'km/s avg', l2:'km altitude', l3:'cascade', l4:'collisions' },
    fcLabel: 'Total Fragments',
    totalFrags: 15000,
    camera: { pos: [0, 10000, 28000], lookAt: [0, 0, 0] },
    build() {
      clearScene();
      // 6 initial satellite orbits at slightly different inclinations
      const sats = [];
      const orbitDefs = [
        { a:7180, e:0.001, i:82, O:0   },
        { a:7200, e:0.001, i:86, O:60  },
        { a:7160, e:0.001, i:78, O:120 },
        { a:7220, e:0.001, i:94, O:180 },
        { a:7190, e:0.001, i:90, O:240 },
        { a:7170, e:0.001, i:72, O:300 },
      ];
      const colors = [0x4a9eff, 0x34d399, 0xa78bfa, 0xf59e0b, 0xf87171, 0x67e8f9];
      orbitDefs.forEach((o, idx) => {
        const pts = orbitPoints(o.a, o.e, o.i, o.O, 0);
        sceneGroup.add(mkLine(pts, colors[idx], 0.4));
        const s = mkSphere(90, colors[idx], 0);
        s.position.copy(satPos(o.a, o.e, o.i, o.O, 0, idx * 60));
        sceneGroup.add(s);
        sats.push({ ...o, mesh: s, nu: idx * 60 });
      });

      // Debris cloud particles (grows over time)
      const MAXFRAG = 1200;
      const fragGeo = new THREE.BufferGeometry();
      const fragPos = new Float32Array(MAXFRAG * 3);
      // start all at center far away
      for (let i = 0; i < MAXFRAG * 3; i++) fragPos[i] = 999999;
      fragGeo.setAttribute('position', new THREE.BufferAttribute(fragPos, 3));
      const fragMat = new THREE.PointsMaterial({ color: 0xff6644, size: 35, transparent: true, opacity: 0.6 });
      const fragPoints = new THREE.Points(fragGeo, fragMat);
      sceneGroup.add(fragPoints);

      // Collision flash meshes (one per event)
      const flashes = [];
      const flashPositions = [
        satPos(7180, 0.001, 82, 0, 0, 130),
        satPos(7200, 0.001, 86, 60, 0, 220),
        satPos(7190, 0.001, 90, 240, 0, 310),
        satPos(7170, 0.001, 72, 300, 0, 45),
      ];
      flashPositions.forEach(p => {
        const f = mkSphere(280, 0xff8800, 0xff4400);
        f.material.transparent = true; f.material.opacity = 0;
        f.position.copy(p);
        sceneGroup.add(f);
        flashes.push(f);
      });

      animState = { sats, fragPoints, fragPos, fragMat, flashes, flashPositions, MAXFRAG };
      fragments = Array.from({ length: MAXFRAG }, () => ({
        vx: (Math.random()-.5)*2000, vy: (Math.random()-.5)*2000, vz: (Math.random()-.5)*2000,
        spawnT: Math.random(),
        baseX: flashPositions[Math.floor(Math.random()*flashPositions.length)].x,
        baseY: flashPositions[Math.floor(Math.random()*flashPositions.length)].y,
        baseZ: flashPositions[Math.floor(Math.random()*flashPositions.length)].z,
      }));
    },
    tick(t) {
      const s = animState;
      if (!s.sats) return;
      // Move satellites
      s.sats.forEach((sat, i) => {
        sat.nu = (sat.nu + 0.4 * (1 + i*0.05)) % 360;
        sat.mesh.position.copy(satPos(sat.a, sat.e, sat.i, sat.O, 0, sat.nu));
      });
      // Cascade flashes
      const eventTimes = [0.15, 0.35, 0.55, 0.75];
      s.flashes.forEach((f, i) => {
        const dt = t - eventTimes[i];
        if (dt > 0 && dt < 0.12) {
          f.material.opacity = dt < 0.06 ? dt/0.06 : (0.12-dt)/0.06;
        } else {
          f.material.opacity = 0;
        }
      });
      // Grow debris cloud
      let activeFrags = 0;
      fragments.forEach((frag, k) => {
        if (t > frag.spawnT) {
          const age = (t - frag.spawnT) * 1.5;
          s.fragPos[k*3]   = frag.baseX + frag.vx * age;
          s.fragPos[k*3+1] = frag.baseY + frag.vy * age;
          s.fragPos[k*3+2] = frag.baseZ + frag.vz * age;
          activeFrags++;
        }
      });
      s.fragPoints.geometry.attributes.position.needsUpdate = true;
      const count = Math.round(t * 15000);
      document.getElementById('fc-val').textContent = count.toLocaleString();
      // Hide sats progressively after first collision
      if (t > 0.2) { s.sats[0].mesh.visible = false; s.sats[1].mesh.visible = false; }
      if (t > 0.4) { s.sats[2].mesh.visible = false; }
      if (t > 0.6) { s.sats[3].mesh.visible = false; }
    }
  },

  fy1c: {
    title: 'FY-1C ASAT Strike — Jan 11, 2007',
    eyebrow: 'Deliberate · 863 km · China',
    heading: 'FY-1C Anti-Satellite Test',
    body: 'China destroyed its own Fengyun-1C weather satellite using a direct-ascent kinetic kill vehicle. Created <strong>3,500+ trackable fragments</strong> — the worst single debris-generating event ever.',
    stats: { v:'9.0', alt:'863', m1:'750', m2:'300', l1:'km/s rel. vel.', l2:'km altitude', l3:'FY-1C mass (kg)', l4:'KKV mass (est., kg)' },
    fcLabel: 'Debris Objects Created',
    totalFrags: 3500,
    camera: { pos: [0, 9000, 24000], lookAt: [0, 0, 0] },
    build() {
      clearScene();
      // FY-1C polar orbit
      const fyPts = orbitPoints(7234, 0.001, 98.8, 200, 0);
      sceneGroup.add(mkLine(fyPts, 0x34d399, 0.6));
      // KKV trajectory (direct ascent from ground, simplified as inclined arc)
      const kkvPts = [];
      for (let k = 0; k <= 60; k++) {
        const frac = k / 60;
        const alt = R_E + 200 + (863 - 200) * frac;
        const lng = 100 + frac * 10; // rough path
        const lat = 28 + frac * 70;
        const lR = lat*Math.PI/180, oR = lng*Math.PI/180;
        kkvPts.push(new THREE.Vector3(alt*Math.cos(lR)*Math.cos(oR), alt*Math.sin(lR), alt*Math.cos(lR)*Math.sin(oR)));
      }
      sceneGroup.add(mkLine(kkvPts, 0xf87171, 0.7));

      const fy1c = mkSphere(110, 0x34d399, 0x106030);
      const kkv  = mkSphere(60, 0xf87171, 0x601010);
      const collPt = satPos(7234, 0.001, 98.8, 200, 0, 260);
      fy1c.position.copy(satPos(7234, 0.001, 98.8, 200, 0, 180));
      kkv.position.copy(kkvPts[0]);
      sceneGroup.add(fy1c); sceneGroup.add(kkv);

      const flash = mkSphere(400, 0xffaa00, 0xff6600);
      flash.material.transparent = true; flash.material.opacity = 0;
      flash.position.copy(collPt);
      sceneGroup.add(flash);

      const NFRAG = 500;
      const fragGeo = new THREE.BufferGeometry();
      const fragPos = new Float32Array(NFRAG * 3);
      for (let i = 0; i < NFRAG*3; i++) fragPos[i] = 999999;
      fragGeo.setAttribute('position', new THREE.BufferAttribute(fragPos, 3));
      const fragMat = new THREE.PointsMaterial({ color: 0xff8844, size: 45, transparent: true, opacity: 0 });
      const fragPoints = new THREE.Points(fragGeo, fragMat);
      sceneGroup.add(fragPoints);

      fragments = Array.from({ length: NFRAG }, () => {
        const phi=Math.random()*Math.PI*2, th=Math.acos(2*Math.random()-1);
        const spd = 400 + Math.random() * 3000;
        return { vx:Math.sin(th)*Math.cos(phi)*spd, vy:Math.cos(th)*spd, vz:Math.sin(th)*Math.sin(phi)*spd };
      });

      animState = { fy1c, kkv, flash, kkvPts, collPt, fragPoints, fragPos, fragMat, NFRAG };
    },
    tick(t) {
      const s = animState;
      if (!s.fy1c) return;
      const fy1cNu = 180 + t * 0.4 * 80;
      s.fy1c.position.copy(satPos(7234, 0.001, 98.8, 200, 0, fy1cNu));
      // KKV rises
      const kkvIdx = Math.floor(Math.min(t / 0.5, 0.99) * 59);
      s.kkv.position.copy(s.kkvPts[kkvIdx]);
      // Flash at t=0.5
      const flashT = Math.max(0, Math.min((t - 0.5) / 0.1, 1));
      s.flash.material.opacity = flashT < 0.5 ? flashT * 2 : (1 - flashT) * 2;
      if (t > 0.52) {
        s.fy1c.visible = false; s.kkv.visible = false;
        s.fragMat.opacity = Math.min((t - 0.52) * 3, 0.85);
        const fT = (t - 0.52) * 2;
        for (let k = 0; k < s.NFRAG; k++) {
          const f = fragments[k];
          s.fragPos[k*3]   = s.collPt.x + f.vx * fT;
          s.fragPos[k*3+1] = s.collPt.y + f.vy * fT;
          s.fragPos[k*3+2] = s.collPt.z + f.vz * fT;
        }
        s.fragPoints.geometry.attributes.position.needsUpdate = true;
      } else {
        s.fy1c.visible = true; s.kkv.visible = true;
        s.fragMat.opacity = 0;
      }
      document.getElementById('fc-val').textContent = t > 0.52 ? Math.round(Math.min((t-0.52)/0.4*3500, 3500)).toLocaleString() : '0';
    }
  },

  maneuver: {
    title: 'Conjunction Avoidance Maneuver',
    eyebrow: 'Operational · 400 km · LEO',
    heading: 'Avoidance Delta-V',
    body: 'When Pc exceeds 1×10⁻⁴, operators execute a small maneuver to change their orbit. Even <strong>0.1 m/s Δv</strong> is enough to move several kilometers in 2 hours.',
    stats: { v:'0.10', alt:'400', m1:'1e-4', m2:'0.1', l1:'Δv (m/s)', l2:'km altitude', l3:'Pc threshold', l4:'m/s burn' },
    fcLabel: 'Miss Distance (km)',
    totalFrags: 12,
    camera: { pos: [0, 7000, 18000], lookAt: [0, 0, 0] },
    build() {
      clearScene();
      // Primary satellite orbit (ISS-like)
      const origPts = orbitPoints(6778, 0.001, 51.6, 0, 0);
      sceneGroup.add(mkLine(origPts, 0x4a9eff, 0.5));
      // Post-maneuver orbit (slightly higher)
      const manPts = orbitPoints(6790, 0.001, 51.6, 0, 0);
      const manLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints(manPts),
        new THREE.LineDashedMaterial({ color: 0x34d399, dashSize: 200, gapSize: 100, transparent: true, opacity: 0 }));
      manLine.computeLineDistances();
      sceneGroup.add(manLine);
      // Debris orbit (crossing)
      const debrisPts = orbitPoints(6780, 0.001, 68.5, 20, 45);
      sceneGroup.add(mkLine(debrisPts, 0xf87171, 0.4));

      const sat   = mkSphere(90, 0x4a9eff, 0x103060);
      const debris = mkSphere(60, 0xf87171, 0x601010);
      const conjPt = satPos(6778, 0.001, 51.6, 0, 0, 310);

      // Warning ring at conjunction point
      const warnGeo = new THREE.RingGeometry(200, 320, 32);
      const warnMat = new THREE.MeshBasicMaterial({ color: 0xf59e0b, transparent: true, opacity: 0, side: THREE.DoubleSide });
      const warnRing = new THREE.Mesh(warnGeo, warnMat);
      warnRing.position.copy(conjPt);
      warnRing.lookAt(0, 0, 0);
      sceneGroup.add(warnRing);

      sat.position.copy(satPos(6778, 0.001, 51.6, 0, 0, 180));
      debris.position.copy(satPos(6780, 0.001, 68.5, 20, 45, 180));
      sceneGroup.add(sat); sceneGroup.add(debris);

      animState = { sat, debris, manLine, warnRing, warnMat, conjPt, maneuvered: false };
    },
    tick(t) {
      const s = animState;
      if (!s.sat) return;
      // Phase 0-0.4: normal orbit, warning ring pulses
      const warnT = Math.min(t / 0.4, 1);
      s.warnMat.opacity = warnT * 0.6 * (0.5 + 0.5 * Math.sin(t * 20));

      const satNu  = 180 + t * 0.4 * 130;
      const debNu  = 180 + t * 0.4 * 110;
      const manNu  = 180 + t * 0.4 * 130;

      if (t < 0.5) {
        s.sat.position.copy(satPos(6778, 0.001, 51.6, 0, 0, satNu));
        s.manLine.material.opacity = 0;
      } else {
        // After maneuver: track higher orbit
        s.sat.position.copy(satPos(6790, 0.001, 51.6, 0, 0, manNu));
        s.manLine.material.opacity = Math.min((t - 0.5) * 5, 0.7);
        s.warnMat.opacity = Math.max(0, 0.6 - (t - 0.5) * 2);
      }
      s.debris.position.copy(satPos(6780, 0.001, 68.5, 20, 45, debNu));

      // Miss distance (km)
      const miss = t < 0.5 ? Math.max(0.1, 3.2 - t * 4) : 0.1 + (t - 0.5) * 28;
      document.getElementById('fc-val').textContent = miss.toFixed(1);
    }
  }
};

// ══ PLAYBACK CONTROLS ════════════════════════════════════════
function loadScenario(key) {
  const sc = SCENARIOS[key];
  document.querySelectorAll('.sc-btn').forEach(b => b.classList.toggle('active', b.textContent.trim().replace(/\s+/g,' ') === {
    iridium:'Iridium-Cosmos', kessler:'Kessler Cascade', fy1c:'FY-1C ASAT', maneuver:'Avoidance Maneuver'
  }[key]));
  document.getElementById('pb-title').textContent = sc.title;
  document.getElementById('io-eyebrow').textContent = sc.eyebrow;
  document.getElementById('io-title').textContent = sc.heading;
  document.getElementById('io-body').innerHTML = sc.body;
  document.getElementById('is-v').textContent  = sc.stats.v;
  document.getElementById('is-alt').textContent = sc.stats.alt;
  document.getElementById('is-m1').textContent = sc.stats.m1;
  document.getElementById('is-m2').textContent = sc.stats.m2;
  document.querySelector('#io-stats .io-stat:nth-child(1) .io-stat-lbl').textContent = sc.stats.l1;
  document.querySelector('#io-stats .io-stat:nth-child(2) .io-stat-lbl').textContent = sc.stats.l2;
  document.querySelector('#io-stats .io-stat:nth-child(3) .io-stat-lbl').textContent = sc.stats.l3;
  document.querySelector('#io-stats .io-stat:nth-child(4) .io-stat-lbl').textContent = sc.stats.l4;
  document.getElementById('fc-lbl').textContent = sc.fcLabel;
  document.getElementById('fc-val').textContent = '0';
  camera.position.set(...sc.camera.pos);
  camera.lookAt(...sc.camera.lookAt);
  radius = camera.position.length();
  sc.build();
  t = 0;
  playing = true;
  document.getElementById('btn-play').textContent = '⏸';
  currentScenario = key;
}

let currentScenario = 'iridium';
let lastTime = null;

function togglePlay() {
  playing = !playing;
  document.getElementById('btn-play').textContent = playing ? '⏸' : '▶';
}
function restart() {
  t = 0; playing = true;
  document.getElementById('btn-play').textContent = '⏸';
  loadScenario(currentScenario);
}
function cycleSpeed() {
  speedIdx = (speedIdx + 1) % speeds.length;
  speed = speeds[speedIdx];
  document.getElementById('btn-speed').textContent = speed + '×';
}
function scrubTo(e) {
  const rect = document.getElementById('pb-progress').getBoundingClientRect();
  t = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
}

// ══ ORBIT CONTROLS ═══════════════════════════════════════════
let isDragging=false, lastX=0, lastY=0, lastTouchDist=0;
let phi=0.5, theta=0.4, radius=24000;

function updateCam() {
  camera.position.set(radius*Math.sin(phi)*Math.cos(theta), radius*Math.cos(phi), radius*Math.sin(phi)*Math.sin(theta));
  camera.lookAt(0,0,0);
}
canvas.addEventListener('mousedown', e => { isDragging=true; lastX=e.clientX; lastY=e.clientY; });
window.addEventListener('mouseup', () => isDragging=false);
window.addEventListener('mousemove', e => {
  if (!isDragging) return;
  theta -= (e.clientX-lastX)*0.005; phi=Math.max(0.1,Math.min(Math.PI-0.1,phi-(e.clientY-lastY)*0.005));
  lastX=e.clientX; lastY=e.clientY; updateCam();
});
canvas.addEventListener('wheel', e => { radius=Math.max(R_E*1.5,Math.min(200000,radius+e.deltaY*12)); updateCam(); }, { passive:true });
canvas.addEventListener('touchstart', e => {
  if (e.touches.length===1) { isDragging=true; lastX=e.touches[0].clientX; lastY=e.touches[0].clientY; }
  else if (e.touches.length===2) { isDragging=false; lastTouchDist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX, e.touches[0].clientY-e.touches[1].clientY); }
}, { passive:true });
canvas.addEventListener('touchmove', e => {
  if (e.touches.length===1 && isDragging) {
    theta-=(e.touches[0].clientX-lastX)*0.007; phi=Math.max(0.1,Math.min(Math.PI-0.1,phi-(e.touches[0].clientY-lastY)*0.007));
    lastX=e.touches[0].clientX; lastY=e.touches[0].clientY; updateCam();
  } else if (e.touches.length===2) {
    const d=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);
    radius=Math.max(R_E*1.5,Math.min(200000,radius*(lastTouchDist/d))); lastTouchDist=d; updateCam();
  }
}, { passive:true });
canvas.addEventListener('touchend', ()=>isDragging=false, { passive:true });

// ══ RESIZE ════════════════════════════════════════════════════
function resize() {
  const w=canvas.parentElement.clientWidth, h=canvas.parentElement.clientHeight;
  renderer.setSize(w,h); camera.aspect=w/h; camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize); resize();

// ══ RENDER LOOP ═══════════════════════════════════════════════
function animate(ts) {
  requestAnimationFrame(animate);
  if (playing && lastTime !== null) {
    const dt = Math.min((ts - lastTime) / 1000, 0.05) * speed * 0.12;
    t = Math.min(t + dt, 1.0);
    if (t >= 1.0) playing = false;
  }
  lastTime = ts;

  // Run scenario tick
  const sc = SCENARIOS[currentScenario];
  if (sc && sc.tick) sc.tick(t);

  // Update timeline
  document.getElementById('pb-fill').style.width = (t * 100) + '%';
  document.getElementById('pb-scrubber').style.left  = (t * 100) + '%';
  const seconds = Math.round(t * 120);
  const mm = Math.floor(seconds / 60), ss = seconds % 60;
  document.getElementById('pb-time').textContent = 'T+' + String(mm).padStart(2,'0') + ':' + String(ss).padStart(2,'0');

  earthMesh.rotation.y += 0.0003;
  renderer.render(scene, camera);
}

// ══ INIT ══════════════════════════════════════════════════════
loadScenario('iridium');
requestAnimationFrame(animate);
</script>
</body>
</html>"""

KEPLER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>Keplerian Orbit Explorer — VectraSpace</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:wght@400;500&family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#080c12;--ink2:#0d1320;--ink3:#111d2e;
  --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.13);
  --text:#ccd6e0;--muted:#8aaac5;--faint:#2a3d50;
  --accent:#4a9eff;--green:#34d399;--amber:#f59e0b;--red:#f87171;--purple:#a78bfa;
  --serif:'Instrument Serif',serif;--mono:'DM Mono',monospace;--sans:'Outfit',sans-serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html,body{background:var(--ink);color:var(--text);font-family:var(--sans);overflow:hidden;width:100%;height:100%;}

/* NAV */
#topbar{position:fixed;top:0;left:0;right:0;z-index:100;height:52px;padding:0 20px;display:flex;align-items:center;justify-content:space-between;background:rgba(8,12,18,0.96);border-bottom:1px solid var(--border);backdrop-filter:blur(12px);}
.tb-brand{font-family:var(--serif);font-size:16px;font-style:italic;color:#fff;text-decoration:none;display:flex;align-items:center;gap:6px;}
.tb-brand em{color:var(--accent);font-style:normal;}
.tb-links{display:flex;gap:4px;}
.tb-link{font-family:var(--mono);font-size:9px;letter-spacing:1px;color:var(--muted);text-decoration:none;padding:5px 12px;border:1px solid transparent;border-radius:4px;transition:all 0.15s;}
.tb-link:hover,.tb-link.active{border-color:var(--border2);color:var(--text);}
.tb-link.active{border-color:rgba(74,158,255,0.4);color:var(--accent);background:rgba(74,158,255,0.06);}

/* LAYOUT */
#app{display:flex;height:100vh;padding-top:52px;}
#canvas-wrap{flex:1;position:relative;overflow:hidden;touch-action:none;}
#three-canvas{display:block;width:100%;height:100%;}

/* PANEL */
#panel{width:300px;min-width:300px;background:var(--ink2);border-left:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;}
#panel-scroll{flex:1;overflow-y:auto;padding:16px;}
#panel-scroll::-webkit-scrollbar{width:3px;}
#panel-scroll::-webkit-scrollbar-thumb{background:var(--faint);border-radius:2px;}

.p-section{margin-bottom:24px;}
.p-label{font-family:var(--mono);font-size:8px;letter-spacing:2.5px;text-transform:uppercase;color:var(--muted);margin-bottom:14px;display:flex;align-items:center;gap:8px;}
.p-label::before{content:'';width:14px;height:1px;background:var(--accent);display:inline-block;}

/* SLIDERS */
.sl-row{margin-bottom:14px;}
.sl-header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px;}
.sl-name{font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:0.5px;}
.sl-name .sym{color:var(--accent);font-style:italic;margin-right:2px;}
.sl-val{font-family:var(--mono);font-size:13px;color:var(--text);min-width:60px;text-align:right;}
.sl-unit{font-family:var(--mono);font-size:9px;color:var(--faint);margin-left:3px;}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:3px;border-radius:2px;background:var(--border2);outline:none;cursor:pointer;}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:var(--accent);cursor:pointer;box-shadow:0 0 6px rgba(74,158,255,0.4);}
input[type=range]::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:var(--accent);cursor:pointer;border:none;}

/* ORBIT INFO */
.orbit-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:4px;}
.os-cell{background:var(--ink3);border:1px solid var(--border);border-radius:6px;padding:10px 12px;}
.os-label{font-family:var(--mono);font-size:8px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);margin-bottom:4px;}
.os-val{font-family:var(--serif);font-size:18px;font-style:italic;color:var(--text);}
.os-unit{font-family:var(--mono);font-size:8px;color:var(--faint);display:block;margin-top:1px;}

/* PRESETS */
.preset-chips{display:flex;flex-wrap:wrap;gap:6px;}
.preset-chip{font-family:var(--mono);font-size:8px;letter-spacing:0.5px;padding:5px 12px;border-radius:20px;border:1px solid var(--border);color:var(--muted);background:transparent;cursor:pointer;transition:all 0.15s;}
.preset-chip:hover{border-color:var(--accent);color:var(--accent);background:rgba(74,158,255,0.06);}
.preset-chip.active{border-color:var(--accent);color:var(--accent);background:rgba(74,158,255,0.08);}

/* LEGEND */
.legend{display:flex;flex-direction:column;gap:7px;}
.legend-row{display:flex;align-items:center;gap:10px;font-family:var(--mono);font-size:9px;color:var(--muted);letter-spacing:0.5px;}
.legend-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0;}

/* INFO BOX */
.info-box{background:var(--ink3);border:1px solid var(--border);border-left:2px solid var(--accent);border-radius:6px;padding:12px 14px;font-size:12px;color:var(--muted);line-height:1.65;}
.info-box strong{color:var(--text);}

/* PANEL TOGGLE (mobile) */
#panel-toggle{display:none;position:fixed;bottom:20px;right:20px;z-index:200;width:48px;height:48px;border-radius:50%;background:var(--accent);border:none;color:#fff;font-size:18px;cursor:pointer;box-shadow:0 4px 16px rgba(74,158,255,0.4);}

/* MOBILE */
@media(max-width:700px){
  #panel{position:fixed;top:52px;right:0;bottom:0;width:280px;min-width:0;transform:translateX(100%);transition:transform 0.3s ease;z-index:50;border-left:1px solid var(--border2);}
  #panel.open{transform:translateX(0);}
  #panel-toggle{display:flex;align-items:center;justify-content:center;}
  .tb-link{display:none;}
  .tb-link.back-link{display:block;}
}
</style>
</head>
<body>
<div id="topbar">
  <a href="/" class="tb-brand">Vectra<em>Space</em></a>
  <div class="tb-links">
    <a href="/" class="tb-link back-link">← Hub</a>
    <a href="/kepler" class="tb-link active">Kepler Explorer</a>
    <a href="/scenarios" class="tb-link">Scenarios</a>
    <a href="/calculator" class="tb-link">Calculator</a>
    <a href="/glossary" class="tb-link">News</a>
  </div>
</div>

<div id="app">
  <div id="canvas-wrap">
    <canvas id="three-canvas"></canvas>
  </div>

  <div id="panel">
    <div id="panel-scroll">

      <div class="p-section">
        <div class="p-label">Keplerian Elements</div>

        <div class="sl-row">
          <div class="sl-header">
            <span class="sl-name"><span class="sym">a</span> Semi-Major Axis</span>
            <span class="sl-val" id="val-a">7,000<span class="sl-unit">km</span></span>
          </div>
          <input type="range" id="sl-a" min="6600" max="42000" value="7000" step="50">
        </div>

        <div class="sl-row">
          <div class="sl-header">
            <span class="sl-name"><span class="sym">e</span> Eccentricity</span>
            <span class="sl-val" id="val-e">0.000</span>
          </div>
          <input type="range" id="sl-e" min="0" max="0.95" value="0" step="0.005">
        </div>

        <div class="sl-row">
          <div class="sl-header">
            <span class="sl-name"><span class="sym">i</span> Inclination</span>
            <span class="sl-val" id="val-i">0°</span>
          </div>
          <input type="range" id="sl-i" min="0" max="180" value="0" step="1">
        </div>

        <div class="sl-row">
          <div class="sl-header">
            <span class="sl-name"><span class="sym">Ω</span> RAAN</span>
            <span class="sl-val" id="val-O">0°</span>
          </div>
          <input type="range" id="sl-O" min="0" max="360" value="0" step="1">
        </div>

        <div class="sl-row">
          <div class="sl-header">
            <span class="sl-name"><span class="sym">ω</span> Arg. of Perigee</span>
            <span class="sl-val" id="val-w">0°</span>
          </div>
          <input type="range" id="sl-w" min="0" max="360" value="0" step="1">
        </div>
      </div>

      <div class="p-section">
        <div class="p-label">Derived Properties</div>
        <div class="orbit-stats">
          <div class="os-cell">
            <div class="os-label">Perigee Alt</div>
            <div class="os-val" id="stat-pe">629</div>
            <span class="os-unit">km</span>
          </div>
          <div class="os-cell">
            <div class="os-label">Apogee Alt</div>
            <div class="os-val" id="stat-ap">629</div>
            <span class="os-unit">km</span>
          </div>
          <div class="os-cell">
            <div class="os-label">Period</div>
            <div class="os-val" id="stat-T">98.8</div>
            <span class="os-unit">min</span>
          </div>
          <div class="os-cell">
            <div class="os-label">Perigee Speed</div>
            <div class="os-val" id="stat-v">7.51</div>
            <span class="os-unit">km/s</span>
          </div>
          <div class="os-cell">
            <div class="os-label">Regime</div>
            <div class="os-val" id="stat-reg" style="font-size:14px;font-style:normal;font-family:var(--mono)">LEO</div>
            <span class="os-unit"></span>
          </div>
          <div class="os-cell">
            <div class="os-label">J₂ RAAN drift</div>
            <div class="os-val" id="stat-j2" style="font-size:14px;font-style:normal;font-family:var(--mono)">-7.1</div>
            <span class="os-unit">°/day</span>
          </div>
        </div>
      </div>

      <div class="p-section">
        <div class="p-label">Quick Presets</div>
        <div class="preset-chips">
          <button class="preset-chip active" onclick="applyPreset('iss')">ISS</button>
          <button class="preset-chip" onclick="applyPreset('sso')">Sun-Sync</button>
          <button class="preset-chip" onclick="applyPreset('gto')">GTO</button>
          <button class="preset-chip" onclick="applyPreset('geo')">GEO</button>
          <button class="preset-chip" onclick="applyPreset('molniya')">Molniya</button>
          <button class="preset-chip" onclick="applyPreset('polar')">Polar</button>
        </div>
      </div>

      <div class="p-section">
        <div class="p-label">Legend</div>
        <div class="legend">
          <div class="legend-row"><div class="legend-dot" style="background:#4a9eff;"></div>Orbit path</div>
          <div class="legend-row"><div class="legend-dot" style="background:#34d399;"></div>Satellite (current position)</div>
          <div class="legend-row"><div class="legend-dot" style="background:#f59e0b;opacity:0.7;"></div>Perigee marker</div>
          <div class="legend-row"><div class="legend-dot" style="background:#a78bfa;opacity:0.7;"></div>Apogee marker</div>
          <div class="legend-row"><div class="legend-dot" style="background:rgba(255,255,255,0.15);"></div>Earth (to scale)</div>
        </div>
      </div>

      <div class="p-section">
        <div class="info-box" id="insight-box">
          <strong>Tip:</strong> Drag the <em>Eccentricity</em> slider to watch a circular orbit stretch into an ellipse. Notice how perigee speed rises while apogee speed drops — that's conservation of angular momentum.
        </div>
      </div>

    </div>
  </div>
</div>

<button id="panel-toggle" onclick="togglePanel()">⚙</button>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
// ── CONSTANTS ─────────────────────────────────────────────────
const MU  = 398600.4418; // km³/s²
const R_E = 6371.0;      // km

// ── THREE.JS SETUP ────────────────────────────────────────────
const canvas = document.getElementById('three-canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x080c12, 1);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 500000);
// camera positioned via updateCameraForOrbit() on init

// ── EARTH ──────────────────────────────────────────────────────
const earthGeo = new THREE.SphereGeometry(R_E, 64, 64);
const earthMat = new THREE.MeshPhongMaterial({
  color: 0x1a3a6a,
  emissive: 0x0a1a3a,
  shininess: 8,
  specular: 0x204060,
});
const earthMesh = new THREE.Mesh(earthGeo, earthMat);
scene.add(earthMesh);

// Earth grid lines
const gridMat = new THREE.LineBasicMaterial({ color: 0x1a3a6a, opacity: 0.4, transparent: true });
for (let lat = -80; lat <= 80; lat += 20) {
  const pts = [];
  for (let lng = 0; lng <= 360; lng += 4) {
    const r = R_E + 2;
    const lngR = lng * Math.PI / 180, latR = lat * Math.PI / 180;
    pts.push(new THREE.Vector3(r*Math.cos(latR)*Math.cos(lngR), r*Math.sin(latR), r*Math.cos(latR)*Math.sin(lngR)));
  }
  scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), gridMat));
}
for (let lng = 0; lng < 360; lng += 30) {
  const pts = [];
  for (let lat = -90; lat <= 90; lat += 4) {
    const r = R_E + 2;
    const lngR = lng * Math.PI / 180, latR = lat * Math.PI / 180;
    pts.push(new THREE.Vector3(r*Math.cos(latR)*Math.cos(lngR), r*Math.sin(latR), r*Math.cos(latR)*Math.sin(lngR)));
  }
  scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), gridMat));
}

// Atmosphere glow sphere
const atmoGeo = new THREE.SphereGeometry(R_E * 1.04, 32, 32);
const atmoMat = new THREE.MeshPhongMaterial({
  color: 0x2a5f9f, transparent: true, opacity: 0.08, side: THREE.FrontSide,
});
scene.add(new THREE.Mesh(atmoGeo, atmoMat));

// Equatorial plane circle (line loop — works in r128)
(function() {
  const pts = [];
  for (let k = 0; k <= 128; k++) {
    const a = (k / 128) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a) * (R_E + 120), 0, Math.sin(a) * (R_E + 120)));
  }
  scene.add(new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: 0x1a3050, transparent: true, opacity: 0.4 })
  ));
})();

// Lights
scene.add(new THREE.AmbientLight(0x223355, 1.4));
const sunLight = new THREE.DirectionalLight(0xffffff, 1.8);
sunLight.position.set(50000, 20000, 30000);
scene.add(sunLight);

// Starfield
const starGeo = new THREE.BufferGeometry();
const starPos = [];
for (let i = 0; i < 2000; i++) {
  const r = 300000;
  starPos.push((Math.random()-0.5)*r*2, (Math.random()-0.5)*r*2, (Math.random()-0.5)*r*2);
}
starGeo.setAttribute('position', new THREE.Float32BufferAttribute(starPos, 3));
scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xffffff, size: 60, transparent: true, opacity: 0.6 })));

// ── ORBIT OBJECTS ─────────────────────────────────────────────
let orbitLine = null, satMesh = null, perigeeMesh = null, apogeeMesh = null;
let orbitGroup = new THREE.Group();
scene.add(orbitGroup);

function clearOrbit() {
  orbitGroup.clear();
}

// ── KEPLERIAN → CARTESIAN ─────────────────────────────────────
function keplerToCartesian(a, e, iDeg, OmegaDeg, omegaDeg, nuDeg) {
  const i = iDeg * Math.PI / 180;
  const O = OmegaDeg * Math.PI / 180;
  const w = omegaDeg * Math.PI / 180;
  const nu = nuDeg * Math.PI / 180;
  const p = a * (1 - e*e);
  const r = p / (1 + e * Math.cos(nu));
  // Position in perifocal frame
  const xp = r * Math.cos(nu);
  const yp = r * Math.sin(nu);
  // Rotate: ω, i, Ω
  const cosO=Math.cos(O), sinO=Math.sin(O);
  const cosi=Math.cos(i), sini=Math.sin(i);
  const cosw=Math.cos(w), sinw=Math.sin(w);
  const x = (cosO*cosw - sinO*sinw*cosi)*xp + (-cosO*sinw - sinO*cosw*cosi)*yp;
  const y = (sini*sinw)*xp + (sini*cosw)*yp;
  const z = (sinO*cosw + cosO*sinw*cosi)*xp + (-sinO*sinw + cosO*cosw*cosi)*yp;
  return new THREE.Vector3(x, y, z);
}

function buildOrbit(a, e, i, O, w) {
  clearOrbit();
  const N = 256;
  const pts = [];
  for (let k = 0; k <= N; k++) {
    const nu = (k / N) * 360;
    pts.push(keplerToCartesian(a, e, i, O, w, nu));
  }

  // Orbit line
  const lineMat = new THREE.LineBasicMaterial({ color: 0x4a9eff, linewidth: 2 });
  const lineGeo = new THREE.BufferGeometry().setFromPoints(pts);
  orbitLine = new THREE.Line(lineGeo, lineMat);
  orbitGroup.add(orbitLine);

  // Satellite position (nu=45 for visual interest)
  const satPos = keplerToCartesian(a, e, i, O, w, 45);
  satMesh = new THREE.Mesh(
    new THREE.SphereGeometry(120, 12, 12),
    new THREE.MeshPhongMaterial({ color: 0x34d399, emissive: 0x16a064 })
  );
  satMesh.position.copy(satPos);
  orbitGroup.add(satMesh);

  // Perigee marker (nu=0)
  const pePos = keplerToCartesian(a, e, i, O, w, 0);
  perigeeMesh = new THREE.Mesh(
    new THREE.SphereGeometry(90, 8, 8),
    new THREE.MeshPhongMaterial({ color: 0xf59e0b, emissive: 0x805300, transparent: true, opacity: 0.85 })
  );
  perigeeMesh.position.copy(pePos);
  orbitGroup.add(perigeeMesh);

  // Apogee marker (nu=180)
  const apPos = keplerToCartesian(a, e, i, O, w, 180);
  apogeeMesh = new THREE.Mesh(
    new THREE.SphereGeometry(90, 8, 8),
    new THREE.MeshPhongMaterial({ color: 0xa78bfa, emissive: 0x5030a0, transparent: true, opacity: 0.85 })
  );
  apogeeMesh.position.copy(apPos);
  orbitGroup.add(apogeeMesh);

  // Apse line
  const apseMat = new THREE.LineBasicMaterial({ color: 0x2a3d50 });
  orbitGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([pePos, apPos]), apseMat));

  // Normal vector indicator
  const normalDir = new THREE.Vector3(
    Math.sin(O * Math.PI/180) * Math.sin(i * Math.PI/180),
    Math.cos(i * Math.PI/180),
    -Math.cos(O * Math.PI/180) * Math.sin(i * Math.PI/180)
  ).normalize();
  const arrowLen = a * 0.4;
  const arrowEnd = normalDir.clone().multiplyScalar(arrowLen);
  const normalMat = new THREE.LineBasicMaterial({ color: 0x2a5580, transparent: true, opacity: 0.6 });
  orbitGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0), arrowEnd]), normalMat));

  updateStats(a, e, i);
}

// ── DERIVED STATS ─────────────────────────────────────────────
function updateStats(a, e, i) {
  const pe = a * (1 - e) - R_E;
  const ap = a * (1 + e) - R_E;
  const T  = 2 * Math.PI * Math.sqrt(a*a*a / MU) / 60; // minutes
  // vis-viva at perigee
  const r_pe = a * (1 - e);
  const v_pe = Math.sqrt(MU * (2/r_pe - 1/a));
  // J2 RAAN drift (deg/day)
  const J2 = 1.08263e-3;
  const n  = Math.sqrt(MU / (a*a*a)); // rad/s
  const iR = i * Math.PI / 180;
  const j2_drift = -1.5 * n * J2 * (R_E/a)**2 * Math.cos(iR) * (180/Math.PI) * 86400;
  // regime
  const regime = a-R_E < 2000 ? 'LEO' : a-R_E < 35000 ? 'MEO' : 'GEO';

  document.getElementById('stat-pe').textContent = Math.round(pe);
  document.getElementById('stat-ap').textContent = Math.round(ap);
  document.getElementById('stat-T').textContent  = T.toFixed(1);
  document.getElementById('stat-v').textContent  = v_pe.toFixed(2);
  document.getElementById('stat-reg').textContent = regime;
  document.getElementById('stat-j2').textContent = j2_drift.toFixed(1);

  // Contextual insight
  const iBox = document.getElementById('insight-box');
  if (e > 0.5) {
    iBox.innerHTML = '<strong>Highly elliptical orbit.</strong> At perigee the satellite moves at <strong>' + v_pe.toFixed(2) + ' km/s</strong>. This Hohmann-transfer-style shape is used for Molniya communications satellites to linger over high latitudes.';
  } else if (Math.abs(j2_drift + 0.9856) < 0.5 && i > 85) {
    iBox.innerHTML = '<strong>Near sun-synchronous!</strong> J₂ RAAN drift ≈ +0.986°/day matches Earth\'s orbital rate, so the orbit plane stays fixed relative to the Sun — ideal for Earth observation.';
  } else if (a > 40000) {
    iBox.innerHTML = '<strong>Geostationary regime.</strong> At 35,786 km, orbital period ≈ 24 h. The satellite appears stationary over the equator. GEO slot crowding is a major policy issue.';
  } else if (i > 85 && i < 95) {
    iBox.innerHTML = '<strong>Polar orbit.</strong> The satellite crosses both poles, eventually seeing every point on Earth. Used for weather satellites and full-coverage Earth observation.';
  } else {
    iBox.innerHTML = '<strong>Tip:</strong> Watch the RAAN drift value change as you move inclination — at <strong>~97–98°</strong> for low altitudes, the drift matches Earth\'s orbit around the Sun, creating a sun-synchronous orbit.';
  }
}

// ── SLIDER WIRING ─────────────────────────────────────────────
const state = { a: 7000, e: 0, i: 0, O: 0, w: 0 };

function wire(id, key, fmt) {
  const sl = document.getElementById('sl-' + id);
  const vl = document.getElementById('val-' + id);
  sl.addEventListener('input', () => {
    state[key] = parseFloat(sl.value);
    vl.innerHTML = fmt(state[key]);
    buildOrbit(state.a, state.e, state.i, state.O, state.w);
    updateCameraForOrbit();
  });
  // touch: prevent page scroll when on slider
  sl.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });
}

wire('a', 'a', v => (v >= 10000 ? (v/1000).toFixed(1)+'k' : Math.round(v).toLocaleString()) + '<span class="sl-unit">km</span>');
wire('e', 'e', v => v.toFixed(3));
wire('i', 'i', v => v + '°');
wire('O', 'O', v => v + '°');
wire('w', 'w', v => v + '°');

// ── PRESETS ───────────────────────────────────────────────────
const PRESETS = {
  iss:     { a: 6778,  e: 0.001, i: 51.6, O: 0,   w: 0,   label: 'ISS' },
  sso:     { a: 7078,  e: 0.001, i: 98.2, O: 90,  w: 0,   label: 'Sun-Sync' },
  gto:     { a: 24396, e: 0.73,  i: 27,   O: 0,   w: 178, label: 'GTO' },
  geo:     { a: 42164, e: 0.001, i: 0.1,  O: 0,   w: 0,   label: 'GEO' },
  molniya: { a: 26560, e: 0.74,  i: 63.4, O: 270, w: 270, label: 'Molniya' },
  polar:   { a: 7378,  e: 0.001, i: 90,   O: 180, w: 0,   label: 'Polar' },
};

function applyPreset(key) {
  const p = PRESETS[key];
  state.a = p.a; state.e = p.e; state.i = p.i; state.O = p.O; state.w = p.w;
  document.getElementById('sl-a').value = p.a;
  document.getElementById('sl-e').value = p.e;
  document.getElementById('sl-i').value = p.i;
  document.getElementById('sl-O').value = p.O;
  document.getElementById('sl-w').value = p.w;
  document.getElementById('val-a').innerHTML = (p.a >= 10000 ? (p.a/1000).toFixed(1)+'k' : Math.round(p.a).toLocaleString()) + '<span class="sl-unit">km</span>';
  document.getElementById('val-e').textContent = p.e.toFixed(3);
  document.getElementById('val-i').textContent = p.i + '°';
  document.getElementById('val-O').textContent = p.O + '°';
  document.getElementById('val-w').textContent = p.w + '°';
  document.querySelectorAll('.preset-chip').forEach(c => c.classList.remove('active'));
  event.target.classList.add('active');
  buildOrbit(state.a, state.e, state.i, state.O, state.w);
  updateCameraForOrbit();
}

function updateCameraForOrbit() {
  const dist = state.a * 3.2;
  camera.position.set(0, dist * 0.45, dist);
  camera.lookAt(0, 0, 0);
  // Sync orbit control state so dragging works immediately after
  radius = camera.position.length();
  phi    = Math.acos(camera.position.y / radius);
  theta  = Math.atan2(camera.position.z, camera.position.x);
}

// ── ORBIT CONTROLS (manual, no import needed) ────────────────
let isDragging = false, lastX = 0, lastY = 0;
let phi = 0.4, theta = 0.5, radius = 20000;
let lastTouchDist = 0;

function updateCamera() {
  camera.position.set(
    radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta)
  );
  camera.lookAt(0, 0, 0);
}

// Mouse
canvas.addEventListener('mousedown', e => { isDragging = true; lastX = e.clientX; lastY = e.clientY; });
window.addEventListener('mouseup',   () => { isDragging = false; });
window.addEventListener('mousemove', e => {
  if (!isDragging) return;
  theta += (e.clientX - lastX) * 0.006;
  phi    = Math.max(0.05, Math.min(Math.PI - 0.05, phi - (e.clientY - lastY) * 0.006));
  lastX  = e.clientX; lastY = e.clientY;
  updateCamera();
});
canvas.addEventListener('wheel', e => {
  radius = Math.max(R_E * 1.8, Math.min(300000, radius * (1 + e.deltaY * 0.001)));
  updateCamera();
}, { passive: true });

// Touch orbit
canvas.addEventListener('touchstart', e => {
  if (e.touches.length === 1) {
    isDragging = true;
    lastX = e.touches[0].clientX;
    lastY = e.touches[0].clientY;
  } else if (e.touches.length === 2) {
    isDragging = false;
    const dx = e.touches[0].clientX - e.touches[1].clientX;
    const dy = e.touches[0].clientY - e.touches[1].clientY;
    lastTouchDist = Math.hypot(dx, dy);
  }
}, { passive: true });

canvas.addEventListener('touchmove', e => {
  if (e.touches.length === 1 && isDragging) {
    theta -= (e.touches[0].clientX - lastX) * 0.007;
    phi   = Math.max(0.1, Math.min(Math.PI - 0.1, phi - (e.touches[0].clientY - lastY) * 0.007));
    lastX = e.touches[0].clientX;
    lastY = e.touches[0].clientY;
    radius = camera.position.length();
    updateCamera();
  } else if (e.touches.length === 2) {
    const dx = e.touches[0].clientX - e.touches[1].clientX;
    const dy = e.touches[0].clientY - e.touches[1].clientY;
    const d  = Math.hypot(dx, dy);
    radius = Math.max(R_E * 1.5, Math.min(200000, radius * (lastTouchDist / d)));
    lastTouchDist = d;
    updateCamera();
  }
}, { passive: true });

canvas.addEventListener('touchend', () => { isDragging = false; }, { passive: true });

// ── RESIZE ────────────────────────────────────────────────────
function resize() {
  const wrap = document.getElementById('canvas-wrap');
  const w = wrap.offsetWidth  || window.innerWidth;
  const h = wrap.offsetHeight || window.innerHeight - 52;
  if (w > 0 && h > 0) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
}
window.addEventListener('resize', resize);

// ── ANIMATE ───────────────────────────────────────────────────
let satNu = 45;
function animate() {
  requestAnimationFrame(animate);
  satNu = (satNu + 0.08) % 360;
  if (satMesh) {
    const p = keplerToCartesian(state.a, state.e, state.i, state.O, state.w, satNu);
    satMesh.position.copy(p);
  }
  earthMesh.rotation.y += 0.0008;
  renderer.render(scene, camera);
}

// ── MOBILE PANEL ─────────────────────────────────────────────
function togglePanel() {
  document.getElementById('panel').classList.toggle('open');
}

// ── INIT ──────────────────────────────────────────────────────
// Defer to rAF so flex layout is fully computed before we measure
requestAnimationFrame(function() {
  resize();
  buildOrbit(state.a, state.e, state.i, state.O, state.w);
  updateCameraForOrbit();
  animate();
});
</script>
</body>
</html>"""

CALC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Impact Calculator — VectraSpace</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:ital,wght@0,400;0,500&family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#080c12;--ink2:#0d1320;--ink3:#131d2e;--panel:#0f1925;
  --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.13);
  --text:#ccd6e0;--muted:#8aaac5;--faint:#2a3d50;
  --accent:#4a9eff;--accent2:#7bc4ff;--green:#34d399;--amber:#f59e0b;--red:#f87171;--purple:#a78bfa;
  --serif:'Instrument Serif',Georgia,serif;--mono:'DM Mono',monospace;--sans:'Outfit',sans-serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--ink);color:var(--text);font-family:var(--sans);line-height:1.6;overflow-x:hidden;min-height:100vh;}

nav{position:fixed;top:0;left:0;right:0;z-index:100;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between;background:rgba(8,12,18,0.94);border-bottom:1px solid var(--border);backdrop-filter:blur(16px);}
.nav-brand{display:flex;align-items:center;text-decoration:none;color:#fff;}
.nav-brand-name{font-family:var(--serif);font-size:17px;font-style:italic;letter-spacing:-0.2px;}
.nav-brand-name em{color:var(--accent);font-style:normal;}
.nav-links{display:flex;gap:4px;align-items:center;}
.nav-link{font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--muted);text-decoration:none;padding:7px 14px;border-radius:4px;transition:all 0.2s;border:1px solid transparent;}
.nav-link:hover{color:var(--text);border-color:var(--border);}
.nav-link.active{color:var(--accent);border-color:rgba(74,158,255,0.3);background:rgba(74,158,255,0.05);}

/* PAGE LAYOUT */
.page{padding:96px 48px 80px;max-width:1100px;margin:0 auto;}
.page-hero{margin-bottom:56px;}
.page-eyebrow{font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--amber);margin-bottom:14px;display:flex;align-items:center;gap:10px;}
.page-eyebrow::before{content:'';width:24px;height:1px;background:var(--amber);display:inline-block;}
.page-title{font-family:var(--serif);font-size:clamp(38px,4.5vw,60px);font-weight:400;color:#fff;line-height:1.1;letter-spacing:-0.5px;margin-bottom:16px;}
.page-title em{font-style:italic;color:var(--accent2);}
.page-subtitle{font-size:15px;color:var(--muted);line-height:1.8;max-width:620px;}

/* GRID */
.calc-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start;}

/* PANEL */
.calc-panel{background:var(--ink2);border:1px solid var(--border);border-radius:10px;overflow:hidden;}
.calc-panel-header{padding:20px 28px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;}
.calc-panel-icon{font-size:16px;}
.calc-panel-title{font-family:var(--mono);font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);}
.calc-panel-body{padding:28px;}

/* INPUTS */
.field{margin-bottom:24px;}
.field:last-child{margin-bottom:0;}
.field-label{font-family:var(--mono);font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;}
.field-hint{font-family:var(--mono);font-size:9px;color:var(--faint);letter-spacing:0;text-transform:none;}
.field-row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;}
.field-input{
  width:100%;padding:11px 16px;background:var(--ink3);
  border:1px solid var(--border);border-radius:6px;
  color:var(--text);font-family:var(--mono);font-size:14px;
  outline:none;transition:border-color 0.2s;
}
.field-input:focus{border-color:var(--accent);}
.field-input::placeholder{color:var(--faint);}
.field-unit{font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:1px;white-space:nowrap;padding:0 4px;}

/* SLIDER */
.slider-wrap{margin-top:6px;}
.range-slider{-webkit-appearance:none;appearance:none;width:100%;height:4px;border-radius:2px;background:var(--border2);outline:none;cursor:pointer;}
.range-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:16px;border-radius:50%;background:var(--accent);cursor:pointer;box-shadow:0 0 8px rgba(74,158,255,0.5);}
.range-slider::-moz-range-thumb{width:16px;height:16px;border-radius:50%;background:var(--accent);cursor:pointer;border:none;}
.slider-labels{display:flex;justify-content:space-between;margin-top:4px;font-family:var(--mono);font-size:8px;color:var(--faint);letter-spacing:1px;}

/* PRESETS */
.preset-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
.preset-btn{
  font-family:var(--mono);font-size:8px;letter-spacing:0.5px;
  padding:4px 10px;border-radius:4px;border:1px solid var(--border);
  color:var(--muted);background:transparent;cursor:pointer;transition:all 0.15s;
}
.preset-btn:hover,.preset-btn.active{border-color:var(--accent);color:var(--accent);background:rgba(74,158,255,0.06);}

/* RESULTS */
.results-empty{padding:48px 28px;text-align:center;}
.results-empty-icon{font-size:32px;margin-bottom:12px;opacity:0.3;}
.results-empty-text{font-family:var(--mono);font-size:10px;color:var(--faint);letter-spacing:1.5px;text-transform:uppercase;line-height:1.8;}

.result-hero{padding:28px;border-bottom:1px solid var(--border);text-align:center;}
.result-hero-label{font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:10px;}
.result-hero-val{font-family:var(--serif);font-size:52px;font-style:italic;color:var(--accent);line-height:1;}
.result-hero-unit{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:4px;}

.result-grid{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid var(--border);}
.result-cell{padding:20px 24px;border-right:1px solid var(--border);}
.result-cell:nth-child(even){border-right:none;}
.result-cell-label{font-family:var(--mono);font-size:8px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-bottom:6px;}
.result-cell-val{font-family:var(--serif);font-size:24px;font-style:italic;color:var(--text);}
.result-cell-sub{font-family:var(--mono);font-size:9px;color:var(--faint);margin-top:2px;}

/* SEVERITY METER */
.severity-wrap{padding:24px 28px;border-bottom:1px solid var(--border);}
.severity-label{font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:12px;display:flex;justify-content:space-between;}
.severity-bar-track{height:6px;background:var(--border2);border-radius:3px;overflow:hidden;margin-bottom:8px;}
.severity-bar-fill{height:100%;border-radius:3px;transition:width 0.7s cubic-bezier(0.4,0,0.2,1),background 0.4s;}
.severity-ticks{display:flex;justify-content:space-between;font-family:var(--mono);font-size:8px;color:var(--faint);letter-spacing:0.5px;}

/* ANALOG */
.analogy-wrap{padding:20px 28px;border-bottom:1px solid var(--border);}
.analogy-eyebrow{font-family:var(--mono);font-size:8px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:10px;}
.analogy-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;}
.analogy-card{background:var(--ink3);border:1px solid var(--border);border-radius:6px;padding:12px;text-align:center;}
.analogy-icon{font-size:20px;margin-bottom:6px;}
.analogy-name{font-family:var(--mono);font-size:8px;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:2px;}
.analogy-val{font-family:var(--serif);font-size:15px;color:var(--text);}
.analogy-active{border-color:var(--accent2);background:rgba(74,158,255,0.06);}
.analogy-active .analogy-val{color:var(--accent2);}

/* FRAGMENT */
.fragment-wrap{padding:20px 28px;}
.fragment-label{font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:14px;}
.fragment-bars{display:flex;flex-direction:column;gap:8px;}
.fbar-row{display:grid;grid-template-columns:70px 1fr 80px;gap:10px;align-items:center;}
.fbar-cat{font-family:var(--mono);font-size:9px;color:var(--muted);letter-spacing:0.5px;}
.fbar-track{height:6px;background:var(--ink3);border-radius:3px;overflow:hidden;}
.fbar-fill{height:100%;border-radius:3px;transition:width 0.7s cubic-bezier(0.4,0,0.2,1);}
.fbar-count{font-family:var(--mono);font-size:10px;color:var(--text);text-align:right;}

/* KESSLER RISK */
.kessler-wrap{padding:20px 28px;border-top:1px solid var(--border);}
.kessler-badge{display:inline-flex;align-items:center;gap:8px;padding:10px 16px;border-radius:6px;border:1px solid;font-family:var(--mono);font-size:11px;letter-spacing:0.5px;}
.kessler-dot{width:8px;height:8px;border-radius:50%;animation:pulse 2s infinite;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:0.4;}}
.kessler-msg{font-size:13px;color:var(--muted);line-height:1.6;margin-top:10px;}

/* SHARE */
.share-wrap{padding:20px 28px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;}
.share-label{font-family:var(--mono);font-size:9px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);}
.share-btn{font-family:var(--mono);font-size:9px;letter-spacing:1.5px;text-transform:uppercase;padding:8px 18px;border-radius:5px;border:1px solid var(--border);color:var(--muted);background:transparent;cursor:pointer;transition:all 0.2s;}
.share-btn:hover{border-color:var(--accent);color:var(--accent);}
.share-btn.copied{border-color:var(--green);color:var(--green);}

/* CALC BUTTON */
.calc-btn{
  width:100%;margin-top:20px;padding:14px;border-radius:7px;
  background:linear-gradient(135deg,rgba(74,158,255,0.15),rgba(74,158,255,0.05));
  border:1px solid rgba(74,158,255,0.4);color:var(--accent2);
  font-family:var(--mono);font-size:10px;letter-spacing:2px;text-transform:uppercase;
  cursor:pointer;transition:all 0.2s;
}
.calc-btn:hover{background:linear-gradient(135deg,rgba(74,158,255,0.25),rgba(74,158,255,0.1));border-color:var(--accent);}

/* SCENARIO CARDS (below) */
.scenarios{margin-top:40px;}
.scenarios-label{font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--muted);margin-bottom:16px;}
.scenario-cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;}
.scenario-card{
  background:var(--ink2);border:1px solid var(--border);border-radius:8px;padding:16px;
  cursor:pointer;transition:all 0.2s;
}
.scenario-card:hover{border-color:var(--accent);transform:translateY(-2px);}
.sc-icon{font-size:22px;margin-bottom:8px;}
.sc-name{font-family:var(--sans);font-size:13px;font-weight:600;color:var(--text);margin-bottom:4px;}
.sc-desc{font-size:11px;color:var(--muted);line-height:1.5;}

/* EDUCATIONAL CALLOUT */
.edu-callout{margin-top:40px;background:var(--ink2);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:8px;padding:24px 28px;}
.edu-callout-eyebrow{font-family:var(--mono);font-size:8px;letter-spacing:2px;color:var(--accent);text-transform:uppercase;margin-bottom:10px;}
.edu-callout-title{font-family:var(--serif);font-size:18px;color:#fff;margin-bottom:8px;}
.edu-callout-body{font-size:13px;color:var(--muted);line-height:1.7;}
.edu-link{color:var(--accent);text-decoration:none;font-family:var(--mono);font-size:10px;letter-spacing:1px;display:inline-flex;align-items:center;gap:5px;margin-top:12px;transition:color 0.2s;}
.edu-link:hover{color:var(--accent2);}

@media(max-width:900px){
  .calc-grid{grid-template-columns:1fr;}
  .scenario-cards{grid-template-columns:repeat(2,1fr);}
}
@media(max-width:600px){
  nav{padding:0 16px;}
  .page{padding:88px 20px 60px;}
  .analogy-cards{grid-template-columns:1fr 1fr;}
  .result-grid{grid-template-columns:1fr;}
  .result-cell{border-right:none;border-bottom:1px solid var(--border);}
  .scenario-cards{grid-template-columns:1fr 1fr;}
}
</style>
</head>
<body>
<nav>
  <a href="/" class="nav-brand"><span class="nav-brand-name">Vectra<em>Space</em></span></a>
  <div class="nav-links">
    <a href="/" class="nav-link">Hub</a>
    <a href="/glossary" class="nav-link">News</a>
    <a href="/calculator" class="nav-link active">Calculator</a>
    <a href="/dashboard" class="nav-link">Dashboard</a>
  </div>
</nav>

<div class="page">
  <div class="page-hero">
    <div class="page-eyebrow">// Interactive Tool</div>
    <h1 class="page-title">Orbital Collision <em>Impact Calculator</em></h1>
    <p class="page-subtitle">Model the kinetic energy, fragment count, and cascade risk of any space collision using the NASA Standard Breakup Model and real orbital physics.</p>
  </div>

  <div class="calc-grid">
    <!-- LEFT: INPUTS -->
    <div>
      <div class="calc-panel">
        <div class="calc-panel-header">
          <span class="calc-panel-icon">⚙</span>
          <span class="calc-panel-title">Collision Parameters</span>
        </div>
        <div class="calc-panel-body">

          <!-- Object A -->
          <div class="field">
            <div class="field-label">Object A — Mass<span class="field-hint">Primary satellite</span></div>
            <div class="field-row">
              <input type="number" class="field-input" id="massA" value="800" min="0.01" max="500000" step="1" placeholder="800">
              <span class="field-unit">kg</span>
            </div>
            <div class="slider-wrap">
              <input type="range" class="range-slider" id="slMassA" min="1" max="20000" value="800" step="1">
              <div class="slider-labels"><span>1 kg</span><span>1,000</span><span>5,000</span><span>10,000</span><span>20,000</span></div>
            </div>
          </div>

          <!-- Object B -->
          <div class="field">
            <div class="field-label">Object B — Mass<span class="field-hint">Impactor / debris</span></div>
            <div class="field-row">
              <input type="number" class="field-input" id="massB" value="900" min="0.01" max="500000" step="1" placeholder="900">
              <span class="field-unit">kg</span>
            </div>
            <div class="slider-wrap">
              <input type="range" class="range-slider" id="slMassB" min="1" max="20000" value="900" step="1">
              <div class="slider-labels"><span>1 kg</span><span>1,000</span><span>5,000</span><span>10,000</span><span>20,000</span></div>
            </div>
          </div>

          <!-- Relative velocity -->
          <div class="field">
            <div class="field-label">Relative Velocity<span class="field-hint">At impact (0–15 km/s for LEO)</span></div>
            <div class="field-row">
              <input type="number" class="field-input" id="velRel" value="11.7" min="0.1" max="15" step="0.1" placeholder="11.7">
              <span class="field-unit">km/s</span>
            </div>
            <div class="slider-wrap">
              <input type="range" class="range-slider" id="slVel" min="0.1" max="15" value="11.7" step="0.1">
              <div class="slider-labels"><span>0</span><span>3.75</span><span>7.5</span><span>11.25</span><span>15 km/s</span></div>
            </div>
          </div>

          <!-- Altitude -->
          <div class="field">
            <div class="field-label">Altitude<span class="field-hint">Affects cascade risk assessment</span></div>
            <div class="field-row">
              <input type="number" class="field-input" id="altitude" value="789" min="160" max="36000" step="1" placeholder="789">
              <span class="field-unit">km</span>
            </div>
            <div class="preset-row">
              <button class="preset-btn" onclick="setAlt(400)">ISS (400 km)</button>
              <button class="preset-btn active" onclick="setAlt(789)">Iridium/Cosmos (789)</button>
              <button class="preset-btn" onclick="setAlt(863)">FY-1C (863)</button>
              <button class="preset-btn" onclick="setAlt(1200)">High LEO (1,200)</button>
            </div>
          </div>

          <div class="field">
            <div class="field-label">Common Scenarios</div>
            <div class="preset-row">
              <button class="preset-btn" onclick="loadPreset('frag')">1 cm fragment</button>
              <button class="preset-btn" onclick="loadPreset('smallsat')">SmallSat vs debris</button>
              <button class="preset-btn" onclick="loadPreset('iridium')">Iridium-Cosmos</button>
              <button class="preset-btn" onclick="loadPreset('fy1c')">FY-1C ASAT</button>
            </div>
          </div>

          <button class="calc-btn" onclick="calculate()">▶ Calculate Collision Physics</button>
        </div>
      </div>

      <div class="edu-callout" style="margin-top:20px;">
        <div class="edu-callout-eyebrow">// How the math works</div>
        <div class="edu-callout-title">NASA Standard Breakup Model</div>
        <div class="edu-callout-body">Fragment count follows N(L<sub>c</sub>) = 6·M<sup>0.75</sup>·L<sub>c</sub><sup>−1.6</sup> where M is the mass of the smaller object (kg) and L<sub>c</sub> is the minimum fragment characteristic length (m). Kinetic energy KE = ½μv² uses the reduced mass μ = m₁m₂/(m₁+m₂). The specific energy E* = KE/M_total determines whether a collision is catastrophic (E* > 40 kJ/kg) or cratering.</div>
        <a href="/education/debris-modeling" class="edu-link">Read Chapter 04: Debris Modeling →</a>
      </div>
    </div>

    <!-- RIGHT: RESULTS -->
    <div class="calc-panel" id="results-panel">
      <div class="calc-panel-header">
        <span class="calc-panel-icon">📊</span>
        <span class="calc-panel-title">Results</span>
      </div>
      <div class="results-empty" id="results-empty">
        <div class="results-empty-icon">⚡</div>
        <div class="results-empty-text">Set parameters<br>and calculate</div>
      </div>
      <div id="results-body" style="display:none;">
        <!-- injected by JS -->
      </div>
    </div>
  </div>

  <!-- SCENARIO CARDS -->
  <div class="scenarios">
    <div class="scenarios-label">// Historical & Reference Events</div>
    <div class="scenario-cards">
      <div class="scenario-card" onclick="loadPreset('iridium')">
        <div class="sc-icon">🛰</div>
        <div class="sc-name">Iridium-Cosmos 2009</div>
        <div class="sc-desc">First accidental collision — 789 km, 11.7 km/s, ~2,300 trackable fragments</div>
      </div>
      <div class="scenario-card" onclick="loadPreset('fy1c')">
        <div class="sc-icon">💥</div>
        <div class="sc-name">FY-1C ASAT 2007</div>
        <div class="sc-desc">Deliberate kinetic impact — 863 km, 9.0 km/s, worst single debris event</div>
      </div>
      <div class="scenario-card" onclick="loadPreset('smallsat')">
        <div class="sc-icon">📦</div>
        <div class="sc-name">CubeSat Impact</div>
        <div class="sc-desc">3U CubeSat vs 10 cm fragment at typical LEO crossing velocity</div>
      </div>
      <div class="scenario-card" onclick="loadPreset('frag')">
        <div class="sc-icon">🔩</div>
        <div class="sc-name">Paint Fleck / Bolt</div>
        <div class="sc-desc">1 cm fragment vs 500 kg satellite — surprisingly lethal at orbital speeds</div>
      </div>
    </div>
  </div>
</div>

<script>
// ── SYNC SLIDERS TO INPUTS ────────────────────────────────────
function syncSlider(inputId, sliderId) {
  const input  = document.getElementById(inputId);
  const slider = document.getElementById(sliderId);
  input.addEventListener('input', () => { slider.value = input.value; });
  slider.addEventListener('input', () => { input.value = slider.value; });
}
syncSlider('massA','slMassA');
syncSlider('massB','slMassB');
syncSlider('velRel','slVel');

function setAlt(v) {
  document.getElementById('altitude').value = v;
  document.querySelectorAll('.preset-btn').forEach(b => {
    b.classList.toggle('active', b.textContent.includes('(' + v));
  });
}

// ── PRESETS ───────────────────────────────────────────────────
const PRESETS = {
  frag:     { massA: 500,   massB: 0.01,  vel: 7.7,  alt: 500,  label: '1 cm fragment vs 500 kg satellite' },
  smallsat: { massA: 4,     massB: 1,     vel: 10.3, alt: 550,  label: '3U CubeSat vs 1 kg fragment' },
  iridium:  { massA: 560,   massB: 900,   vel: 11.7, alt: 789,  label: 'Iridium 33 vs Cosmos 2251 (2009)' },
  fy1c:     { massA: 750,   massB: 300,   vel: 9.0,  alt: 863,  label: 'FY-1C ASAT Impact (2007)' },
};
function loadPreset(key) {
  const p = PRESETS[key];
  document.getElementById('massA').value  = p.massA;
  document.getElementById('massB').value  = p.massB;
  document.getElementById('velRel').value = p.vel;
  document.getElementById('altitude').value = p.alt;
  document.getElementById('slMassA').value = p.massA;
  document.getElementById('slMassB').value = p.massB;
  document.getElementById('slVel').value   = p.vel;
  calculate();
}

// ── PHYSICS ───────────────────────────────────────────────────
function formatNum(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2) + ' GJ';
  if (n >= 1e6) return (n/1e6).toFixed(2) + ' MJ';
  if (n >= 1e3) return (n/1e3).toFixed(1) + ' kJ';
  return n.toFixed(0) + ' J';
}
function formatCount(n) {
  if (n >= 1e6) return '>' + (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return '~' + Math.round(n/100)*100;
  return '~' + Math.round(n);
}

function calculate() {
  const mA  = parseFloat(document.getElementById('massA').value)  || 0;
  const mB  = parseFloat(document.getElementById('massB').value)  || 0;
  const vel = parseFloat(document.getElementById('velRel').value) || 0;
  const alt = parseFloat(document.getElementById('altitude').value) || 0;

  if (!mA || !mB || !vel) return;

  // Reduced mass & kinetic energy
  const mu    = (mA * mB) / (mA + mB);          // reduced mass, kg
  const vMs   = vel * 1000;                       // m/s
  const keJ   = 0.5 * mu * vMs * vMs;            // Joules
  const kekJ  = keJ / 1000;

  // Full system KE (as reference)
  const fullKe = 0.5 * (mA + mB) * vMs * vMs;

  // Specific energy (determines catastrophic vs cratering)
  const eStar = keJ / ((mA + mB) * 1000);   // J/kg → kJ/kg after /1000
  const eStarkJ = eStar / 1000;
  const catastrophic = eStarkJ >= 40;

  // NASA SBM fragment counts (using smaller mass as target for catastrophic)
  // N(Lc) = 6 * M^0.75 * Lc^-1.6
  const mSBM = catastrophic ? Math.min(mA, mB) : Math.min(mA, mB);
  const sbmCoeff = 6 * Math.pow(mSBM, 0.75);
  const nTrackable  = Math.round(sbmCoeff * Math.pow(0.10, -1.6));  // ≥10 cm
  const nLethal     = Math.round(sbmCoeff * Math.pow(0.01, -1.6));  // ≥1 cm
  const nTiny       = Math.round(sbmCoeff * Math.pow(0.001,-1.6)); // ≥1 mm

  // Analogy table (kJ)
  const analogies = [
    { icon:'🔫', name:'Rifle bullet', kJ: 3 },
    { icon:'💣', name:'Hand grenade', kJ: 400 },
    { icon:'🚗', name:'Car at 100 mph', kJ: 540 },
    { icon:'🎯', name:'AT missile', kJ: 5000 },
    { icon:'✈', name:'747 at cruise', kJ: 3.7e8 },
    { icon:'🌋', name:'Hiroshima', kJ: 6.3e10 },
  ];
  let closestIdx = 0;
  let closestDiff = Infinity;
  analogies.forEach((a,i) => {
    const diff = Math.abs(Math.log10(kekJ+1) - Math.log10(a.kJ+1));
    if (diff < closestDiff) { closestDiff = diff; closestIdx = i; }
  });
  const showAnalogies = analogies.slice(Math.max(0,closestIdx-1), closestIdx+2);

  // Severity (0–100 log scale)
  const sevPct = Math.min(100, Math.log10(kekJ + 1) / Math.log10(1e12) * 100);
  const sevColor = sevPct < 30 ? 'var(--green)' : sevPct < 60 ? 'var(--amber)' : 'var(--red)';

  // Kessler cascade risk
  const altRisk = alt >= 800 && alt <= 1400;
  const massRisk = catastrophic && (mA > 100 || mB > 100);
  let kesslerLevel, kesslerColor, kesslerMsg;
  if (altRisk && massRisk) {
    kesslerLevel = 'HIGH CASCADE RISK';
    kesslerColor = 'var(--red)';
    kesslerMsg = `At ${alt} km with ${formatCount(nTrackable)} new trackable fragments, this collision falls in the critical density altitude band. Without active debris removal, fragments from this event could trigger further cascading collisions. This is exactly the Kessler scenario.`;
  } else if (altRisk || (catastrophic && (mA>50||mB>50))) {
    kesslerLevel = 'ELEVATED RISK';
    kesslerColor = 'var(--amber)';
    kesslerMsg = `This collision generates a significant debris cloud${altRisk ? ` at a high-risk altitude (${alt} km)` : ''}. Below 600 km, atmospheric drag will naturally remove most fragments within years. Above 800 km, fragments can persist for decades to centuries.`;
  } else {
    kesslerLevel = 'CONTAINED EVENT';
    kesslerColor = 'var(--green)';
    kesslerMsg = `This collision is relatively contained. ${catastrophic ? 'The small masses involved limit fragment count.' : 'Non-catastrophic: the impactor cratered rather than fully fragmenting the target.'} Atmospheric drag at this altitude will deorbit most small fragments over time.`;
  }

  // Orbital lifetime of fragments (rough)
  let lifetime;
  if (alt < 350) lifetime = 'weeks to months';
  else if (alt < 500) lifetime = '1–5 years';
  else if (alt < 700) lifetime = '5–25 years';
  else if (alt < 900) lifetime = '25–100 years';
  else lifetime = 'centuries';

  // Fragment bar max
  const maxN = Math.max(nTiny, 1);

  // Build result HTML
  const html = `
    <div class="result-hero">
      <div class="result-hero-label">Kinetic Energy Released</div>
      <div class="result-hero-val">${formatNum(keJ)}</div>
      <div class="result-hero-unit">reduced-mass · (${vel} km/s)²</div>
    </div>
    <div class="result-grid">
      <div class="result-cell">
        <div class="result-cell-label">Collision Type</div>
        <div class="result-cell-val" style="color:${catastrophic?'var(--red)':'var(--amber)'}">${catastrophic?'Catastrophic':'Cratering'}</div>
        <div class="result-cell-sub">E* = ${eStarkJ.toFixed(0)} kJ/kg${catastrophic?' (>40 threshold)':' (<40 threshold)'}</div>
      </div>
      <div class="result-cell">
        <div class="result-cell-label">Fragment Lifetime</div>
        <div class="result-cell-val" style="font-size:16px;font-style:normal;font-family:var(--mono);color:var(--muted)">${lifetime}</div>
        <div class="result-cell-sub">at ${alt} km altitude</div>
      </div>
      <div class="result-cell">
        <div class="result-cell-label">Relative Velocity</div>
        <div class="result-cell-val">${vel}</div>
        <div class="result-cell-sub">km/s · ${(vel/29.8*100).toFixed(0)}% of Earth orbital speed</div>
      </div>
      <div class="result-cell">
        <div class="result-cell-label">Reduced Mass</div>
        <div class="result-cell-val">${mu.toFixed(1)}</div>
        <div class="result-cell-sub">kg · m₁m₂/(m₁+m₂)</div>
      </div>
    </div>
    <div class="severity-wrap">
      <div class="severity-label">
        <span>Impact Severity</span>
        <span style="color:${sevColor}">${sevPct.toFixed(0)}%</span>
      </div>
      <div class="severity-bar-track"><div class="severity-bar-fill" style="width:${sevPct}%;background:${sevColor};"></div></div>
      <div class="severity-ticks"><span>Tiny</span><span>Hand grenade</span><span>Car</span><span>Bomb</span><span>Nuclear</span></div>
    </div>
    <div class="analogy-wrap">
      <div class="analogy-eyebrow">Energy equivalents — closest matches</div>
      <div class="analogy-cards">
        ${analogies.slice(Math.max(0,closestIdx-1),closestIdx+2).map((a,i) => `
          <div class="analogy-card ${i===Math.min(closestIdx,1)?'analogy-active':''}">
            <div class="analogy-icon">${a.icon}</div>
            <div class="analogy-name">${a.name}</div>
            <div class="analogy-val">${formatNum(a.kJ*1000)}</div>
          </div>
        `).join('')}
      </div>
    </div>
    <div class="fragment-wrap">
      <div class="fragment-label">NASA SBM Fragment Estimates</div>
      <div class="fragment-bars">
        <div class="fbar-row">
          <span class="fbar-cat">≥10 cm</span>
          <div class="fbar-track"><div class="fbar-fill" style="width:${(nTrackable/maxN*100)}%;background:var(--red);"></div></div>
          <span class="fbar-count">${formatCount(nTrackable)}</span>
        </div>
        <div class="fbar-row">
          <span class="fbar-cat">≥1 cm</span>
          <div class="fbar-track"><div class="fbar-fill" style="width:${(nLethal/maxN*100)}%;background:var(--amber);"></div></div>
          <span class="fbar-count">${formatCount(nLethal)}</span>
        </div>
        <div class="fbar-row">
          <span class="fbar-cat">≥1 mm</span>
          <div class="fbar-track"><div class="fbar-fill" style="width:100%;background:var(--faint);"></div></div>
          <span class="fbar-count">${formatCount(nTiny)}</span>
        </div>
      </div>
    </div>
    <div class="kessler-wrap">
      <div class="kessler-badge" style="color:${kesslerColor};border-color:${kesslerColor};background:${kesslerColor}22;">
        <div class="kessler-dot" style="background:${kesslerColor};"></div>
        ${kesslerLevel}
      </div>
      <div class="kessler-msg">${kesslerMsg}</div>
    </div>
    <div class="share-wrap">
      <span class="share-label">Share this result</span>
      <button class="share-btn" id="share-btn" onclick="shareResult(${mA},${mB},${vel},${alt})">↗ Copy Link</button>
    </div>
  `;

  document.getElementById('results-empty').style.display = 'none';
  const body = document.getElementById('results-body');
  body.style.display = 'block';
  body.innerHTML = html;
}

function shareResult(mA,mB,vel,alt) {
  const url = `${location.origin}/calculator?mA=${mA}&mB=${mB}&v=${vel}&alt=${alt}`;
  navigator.clipboard.writeText(url).then(() => {
    const btn = document.getElementById('share-btn');
    btn.textContent = '✓ Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = '↗ Copy Link'; btn.classList.remove('copied'); }, 2000);
  });
}

// ── AUTO-LOAD FROM URL PARAMS ─────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  const p = new URLSearchParams(location.search);
  if (p.has('mA')) document.getElementById('massA').value = p.get('mA');
  if (p.has('mB')) document.getElementById('massB').value = p.get('mB');
  if (p.has('v'))  document.getElementById('velRel').value = p.get('v');
  if (p.has('alt')) document.getElementById('altitude').value = p.get('alt');
  // sync sliders
  ['slMassA','slMassB','slVel'].forEach(id => {
    const linked = {slMassA:'massA',slMassB:'massB',slVel:'velRel'}[id];
    document.getElementById(id).value = document.getElementById(linked).value;
  });
  if (p.has('mA') || p.has('mB') || p.has('v')) calculate();
  else loadPreset('iridium'); // default
});
</script>
</body>
</html>"""

GLOSSARY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Space News — VectraSpace</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:ital,wght@0,400;0,500;1,400&family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --ink:#080c12; --ink2:#0d1320; --ink3:#131d2e; --panel:#0f1925;
  --border:rgba(255,255,255,0.07); --border2:rgba(255,255,255,0.13);
  --text:#ccd6e0; --muted:#8aaac5; --faint:#2a3d50;
  --accent:#4a9eff; --accent2:#7bc4ff; --green:#34d399; --amber:#f59e0b; --red:#f87171;
  --serif:"Instrument Serif",Georgia,serif;
  --mono:"DM Mono",monospace;
  --sans:"Outfit",sans-serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--ink);color:var(--text);font-family:var(--sans);line-height:1.6;overflow-x:hidden;min-height:100vh;}

/* NAV */
nav{position:fixed;top:0;left:0;right:0;z-index:100;padding:0 40px;height:60px;display:flex;align-items:center;justify-content:space-between;background:rgba(8,12,18,0.96);border-bottom:1px solid var(--border);backdrop-filter:blur(16px);}
.nav-brand{display:flex;align-items:center;text-decoration:none;color:#fff;}
.nav-brand-name{font-family:var(--serif);font-size:17px;font-style:italic;letter-spacing:-0.2px;}
.nav-brand-name em{color:var(--accent);font-style:normal;}
.nav-right{display:flex;align-items:center;gap:8px;}
.nav-back{font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--muted);text-decoration:none;padding:7px 16px;border:1px solid var(--border);border-radius:4px;transition:all 0.2s;}
.nav-back:hover{color:var(--text);border-color:var(--border2);}

/* HERO */
.hero{padding:110px 48px 48px;max-width:1160px;margin:0 auto;}
.hero-eyebrow{font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--amber);margin-bottom:16px;display:flex;align-items:center;gap:10px;}
.hero-eyebrow::before{content:"";width:14px;height:1px;background:var(--amber);display:inline-block;}
.hero-title{font-family:var(--serif);font-size:clamp(36px,5vw,60px);font-weight:400;color:#fff;line-height:1.1;letter-spacing:-0.5px;margin-bottom:14px;}
.hero-title em{font-style:italic;color:var(--accent2);}
.hero-row{display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:20px;}
.hero-body{font-size:15px;color:var(--muted);line-height:1.8;max-width:520px;}
.live-badge{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--green);background:rgba(52,211,153,0.07);border:1px solid rgba(52,211,153,0.2);padding:7px 14px;border-radius:20px;flex-shrink:0;}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 2s infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}

/* CONTROLS */
.controls{max-width:1160px;margin:0 auto;padding:0 48px 32px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;}
.search-wrap{position:relative;flex:1;min-width:220px;}
.search-icon{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--faint);pointer-events:none;}
.search-input{width:100%;background:var(--ink2);border:1px solid var(--border);border-radius:8px;padding:11px 14px 11px 42px;font-family:var(--mono);font-size:11px;color:var(--text);outline:none;transition:border-color 0.2s;letter-spacing:0.3px;}
.search-input::placeholder{color:var(--faint);}
.search-input:focus{border-color:rgba(74,158,255,0.35);}
.filters{display:flex;gap:6px;flex-wrap:wrap;}
.filter-btn{font-family:var(--mono);font-size:8px;letter-spacing:1px;text-transform:uppercase;padding:6px 14px;border-radius:20px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;transition:all 0.15s;white-space:nowrap;}
.filter-btn:hover{border-color:var(--border2);color:var(--text);}
.filter-btn.on{border-color:var(--accent);color:var(--accent);background:rgba(74,158,255,0.07);}
.filter-btn.on.amber{border-color:var(--amber);color:var(--amber);background:rgba(245,158,11,0.07);}
.filter-btn.on.green{border-color:var(--green);color:var(--green);background:rgba(52,211,153,0.07);}
.sort-btn{font-family:var(--mono);font-size:8px;letter-spacing:1px;text-transform:uppercase;padding:6px 14px;border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;transition:all 0.15s;white-space:nowrap;display:flex;align-items:center;gap:6px;}
.sort-btn:hover{color:var(--text);border-color:var(--border2);}

/* META BAR */
.meta-bar{max-width:1160px;margin:0 auto;padding:0 48px 20px;display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:9px;letter-spacing:1px;color:var(--faint);}

/* GRID */
.grid{max-width:1160px;margin:0 auto;padding:0 48px 80px;display:grid;grid-template-columns:repeat(3,1fr);gap:20px;}

/* ARTICLE CARD */
.article-card{background:var(--panel);border:1px solid var(--border);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;transition:border-color 0.2s,transform 0.2s;cursor:pointer;}
.article-card:hover{border-color:var(--border2);transform:translateY(-2px);}
.card-img{width:100%;height:180px;object-fit:cover;background:var(--ink3);display:block;}
.card-img-placeholder{width:100%;height:180px;background:linear-gradient(135deg,var(--ink3),var(--ink2));display:flex;align-items:center;justify-content:center;font-size:32px;color:var(--faint);}
.card-body{padding:18px 20px;flex:1;display:flex;flex-direction:column;gap:10px;}
.card-meta{display:flex;align-items:center;justify-content:space-between;gap:8px;}
.card-source{font-family:var(--mono);font-size:8px;letter-spacing:1.5px;text-transform:uppercase;color:var(--accent);background:rgba(74,158,255,0.08);border:1px solid rgba(74,158,255,0.15);padding:3px 9px;border-radius:12px;white-space:nowrap;}
.card-source.blog{color:var(--amber);background:rgba(245,158,11,0.08);border-color:rgba(245,158,11,0.15);}
.card-source.report{color:var(--green);background:rgba(52,211,153,0.08);border-color:rgba(52,211,153,0.15);}
.card-date{font-family:var(--mono);font-size:8px;letter-spacing:0.5px;color:var(--faint);}
.card-title{font-family:var(--serif);font-size:17px;font-style:italic;color:#fff;line-height:1.35;flex:1;}
.card-summary{font-size:12px;color:var(--muted);line-height:1.7;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;}
.card-footer{padding:12px 20px 16px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;}
.card-read{font-family:var(--mono);font-size:9px;letter-spacing:1px;text-transform:uppercase;color:var(--accent);text-decoration:none;transition:gap 0.15s;display:flex;align-items:center;gap:6px;}
.card-read:hover{color:var(--accent2);}
.card-read svg{transition:transform 0.15s;}
.card-read:hover svg{transform:translateX(3px);}
.card-tags{display:flex;gap:4px;flex-wrap:wrap;}
.card-tag{font-family:var(--mono);font-size:7px;letter-spacing:0.5px;color:var(--faint);border:1px solid var(--faint);padding:2px 7px;border-radius:8px;}

/* FEATURED CARD (spans 2 cols) */
.article-card.featured{grid-column:span 2;}
.article-card.featured .card-img,.article-card.featured .card-img-placeholder{height:240px;}
.article-card.featured .card-title{font-size:22px;}
.article-card.featured .card-summary{-webkit-line-clamp:4;}

/* SKELETON */
.skeleton{background:var(--panel);border:1px solid var(--border);border-radius:12px;overflow:hidden;animation:shimmer 1.6s infinite;}
@keyframes shimmer{0%,100%{opacity:0.6}50%{opacity:1}}
.sk-img{height:180px;background:var(--ink3);}
.sk-body{padding:18px 20px;display:flex;flex-direction:column;gap:10px;}
.sk-line{height:10px;border-radius:4px;background:var(--ink3);}

/* LOAD MORE */
.load-more-wrap{max-width:1160px;margin:0 auto;padding:0 48px 80px;display:flex;justify-content:center;}
.load-more-btn{font-family:var(--mono);font-size:10px;letter-spacing:2px;text-transform:uppercase;padding:13px 36px;border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;transition:all 0.2s;}
.load-more-btn:hover{border-color:var(--accent);color:var(--accent);}
.load-more-btn:disabled{opacity:0.4;cursor:not-allowed;}

/* ERROR / EMPTY */
.msg-box{max-width:1160px;margin:0 auto;padding:0 48px 80px;text-align:center;}
.msg-box p{font-family:var(--mono);font-size:11px;letter-spacing:1px;color:var(--faint);padding:60px 0;}
.msg-box a{color:var(--accent);}

/* RESPONSIVE */
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr);}.article-card.featured{grid-column:span 2;}}
@media(max-width:640px){
  nav{padding:0 20px;}
  .hero,.controls,.meta-bar,.grid,.load-more-wrap,.msg-box{padding-left:20px;padding-right:20px;}
  .hero{padding-top:90px;}
  .grid{grid-template-columns:1fr;}
  .article-card.featured{grid-column:span 1;}
  .article-card.featured .card-img,.article-card.featured .card-img-placeholder{height:180px;}
  .article-card.featured .card-title{font-size:17px;}
}
</style>
</head>
<body>
<nav>
  <a href="/" class="nav-brand"><span class="nav-brand-name">Vectra<em>Space</em></span></a>
  <div class="nav-right">
    <a href="/" class="nav-back">← Hub</a>
    <a href="/dashboard" class="nav-back">Dashboard →</a>
  </div>
</nav>

<div class="hero">
  <div class="hero-eyebrow">Latest Updates</div>
  <div class="hero-row">
    <div>
      <h1 class="hero-title">Space <em>News</em></h1>
      <p class="hero-body">Live feed of the latest space industry news, mission updates, and orbital events — pulled directly from Spaceflight News API.</p>
    </div>
    <div class="live-badge"><span class="live-dot"></span>Live Feed</div>
  </div>
</div>

<div class="controls">
  <div class="search-wrap">
    <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    <input type="text" class="search-input" id="ns-search" placeholder="Search headlines, missions, agencies..." autocomplete="off" spellcheck="false">
  </div>
  <div class="filters">
    <button class="filter-btn on" data-type="articles">Articles</button>
    <button class="filter-btn amber" data-type="blogs">Blogs</button>
    <button class="filter-btn green" data-type="reports">Reports</button>
  </div>
</div>

<div class="meta-bar">
  <span id="ns-meta">Loading...</span>
  <span id="ns-updated"></span>
</div>

<div class="grid" id="ns-grid"></div>
<div class="load-more-wrap"><button class="load-more-btn" id="ns-more" onclick="loadMore()" style="display:none">Load More</button></div>
<div class="msg-box" id="ns-msg" style="display:none"></div>

<script>
(function() {
  var BASE    = "https://api.spaceflightnewsapi.net/v4/";
  var TYPE    = "articles";
  var OFFSET  = 0;
  var LIMIT   = 12;
  var QUERY   = "";
  var loading = false;
  var total   = 0;
  var debounceTimer = null;

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return d.toLocaleDateString("en-US", { month:"short", day:"numeric", year:"numeric" });
  }

  function timeAgo(iso) {
    if (!iso) return "";
    var diff = (Date.now() - new Date(iso)) / 1000;
    if (diff < 3600)  return Math.floor(diff/60) + "m ago";
    if (diff < 86400) return Math.floor(diff/3600) + "h ago";
    if (diff < 604800) return Math.floor(diff/86400) + "d ago";
    return fmtDate(iso);
  }

  function skeleton() {
    var h = "";
    for (var k = 0; k < 6; k++) {
      h += '<div class="skeleton"><div class="sk-img"></div><div class="sk-body">';
      h += '<div class="sk-line" style="width:60%"></div>';
      h += '<div class="sk-line" style="width:90%"></div>';
      h += '<div class="sk-line" style="width:75%"></div>';
      h += '</div></div>';
    }
    return h;
  }

  function cardHTML(art, featured) {
    var cls = "article-card" + (featured ? " featured" : "");
    var srcCls = "card-source" + (TYPE === "blogs" ? " blog" : TYPE === "reports" ? " report" : "");
    var img = art.image_url
      ? '<img class="card-img" src="' + art.image_url + '" alt="" loading="lazy" onerror="this.style.display=\'none\';this.nextSibling.style.display=\'flex\'">'
        + '<div class="card-img-placeholder" style="display:none">🛰</div>'
      : '<div class="card-img-placeholder">🛰</div>';

    var tags = "";
    if (art.launches && art.launches.length) {
      tags += '<span class="card-tag">🚀 launch</span>';
    }
    if (art.events && art.events.length) {
      tags += '<span class="card-tag">📡 event</span>';
    }

    return '<div class="' + cls + '" onclick="window.open(\'' + art.url.replace(/'/g,"&#39;") + '\',\'_blank\')">'
      + img
      + '<div class="card-body">'
      + '<div class="card-meta"><span class="' + srcCls + '">' + (art.news_site||"") + '</span>'
      + '<span class="card-date">' + timeAgo(art.published_at) + '</span></div>'
      + '<div class="card-title">' + art.title + '</div>'
      + '<div class="card-summary">' + (art.summary||"") + '</div>'
      + '</div>'
      + '<div class="card-footer">'
      + '<a class="card-read" href="' + art.url + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">'
      + 'Read full article'
      + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>'
      + '</a>'
      + '<div class="card-tags">' + tags + '</div>'
      + '</div>'
      + '</div>';
  }

  function fetchNews(append) {
    if (loading) return;
    loading = true;
    var grid = document.getElementById("ns-grid");
    var msg  = document.getElementById("ns-msg");
    var more = document.getElementById("ns-more");
    var meta = document.getElementById("ns-meta");

    if (!append) {
      OFFSET = 0;
      grid.innerHTML = skeleton();
      more.style.display = "none";
      msg.style.display  = "none";
    }

    var url = BASE + TYPE + "/?limit=" + LIMIT + "&offset=" + OFFSET;
    if (QUERY) url += "&search=" + encodeURIComponent(QUERY);

    fetch(url)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        loading = false;
        total = data.count || 0;
        var results = data.results || [];

        if (!append) grid.innerHTML = "";

        if (results.length === 0 && !append) {
          msg.style.display = "block";
          msg.innerHTML = "<p>No articles found" + (QUERY ? ' for <strong>"' + QUERY + '"</strong>' : "") + '.<br><a href="#" onclick="clearSearch();return false">Clear search</a></p>';
          meta.textContent = "0 results";
          return;
        }

        results.forEach(function(art, idx) {
          var featured = (!append && idx === 0 && !QUERY);
          grid.innerHTML += cardHTML(art, featured);
        });

        OFFSET += results.length;
        var showing = OFFSET;
        meta.textContent = showing + " of " + total + " " + TYPE;

        var updated = document.getElementById("ns-updated");
        updated.textContent = "Updated " + new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});

        more.style.display = (OFFSET < total) ? "block" : "none";
        more.disabled = false;
        more.textContent = "Load More";
      })
      .catch(function(err) {
        loading = false;
        if (!append) {
          grid.innerHTML = "";
          msg.style.display = "block";
          msg.innerHTML = "<p>Could not load news. Check your connection or try again.<br><a href='#' onclick='fetchNews(false);return false'>Retry</a></p>";
          meta.textContent = "";
        }
      });
  }

  window.loadMore = function() {
    var btn = document.getElementById("ns-more");
    btn.disabled = true;
    btn.textContent = "Loading...";
    fetchNews(true);
  };

  window.clearSearch = function() {
    document.getElementById("ns-search").value = "";
    QUERY = "";
    fetchNews(false);
  };

  // Search with 350ms debounce
  document.getElementById("ns-search").addEventListener("input", function() {
    clearTimeout(debounceTimer);
    var val = this.value.trim();
    debounceTimer = setTimeout(function() {
      QUERY = val;
      fetchNews(false);
    }, 350);
  });

  // Type filters (Articles / Blogs / Reports)
  document.querySelectorAll(".filter-btn[data-type]").forEach(function(btn) {
    btn.addEventListener("click", function() {
      document.querySelectorAll(".filter-btn[data-type]").forEach(function(b){ b.classList.remove("on"); });
      btn.classList.add("on");
      TYPE = btn.dataset.type;
      QUERY = "";
      document.getElementById("ns-search").value = "";
      fetchNews(false);
    });
  });

  // Initial load
  fetchNews(false);
})();
</script>
</body>
</html>"""

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




    @app.post("/subscribe")
    async def subscribe(request: Request):
        """Store email subscriber."""
        import json, re
        data = await request.json()
        email = (data.get("email") or "").strip().lower()
        if not email or not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Invalid email address")
        subscribers_file = "subscribers.json"
        try:
            if os.path.exists(subscribers_file):
                with open(subscribers_file) as f:
                    subs = json.load(f)
            else:
                subs = []
        except Exception:
            subs = []
        if email not in subs:
            subs.append(email)
            with open(subscribers_file, "w") as f:
                json.dump(subs, f, indent=2)
        return {"ok": True, "count": len(subs)}

    @app.get("/calculator", response_class=HTMLResponse)
    def calculator_page():
        """Interactive collision physics calculator — no auth required."""
        return HTMLResponse(content=CALC_HTML)

    @app.get("/glossary", response_class=HTMLResponse)
    def glossary_page():
        """Searchable glossary of space safety terms — no auth required."""
        return HTMLResponse(content=GLOSSARY_HTML)

    @app.get("/scenarios", response_class=HTMLResponse)
    def scenarios_page():
        """Interactive scenario modules — Iridium-Cosmos, Kessler, ASAT, maneuver."""
        return HTMLResponse(content=SCENARIOS_HTML)

    @app.get("/kepler", response_class=HTMLResponse)
    def kepler_page():
        """Interactive Keplerian element explorer — no auth required."""
        return HTMLResponse(content=KEPLER_HTML)

    @app.get("/api/live-sats")
    async def live_sats_api(limit: int = 80, regime: str = "LEO"):
        """Stream a sample of live satellite positions from cached TLE data."""
        import json as _j, math as _m
        from datetime import datetime, timezone
        try:
            from skyfield.api import load as _load
            ts = _load.timescale()
            now = ts.now()
            cache_file = Path("tle_cache.txt")
            if not cache_file.exists():
                # Fetch a small CelesTrak subset on-demand (stations + ISS)
                import urllib.request as _ur
                tle_raw = _ur.urlopen(
                    "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle",
                    timeout=8
                ).read().decode()
                cache_file.write_text(tle_raw)
            sats = _load.tle_file(str(cache_file), reload=False)
            results = []
            R_E = 6371.0
            for s in sats[:min(limit * 3, 600)]:
                try:
                    geo = s.at(now)
                    pos = geo.position.km
                    alt = (_m.sqrt(pos[0]**2+pos[1]**2+pos[2]**2) - R_E)
                    if regime == "LEO" and not (160 < alt < 2000): continue
                    if regime == "MEO" and not (2000 <= alt < 35000): continue
                    if regime == "GEO" and not (35000 <= alt < 37000): continue
                    results.append({
                        "name": s.name,
                        "x": round(pos[0], 1),
                        "y": round(pos[1], 1),
                        "z": round(pos[2], 1),
                        "alt": round(alt, 1),
                    })
                    if len(results) >= limit: break
                except Exception:
                    continue
            return JSONResponse({
                "sats": results,
                "count": len(results),
                "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "regime": regime,
            })
        except Exception as e:
            return JSONResponse({"error": str(e), "sats": [], "count": 0}, status_code=500)

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
                    ping_t = asyncio.get_event_loop().time()
                    while not future.done():
                        while sse_logs:
                            yield send(sse_logs.pop(0))
                        now_t = asyncio.get_event_loop().time()
                        if step_idx < len(pct_steps) and now_t - last_step_t >= 4.0:
                            yield send_progress(pct_steps[step_idx], pct_msgs[step_idx])
                            step_idx += 1
                            last_step_t = now_t
                        # Send keepalive ping every 20s to prevent proxy/browser timeouts
                        if now_t - ping_t >= 20.0:
                            yield f"data: {_json.dumps({'type': 'ping'})}\\n\\n"
                            ping_t = now_t
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


    @app.get("/satellite-of-the-day")
    def satellite_of_the_day():
        """Return one featured satellite, rotating daily from a curated list."""
        import hashlib as _hash

        FEATURED = [
            {"name": "ISS (ZARYA)", "norad": 25544, "type": "Space Station",
             "fun_fact": "The ISS has been continuously inhabited since November 2, 2000 — making it the longest continuous human presence in space.",
             "color": "#4a9eff", "operator": "NASA / Roscosmos / ESA / JAXA / CSA"},
            {"name": "HUBBLE SPACE TELESCOPE", "norad": 20580, "type": "Observatory",
             "fun_fact": "Hubble has made over 1.5 million observations and its data has been used in more than 21,000 scientific papers.",
             "color": "#a78bfa", "operator": "NASA / ESA"},
            {"name": "TERRA", "norad": 25994, "type": "Earth Observation",
             "fun_fact": "Terra carries five instruments that together monitor Earth's atmosphere, land, snow/ice, and ocean simultaneously.",
             "color": "#34d399", "operator": "NASA"},
            {"name": "GPS BIIR-2  (PRN 13)", "norad": 24876, "type": "Navigation",
             "fun_fact": "Each GPS satellite broadcasts time signals accurate to 20–30 nanoseconds — without which your phone's location would drift by meters per second.",
             "color": "#f59e0b", "operator": "US Space Force"},
            {"name": "SENTINEL-2A", "norad": 40697, "type": "Earth Observation",
             "fun_fact": "Sentinel-2A images the entire Earth's land surface every 10 days at 10 m resolution — providing free, open data for agriculture, forestry, and disaster response.",
             "color": "#34d399", "operator": "ESA"},
            {"name": "STARLINK-1007", "norad": 44713, "type": "Communications",
             "fun_fact": "Starlink satellites use krypton ion thrusters to maneuver autonomously — performing thousands of collision avoidance maneuvers per year.",
             "color": "#60a5fa", "operator": "SpaceX"},
            {"name": "JASON-3", "norad": 41240, "type": "Ocean Monitoring",
             "fun_fact": "Jason-3 measures sea surface height to within 2.5 cm — tracking sea level rise, ocean currents, and hurricane intensity.",
             "color": "#06b6d4", "operator": "NOAA / EUMETSAT / NASA / CNES"},
            {"name": "AQUA", "norad": 27424, "type": "Earth Observation",
             "fun_fact": "Aqua collects data about Earth's water cycle — oceans, sea ice, clouds, precipitation, and atmospheric water vapor — every 99-minute orbit.",
             "color": "#38bdf8", "operator": "NASA"},
            {"name": "LANDSAT 9", "norad": 49260, "type": "Earth Observation",
             "fun_fact": "Landsat 9 continues a 50-year archive of Earth imagery — the longest continuous record of Earth's surface from space.",
             "color": "#86efac", "operator": "NASA / USGS"},
            {"name": "CHEOPS", "norad": 44874, "type": "Scientific",
             "fun_fact": "CHEOPS (CHaracterising ExOPlanet Satellite) measures the sizes of known exoplanets with unprecedented precision to understand whether they could be rocky, watery, or gaseous.",
             "color": "#f472b6", "operator": "ESA"},
        ]

        # Pick based on day-of-year so it's consistent for all users on the same day
        day_key = datetime.datetime.utcnow().strftime("%Y-%j")
        idx = int(_hash.md5(day_key.encode()).hexdigest(), 16) % len(FEATURED)
        sat = FEATURED[idx].copy()

        # Try to compute live orbital parameters from TLE cache
        try:
            from skyfield.api import load as _sf_load
            cache_file = cfg.tle_cache_file
            if os.path.exists(cache_file):
                _ts = _sf_load.timescale()
                sats = _sf_load.tle_file(cache_file)
                target = next((s for s in sats if str(sat["norad"]) in s.model.satnum.__str__() or
                               sat["name"].split()[0] in s.name.upper()), None)
                if target:
                    t = _ts.now()
                    geo = target.at(t)
                    sub = geo.subpoint()
                    alt_km = round(sub.elevation.km, 0)
                    # Vis-viva for circular approx: v = sqrt(mu/r)
                    mu = 398600.4418
                    r_km = 6371 + alt_km
                    v_kms = round((mu / r_km) ** 0.5, 2)
                    # Period: T = 2*pi*sqrt(r^3/mu)
                    import math as _math
                    period_min = round(2 * _math.pi * (r_km**3 / mu)**0.5 / 60, 1)
                    sat["alt_km"] = int(alt_km)
                    sat["velocity_kms"] = v_kms
                    sat["period_min"] = period_min
                    sat["lat"] = round(float(sub.latitude.degrees), 1)
                    sat["lon"] = round(float(sub.longitude.degrees), 1)
                    sat["live"] = True
        except Exception as _e:
            log.debug(f"SATOD live compute failed: {_e}")
            sat["live"] = False

        if not sat.get("live"):
            # Fallback static estimates
            STATIC = {25544: (420, 7.66, 92.9), 20580: (547, 7.59, 95.4),
                      25994: (705, 7.48, 99.0), 24876: (20200, 3.87, 718),
                      40697: (786, 7.45, 100.4), 44713: (550, 7.61, 95.5),
                      41240: (1336, 5.80, 112.4), 27424: (705, 7.48, 98.8),
                      49260: (705, 7.48, 99.0), 44874: (700, 7.49, 98.7)}
            s = STATIC.get(sat["norad"], (500, 7.62, 94.6))
            sat["alt_km"] = s[0]; sat["velocity_kms"] = s[1]; sat["period_min"] = s[2]
            sat["live"] = False

        sat["updated_utc"] = datetime.datetime.utcnow().strftime("%H:%M UTC")
        return JSONResponse(sat)

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
                    "/admin", "/admin/data", "/feedback", "/tle-status", "/scan-status",
                    "/education/orbital-mechanics", "/education/collision-prediction",
                    "/education/perturbations", "/education/debris-modeling"}

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

    # ── Education chapter pages ───────────────────────────────────
    @app.get("/education/orbital-mechanics", response_class=HTMLResponse)
    def edu_orbital():
        return HTMLResponse(content=_EDU_ORBITAL_HTML)

    @app.get("/education/collision-prediction", response_class=HTMLResponse)
    def edu_collision():
        return HTMLResponse(content=_EDU_COLLISION_HTML)

    @app.get("/education/perturbations", response_class=HTMLResponse)
    def edu_perturbations():
        return HTMLResponse(content=_EDU_PERTURBATIONS_HTML)

    @app.get("/education/debris-modeling", response_class=HTMLResponse)
    def edu_debris():
        return HTMLResponse(content=_EDU_DEBRIS_HTML)

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
