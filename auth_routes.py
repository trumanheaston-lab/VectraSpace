"""
VectraSpace v11 — auth_routes.py
Login, signup, forgot/reset password, preferences, change-password.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from config import CFG
from users import (
    get_current_user, verify_password, make_session_token,
    check_login_rate_limit, register_user, make_reset_token,
    verify_reset_token, update_password, get_user_email, load_users,
)
from database import get_user_prefs, save_user_prefs

log    = logging.getLogger("VectraSpace")
router = APIRouter()

SIGNUP_OPEN = os.environ.get("SIGNUP_OPEN", "true").lower() == "true"

# ── HTML templates (inline for auth pages — small and self-contained) ─────────
# These are intentionally kept small. Large pages live in templates_loader.py.

_AUTH_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #050a0f; color: #c8dff0; font-family: 'Exo 2', sans-serif;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; padding: 24px 16px; }
  .card { background: #090f17; border: 1px solid #0d2137; border-radius: 8px;
          padding: 40px 36px; width: 100%; max-width: 420px;
          box-shadow: 0 0 40px rgba(0,212,255,0.08); }
  .logo { font-family: 'Share Tech Mono', monospace; font-size: 10px;
          color: #00d4ff; letter-spacing: 4px; margin-bottom: 6px; }
  h1 { font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 6px; }
  .sub { font-size: 11px; color: #4a6a85; font-family: 'Share Tech Mono', monospace;
         margin-bottom: 28px; }
  label { display: block; font-size: 10px; color: #4a6a85; letter-spacing: 1px;
          text-transform: uppercase; margin-bottom: 5px; margin-top: 12px; }
  input { width: 100%; background: #0a1520; border: 1px solid #0d2137;
          border-radius: 4px; color: #c8dff0; font-family: 'Share Tech Mono', monospace;
          font-size: 13px; padding: 9px 12px; outline: none; transition: border-color 0.2s; }
  input:focus { border-color: #00d4ff; }
  button { display: block; width: 100%; margin-top: 20px; padding: 11px;
           background: transparent; border: 1px solid #00d4ff; border-radius: 4px;
           color: #00d4ff; font-family: 'Share Tech Mono', monospace; font-size: 12px;
           letter-spacing: 3px; cursor: pointer; transition: all 0.2s; text-transform: uppercase; }
  button:hover { background: rgba(0,212,255,0.08); }
  .err { color: #ff4444; font-size: 11px; margin-bottom: 12px; padding: 8px 10px;
          background: rgba(255,68,68,0.08); border-radius: 4px; border-left: 2px solid #ff4444; }
  .ok  { color: #00ff88; font-size: 11px; margin-bottom: 12px; padding: 8px 10px;
          background: rgba(0,255,136,0.08); border-radius: 4px; border-left: 2px solid #00ff88; }
  .nav { margin-top: 18px; text-align: center; font-size: 10px; color: #4a6a85;
          font-family: 'Share Tech Mono', monospace; line-height: 2; }
  .nav a { color: #00d4ff; text-decoration: none; margin: 0 6px; }
  .hint { font-size: 9px; color: #4a6a85; margin-top: 3px; font-family: 'Share Tech Mono', monospace; }
  .pw-rules { font-size: 9px; color: #4a6a85; margin-top: 6px; padding: 6px 8px;
              background: #040a10; border-radius: 3px; font-family: 'Share Tech Mono', monospace; }
"""

_FONTS = '<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@400;700&display=swap" rel="stylesheet">'

_ANALYTICS = '<script defer src="https://cloud.umami.is/script.js" data-website-id="4e12fc04-8b26-4e42-8b69-0700a95c7d30"></script>'


def _auth_page(title: str, heading: str, subtitle: str,
               message: str, form: str, nav: str) -> str:
    return f"""<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>{_FONTS}<style>{_AUTH_CSS}</style>{_ANALYTICS}</head>
<body><div class="card">
  <div class="logo">VectraSpace // Mission Control</div>
  <h1>{heading}</h1><div class="sub">{subtitle}</div>
  {message}{form}
  <div class="nav">{nav}</div>
</div></body></html>"""


