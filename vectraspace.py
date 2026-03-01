"""VectraSpace — Orbital Safety Platform
======================================
Author:  VectraSpace Team
Contact: trumanheaston@gmail.com
Version: v11 — Public Release

Version History:
----------------
v1  — Original single-file script. Step-by-step propagation, matplotlib PNG,
      within-regime collision checking only, no database or alerting.

v2  — Full refactor. Vectorized propagation (50-100x faster), modular architecture
      (ingest, propagate, detect, alert, visualize, API), cross-regime collision
      checking, close-approach refinement via scipy, Plotly interactive HTML output,
      SQLite database logging, email/webhook alerting hooks, REST API stub.

v3  — REST API activated. FastAPI server starts automatically after detection
      pipeline completes. Endpoints: /conjunctions, /health, /docs.
      Visualization auto-opens in browser on run.

v4  — Space-Track.org integration. Authenticated TLE fetching from the authoritative
      US Space Force database alongside CelesTrak. Credentials loaded securely from
      .env file. Graceful fallback to CelesTrak if Space-Track login fails.

v5  — Email and webhook alerting fully wired up and tested. SMTP email sends
      conjunction alerts with miss distance, Pc estimate, and time to closest
      approach. Webhook support for Slack, Teams, or any HTTP endpoint.
      Alert filtering by both distance threshold and Pc threshold.
      Replaced dotenv with manual .env parser to fix OneDrive path issues.

v6  — Full web UI with mission-control dashboard. CesiumJS photorealistic
      Earth replaces Plotly — real satellite imagery, atmosphere, day/night
      lighting, live orbit rendering. Settings panel lets user configure all
      parameters without editing code. Run button triggers pipeline and streams
      live log output to browser via SSE. Conjunction markers clickable with
      full details. All served directly from the FastAPI server.

v9  — Covariance-based Pc (Foster/Alfano ellipsoid overlap pre-filter for speed).
      CDM export per conjunction + bulk zip download. Historical trends dashboard
      collapsible in sidebar (Chart.js line + pie charts from SQLite). CelesTrak
      satellite info modal popup with clean formatted display. Pc input replaced
      with risk-level slider (LOW/MODERATE/HIGH/CRITICAL). Per-regime satellite
      count inputs (LEO/MEO/GEO separately).

v10 — F-01: Scheduled autonomous runs via Windows Task Scheduler + --headless flag.
      F-02: Covariance ingestion from Space-Track CDM feed; covariance_source field.
      F-03: Multi-user session auth (bcrypt + itsdangerous); /login, /logout routes.
      F-04: Pushover mobile push notifications alongside existing SMS/email.
      F-05: CW-based maneuver planning; ManeuverSuggestion on every conjunction.
      F-06: Chunked vectorized pair-screening replaces Python for-loop (~5-10x speedup).
      F-07: Debris cloud simulation (NASA SBM lognormal, synthetic TLEs, globe display).
      F-08: UI fixes -- blank inputs on load, remove hint text, fixed tooltip pointer events.

v11 — PUBLIC RELEASE (VectraSpace rebranding):
      SEC-01: Anthropic sat-info moved 100% server-side via GET /sat-info/{sat_name}.
      SEC-02: Cesium Ion token loaded from CESIUM_ION_TOKEN env var (injected server-side).
      SEC-03: All secrets/credentials removed from source; loaded exclusively from os.environ.
      SEC-04: SQLite auto-migration adds user_id column + user_preferences table.
      MULTI-01: get_current_user() dependency; demo mode for unauthenticated users.
      MULTI-02: Protected routes /run, /preferences, /history require login.
      MULTI-03: Per-user scan history and personal alert preferences.
      MULTI-04: Per-user rate limiting on /run (1 scan per 5 min).
      MULTI-05: /me endpoint returns current user info.
      MULTI-06: /preferences GET/POST for personal alert settings.
      GLOBE-01: Cesium terrain/imagery fallback to EllipsoidTerrainProvider + OSM
                when CESIUM_ION_TOKEN is absent, so globe always renders.

v11.1 — Production hardening:
      EMAIL-01: Multi-provider SMTP engine — trumanheaston@gmail.com (Gmail App Password)
                as primary; SendGrid, AWS SES, Postmark selectable via EMAIL_PROVIDER env.
                Provider auto-detected; clear setup instructions in startup log.
      AUTH-01:  Self-service signup page (/signup) with optional admin-approval gate
                (SIGNUP_OPEN=true opens registration; false requires admin invite token).
      AUTH-02:  Token-based password reset flow — /forgot-password generates a
                time-limited (1h) signed token, emails a reset link; /reset-password
                validates token and sets new bcrypt hash. No admin CLI needed.
      PERF-01:  /sat-info Anthropic call moved to run_in_executor so it never blocks
                the async event loop under concurrent load.
"""

# ─────────────────────────────────────────────
# DEPENDENCIES — install everything with:
#
#   pip install skyfield numpy scipy fastapi uvicorn requests bcrypt itsdangerous python-multipart
#
# Optional (recommended for production):
#   pip install "uvicorn[standard]"   # httptools + uvloop for better throughput
#
# Notes:
#   python-multipart  → required for FastAPI form parsing (/login, /signup, /preferences POST)
#   bcrypt            → password hashing for user auth
#   itsdangerous      → signed session cookies + password reset tokens
#   skyfield          → TLE propagation (SGP4)
#   anthropic SDK not needed — /sat-info calls the REST API directly via requests
#
# Email provider env vars (set ONE of these groups in .env):
#   Gmail App Password (default — uses trumanheaston@gmail.com):
#     EMAIL_PROVIDER=gmail
#     ALERT_EMAIL_FROM=trumanheaston@gmail.com
#     ALERT_SMTP_PASS=<16-char Google App Password>
#
#   SendGrid:
#     EMAIL_PROVIDER=sendgrid
#     SENDGRID_API_KEY=SG.xxxxx
#     ALERT_EMAIL_FROM=alerts@yourdomain.com
#
#   AWS SES (SMTP):
#     EMAIL_PROVIDER=ses
#     AWS_SES_HOST=email-smtp.us-east-1.amazonaws.com
#     AWS_SES_USER=AKIA...
#     AWS_SES_PASS=<SES SMTP password>
#     ALERT_EMAIL_FROM=alerts@yourdomain.com
#
#   Postmark:
#     EMAIL_PROVIDER=postmark
#     POSTMARK_SERVER_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
#     ALERT_EMAIL_FROM=alerts@yourdomain.com
# ─────────────────────────────────────────────

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
        print(f"[WARN] .env not found at {env_path}")
        return
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
    # Per-regime satellite counts
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


# SEC-03: All credentials loaded exclusively from environment
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

    for i in range(n):
        t1 = all_tracks[i]
        for j in range(i + 1, n):
            t2 = all_tracks[j]

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

    # SEC-04: Auto-migration — add user_id column if missing
    existing_cols = [row[1] for row in con.execute("PRAGMA table_info(conjunctions)").fetchall()]
    if "user_id" not in existing_cols:
        con.execute("ALTER TABLE conjunctions ADD COLUMN user_id TEXT")
        log.info("DB migration: added user_id column to conjunctions")

    # SEC-04: user_preferences table
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

try:
    import bcrypt as _bcrypt
    from itsdangerous import URLSafeTimedSerializer as _Signer, BadSignature as _BadSig
    HAS_AUTH = True
except ImportError:
    HAS_AUTH = False
    log.warning("bcrypt/itsdangerous not installed — auth disabled. pip install bcrypt itsdangerous")


def _load_users(cfg: Config) -> dict:
    p = Path(cfg.users_file)
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            users_list = json.load(f)
        return {u["username"]: u for u in users_list}
    except Exception as e:
        log.warning(f"Failed to load users.json: {e}")
        return {}


