"""
VectraSpace v11 — alerts.py
Email (Gmail/SendGrid/SES/Postmark), webhook, and Pushover notifications.
"""

import datetime
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests

from config import Config

log = logging.getLogger("VectraSpace")

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

# ── HTML email templates ──────────────────────────────────────────────────────

_EMAIL_CSS = """
  body { margin:0; padding:0; background:#050a0f; font-family:'Courier New',monospace; }
  .wrap { max-width:640px; margin:0 auto; background:#090f17; border:1px solid #0d2137; }
  .header { background:linear-gradient(135deg,#0a1929 0%,#0d2137 100%);
            padding:28px 32px 20px; border-bottom:2px solid #00d4ff; }
  .header .badge { font-size:10px; color:#00d4ff; letter-spacing:4px; text-transform:uppercase; margin-bottom:8px; }
  .header h1 { margin:0; font-size:22px; color:#ffffff; font-weight:700; letter-spacing:1px; }
  .header .sub { margin-top:6px; font-size:11px; color:#4a6a85; }
  .meta-bar { background:#0a1520; padding:12px 32px; border-bottom:1px solid #0d2137;
              display:flex; gap:32px; }
  .meta-item { font-size:10px; color:#4a6a85; letter-spacing:1px; }
  .meta-item span { color:#c8dff0; display:block; font-size:12px; margin-top:2px; }
  .body { padding:24px 32px; }
  .section-title { font-size:9px; color:#00d4ff; letter-spacing:3px; text-transform:uppercase;
                   margin-bottom:12px; padding-bottom:6px; border-bottom:1px solid #0d2137; }
  .event-card { background:#0a1520; border:1px solid #0d2137;
                border-left:3px solid #ff4444; border-radius:4px; padding:16px 18px; margin-bottom:12px; }
  .sat-line { font-size:14px; color:#ffffff; font-weight:700; margin-bottom:10px; }
  .stats { display:flex; gap:0; margin-top:8px; }
  .stat { flex:1; padding:8px 12px; background:#050a0f; border-right:1px solid #0d2137; }
  .stat:last-child { border-right:none; }
  .stat .label { font-size:8px; color:#4a6a85; letter-spacing:2px; text-transform:uppercase; margin-bottom:3px; }
  .stat .value { font-size:13px; font-weight:700; }
  .stat .value.danger  { color:#ff4444; }
  .stat .value.warning { color:#ffaa44; }
  .stat .value.info    { color:#00d4ff; }
  .footer { background:#050a0f; padding:16px 32px; border-top:1px solid #0d2137; text-align:center; }
  .footer p { margin:0; font-size:9px; color:#4a6a85; letter-spacing:1px; }
"""


def _build_conjunction_email(alerts: list, run_utc: str, total_sats: int = 0) -> str:
    count  = len(alerts)
    events = ""
    for i, c in enumerate(alerts, 1):
        h, m = divmod(int(c.time_min), 60)
        pc_color   = "danger" if c.pc_estimate >= 1e-3 else "warning" if c.pc_estimate >= 1e-5 else "info"
        dist_color = "danger" if c.min_dist_km < 1.0 else "warning" if c.min_dist_km < 5.0 else "info"
        events += f"""
        <div class="event-card">
          <div class="sat-line">{c.sat1} ↔ {c.sat2}</div>
          <div class="stats">
            <div class="stat"><div class="label">Miss Distance</div>
              <div class="value {dist_color}">{c.min_dist_km:.3f} km</div></div>
            <div class="stat"><div class="label">Pc Estimate</div>
              <div class="value {pc_color}">{c.pc_estimate:.2e}</div></div>
            <div class="stat"><div class="label">Time to CA</div>
              <div class="value info">+{h}h {m:02d}m</div></div>
          </div>
        </div>"""
    sats_html = (f'<div class="meta-item">SATELLITES<span>{total_sats}</span></div>'
                 if total_sats else "")
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>{_EMAIL_CSS}</style></head><body><div class="wrap">
  <div class="header"><div class="badge">VectraSpace // Mission Control</div>
    <h1>⚠ Conjunction Alert</h1>
    <div class="sub">VectraSpace v11 — Automated Report</div></div>
  <div class="meta-bar">
    <div class="meta-item">RUN TIME<span>{run_utc}</span></div>
    <div class="meta-item">EVENTS<span style="color:#ff4444">{count}</span></div>
    {sats_html}
  </div>
  <div class="body"><div class="section-title">Conjunction Events</div>{events}</div>
  <div class="footer"><p>VectraSpace v11 — trumanheaston@gmail.com</p></div>