def _signup_form() -> str:
    return """<form method="post" action="/signup">
  <label>Username</label>
  <input type="text" name="username" required minlength="3" maxcimum="32" autocomplete="username">
  <label>Email</label>
  <input type="email" name="email" required autocomplete="email">
  <div class="hint">Used for password resets and conjunction alerts</div>
  <label>Password</label>
  <input type="password" name="password" required minlength="8" autocomplete="new-password">
  <label>Confirm Password</label>
  <input type="password" name="confirm" required minlength="8" autocomplete="new-password">
  <div class="pw-rules">Min 8 chars · Mix letters, numbers, symbols</div>
  <button type="submit">Create Account</button>
</form>"""


def _reset_form(token: str) -> str:
    return f"""<form method="post" action="/reset-password">
  <input type="hidden" name="token" value="{token}">
  <label>New Password</label>
  <input type="password" name="password" required minlength="8" autocomplete="new-password">
  <label>Confirm New Password</label>
  <input type="password" name="confirm" required minlength="8" autocomplete="new-password">
  <div class="pw-rules">Min 8 chars</div>
  <button type="submit">Set New Password</button>
</form>"""


# ── /me ───────────────────────────────────────────────────────────────────────

@router.get("/me")
def me(request: Request):
    user = get_current_user(request, CFG)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({"authenticated": True,
                          "username": user["username"], "role": user["role"]})


# ── Login / logout ────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    signup_link = ('<a href="/signup">Create account</a>' if SIGNUP_OPEN
                   else '<a href="mailto:trumanheaston@gmail.com">Request access</a>')
    err_html    = f'<div class="err">⚠ {error}</div>' if error else ""
    next_url    = str(request.query_params.get("next", "")).strip()
    action      = f'/login?next={next_url}' if next_url else "/login"
    form = f"""<form method="post" action="{action}">
  <label>Username</label>
  <input type="text" name="username" autocomplete="username" required>
  <label>Password</label>
  <input type="password" name="password" autocomplete="current-password" required>
  <button type="submit">Sign In</button>
</form>"""
    nav = (f'<a href="/forgot-password">Forgot password?</a> · '
           f'{signup_link} · <a href="/dashboard">View Demo</a>')
    return HTMLResponse(_auth_page("VectraSpace — Sign In", "Sign In",
                                   "Orbital Safety Platform — v11", err_html, form, nav))


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    def _err(msg):
        signup_link = ('<a href="/signup">Create account</a>' if SIGNUP_OPEN
                       else '<a href="mailto:trumanheaston@gmail.com">Request access</a>')
        form = """<form method="post" action="/login">
  <label>Username</label><input type="text" name="username" autocomplete="username" required>
  <label>Password</label><input type="password" name="password" autocomplete="current-password" required>
  <button type="submit">Sign In</button></form>"""
        nav = f'<a href="/forgot-password">Forgot password?</a> · {signup_link}'
        return HTMLResponse(_auth_page("VectraSpace — Sign In", "Sign In",
                                       "Orbital Safety Platform — v11",
                                       f'<div class="err">⚠ {msg}</div>', form, nav))

    xff = request.headers.get("X-Forwarded-For", "")
    ip  = xff.split(",")[0].strip() if xff else (request.client.host or "0.0.0.0")
    if not check_login_rate_limit(ip):
        return _err("Too many login attempts. Try again in 60s.")

    form = await request.form()
    username = str(form.get("username", "")).strip().lower()
    password = str(form.get("password", "")).strip()
    users = load_users(CFG)
    user  = users.get(username)
    if not user or not verify_password(password, user.get("password_hash", "")):
        return _err("Invalid username or password.")
    if not user.get("approved", True):
        return _err("Account pending approval. Contact trumanheaston@gmail.com.")

    next_url = str(request.query_params.get("next", "") or "/dashboard").strip()
    if not next_url.startswith("/"):
        next_url = "/dashboard"
    token = make_session_token(username, user.get("role","operator"), CFG.session_secret)
    resp  = RedirectResponse(url=next_url, status_code=303)
    resp.set_cookie("vs_session", token, httponly=True, samesite="lax", path="/", max_age=2592000)
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("vs_session")
    return resp


