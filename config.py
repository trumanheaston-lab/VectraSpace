"""
VectraSpace v11 — config.py
Single source of truth for all runtime configuration.
Values are read from environment variables (set in Render dashboard or .env).
"""

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _load_env():
    """Load .env from the project root into os.environ (dev only)."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    print(f"[config] Loading .env from {env_path}")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


_load_env()


@dataclass
class Config:
    # ── Regime scan limits ───────────────────────────────────
    num_leo: int = 100
    num_meo: int = 50
    num_geo: int = 20
    num_satellites_per_regime: int = 100

    # ── Detection windows ────────────────────────────────────
    time_window_hours: float = 12
    coarse_step_minutes: float = 1.0
    refine_threshold_km: float = 50.0
    collision_alert_km: float = 10.0
    pc_alert_threshold: float = 1e-4

    # ── Assumed covariance sigmas (km) ───────────────────────
    sigma_along: float = 0.5
    sigma_cross: float = 0.2
    sigma_radial: float = 0.1

    # ── TLE sources ──────────────────────────────────────────
    tle_sources: list = field(default_factory=lambda: [
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle",
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle",
    ])
    tle_cache_file: str = "tle_cache.txt"
    tle_max_age_hours: float = 6.0

    # ── Persistence ──────────────────────────────────────────
    db_path: str = "conjunctions.db"
    users_file: str = "users.json"

    # ── Alerting ─────────────────────────────────────────────
    alert_email_to: Optional[str] = None
    alert_email_from: Optional[str] = None
    alert_smtp_host: str = "smtp.gmail.com"
    alert_webhook_url: Optional[str] = None
    alert_phone: Optional[str] = None
    pushover_token: Optional[str] = None
    pushover_user_key: Optional[str] = None

    # ── Physics ──────────────────────────────────────────────
    maneuver_safe_dist_km: float = 50.0
    vector_chunk_size: int = 50

    # ── Auth ─────────────────────────────────────────────────
    session_secret: str = ""


# ── Singleton built from environment ────────────────────────────────────────
CFG = Config(
    alert_email_from=os.environ.get("ALERT_EMAIL_FROM", "trumanheaston@gmail.com"),
    alert_email_to=os.environ.get("ALERT_EMAIL_TO", ""),
    alert_smtp_host=os.environ.get("ALERT_SMTP_HOST", "smtp.gmail.com"),
    alert_phone=os.environ.get("ALERT_PHONE"),
    pushover_token=os.environ.get("PUSHOVER_TOKEN"),
    pushover_user_key=os.environ.get("PUSHOVER_USER_KEY"),
    session_secret=os.environ.get("SESSION_SECRET", secrets.token_hex(32)),
    collision_alert_km=float(os.environ.get("COLLISION_ALERT_KM", "10.0")),
)