</div></body></html>"""


# ── SMTP helpers ──────────────────────────────────────────────────────────────

def _build_mime(subject, from_addr, to_addr, html_body, plain_body="") -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    msg["X-Mailer"] = "VectraSpace v11"
    msg.attach(MIMEText(plain_body or subject, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))
    return msg


def _gmail(subject, html, to_addr, from_addr, plain="") -> bool:
    pw = os.environ.get("ALERT_SMTP_PASS", "").strip()
    if not pw:
        log.warning("  ✗ Gmail: ALERT_SMTP_PASS not set")
        return False
    try:
        msg = _build_mime(subject, from_addr, to_addr, html, plain)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
            s.login(from_addr, pw)
            s.send_message(msg)
        log.info(f"  ✓ [Gmail] sent → {to_addr}")
        return True
    except Exception as e:
        log.warning(f"  ✗ Gmail: {e}")
        return False


def _sendgrid(subject, html, to_addr, from_addr, plain="") -> bool:
    key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not key:
        return False
    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"personalizations": [{"to": [{"email": to_addr}]}],
                  "from": {"email": from_addr, "name": "VectraSpace"},
                  "subject": subject,
                  "content": [{"type": "text/plain", "value": plain or subject},
                               {"type": "text/html", "value": html or plain or subject}]},
            timeout=15,
        )
        if resp.status_code in (200, 202):
            log.info(f"  ✓ [SendGrid] sent → {to_addr}")
            return True
        log.warning(f"  ✗ SendGrid HTTP {resp.status_code}")
        return False
    except Exception as e:
        log.warning(f"  ✗ SendGrid: {e}")
        return False


def _ses(subject, html, to_addr, from_addr, plain="") -> bool:
    host = os.environ.get("AWS_SES_HOST", "email-smtp.us-east-1.amazonaws.com")
    user = os.environ.get("AWS_SES_USER", "").strip()
    pwd  = os.environ.get("AWS_SES_PASS", "").strip()
    if not user or not pwd:
        return False
    try:
        msg = _build_mime(subject, from_addr, to_addr, html, plain)
        with smtplib.SMTP_SSL(host, 465, timeout=15) as s:
            s.login(user, pwd)
            s.send_message(msg)
        log.info(f"  ✓ [SES] sent → {to_addr}")
        return True
    except Exception as e:
        log.warning(f"  ✗ SES: {e}")
        return False


def _postmark(subject, html, to_addr, from_addr, plain="") -> bool:
    token = os.environ.get("POSTMARK_SERVER_TOKEN", "").strip()
    if not token:
        return False
    try:
        resp = requests.post(
            "https://api.postmarkapp.com/email",
            headers={"X-Postmark-Server-Token": token, "Content-Type": "application/json"},
            json={"From": from_addr, "To": to_addr, "Subject": subject,
                  "TextBody": plain or subject, "HtmlBody": html or plain or subject,
                  "MessageStream": "outbound"},
            timeout=15,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("ErrorCode", 1) == 0:
            log.info(f"  ✓ [Postmark] sent → {to_addr}")
            return True
        log.warning(f"  ✗ Postmark error {data.get('ErrorCode')}")
        return False
    except Exception as e:
        log.warning(f"  ✗ Postmark: {e}")
        return False


_PROVIDERS = {"gmail": _gmail, "sendgrid": _sendgrid, "ses": _ses, "postmark": _postmark}


def send_email(subject: str, html: str, to_addr: str,
               cfg: Config, plain: str = "") -> bool:
    if not to_addr or not cfg.alert_email_from:
        return False
    provider = os.environ.get("EMAIL_PROVIDER", "gmail").lower().strip()
    fn = _PROVIDERS.get(provider)
    if not fn:
        log.warning(f"Unknown EMAIL_PROVIDER='{provider}'")
        return False
    return fn(subject, html, to_addr, cfg.alert_email_from, plain)


# ── Webhook + Pushover ────────────────────────────────────────────────────────

def send_webhook(body: str, cfg: Config):
    if not cfg.alert_webhook_url:
        return
    try:
        requests.post(cfg.alert_webhook_url,
                      json={"text": f"```\n{body}\n```"}, timeout=10).raise_for_status()
        log.info("  ✓ Webhook alert sent")
    except Exception as e:
        log.warning(f"  ✗ Webhook: {e}")


def send_pushover(title: str, message: str, priority: int, cfg: Config,
                  url: str = "", url_title: str = "Open Dashboard",
                  pushover_user_key: Optional[str] = None):
    token = cfg.pushover_token or os.environ.get("PUSHOVER_TOKEN")
    user  = pushover_user_key or os.environ.get("PUSHOVER_USER_KEY_RUNTIME") or cfg.pushover_user_key
    if not token or not user:
        return
    try:
        requests.post(PUSHOVER_URL, data={
            "token": token, "user": user, "title": title,
            "message": message, "priority": priority,
            "url": url, "url_title": url_title,
        }, timeout=10).raise_for_status()
        log.info("  ✓ Pushover sent")
    except Exception as e:
        log.warning(f"  ✗ Pushover: {e}")


# ── High-level send ───────────────────────────────────────────────────────────

def send_alerts(conjunctions: list, cfg: Config,
                total_sats: int = 0, user_prefs: Optional[dict] = None):
    pc_thresh = (user_prefs or {}).get("pc_alert_threshold", cfg.pc_alert_threshold)
    alert_km  = (user_prefs or {}).get("collision_alert_km", cfg.collision_alert_km)
    alerts    = [c for c in conjunctions
                 if c.pc_estimate >= pc_thresh or c.min_dist_km < alert_km]
    if not alerts:
        log.info("No conjunctions crossed thresholds — no alerts sent")
        return

    run_utc  = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    email_to = (user_prefs or {}).get("email") or cfg.alert_email_to
    pv_key   = (user_prefs or {}).get("pushover_key") or cfg.pushover_user_key

    html = _build_conjunction_email(alerts, run_utc, total_sats)
    send_email("[VectraSpace] ⚠ Conjunction Alert", html, email_to, cfg)
    send_webhook(f"VectraSpace CONJUNCTION ALERT — {len(alerts)} event(s) at {run_utc}", cfg)

    top = alerts[:3]
    pv_body = ", ".join(
        f"{c.sat1[:10]}↔{c.sat2[:10]} ({c.min_dist_km:.1f}km)" for c in top
    )
    if len(alerts) > 3:
        pv_body += f" ...and {len(alerts)-3} more"
    send_pushover("VectraSpace ⚠ Conjunction Alert", pv_body, priority=1,
                  cfg=cfg, pushover_user_key=pv_key)


def send_propagation_complete(total_sats: int, conjunctions: list,
                               duration_s: float, cfg: Config,
                               user_prefs: Optional[dict] = None):
    email_to = (user_prefs or {}).get("email") or cfg.alert_email_to
    pv_key   = (user_prefs or {}).get("pushover_key") or cfg.pushover_user_key
    if not email_to and not cfg.pushover_user_key:
        return
    count = len(conjunctions)
    mins  = int(duration_s // 60)
    secs  = int(duration_s % 60)
    send_pushover("VectraSpace ✓ Scan Complete",
                  f"{total_sats} sats tracked. {count} conjunction(s). {mins}m{secs}s.",
                  priority=-1, cfg=cfg, pushover_user_key=pv_key)