# ── Signup ────────────────────────────────────────────────────────────────────

@router.get("/signup", response_class=HTMLResponse)
def signup_page():
    if not SIGNUP_OPEN:
        return HTMLResponse(_auth_page(
            "VectraSpace — Registration", "Registration Closed",
            "Public signups are currently disabled",
            '<p style="font-size:11px;color:#4a6a85;margin-top:16px;line-height:1.7;">'
            'Contact <a href="mailto:trumanheaston@gmail.com">trumanheaston@gmail.com</a> to request access.</p>',
            "", '<a href="/login">← Back to Sign In</a>',
        ))
    nav = 'Already have an account? <a href="/login">Sign in</a>'
    return HTMLResponse(_auth_page("VectraSpace — Create Account", "Create Account",
                                   "Orbital Safety Platform — v11", "", _signup_form(), nav))


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request):
    if not SIGNUP_OPEN:
        return HTMLResponse(_auth_page(
            "VectraSpace — Registration", "Registration Closed", "",
            '<div class="err">Registration is currently closed.</div>', "",
            '<a href="/login">← Sign In</a>'), status_code=403)

    form    = await request.form()
    un      = str(form.get("username","")).strip().lower()
    email   = str(form.get("email","")).strip().lower()
    pw      = str(form.get("password","")).strip()
    confirm = str(form.get("confirm","")).strip()
    nav     = 'Already have an account? <a href="/login">Sign in</a>'

    if pw != confirm:
        return HTMLResponse(_auth_page("VectraSpace — Create Account", "Create Account",
                                       "Orbital Safety Platform — v11",
                                       '<div class="err">⚠ Passwords do not match.</div>',
                                       _signup_form(), nav))

    ok, errmsg = register_user(un, email, pw, CFG, approved=True)
    if not ok:
        return HTMLResponse(_auth_page("VectraSpace — Create Account", "Create Account",
                                       "Orbital Safety Platform — v11",
                                       f'<div class="err">⚠ {errmsg}</div>',
                                       _signup_form(), nav))

    token = make_session_token(un, "operator", CFG.session_secret)
    resp  = RedirectResponse(url="/dashboard", status_code=303)
    resp.set_cookie("vs_session", token, httponly=True, samesite="lax", path="/", max_age=2592000)
    return resp


# ── Forgot / reset password ───────────────────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_page():
    form = """<form method="post" action="/forgot-password">
  <label>Username</label>
  <input type="text" name="username" required autocomplete="username">
  <div class="hint">We'll send a reset link to your registered email</div>
  <button type="submit">Send Reset Link</button>
</form>"""
    return HTMLResponse(_auth_page("VectraSpace — Reset Password", "Reset Password",
                                   "Enter your username to receive a reset link",
                                   "", form, '<a href="/login">← Back to Sign In</a>'))


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_submit(request: Request):
    form     = await request.form()
    username = str(form.get("username","")).strip().lower()
    ok_msg   = '<div class="ok">✓ If that username exists, a reset link has been sent.</div>'
    users    = load_users(CFG)
    if username in users:
        token = make_reset_token(username, CFG.session_secret)
        import asyncio, functools
        asyncio.get_event_loop().run_in_executor(
            None, functools.partial(_send_reset_email, username, token)
        )
    return HTMLResponse(_auth_page("VectraSpace — Reset Password", "Reset Password",
                                   "Check your inbox",
                                   ok_msg, "", '<a href="/login">← Back to Sign In</a>'))


