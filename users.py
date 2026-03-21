"""
VectraSpace v11 — users.py
User CRUD, password hashing (PBKDF2-SHA256), session tokens, reset tokens.
All auth logic lives here — routes are in auth_routes.py.
"""

import hashlib as _hashlib
import hmac as _hmac
import json
import logging
import secrets as _secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

from config import Config

log = logging.getLogger("VectraSpace")

_PBKDF2_ITERS = 260_000   # OWASP 2023 recommendation
_SESSION_SEP  = "|"        # never appears in base64 payloads
_login_attempts: dict = {}
_run_rate_limits: dict = {}


# ── Password ─────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    salt = _secrets.token_hex(16)
    dk   = _hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), _PBKDF2_ITERS)
    return f"pbkdf2:sha256:{_PBKDF2_ITERS}:{salt}:{dk.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    if not plain or not stored:
        return False
    if stored.startswith("pbkdf2:sha256:"):
        try:
            _, _, iters, salt, hx = stored.split(":")
            dk = _hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), int(iters))
            return _hmac.compare_digest(dk.hex(), hx)
        except Exception:
            return False
    try:
        import bcrypt
        return bcrypt.checkpw(plain.encode(), stored.encode())
    except Exception:
        return False


# ── Session tokens ────────────────────────────────────────────────────────────

def make_session_token(username: str, role: str, secret: str) -> str:
    import base64
    ts      = str(int(time.time()))
    payload = base64.urlsafe_b64encode(f"{username}\x00{role}\x00{ts}".encode()).decode()
    sig     = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    return f"{payload}{_SESSION_SEP}{sig}"


def verify_session_token(token: str, secret: str, max_age: int = 2592000):
    import base64
    if not token or _SESSION_SEP not in token:
        raise ValueError("malformed")
    payload, sig = token.split(_SESSION_SEP, 1)
    expected = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(sig, expected):
        raise ValueError("bad signature")
    decoded = base64.urlsafe_b64decode(payload.encode()).decode()
    parts   = decoded.split("\x00")
    if len(parts) != 3:
        raise ValueError("malformed payload")
    username, role, ts = parts
    if int(time.time()) - int(ts) > max_age:
        raise ValueError("expired")
    return username, role


# ── Reset tokens ──────────────────────────────────────────────────────────────

def make_reset_token(username: str, secret: str) -> str:
    import base64
    ts      = str(int(time.time()))
    payload = base64.urlsafe_b64encode(f"{username}:{ts}".encode()).decode()
    sig     = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_reset_token(token: str, secret: str, max_age: int = 3600) -> Optional[str]:
    import base64
    try:
        payload, sig = token.rsplit(".", 1)
        expected = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        decoded  = base64.urlsafe_b64decode(payload.encode()).decode()
        username, ts = decoded.split(":", 1)
        if int(time.time()) - int(ts) > max_age:
            return None
        return username
    except Exception:
        return None


# ── Request helpers ───────────────────────────────────────────────────────────

def get_current_user(request, cfg: Config) -> Optional[dict]:
    token = request.cookies.get("vs_session", "")
    if not token:
        return None
    try:
        username, role = verify_session_token(token, cfg.session_secret)
        return {"username": username, "role": role}
    except Exception:
        return None


def check_login_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < 300]
    _login_attempts[ip] = attempts
    if len(attempts) >= 20:
        return False
    _login_attempts[ip].append(now)
    return True


def check_run_rate_limit(username: str, window_seconds: int = 300) -> bool:
    now   = time.time()
    times = [t for t in _run_rate_limits.get(username, []) if now - t < window_seconds]
    _run_rate_limits[username] = times
    if times:
        return False
    _run_rate_limits[username].append(now)
    return True


# ── CRUD ──────────────────────────────────────────────────────────────────────

def load_users(cfg: Config) -> dict:
    try:
        con = sqlite3.connect(cfg.db_path)
        tbl = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if tbl:
            rows = con.execute(
                "SELECT username, password_hash, role, email, approved, created_at FROM users"
            ).fetchall()
            con.close()
            if rows:
                return {r[0]: {"username":r[0], "password_hash":r[1], "role":r[2],
                               "email":r[3], "approved":bool(r[4]), "created_at":r[5]}
                        for r in rows}
        con.close()
    except Exception as e:
        log.warning(f"load_users DB error: {e}")
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
        log.warning(f"load_users file error: {e}")
    return {}


def save_users(users: dict, cfg: Config):
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
                  u.get("email",""), 1 if u.get("approved", True) else 0,
                  u.get("created_at","")))
        con.commit()
        con.close()
    except Exception as e:
        log.warning(f"save_users DB error: {e}")
        try:
            Path(cfg.users_file).write_text(json.dumps(list(users.values()), indent=2))
        except Exception:
            pass


def create_user(username: str, password: str, role: str = "operator", cfg=None):
    from config import CFG
    if cfg is None:
        cfg = CFG
    username = username.strip().lower()
    users    = load_users(cfg)
    users[username] = {
        "username":      username,
        "password_hash": hash_password(password),
        "role":          role,
        "email":         users.get(username, {}).get("email", ""),
        "approved":      True,
        "created_at":    users.get(username, {}).get("created_at",
                             time.strftime("%Y-%m-%dT%H:%M:%SZ")),
    }
    save_users(users, cfg)
    log.info(f"User '{username}' saved with role '{role}'")


def register_user(username: str, email: str, password: str,
                  cfg: Config, approved: bool = True) -> tuple[bool, str]:
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
    users = load_users(cfg)
    if username in users:
        return False, "Username already taken"
    if any(u.get("email","").lower() == email for u in users.values()):
        return False, "An account with that email already exists"
    users[username] = {
        "username":      username,
        "password_hash": hash_password(password),
        "role":          "operator",
        "email":         email,
        "approved":      approved,
        "created_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_users(users, cfg)
    log.info(f"Registered user '{username}' <{email}>")
    return True, ""


def update_password(username: str, new_password: str, cfg: Config) -> bool:
    users = load_users(cfg)
    if username not in users:
        return False
    users[username]["password_hash"] = hash_password(new_password)
    save_users(users, cfg)
    log.info(f"Password updated for '{username}'")
    return True


def get_user_email(username: str, cfg: Config) -> Optional[str]:
    from database import get_user_prefs
    users = load_users(cfg)
    u = users.get(username, {})
    if u.get("email"):
        return u["email"]
    return get_user_prefs(username, cfg).get("email")