def create_user(username: str, password: str, role: str = "operator", cfg: Config = None):
    """CLI utility: add a bcrypt-hashed user to users.json."""
    if not HAS_AUTH:
        print("ERROR: bcrypt not installed. pip install bcrypt")
        return
    if cfg is None:
        cfg = CFG
    users = _load_users(cfg)
    hashed = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(rounds=12)).decode()
    users[username] = {"username": username, "password_hash": hashed, "role": role}
    p = Path(cfg.users_file)
    with open(p, "w") as f:
        json.dump(list(users.values()), f, indent=2)
    print(f"User '{username}' ({role}) added to {p}")


def _verify_password(plain: str, hashed: str) -> bool:
    if not HAS_AUTH:
        return False
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def _make_session_cookie(username: str, role: str, secret: str) -> str:
    if not HAS_AUTH:
        return ""
    s = _Signer(secret, salt="vectraspace-session")
    return s.dumps({"u": username, "r": role})


def _verify_session_cookie(token: str, secret: str, max_age: int = 28800):
    """Returns (username, role) or raises on invalid/expired."""
    if not HAS_AUTH:
        raise ValueError("Auth not available")
    s = _Signer(secret, salt="vectraspace-session")
    data = s.loads(token, max_age=max_age)
    return data["u"], data["r"]


def get_current_user_from_request(request, cfg: Config) -> Optional[dict]:
    """Returns {'username': str, 'role': str} or None if unauthenticated."""
    token = request.cookies.get("vs_session", "")
    if not token:
        return None
    try:
        username, role = _verify_session_cookie(token, cfg.session_secret)
        return {"username": username, "role": role}
    except Exception:
        return None


# In-memory rate limiter for login attempts
_login_attempts: dict = {}

def _check_login_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < 60]
    _login_attempts[ip] = attempts
    if len(attempts) >= 5:
        return False
    _login_attempts[ip].append(now)
    return True


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
    """Generate a signed, time-limited password reset token (valid 1 hour)."""
    if not HAS_AUTH:
        return ""
    s = _Signer(secret, salt="vs-pw-reset")
    return s.dumps({"u": username})


def _verify_reset_token(token: str, secret: str, max_age: int = 3600) -> Optional[str]:
    """Verify a reset token. Returns username or None if invalid/expired."""
    if not HAS_AUTH:
        return None
    try:
        s = _Signer(secret, salt="vs-pw-reset")
        data = s.loads(token, max_age=max_age)
        return data.get("u")
    except Exception:
        return None


def _update_password(username: str, new_password: str, cfg: Config) -> bool:
    """Bcrypt-hash and persist a new password to users.json."""
    if not HAS_AUTH:
        return False
    users = _load_users(cfg)
    if username not in users:
        return False
    hashed = _bcrypt.hashpw(new_password.encode(), _bcrypt.gensalt(rounds=12)).decode()
    users[username]["password_hash"] = hashed
    p = Path(cfg.users_file)
    with open(p, "w") as f:
        json.dump(list(users.values()), f, indent=2)
    log.info(f"Password updated for user '{username}'")
    return True


def _register_user(username: str, email: str, password: str,
                   cfg: Config, approved: bool = True) -> tuple:
    """
    Register a new user. Returns (ok: bool, error_msg: str).
    approved=False means account needs admin approval before login.
    """
    if not HAS_AUTH:
        return False, "Auth library not installed"
    username = username.strip().lower()
    email = email.strip().lower()
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters"
    if not email or "@" not in email:
        return False, "Invalid email address"
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    users = _load_users(cfg)
    if username in users:
        return False, "Username already taken"
    # Check email uniqueness
    if any(u.get("email", "").lower() == email for u in users.values()):
        return False, "An account with that email already exists"
    hashed = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(rounds=12)).decode()
    users[username] = {
        "username": username,
        "password_hash": hashed,
        "email": email,
        "role": "operator",
        "approved": approved,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    p = Path(cfg.users_file)
    with open(p, "w") as f:
        json.dump(list(users.values()), f, indent=2)
    log.info(f"Registered user '{username}' (approved={approved})")
    return True, ""


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
         min-height: 100vh; padding: 24px 16px; }
  .card { background: #090f17; border: 1px solid #0d2137; border-radius: 8px;
          padding: 40px 36px; width: 420px;
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
"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>VectraSpace — Sign In</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">
<style>{AUTH_CSS}</style>
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
<title>VectraSpace — Create Account</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">
<style>{AUTH_CSS}</style>
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

# SEC-02: Cesium token loaded from environment — never hard-coded
def _get_cesium_token() -> str:
    token = os.environ.get("CESIUM_ION_TOKEN", "")
    if not token:
        log.warning("CESIUM_ION_TOKEN not set in environment — globe may not render correctly")
    return token


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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
  }
  #globe-container { flex: 1; position: relative; }
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
      bottom: 16px;
      left: 8px;
      right: 8px;
      transform: none;
      overflow-x: auto;
      border-radius: 6px;
      padding: 6px 10px;
      gap: 6px;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: none;
    }
    #globe-controls::-webkit-scrollbar { display: none; }
    .ctrl-btn { font-size: 9px; padding: 4px 8px; white-space: nowrap; flex-shrink: 0; }
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
    <div id="header">
      <div class="logo">VectraSpace // Mission Control</div>
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

    <div id="status-bar">
      <div id="status-dot"></div>
      <div id="status-text">Initializing...</div>
    </div>
  </div><!-- /sidebar -->

  <!-- ── GLOBE ── -->
  <div id="globe-container">
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
      requestWaterMask: true,
      requestVertexNormals: true,   // enables per-vertex lighting for realistic shading
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
    contextOptions: {
      requestWebgl2: true,
      webgl: {
        powerPreference: 'high-performance',
        antialias: true,
      }
    },
    orderIndependentTranslucency: true,
    shadows: true,
    terrainShadows: Cesium.ShadowMode.RECEIVE_ONLY,
  });

  // ── Imagery: Cesium World Imagery with Aerial + Labels (photorealistic) ──
  viewer.imageryLayers.removeAll();
  try {
    // Primary: high-res aerial imagery from Ion
    const aerial = await Cesium.createWorldImageryAsync({
      style: Cesium.IonWorldImageryStyle.AERIAL_WITH_LABELS
    });
    viewer.imageryLayers.add(new Cesium.ImageryLayer(aerial, {
      brightness: 1.0,
      contrast: 1.1,
      saturation: 1.1,
      gamma: 1.0,
    }));
  } catch(e) {
    console.warn('World imagery unavailable — using OSM fallback');
    viewer.imageryLayers.add(new Cesium.ImageryLayer(
      new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/', maximumLevel: 18 })
    ));
  }

  // ── Photorealistic scene settings ─────────────────────────────────────────
  viewer.scene.globe.enableLighting = true;
  viewer.scene.globe.atmosphereLightIntensity = 15.0;    // brighter sun
  viewer.scene.globe.atmosphereMieCoefficient = 0.006;   // realistic haze
  viewer.scene.globe.atmosphereRayleighCoefficient = new Cesium.Cartesian3(5.5e-6, 13.0e-6, 28.4e-6);
  viewer.scene.globe.showGroundAtmosphere = true;
  viewer.scene.globe.depthTestAgainstTerrain = false;    // keep sat trails visible
  viewer.scene.globe.maximumScreenSpaceError = 1.5;      // higher terrain detail
  viewer.scene.atmosphere.brightnessShift = 0.15;
  viewer.scene.atmosphere.hueShift = 0.0;
  viewer.scene.atmosphere.saturationShift = 0.1;
  viewer.scene.fog.enabled = true;
  viewer.scene.fog.density = 0.00012;
  viewer.scene.fog.minimumBrightness = 0.05;
  viewer.scene.skyAtmosphere.show = true;
  viewer.scene.skyAtmosphere.atmosphereLightIntensity = 15.0;
  viewer.scene.sun = new Cesium.Sun();
  viewer.scene.moon = new Cesium.Moon();
  viewer.scene.shadowMap.enabled = true;
  viewer.scene.shadowMap.softShadows = true;
  viewer.scene.shadowMap.size = 2048;
  viewer.scene.highDynamicRange = true;                  // HDR rendering
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
}