def _send_reset_email(username: str, token: str):
    from alerts import send_email
    email    = get_user_email(username, CFG)
    if not email:
        return
    base_url = os.environ.get("VECTRASPACE_BASE_URL", "http://localhost:8000").rstrip("/")
    url      = f"{base_url}/reset-password?token={token}"
    html     = f"""<div style="font-family:monospace;background:#050a0f;color:#c8dff0;padding:24px;">
<h3 style="color:#00d4ff;">Password Reset — VectraSpace</h3>
<p>Reset link for <strong>{username}</strong> (expires in 1 hour):</p>
<a href="{url}" style="display:block;margin:16px 0;padding:12px;border:1px solid #00d4ff;
   border-radius:4px;color:#00d4ff;text-decoration:none;text-align:center;font-family:monospace;">
RESET MY PASSWORD</a>
<p style="font-size:10px;color:#4a6a85;">If you didn't request this, ignore this email.</p>
</div>"""
    send_email("[VectraSpace] Password Reset Link", html, email, CFG,
               plain=f"Reset your VectraSpace password: {url}\nExpires in 1 hour.")


@router.get("/reset-password", response_class=HTMLResponse)
def reset_page(token: str = ""):
    username = verify_reset_token(token, CFG.session_secret)
    if not username:
        return HTMLResponse(_auth_page(
            "VectraSpace — Reset Password", "Reset Password", "",
            '<div class="err">⚠ Link invalid or expired. '
            '<a href="/forgot-password">Request a new one.</a></div>',
            "", '<a href="/login">← Sign In</a>'))
    return HTMLResponse(_auth_page(
        "VectraSpace — Set New Password", "Set New Password",
        f"Resetting password for {username}",
        "", _reset_form(token), '<a href="/login">← Sign In</a>'))


@router.post("/reset-password", response_class=HTMLResponse)
async def reset_submit(request: Request):
    form    = await request.form()
    token   = str(form.get("token","")).strip()
    pw      = str(form.get("password","")).strip()
    confirm = str(form.get("confirm","")).strip()
    username = verify_reset_token(token, CFG.session_secret)
    if not username:
        return HTMLResponse(_auth_page(
            "VectraSpace — Reset", "Reset Password", "",
            '<div class="err">⚠ Link expired. <a href="/forgot-password">Request a new one.</a></div>',
            "", ""))
    if pw != confirm:
        return HTMLResponse(_auth_page(
            "VectraSpace — Reset", "Set New Password", username,
            '<div class="err">⚠ Passwords do not match.</div>',
            _reset_form(token), ""))
    if len(pw) < 8:
        return HTMLResponse(_auth_page(
            "VectraSpace — Reset", "Set New Password", username,
            '<div class="err">⚠ Password must be at least 8 characters.</div>',
            _reset_form(token), ""))
    update_password(username, pw, CFG)
    token_cookie = make_session_token(username, "operator", CFG.session_secret)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("vs_session", token_cookie, httponly=True, samesite="lax", path="/", max_age=2592000)
    return resp


# ── Preferences ───────────────────────────────────────────────────────────────

_PREFS_TEMPLATE = """<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"><title>VectraSpace — Preferences</title>
{FONTS}<style>{AUTH_CSS}
  .card {{ max-width: 460px; }} .section-title {{ font-size:9px;color:#00d4ff;letter-spacing:3px;
  text-transform:uppercase;margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid #0d2137;margin-top:20px; }}
</style>{ANALYTICS}</head>
<body><div class="card">
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
    <input type="number" name="pc_alert_threshold" step="any" value="{PC_THRESH}">
    <label>Collision Alert Distance (km)</label>
    <input type="number" name="collision_alert_km" step="0.1" value="{ALERT_KM}">
    <button type="submit">Save Preferences</button>
  </form>
  <div class="nav">
    <a href="/">← Dashboard</a> · <a href="/change-password">Change Password</a> · <a href="/logout">Sign Out</a>
  </div>
</div></body></html>"""


@router.get("/preferences", response_class=HTMLResponse)
def preferences_page(request: Request):
    user = get_current_user(request, CFG)
    if not user:
        return RedirectResponse(url="/login?next=/preferences")
    prefs = get_user_prefs(user["username"], CFG)
    return HTMLResponse(_PREFS_TEMPLATE
        .replace("{FONTS}", _FONTS).replace("{AUTH_CSS}", _AUTH_CSS).replace("{ANALYTICS}", _ANALYTICS)
        .replace("{USERNAME}", user["username"]).replace("{EMAIL}", prefs.get("email",""))
        .replace("{PUSHOVER_KEY}", prefs.get("pushover_key",""))
        .replace("{PC_THRESH}", str(prefs.get("pc_alert_threshold", 1e-4)))
        .replace("{ALERT_KM}", str(prefs.get("collision_alert_km", 10.0)))
        .replace("{MESSAGE}", ""))


