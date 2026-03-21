"""
VectraSpace v11 — database.py
SQLite schema, migration, CDM generation, covariance ingestion.
"""

import datetime
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import requests

from config import Config

log = logging.getLogger("VectraSpace")

SPACETRACK_LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
SPACETRACK_CDM_URL = (
    "https://www.space-track.org/basicspacedata/query/class/cdm_public"
    "/orderby/TCA desc/limit/100/format/json"
)


# ── Schema init + auto-migration ─────────────────────────────────────────────

def init_db(cfg: Config) -> sqlite3.Connection:
    con = sqlite3.connect(cfg.db_path)

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

    existing_cols = [r[1] for r in con.execute("PRAGMA table_info(conjunctions)").fetchall()]
    if "user_id" not in existing_cols:
        con.execute("ALTER TABLE conjunctions ADD COLUMN user_id TEXT")
        log.info("DB migration: added user_id column")

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

    _migrate_users_json(con, cfg)
    con.commit()
    return con


def _migrate_users_json(con: sqlite3.Connection, cfg: Config):
    """One-time migration: users.json → SQLite users table."""
    uj = Path(cfg.users_file)
    if not uj.exists():
        return
    try:
        raw = json.loads(uj.read_text())
        users_list = raw if isinstance(raw, list) else list(raw.values())
        migrated = 0
        for u in users_list:
            un = u.get("username", "").strip().lower()
            ph = u.get("password_hash", "")
            if not un or not ph:
                continue
            if not con.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
                con.execute(
                    "INSERT INTO users (username, password_hash, role, email, approved, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (un, ph, u.get("role", "operator"), u.get("email", ""),
                     1 if u.get("approved", True) else 0, u.get("created_at", "")),
                )
                migrated += 1
        if migrated:
            log.info(f"DB migration: migrated {migrated} users from users.json")
            uj.rename(str(uj) + ".migrated")
    except Exception as e:
        log.warning(f"users.json migration error: {e}")


# ── Conjunction logging ──────────────────────────────────────────────────────

def log_conjunctions_to_db(conjunctions: list, con: sqlite3.Connection,
                            run_time: str, user_id: Optional[str] = None):
    rows = [
        (run_time, c.sat1, c.sat2, c.regime1, c.regime2,
         c.min_dist_km, c.time_min, c.pc_estimate, user_id)
        for c in conjunctions
    ]
    con.executemany(
        "INSERT INTO conjunctions "
        "(run_time,sat1,sat2,regime1,regime2,min_dist_km,time_min,pc_estimate,user_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    log.info(f"Logged {len(rows)} conjunctions (user_id={user_id})")


# ── CDM generation ───────────────────────────────────────────────────────────

def generate_cdm(c, run_time: str) -> str:
    now = datetime.datetime.utcnow()
    tca = now + datetime.timedelta(minutes=c.time_min)
    sat1 = str(c.sat1 or "UNKNOWN").strip()
    sat2 = str(c.sat2 or "UNKNOWN").strip()
    return f"""CCSDS_CDM_VERS                      = 1.0
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
OBJECT_NAME                         = {sat1}
INTERNATIONAL_DESIGNATOR            = UNKNOWN
OBJECT_TYPE                         = PAYLOAD
COVARIANCE_METHOD                   = CALCULATED
ORBIT_CENTER                        = EARTH
REF_FRAME                           = EME2000

OBJECT                              = OBJECT2
OBJECT_DESIGNATOR                   = {sat2}
OBJECT_NAME                         = {sat2}
INTERNATIONAL_DESIGNATOR            = UNKNOWN
OBJECT_TYPE                         = PAYLOAD
COVARIANCE_METHOD                   = CALCULATED
ORBIT_CENTER                        = EARTH
REF_FRAME                           = EME2000

COMMENT Generated by VectraSpace v11
COMMENT Orbital Regime OBJECT1: {c.regime1}
COMMENT Orbital Regime OBJECT2: {c.regime2}
COMMENT Time to CA: +{int(c.time_min // 60)}h {int(c.time_min % 60):02d}m
"""


# ── Covariance cache from Space-Track CDMs ───────────────────────────────────

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
                sat_name = rec.get(f"{obj}_OBJECT_NAME", "").strip()
                if not sat_name:
                    continue
                try:
                    CR_R = float(rec.get(f"{obj}_CR_R", 0) or 0)
                    CT_R = float(rec.get(f"{obj}_CT_R", 0) or 0)
                    CT_T = float(rec.get(f"{obj}_CT_T", 0) or 0)
                    CN_R = float(rec.get(f"{obj}_CN_R", 0) or 0)
                    CN_T = float(rec.get(f"{obj}_CN_T", 0) or 0)
                    CN_N = float(rec.get(f"{obj}_CN_N", 0) or 0)
                    cov  = np.array([[CR_R,CT_R,CN_R],[CT_R,CT_T,CN_T],[CN_R,CN_T,CN_N]])
                    if np.all(np.isfinite(cov)) and np.any(np.diag(cov) > 0):
                        cov_cache[sat_name] = cov
                except (ValueError, TypeError):
                    pass
        log.info(f"Loaded covariances for {len(cov_cache)} objects from Space-Track CDM")
        return cov_cache
    except Exception as e:
        log.warning(f"Covariance ingestion failed ({e}) — using assumed sigmas")
        return {}


# ── User preferences ─────────────────────────────────────────────────────────

def get_user_prefs(username: str, cfg: Config) -> dict:
    try:
        con = sqlite3.connect(cfg.db_path)
        row = con.execute(
            "SELECT email, phone, pushover_key, pc_alert_threshold, collision_alert_km "
            "FROM user_preferences WHERE user_id=?",
            (username,),
        ).fetchone()
        if row:
            return {"email": row[0], "phone": row[1], "pushover_key": row[2],
                    "pc_alert_threshold": row[3] or 1e-4,
                    "collision_alert_km": row[4] or 10.0}
    except Exception:
        pass
    return {}


def save_user_prefs(username: str, prefs: dict, cfg: Config):
    con = sqlite3.connect(cfg.db_path)
    con.execute("""
        INSERT INTO user_preferences
            (user_id, email, phone, pushover_key, pc_alert_threshold, collision_alert_km, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            email=excluded.email, phone=excluded.phone,
            pushover_key=excluded.pushover_key,
            pc_alert_threshold=excluded.pc_alert_threshold,
            collision_alert_km=excluded.collision_alert_km,
            updated_at=excluded.updated_at
    """, (username, prefs.get("email",""), prefs.get("phone",""),
          prefs.get("pushover_key",""),
          float(prefs.get("pc_alert_threshold", 1e-4)),
          float(prefs.get("collision_alert_km", 10.0)),
          datetime.datetime.utcnow().isoformat()))
    con.commit()