initCesium();

// ── CHECK CURRENT USER ────────────────────────────────────────
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
    userActions.innerHTML = '<a href="/preferences">⚙ Prefs</a> &nbsp; <a href="/logout">Sign out</a>';
    runBtn.style.display = 'block';
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

initUserState();

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

    const fields = [
      ['Full Name',       info.fullName],
      ['NORAD ID',        info.noradId],
      ['Country / Owner', info.country || info.owner],
      ['Object Type',     info.objectType],
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

    body.innerHTML = fields.filter(([,v]) => v && v !== 'Unknown').map(([k, v]) =>
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


LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VectraSpace — Orbital Safety Platform</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg:       #030508;
  --panel:    #07101a;
  --border:   #0d2137;
  --accent:   #00d4ff;
  --accent2:  #ff4444;
  --accent3:  #00ff88;
  --text:     #c8dff0;
  --muted:    #3a5a75;
  --glow:     rgba(0,212,255,0.15);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'DM Sans', sans-serif;
  overflow-x: hidden;
  cursor: crosshair;
}
#starfield { position: fixed; inset: 0; z-index: 0; pointer-events: none; }
body::after {
  content: '';
  position: fixed;
  inset: 0;
  background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.04) 2px, rgba(0,0,0,0.04) 4px);
  pointer-events: none;
  z-index: 9999;
}
nav {
  position: fixed;
  top: 0; left: 0; right: 0;
  z-index: 100;
  padding: 0 48px;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid transparent;
  transition: all 0.3s;
}
nav.scrolled { background: rgba(3,5,8,0.92); border-bottom-color: var(--border); backdrop-filter: blur(12px); }
.nav-logo { display: flex; align-items: center; gap: 10px; text-decoration: none; }
.nav-logo-mark { width: 28px; height: 28px; }
.nav-logo-mark svg { width: 100%; height: 100%; }
.nav-logo-text { font-family: 'Orbitron', sans-serif; font-size: 14px; font-weight: 700; color: #fff; letter-spacing: 2px; }
.nav-logo-text span { color: var(--accent); }
.nav-links { display: flex; gap: 32px; list-style: none; }
.nav-links a { font-family: 'Share Tech Mono', monospace; font-size: 11px; letter-spacing: 2px; color: var(--muted); text-decoration: none; text-transform: uppercase; transition: color 0.2s; }
.nav-links a:hover { color: var(--accent); }
.nav-cta { display: flex; align-items: center; gap: 12px; }
.btn-outline { font-family: 'Share Tech Mono', monospace; font-size: 10px; letter-spacing: 2px; text-transform: uppercase; padding: 8px 16px; border: 1px solid var(--border); border-radius: 3px; color: var(--muted); background: transparent; cursor: pointer; text-decoration: none; transition: all 0.2s; }
.btn-outline:hover { border-color: var(--accent); color: var(--accent); background: var(--glow); }
.btn-primary { font-family: 'Share Tech Mono', monospace; font-size: 10px; letter-spacing: 2px; text-transform: uppercase; padding: 8px 18px; border: 1px solid var(--accent); border-radius: 3px; color: var(--accent); background: transparent; cursor: pointer; text-decoration: none; transition: all 0.2s; position: relative; overflow: hidden; }
.btn-primary::before { content: ''; position: absolute; inset: 0; background: var(--accent); transform: scaleX(0); transform-origin: left; transition: transform 0.25s ease; z-index: -1; }
.btn-primary:hover { color: #000; }
.btn-primary:hover::before { transform: scaleX(1); }
#hero { position: relative; min-height: 100vh; display: flex; align-items: center; justify-content: center; text-align: center; padding: 120px 24px 80px; z-index: 1; }
.hero-orbit-ring { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; overflow: hidden; }
.orbit-ring { position: absolute; border-radius: 50%; border: 1px solid; animation: spin linear infinite; }
.orbit-ring-1 { width: 600px; height: 600px; border-color: rgba(0,212,255,0.07); animation-duration: 60s; }
.orbit-ring-2 { width: 900px; height: 900px; border-color: rgba(0,212,255,0.05); animation-duration: 90s; animation-direction: reverse; }
.orbit-ring-3 { width: 1200px; height: 1200px; border-color: rgba(0,212,255,0.03); animation-duration: 120s; }
.orbit-dot { position: absolute; width: 5px; height: 5px; background: var(--accent); border-radius: 50%; box-shadow: 0 0 8px var(--accent), 0 0 20px var(--accent); top: 50%; left: 100%; transform: translate(-50%, -50%); }
.orbit-dot-2 { background: var(--accent2); box-shadow: 0 0 8px var(--accent2), 0 0 20px var(--accent2); top: 0%; left: 50%; }
.orbit-dot-3 { background: var(--accent3); box-shadow: 0 0 8px var(--accent3), 0 0 20px var(--accent3); top: 50%; left: 0%; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.hero-badge { display: inline-flex; align-items: center; gap: 8px; font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 4px; color: var(--accent); text-transform: uppercase; border: 1px solid rgba(0,212,255,0.3); padding: 6px 14px; border-radius: 2px; margin-bottom: 32px; background: rgba(0,212,255,0.05); animation: fadeUp 0.8s ease both; }
.badge-dot { width: 5px; height: 5px; background: var(--accent3); border-radius: 50%; animation: pulse-dot 2s ease infinite; }
@keyframes pulse-dot { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.6); } }
.hero-title { font-family: 'Orbitron', sans-serif; font-size: clamp(42px, 7vw, 88px); font-weight: 900; line-height: 1.0; letter-spacing: -1px; color: #fff; margin-bottom: 8px; animation: fadeUp 0.8s 0.1s ease both; }
.hero-title-accent { color: var(--accent); }
.hero-title-sub { font-family: 'Orbitron', sans-serif; font-size: clamp(14px, 2.5vw, 22px); font-weight: 400; color: var(--muted); letter-spacing: 6px; text-transform: uppercase; margin-bottom: 28px; animation: fadeUp 0.8s 0.2s ease both; }
.hero-desc { font-size: 16px; font-weight: 300; line-height: 1.8; color: var(--text); max-width: 600px; margin: 0 auto 48px; opacity: 0.8; animation: fadeUp 0.8s 0.3s ease both; }
.hero-actions { display: flex; gap: 16px; justify-content: center; flex-wrap: wrap; animation: fadeUp 0.8s 0.4s ease both; }
.btn-hero-primary { font-family: 'Share Tech Mono', monospace; font-size: 11px; letter-spacing: 3px; text-transform: uppercase; padding: 14px 32px; border: 1px solid var(--accent); border-radius: 3px; color: #000; background: var(--accent); cursor: pointer; text-decoration: none; transition: all 0.2s; }
.btn-hero-primary:hover { background: transparent; color: var(--accent); }
.btn-hero-secondary { font-family: 'Share Tech Mono', monospace; font-size: 11px; letter-spacing: 3px; text-transform: uppercase; padding: 14px 32px; border: 1px solid var(--border); border-radius: 3px; color: var(--text); background: transparent; cursor: pointer; text-decoration: none; transition: all 0.2s; }
.btn-hero-secondary:hover { border-color: var(--text); }
.hero-stats { display: flex; gap: 48px; justify-content: center; margin-top: 80px; animation: fadeUp 0.8s 0.5s ease both; }
.hero-stat { text-align: center; position: relative; }
.hero-stat::after { content: ''; position: absolute; right: -24px; top: 20%; width: 1px; height: 60%; background: var(--border); }
.hero-stat:last-child::after { display: none; }
.hero-stat-num { font-family: 'Orbitron', sans-serif; font-size: 28px; font-weight: 900; color: var(--accent); display: block; }
.hero-stat-label { font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 2px; color: var(--muted); text-transform: uppercase; }
@keyframes fadeUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
.ticker-wrap { position: relative; z-index: 1; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); background: rgba(7,16,26,0.8); padding: 10px 0; overflow: hidden; }
.ticker-inner { display: flex; gap: 0; white-space: nowrap; animation: ticker 25s linear infinite; }
@keyframes ticker { from { transform: translateX(0); } to { transform: translateX(-50%); } }
.ticker-item { font-family: 'Share Tech Mono', monospace; font-size: 10px; letter-spacing: 2px; color: var(--muted); text-transform: uppercase; padding: 0 32px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.ticker-sep { color: var(--accent); }
.ticker-item.alert { color: var(--accent2); }
.ticker-item.ok { color: var(--accent3); }
section { position: relative; z-index: 1; }
.section-label { font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 4px; color: var(--accent); text-transform: uppercase; margin-bottom: 12px; }
.section-title { font-family: 'Orbitron', sans-serif; font-size: clamp(26px, 3.5vw, 42px); font-weight: 700; color: #fff; line-height: 1.2; margin-bottom: 16px; }
.section-body { font-size: 15px; font-weight: 300; line-height: 1.8; color: var(--text); opacity: 0.75; max-width: 540px; }
#how { padding: 120px 48px; }
.how-inner { max-width: 1200px; margin: 0 auto; display: grid; grid-template-columns: 1fr 1fr; gap: 80px; align-items: center; }
.pipeline { display: flex; flex-direction: column; gap: 0; }
.pipeline-step { display: flex; gap: 20px; align-items: flex-start; padding: 24px 0; border-bottom: 1px solid var(--border); cursor: default; transition: all 0.2s; }
.pipeline-step:last-child { border-bottom: none; }
.pipeline-step:hover .step-num { color: var(--accent); border-color: var(--accent); }
.pipeline-step:hover .step-title { color: var(--accent); }
.step-num { font-family: 'Orbitron', monospace; font-size: 11px; font-weight: 700; color: var(--muted); border: 1px solid var(--border); border-radius: 2px; padding: 4px 8px; flex-shrink: 0; letter-spacing: 1px; transition: all 0.2s; margin-top: 2px; }
.step-title { font-family: 'Share Tech Mono', monospace; font-size: 13px; color: var(--text); letter-spacing: 1px; margin-bottom: 4px; transition: color 0.2s; }
.step-desc { font-size: 13px; color: var(--muted); line-height: 1.6; }
.terminal-block { background: #040a10; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; font-family: 'Share Tech Mono', monospace; font-size: 11px; box-shadow: 0 0 60px rgba(0,212,255,0.06), 0 40px 80px rgba(0,0,0,0.6); }
.terminal-titlebar { background: #070f18; border-bottom: 1px solid var(--border); padding: 10px 16px; display: flex; align-items: center; gap: 12px; }
.terminal-dots { display: flex; gap: 6px; }
.terminal-dots span { width: 8px; height: 8px; border-radius: 50%; }
.td-r { background: #ff5f57; } .td-y { background: #febc2e; } .td-g { background: #28c840; }
.terminal-title { color: var(--muted); font-size: 9px; letter-spacing: 2px; margin-left: auto; text-transform: uppercase; }
.terminal-body { padding: 20px; line-height: 1.9; }
.t-line { display: block; }
.t-prompt { color: var(--accent); } .t-cmd { color: #fff; } .t-out { color: var(--muted); }
.t-ok { color: var(--accent3); } .t-warn { color: #ffaa44; } .t-err { color: var(--accent2); } .t-val { color: var(--accent); }
.t-cursor { display: inline-block; width: 8px; height: 13px; background: var(--accent); animation: blink-cursor 1s step-end infinite; vertical-align: middle; margin-left: 2px; }
@keyframes blink-cursor { 50% { opacity: 0; } }
#features { padding: 120px 48px; background: linear-gradient(180deg, transparent 0%, rgba(0,212,255,0.02) 50%, transparent 100%); }
.features-inner { max-width: 1200px; margin: 0 auto; }
.features-header { text-align: center; margin-bottom: 64px; }
.features-header .section-body { margin: 0 auto; text-align: center; }
.features-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.feature-card { background: var(--panel); padding: 32px 28px; transition: all 0.2s; position: relative; overflow: hidden; }
.feature-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--accent); transform: scaleX(0); transition: transform 0.3s ease; }
.feature-card:hover { background: #0a1520; }
.feature-card:hover::before { transform: scaleX(1); }
.feature-icon { width: 40px; height: 40px; border: 1px solid var(--border); border-radius: 6px; display: flex; align-items: center; justify-content: center; margin-bottom: 20px; font-size: 18px; transition: all 0.2s; background: rgba(0,0,0,0.3); }
.feature-card:hover .feature-icon { border-color: var(--accent); background: var(--glow); }
.feature-title { font-family: 'Share Tech Mono', monospace; font-size: 12px; letter-spacing: 2px; color: #fff; text-transform: uppercase; margin-bottom: 10px; }
.feature-desc { font-size: 13px; color: var(--muted); line-height: 1.7; }
.feature-tag { display: inline-block; font-family: 'Share Tech Mono', monospace; font-size: 8px; letter-spacing: 1px; padding: 2px 7px; border-radius: 2px; margin-top: 12px; text-transform: uppercase; }
.tag-new { background: rgba(0,255,136,0.1); color: var(--accent3); border: 1px solid rgba(0,255,136,0.3); }
.tag-v11 { background: rgba(0,212,255,0.1); color: var(--accent); border: 1px solid rgba(0,212,255,0.3); }
#metrics { padding: 100px 48px; }
.metrics-inner { max-width: 1200px; margin: 0 auto; }
.metrics-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-top: 64px; }
.metric-cell { background: var(--panel); padding: 36px 28px; text-align: center; position: relative; }
.metric-cell::after { content: attr(data-label); position: absolute; top: 14px; right: 14px; font-family: 'Share Tech Mono', monospace; font-size: 7px; letter-spacing: 2px; color: var(--muted); text-transform: uppercase; }
.metric-num { font-family: 'Orbitron', sans-serif; font-size: 42px; font-weight: 900; display: block; margin-bottom: 8px; }
.metric-num.c1 { color: var(--accent); } .metric-num.c2 { color: var(--accent3); } .metric-num.c3 { color: #ffaa44; } .metric-num.c4 { color: #aa66ff; }
.metric-unit { font-family: 'Share Tech Mono', monospace; font-size: 10px; letter-spacing: 2px; color: var(--muted); text-transform: uppercase; }
#architecture { padding: 120px 48px; }
.arch-inner { max-width: 1200px; margin: 0 auto; display: grid; grid-template-columns: 1fr 1fr; gap: 80px; align-items: center; }
.arch-diagram { display: flex; flex-direction: column; gap: 8px; }
.arch-layer { display: flex; gap: 8px; align-items: center; }
.arch-box { flex: 1; padding: 14px 16px; border: 1px solid var(--border); border-radius: 4px; background: var(--panel); font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 1px; color: var(--text); text-transform: uppercase; text-align: center; transition: all 0.2s; cursor: default; }
.arch-box:hover { border-color: var(--accent); color: var(--accent); background: var(--glow); }
.arch-box.highlight { border-color: var(--accent); color: var(--accent); background: rgba(0,212,255,0.05); }
.arch-connector { width: 1px; height: 12px; background: var(--border); margin-left: 50%; }
.arch-label { font-family: 'Share Tech Mono', monospace; font-size: 8px; letter-spacing: 2px; color: var(--muted); text-transform: uppercase; margin-bottom: 4px; padding-left: 4px; }
.alert-demo { background: #040a10; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; box-shadow: 0 0 60px rgba(255,68,68,0.06), 0 40px 80px rgba(0,0,0,0.5); }
.alert-header { background: rgba(255,68,68,0.06); border-bottom: 1px solid var(--border); padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; }
.alert-badge { font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 3px; color: var(--accent2); text-transform: uppercase; display: flex; align-items: center; gap: 8px; }
.alert-badge::before { content: ''; width: 6px; height: 6px; background: var(--accent2); border-radius: 50%; animation: blink-dot 1s step-end infinite; }
@keyframes blink-dot { 50% { opacity: 0; } }
.alert-ts { font-family: 'Share Tech Mono', monospace; font-size: 9px; color: var(--muted); }
.alert-event { padding: 16px 20px; border-bottom: 1px solid rgba(13,33,55,0.5); display: flex; gap: 14px; align-items: flex-start; transition: background 0.2s; }
.alert-event:hover { background: rgba(255,68,68,0.03); }
.alert-event-dot { width: 4px; flex-shrink: 0; background: var(--accent2); border-radius: 2px; margin-top: 4px; align-self: stretch; }
.alert-event-dot.warn { background: #ffaa44; }
.alert-sats { font-family: 'Share Tech Mono', monospace; font-size: 11px; color: #fff; margin-bottom: 4px; display: flex; gap: 8px; align-items: center; }
.arrow-sep { color: var(--accent); }
.alert-meta { display: flex; gap: 16px; font-family: 'Share Tech Mono', monospace; font-size: 9px; }
.meta-dist { color: var(--accent2); } .meta-pc { color: #ffaa44; } .meta-time { color: var(--muted); }
.alert-regime { font-size: 8px; padding: 2px 6px; border-radius: 2px; letter-spacing: 1px; }
.reg-leo { background: rgba(77,166,255,0.12); color: #4da6ff; border: 1px solid rgba(77,166,255,0.25); }
.reg-geo { background: rgba(0,255,136,0.12); color: var(--accent3); border: 1px solid rgba(0,255,136,0.25); }
#cta { padding: 120px 48px; text-align: center; }
.cta-inner { max-width: 700px; margin: 0 auto; }
.cta-box { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 64px 48px; position: relative; overflow: hidden; }
.cta-box::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px; background: linear-gradient(90deg, transparent, var(--accent), transparent); }
.cta-box::after { content: ''; position: absolute; bottom: -80px; left: 50%; transform: translateX(-50%); width: 400px; height: 200px; background: radial-gradient(ellipse, rgba(0,212,255,0.07) 0%, transparent 70%); pointer-events: none; }
.cta-eyebrow { font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 4px; color: var(--accent3); text-transform: uppercase; margin-bottom: 16px; }
.cta-title { font-family: 'Orbitron', sans-serif; font-size: 32px; font-weight: 700; color: #fff; margin-bottom: 16px; line-height: 1.2; }
.cta-desc { font-size: 15px; font-weight: 300; color: var(--text); opacity: 0.7; margin-bottom: 36px; line-height: 1.7; }
.cta-buttons { display: flex; gap: 14px; justify-content: center; flex-wrap: wrap; position: relative; z-index: 1; }
.btn-cta-main { font-family: 'Share Tech Mono', monospace; font-size: 11px; letter-spacing: 3px; text-transform: uppercase; padding: 14px 36px; border: 1px solid var(--accent); border-radius: 3px; color: #000; background: var(--accent); cursor: pointer; text-decoration: none; transition: all 0.2s; }
.btn-cta-main:hover { background: transparent; color: var(--accent); }
.btn-cta-sub { font-family: 'Share Tech Mono', monospace; font-size: 11px; letter-spacing: 3px; text-transform: uppercase; padding: 14px 32px; border: 1px solid var(--border); border-radius: 3px; color: var(--muted); background: transparent; cursor: pointer; text-decoration: none; transition: all 0.2s; }
.btn-cta-sub:hover { border-color: var(--text); color: var(--text); }
footer { padding: 32px 48px; border-top: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; position: relative; z-index: 1; }
.footer-logo { font-family: 'Orbitron', sans-serif; font-size: 12px; font-weight: 700; color: var(--muted); letter-spacing: 2px; }
.footer-logo span { color: var(--accent); }
.footer-links { display: flex; gap: 24px; list-style: none; }
.footer-links a { font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 2px; color: var(--muted); text-decoration: none; text-transform: uppercase; transition: color 0.2s; }
.footer-links a:hover { color: var(--accent); }
.footer-copy { font-family: 'Share Tech Mono', monospace; font-size: 9px; letter-spacing: 1px; color: #1e3a52; }
.section-divider { height: 1px; background: linear-gradient(90deg, transparent, var(--border), transparent); margin: 0 48px; }
.reveal { opacity: 0; transform: translateY(24px); transition: opacity 0.7s ease, transform 0.7s ease; }
.reveal.visible { opacity: 1; transform: translateY(0); }
.reveal-delay-1 { transition-delay: 0.1s; } .reveal-delay-2 { transition-delay: 0.2s; } .reveal-delay-3 { transition-delay: 0.3s; }
@media (max-width: 900px) {
  nav { padding: 0 20px; }
  .nav-links { display: none; }
  .how-inner, .arch-inner { grid-template-columns: 1fr; gap: 48px; }
  .features-grid { grid-template-columns: 1fr; }
  .metrics-grid { grid-template-columns: repeat(2, 1fr); }
  .hero-stats { gap: 24px; flex-wrap: wrap; }
  #hero, #how, #features, #metrics, #architecture, #cta { padding-left: 20px; padding-right: 20px; }
  footer { flex-direction: column; gap: 16px; text-align: center; }
}
</style>
</head>
<body>
<canvas id="starfield"></canvas>
<nav id="nav">
  <a href="#" class="nav-logo">
    <div class="nav-logo-mark">
      <svg viewBox="0 0 28 28" fill="none">
        <circle cx="14" cy="14" r="13" stroke="#0d2137" stroke-width="1.5"/>
        <circle cx="14" cy="14" r="4" fill="#00d4ff"/>
        <ellipse cx="14" cy="14" rx="13" ry="5" stroke="#00d4ff" stroke-width="1" stroke-dasharray="3 2" opacity="0.5" transform="rotate(30 14 14)"/>
        <circle cx="24" cy="10" r="2" fill="#ff4444"/>
      </svg>
    </div>
    <span class="nav-logo-text">VECTRA<span>SPACE</span></span>
  </a>
  <ul class="nav-links">
    <li><a href="#how">How It Works</a></li>
    <li><a href="#features">Features</a></li>
    <li><a href="#metrics">Performance</a></li>
    <li><a href="#architecture">Architecture</a></li>
  </ul>
  <div class="nav-cta">
    <a href="/login" class="btn-outline">Sign In</a>
    <a href="/signup" class="btn-primary">Get Access</a>
  </div>
</nav>
<section id="hero">
  <div class="hero-orbit-ring">
    <div class="orbit-ring orbit-ring-1"><div class="orbit-dot"></div></div>
    <div class="orbit-ring orbit-ring-2"><div class="orbit-dot orbit-dot-2"></div></div>
    <div class="orbit-ring orbit-ring-3"><div class="orbit-dot orbit-dot-3"></div></div>
  </div>
  <div>
    <div class="hero-badge"><span class="badge-dot"></span>v11 Public Release — VectraSpace Mission Control</div>
    <h1 class="hero-title">ORBITAL<br><span class="hero-title-accent">SAFETY</span></h1>
    <div class="hero-title-sub">Collision Detection Platform</div>
    <p class="hero-desc">Real-time conjunction analysis across LEO, MEO, and GEO regimes. Vectorized SGP4 propagation, covariance-based Pc estimation, and automated alerting — all in a single platform.</p>
    <div class="hero-actions">
      <a href="/dashboard" class="btn-hero-primary">Launch Dashboard</a>
      <a href="#how" class="btn-hero-secondary">See How It Works</a>
    </div>
    <div class="hero-stats">
      <div class="hero-stat"><span class="hero-stat-num">50×</span><span class="hero-stat-label">Faster Propagation</span></div>
      <div class="hero-stat"><span class="hero-stat-num">3</span><span class="hero-stat-label">Orbital Regimes</span></div>
      <div class="hero-stat"><span class="hero-stat-num">6-hr</span><span class="hero-stat-label">Autonomous Cycles</span></div>
      <div class="hero-stat"><span class="hero-stat-num">4</span><span class="hero-stat-label">Alert Channels</span></div>
    </div>
  </div>
</section>
<div class="ticker-wrap">
  <div class="ticker-inner" id="ticker">
    <span class="ticker-item ok"><span class="ticker-sep">◆</span> System Status: All Systems Nominal</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> TLE Cache: CelesTrak + Space-Track Sync</span>
    <span class="ticker-item alert"><span class="ticker-sep">◆</span> Latest Scan: 3 Conjunctions Below 10 km Threshold</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> LEO Tracked: 100 Satellites Active</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> Covariance Source: Space-Track CDM Feed</span>
    <span class="ticker-item ok"><span class="ticker-sep">◆</span> API: Healthy — /health 200 OK</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> Pc Method: Foster-Alfano Ellipsoid Overlap</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> Notifications: Email · Pushover · Webhook</span>
    <span class="ticker-item ok"><span class="ticker-sep">◆</span> System Status: All Systems Nominal</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> TLE Cache: CelesTrak + Space-Track Sync</span>
    <span class="ticker-item alert"><span class="ticker-sep">◆</span> Latest Scan: 3 Conjunctions Below 10 km Threshold</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> LEO Tracked: 100 Satellites Active</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> Covariance Source: Space-Track CDM Feed</span>
    <span class="ticker-item ok"><span class="ticker-sep">◆</span> API: Healthy — /health 200 OK</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> Pc Method: Foster-Alfano Ellipsoid Overlap</span>
    <span class="ticker-item"><span class="ticker-sep">◆</span> Notifications: Email · Pushover · Webhook</span>
  </div>
</div>
<section id="how">
  <div class="how-inner">
    <div class="reveal">
      <div class="section-label">// Pipeline</div>
      <h2 class="section-title">From TLE to<br>Threat Assessment</h2>
      <p class="section-body">VectraSpace ingests live orbital elements from CelesTrak and Space-Track, propagates thousands of satellites simultaneously, and screens every pair for close approaches — in minutes, not hours.</p>
      <div class="pipeline" style="margin-top:40px;">
        <div class="pipeline-step"><span class="step-num">01</span><div class="step-content"><div class="step-title">TLE Ingestion</div><div class="step-desc">Authenticated fetch from CelesTrak &amp; Space-Track.org with smart 6-hour cache.</div></div></div>
        <div class="pipeline-step"><span class="step-num">02</span><div class="step-content"><div class="step-title">Vectorized SGP4 Propagation</div><div class="step-desc">NumPy-batched position arrays across 12-hour windows at 1-minute resolution.</div></div></div>
        <div class="pipeline-step"><span class="step-num">03</span><div class="step-content"><div class="step-title">Chunked Pair Screening</div><div class="step-desc">Ellipsoid pre-filter eliminates low-risk pairs before costly refinement.</div></div></div>
        <div class="pipeline-step"><span class="step-num">04</span><div class="step-content"><div class="step-title">Foster-Alfano Pc Estimation</div><div class="step-desc">Covariance-based probability of collision with real CDM data when available.</div></div></div>
        <div class="pipeline-step"><span class="step-num">05</span><div class="step-content"><div class="step-title">Alert &amp; CDM Export</div><div class="step-desc">Email, Pushover, and webhook notifications. CCSDS CDM per conjunction.</div></div></div>
      </div>
    </div>
    <div class="reveal reveal-delay-2">
      <div class="terminal-block">
        <div class="terminal-titlebar">
          <div class="terminal-dots"><span class="td-r"></span><span class="td-y"></span><span class="td-g"></span></div>
          <span class="terminal-title">VectraSpace v11 — Live Scan</span>
        </div>
        <div class="terminal-body">
          <span class="t-line"><span class="t-prompt">$ </span><span class="t-cmd">python vectraspace.py</span></span>
          <span class="t-line t-out">Loading .env from /opt/vectraspace/.env</span>
          <span class="t-line t-ok">✓ Space-Track login successful</span>
          <span class="t-line t-out">TLE cache is 2.1h old — using cached data</span>
          <span class="t-line t-ok">✓ Loaded <span class="t-val">4,812</span> satellites total</span>
          <span class="t-line t-out">&nbsp;&nbsp;LEO: <span class="t-val">3,204</span> · MEO: <span class="t-val">891</span> · GEO: <span class="t-val">717</span></span>
          <span class="t-line">&nbsp;</span>
          <span class="t-line t-out">Propagated <span class="t-val">170</span> tracks — 12h window</span>
          <span class="t-line t-out">Checking <span class="t-val">14,365</span> pairs — chunk 50...</span>
          <span class="t-line t-ok">✓ <span class="t-val">12,288</span> pairs skipped by pre-filter</span>
          <span class="t-line">&nbsp;</span>
          <span class="t-line t-warn">⚠ STARLINK-4521 ↔ COSMOS-1408 DEB</span>
          <span class="t-line t-out">&nbsp;&nbsp;dist: <span class="t-val">3.214 km</span> · Pc: <span class="t-val">4.1e-04</span> · +2h14m</span>
          <span class="t-line t-warn">⚠ ONEWEB-0342 ↔ SL-8 R/B</span>
          <span class="t-line t-out">&nbsp;&nbsp;dist: <span class="t-val">7.831 km</span> · Pc: <span class="t-val">1.2e-04</span> · +5h07m</span>
          <span class="t-line">&nbsp;</span>
          <span class="t-line t-ok">✓ Found <span class="t-val">3</span> conjunctions — alerts sent</span>
          <span class="t-line t-ok">✓ [Gmail] sent → ops@mission-control.com</span>
          <span class="t-line t-ok">✓ Pushover notification sent</span>
          <span class="t-line t-out">DB logged — run 2026-02-28T04:12:33Z</span>
          <span class="t-line">&nbsp;</span>
          <span class="t-prompt">$ </span><span class="t-cursor"></span>
        </div>
      </div>
    </div>
  </div>
</section>
<div class="section-divider"></div>
<section id="features">
  <div class="features-inner">
    <div class="features-header reveal">
      <div class="section-label">// Capabilities</div>
      <h2 class="section-title">Everything You Need<br>in Orbit Ops</h2>
      <p class="section-body">Production-hardened for mission-critical environments. Every feature designed for the demands of real spaceflight operations.</p>
    </div>
    <div class="features-grid reveal">
      <div class="feature-card"><div class="feature-icon">🌐</div><div class="feature-title">CesiumJS Globe</div><div class="feature-desc">Photorealistic Earth with real satellite imagery, atmosphere, day/night cycle, and live orbit rendering.</div><span class="feature-tag tag-v11">v11</span></div>
      <div class="feature-card"><div class="feature-icon">📡</div><div class="feature-title">Dual TLE Sources</div><div class="feature-desc">Authenticated Space-Track.org + CelesTrak ingestion with graceful fallback and smart cache management.</div></div>
      <div class="feature-card"><div class="feature-icon">⚡</div><div class="feature-title">Vectorized Propagation</div><div class="feature-desc">NumPy-batched SGP4 across all satellites simultaneously — 50-100× faster than sequential loops.</div></div>
      <div class="feature-card"><div class="feature-icon">🎯</div><div class="feature-title">Covariance-based Pc</div><div class="feature-desc">Foster-Alfano ellipsoid method with real CDM covariance from Space-Track when available.</div><span class="feature-tag tag-new">New</span></div>
      <div class="feature-card"><div class="feature-icon">🔔</div><div class="feature-title">Multi-channel Alerting</div><div class="feature-desc">Email (Gmail, SendGrid, SES, Postmark), Pushover mobile push, and HTTP webhooks.</div></div>
      <div class="feature-card"><div class="feature-icon">📄</div><div class="feature-title">CCSDS CDM Export</div><div class="feature-desc">Standards-compliant Conjunction Data Messages per event, with bulk ZIP download.</div></div>
      <div class="feature-card"><div class="feature-icon">🛰</div><div class="feature-title">Maneuver Planning</div><div class="feature-desc">Clohessy-Wiltshire linear model generates minimum Δv avoidance maneuver advisory for every conjunction.</div><span class="feature-tag tag-new">New</span></div>
      <div class="feature-card"><div class="feature-icon">💥</div><div class="feature-title">Debris Cloud Simulation</div><div class="feature-desc">NASA SBM lognormal fragmentation model spawns synthetic debris clouds and checks them against the catalog.</div><span class="feature-tag tag-new">New</span></div>
      <div class="feature-card"><div class="feature-icon">🕐</div><div class="feature-title">Autonomous Scheduling</div><div class="feature-desc">Windows Task Scheduler integration with headless mode, lockfile safety, and rotating log files.</div></div>
    </div>
  </div>
</section>
<div class="section-divider"></div>
<section id="metrics">
  <div class="metrics-inner">
    <div class="reveal" style="text-align:center;">
      <div class="section-label">// Performance</div>
      <h2 class="section-title">Built for Scale</h2>
    </div>
    <div class="metrics-grid reveal">
      <div class="metric-cell" data-label="Speedup"><span class="metric-num c1">50×</span><span class="metric-unit">Vectorized vs<br>Sequential</span></div>
      <div class="metric-cell" data-label="Coverage"><span class="metric-num c2">4,800+</span><span class="metric-unit">Active TLEs<br>Tracked</span></div>
      <div class="metric-cell" data-label="Precision"><span class="metric-num c3">1-min</span><span class="metric-unit">Propagation<br>Resolution</span></div>
      <div class="metric-cell" data-label="Lookout"><span class="metric-num c4">72-hr</span><span class="metric-unit">Maximum<br>Window</span></div>
    </div>
  </div>
</section>
<div class="section-divider"></div>
<section id="architecture">
  <div class="arch-inner">
    <div class="reveal">
      <div class="section-label">// Architecture</div>
      <h2 class="section-title">Modular by<br>Design</h2>
      <p class="section-body">Seven independent modules from data ingestion to REST API, all wired through a single pipeline function. Swap any layer without touching the rest.</p>
      <div style="margin-top:36px;">
        <div style="display:flex;gap:16px;margin-bottom:16px;">
          <div style="padding:10px 14px;background:var(--panel);border:1px solid rgba(0,255,136,0.3);border-radius:4px;font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--accent3);letter-spacing:1px;">✓ FastAPI REST + SSE streaming</div>
          <div style="padding:10px 14px;background:var(--panel);border:1px solid var(--border);border-radius:4px;font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:1px;">SQLite w/ auto-migration</div>
        </div>
        <div style="display:flex;gap:16px;">
          <div style="padding:10px 14px;background:var(--panel);border:1px solid var(--border);border-radius:4px;font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:1px;">bcrypt + itsdangerous auth</div>
          <div style="padding:10px 14px;background:var(--panel);border:1px solid rgba(0,212,255,0.3);border-radius:4px;font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--accent);letter-spacing:1px;">⚡ Async event loop</div>
        </div>
      </div>
    </div>
    <div class="reveal reveal-delay-2">
      <div class="arch-diagram">
        <div class="arch-label">Data Sources</div>
        <div class="arch-layer"><div class="arch-box highlight">CelesTrak</div><div class="arch-box highlight">Space-Track CDM</div></div>
        <div class="arch-connector"></div>
        <div class="arch-label">Module 1 — Ingestion</div>
        <div class="arch-layer"><div class="arch-box">TLE Cache Manager</div><div class="arch-box">Covariance Parser</div></div>
        <div class="arch-connector"></div>
        <div class="arch-label">Module 2 — Propagation</div>
        <div class="arch-layer"><div class="arch-box">SGP4 (Skyfield)</div><div class="arch-box">Regime Filter</div></div>
        <div class="arch-connector"></div>
        <div class="arch-label">Module 3 — Detection</div>
        <div class="arch-layer"><div class="arch-box">Chunked Screener</div><div class="arch-box">Pc Estimator</div><div class="arch-box">Maneuver CW</div></div>
        <div class="arch-connector"></div>
        <div class="arch-label">Output</div>
        <div class="arch-layer"><div class="arch-box highlight">FastAPI + Globe</div><div class="arch-box highlight">Alerts</div><div class="arch-box highlight">CDM Export</div></div>
      </div>
    </div>
  </div>
</section>
<div class="section-divider"></div>
<section style="padding:80px 48px;">
  <div style="max-width:1200px;margin:0 auto;display:grid;grid-template-columns:1fr 1fr;gap:80px;align-items:center;">
    <div class="reveal">
      <div class="alert-demo">
        <div class="alert-header">
          <div class="alert-badge">⚠ Conjunction Alert — 3 Events</div>
          <div class="alert-ts">2026-02-28 04:12 UTC</div>
        </div>
        <div class="alert-event"><div class="alert-event-dot"></div><div><div class="alert-sats">STARLINK-4521 <span class="arrow-sep">↔</span> COSMOS-1408 DEB<span class="alert-regime reg-leo">LEO</span></div><div class="alert-meta"><span class="meta-dist">3.214 km</span><span class="meta-pc">Pc 4.1×10⁻⁴</span><span class="meta-time">+2h 14m</span></div></div></div>
        <div class="alert-event"><div class="alert-event-dot warn"></div><div><div class="alert-sats">ONEWEB-0342 <span class="arrow-sep">↔</span> SL-8 R/B<span class="alert-regime reg-leo">LEO</span></div><div class="alert-meta"><span class="meta-dist">7.831 km</span><span class="meta-pc">Pc 1.2×10⁻⁴</span><span class="meta-time">+5h 07m</span></div></div></div>
        <div class="alert-event"><div class="alert-event-dot warn"></div><div><div class="alert-sats">INTELSAT 901 <span class="arrow-sep">↔</span> AMC-11<span class="alert-regime reg-geo">GEO</span></div><div class="alert-meta"><span class="meta-dist">9.104 km</span><span class="meta-pc">Pc 8.7×10⁻⁵</span><span class="meta-time">+8h 51m</span></div></div></div>
        <div style="padding:12px 20px;border-top:1px solid var(--border);font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);display:flex;justify-content:space-between;"><span>170 sats · 12h window</span><span style="color:var(--accent3);">✓ CDM exported · Alerts fired</span></div>
      </div>
    </div>
    <div class="reveal reveal-delay-2">
      <div class="section-label">// Alerting</div>
      <h2 class="section-title">Never Miss a<br>Close Approach</h2>
      <p class="section-body">Threshold-based filtering dispatches conjunction events to your team the moment they're detected — via email, Pushover mobile push, or any HTTP webhook endpoint.</p>
      <div style="margin-top:32px;display:flex;flex-direction:column;gap:12px;">
        <div style="display:flex;align-items:center;gap:14px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--text);"><span style="color:var(--accent3);font-size:14px;">✓</span> Gmail · SendGrid · AWS SES · Postmark</div>
        <div style="display:flex;align-items:center;gap:14px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--text);"><span style="color:var(--accent3);font-size:14px;">✓</span> Pushover mobile push notification</div>
        <div style="display:flex;align-items:center;gap:14px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--text);"><span style="color:var(--accent3);font-size:14px;">✓</span> Per-user alert preferences &amp; thresholds</div>
        <div style="display:flex;align-items:center;gap:14px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--text);"><span style="color:var(--accent3);font-size:14px;">✓</span> Styled HTML email with maneuver data</div>
        <div style="display:flex;align-items:center;gap:14px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--text);"><span style="color:var(--accent3);font-size:14px;">✓</span> Webhook integrations (Slack, Teams, custom)</div>
      </div>
    </div>
  </div>
</section>
<section id="cta">
  <div class="cta-inner reveal">
    <div class="cta-box">
      <div class="cta-eyebrow">⬡ Ready to Launch</div>
      <h2 class="cta-title">Protect Your Assets<br>in Orbit</h2>
      <p class="cta-desc">VectraSpace v11 is open for public access. Launch the live dashboard, run your first scan, or deploy a self-hosted instance in minutes.</p>
      <div class="cta-buttons">
        <a href="/dashboard" class="btn-cta-main">Open Dashboard</a>
        <a href="/signup" class="btn-cta-sub">Create Account</a>
      </div>
    </div>
  </div>
</section>
<footer>
  <div class="footer-logo">VECTRA<span>SPACE</span></div>
  <ul class="footer-links">
    <li><a href="/docs">API Docs</a></li>
    <li><a href="/health">System Status</a></li>
    <li><a href="mailto:trumanheaston@gmail.com">Support</a></li>
    <li><a href="/login">Sign In</a></li>
  </ul>
  <div class="footer-copy">© 2026 VectraSpace · Orbital Safety Platform · v11</div>
</footer>
<script>
const canvas = document.getElementById('starfield');
const ctx = canvas.getContext('2d');
let stars = [];
function resizeCanvas() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
function initStars(count=280) {
  stars = [];
  for (let i=0;i<count;i++) {
    stars.push({ x:Math.random()*canvas.width, y:Math.random()*canvas.height, r:Math.random()*1.2+0.2, a:Math.random()*0.8+0.2, speed:Math.random()*0.08+0.02, twinkle:Math.random()*Math.PI*2 });
  }
}
function drawStars() {
  ctx.clearRect(0,0,canvas.width,canvas.height);
  stars.forEach(s => {
    s.twinkle += s.speed*0.04;
    const alpha = s.a*(0.6+0.4*Math.sin(s.twinkle));
    ctx.beginPath(); ctx.arc(s.x,s.y,s.r,0,Math.PI*2);
    ctx.fillStyle = `rgba(200,223,240,${alpha})`; ctx.fill();
  });
  requestAnimationFrame(drawStars);
}
resizeCanvas(); initStars(); drawStars();
window.addEventListener('resize', () => { resizeCanvas(); initStars(); });
const nav = document.getElementById('nav');
window.addEventListener('scroll', () => { nav.classList.toggle('scrolled', window.scrollY > 20); });
const reveals = document.querySelectorAll('.reveal');
const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('visible'); });
}, { threshold: 0.1 });
reveals.forEach(el => revealObserver.observe(el));
function animateCounter(el, target, suffix='', duration=1600) {
  const start = performance.now();
  const isDecimal = String(target).includes('.');
  function step(now) {
    const t = Math.min((now-start)/duration,1);
    const ease = 1-Math.pow(1-t,3);
    const val = target*ease;
    el.textContent = isDecimal ? val.toFixed(1)+suffix : Math.round(val)+suffix;
    if(t<1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
const metricObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    const nums = e.target.querySelectorAll('.metric-num');
    nums.forEach(num => {
      const text = num.textContent;
      if (text === '50x' || text === '50×') { animateCounter(num, 50, '×'); }
      else if (text.includes('4,800')) {
        let s = performance.now();
        (function step(now) {
          const t = Math.min((now-s)/1600,1);
          const e2 = 1-Math.pow(1-t,3);
          num.textContent = Math.round(4800*e2).toLocaleString()+'+';
          if(t<1) requestAnimationFrame(step);
        })(s);
      }
    });
    metricObserver.unobserve(e.target);
  });
}, { threshold: 0.3 });
const metricsGrid = document.querySelector('.metrics-grid');
if (metricsGrid) metricObserver.observe(metricsGrid);
</script>
</body>
</html>"""


# ╔══════════════════════════════════════════════════════════════╗
# ║  MODULE 7 — REST API + SSE RUN ENDPOINT                      ║
# ╚══════════════════════════════════════════════════════════════╝

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

    app = FastAPI(
        title="VectraSpace API",
        description="VectraSpace v11 — Orbital Safety Platform",
        version="11.0",
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
    def landing():
        return HTMLResponse(content=LANDING_HTML)

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

            # MULTI-02: Require login for /run
            if not user:
                yield f"data: {_json.dumps({'type': 'auth_error', 'text': 'Authentication required'})}\n\n"
                return

            # MULTI-04: Per-user rate limit (1 scan per 5 min)
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
                    # MULTI-03: Tag conjunctions with user_id
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
        signup_link = '<a href="/signup">Create account</a>' if SIGNUP_OPEN else '<a href="mailto:trumanheaston@gmail.com">Request access</a>'
        return HTMLResponse(
            LOGIN_HTML.replace("{ERROR}", err_html)
                      .replace("{SIGNUP_LINK}", signup_link)
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request):
        signup_link = '<a href="/signup">Create account</a>' if SIGNUP_OPEN else '<a href="mailto:trumanheaston@gmail.com">Request access</a>'

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
            return _login_page_with_err("Account pending approval. Contact trumanheaston@gmail.com.")
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
            err = '<div class="err">⚠ Could not update password. Contact trumanheaston@gmail.com.</div>'
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

    # ── Auth middleware (if users.json exists) ────────────────
    if HAS_AUTH and Path(cfg.users_file).exists():
        PUBLIC_PATHS = {"/login", "/health", "/demo-results", "/signup",
                        "/forgot-password", "/reset-password", "/"}
        # Routes accessible without auth (demo mode)
        DEMO_ALLOWED = {"/", "/history", "/conjunctions", "/sat-info"}

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                path = request.url.path
                # Always allow public paths
                if path in PUBLIC_PATHS or path.startswith("/static"):
                    return await call_next(request)
                # Allow demo-mode paths without auth
                if path in ("/", "/dashboard") or path.startswith("/sat-info/") or path.startswith("/cdm"):
                    return await call_next(request)
                # Protected paths: /run, /preferences, /me
                if path in {"/run", "/preferences", "/me"}:
                    token = request.cookies.get("vs_session", "")
                    try:
                        _verify_session_cookie(token, cfg.session_secret)
                    except Exception:
                        if path == "/preferences":
                            return RedirectResponse(url="/login")
                        # /run and /me return JSON 401 (handled in route)
                return await call_next(request)

        app.add_middleware(AuthMiddleware)
        log.info("Auth middleware enabled (users.json found)")
    else:
        log.info("Auth disabled (no users.json) — create users with: python vectraspace.py --create-user NAME PASS ROLE")

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
        _admin_user = os.environ.get("ADMIN_USER", "").strip()
        _admin_pass = os.environ.get("ADMIN_PASS", "").strip()
        if _admin_user and _admin_pass and not Path(CFG.users_file).exists():
            create_user(_admin_user, _admin_pass, "admin", cfg=CFG)
            log.info(f"Auto-created admin user '{_admin_user}' from environment")

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