@router.post("/preferences", response_class=HTMLResponse)
async def preferences_save(request: Request):
    user = get_current_user(request, CFG)
    if not user:
        return RedirectResponse(url="/login?next=/preferences")
    form  = await request.form()
    prefs = {
        "email":               str(form.get("email","")).strip(),
        "phone":               str(form.get("phone","")).strip(),
        "pushover_key":        str(form.get("pushover_key","")).strip(),
        "pc_alert_threshold":  float(form.get("pc_alert_threshold", 1e-4) or 1e-4),
        "collision_alert_km":  float(form.get("collision_alert_km", 10.0) or 10.0),
    }
    save_user_prefs(user["username"], prefs, CFG)
    return HTMLResponse(_PREFS_TEMPLATE
        .replace("{FONTS}", _FONTS).replace("{AUTH_CSS}", _AUTH_CSS).replace("{ANALYTICS}", _ANALYTICS)
        .replace("{USERNAME}", user["username"]).replace("{EMAIL}", prefs["email"])
        .replace("{PUSHOVER_KEY}", prefs["pushover_key"])
        .replace("{PC_THRESH}", str(prefs["pc_alert_threshold"]))
        .replace("{ALERT_KM}", str(prefs["collision_alert_km"]))
        .replace("{MESSAGE}", '<div class="ok">✓ Preferences saved.</div>'))


# ── Change password ───────────────────────────────────────────────────────────

@router.get("/change-password", response_class=HTMLResponse)
def change_pw_page(request: Request):
    user = get_current_user(request, CFG)
    if not user:
        return RedirectResponse(url="/login")
    form = """<form method="post" action="/change-password">
  <label>Current Password</label>
  <input type="password" name="current" required autocomplete="current-password">
  <label>New Password</label>
  <input type="password" name="password" required minlength="8" autocomplete="new-password">
  <label>Confirm New Password</label>
  <input type="password" name="confirm" required minlength="8" autocomplete="new-password">
  <div class="pw-rules">Min 8 chars</div>
  <button type="submit">Update Password</button>
</form>"""
    return HTMLResponse(_auth_page("VectraSpace — Change Password", "Change Password",
                                   f"Logged in as {user['username']}", "", form,
                                   '<a href="/preferences">← Preferences</a>'))


@router.post("/change-password", response_class=HTMLResponse)
async def change_pw_submit(request: Request):
    user = get_current_user(request, CFG)
    if not user:
        return RedirectResponse(url="/login")
    form    = await request.form()
    current = str(form.get("current","")).strip()
    pw      = str(form.get("password","")).strip()
    confirm = str(form.get("confirm","")).strip()
    users   = load_users(CFG)
    u       = users.get(user["username"])

    def _err(msg):
        frm = """<form method="post" action="/change-password">
  <label>Current Password</label><input type="password" name="current" required>
  <label>New Password</label><input type="password" name="password" required minlength="8">
  <label>Confirm</label><input type="password" name="confirm" required minlength="8">
  <button type="submit">Update Password</button></form>"""
        return HTMLResponse(_auth_page("VectraSpace — Change Password", "Change Password",
                                       f"Logged in as {user['username']}",
                                       f'<div class="err">⚠ {msg}</div>', frm,
                                       '<a href="/preferences">← Preferences</a>'))

    if not u or not verify_password(current, u.get("password_hash","")):
        return _err("Current password is incorrect.")
    if pw != confirm:
        return _err("New passwords do not match.")
    if len(pw) < 8:
        return _err("Password must be at least 8 characters.")
    update_password(user["username"], pw, CFG)
    return RedirectResponse(url="/preferences", status_code=303)
