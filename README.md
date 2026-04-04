# VectraSpace v11 — Modular Architecture

**Orbital Safety Platform** | FastAPI + SGP4 + CesiumJS

---

## Repository Structure

```
VectraSpace/
├── main.py               # App factory, middleware, lifespan, CLI
├── config.py             # Config dataclass + CFG singleton
├── pipeline.py           # Full scan: TLE → propagate → detect → alert
├── tle.py                # TLE fetch (CelesTrak + Space-Track), regime bucketing
├── propagation.py        # SGP4 batch propagation → SatTrack
├── conjunction.py        # Conjunction detection, Pc, CW maneuver
├── debris.py             # NASA SBM fragment generation
├── alerts.py             # Email / webhook / Pushover notifications
├── database.py           # SQLite schema, CDM generation, covariance cache
├── users.py              # User CRUD, PBKDF2, session/reset tokens
├── scheduler.py          # Headless/cron runner
├── pages.py              # Static page routes (APIRouter)
├── auth_routes.py        # Login/signup/reset/preferences (APIRouter)
├── satellites.py         # /run SSE, /conjunctions, /cdm, /sat-info (APIRouter)
├── admin.py              # /admin, /admin/data (APIRouter)
├── trajectory.py         # Suborbital trajectory simulator (APIRouter → /api/tools/trajectory)
├── templates_loader.py   # All HTML constants (populated by build_templates.py)
├── build_templates.py    # One-time extraction script
├── Procfile              # Render start command
└── requirements.txt      # Python dependencies
```

---

## Deployment (Render — Fresh Setup)

### Step 1 — Upload files to GitHub

```bash
# On your machine (Git CLI required)
git clone https://github.com/trumanheaston-lab/VectraSpace.git
cd VectraSpace

# Delete everything except .git
git rm -rf .

# Copy the entire VectraSpace/ folder contents here
# (drag the unzipped folder contents into the repo folder via Explorer)

# Run the template extraction script (requires vectraspace.py present)
python build_templates.py

# Commit and push
git add .
git commit -m "Refactor: modular flat architecture v11"
git push origin main
```

### Step 2 — Render settings

| Setting | Value |
|---------|-------|
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn main:app -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT` |
| **Runtime** | Python 3 |

### Step 3 — Environment variables (Render dashboard → Environment)

**Required:**
```
SESSION_SECRET          = <run: python -c "import secrets; print(secrets.token_hex(32))">
ADMIN_USER              = admin
ADMIN_PASS              = <your strong password>
CESIUM_ION_TOKEN        = <your Cesium Ion token>
```

**Optional (alerts):**
```
ALERT_EMAIL_FROM        = trumanheaston@gmail.com
ALERT_EMAIL_TO          = trumanheaston@gmail.com
ALERT_SMTP_PASS         = <Gmail App Password>
EMAIL_PROVIDER          = gmail          # gmail | sendgrid | ses | postmark
PUSHOVER_TOKEN          = <token>
PUSHOVER_USER_KEY       = <key>
```

**Optional (Space-Track):**
```
SPACETRACK_USER         = <email>
SPACETRACK_PASS         = <password>
```

**Optional (Anthropic — satellite info modal):**
```
ANTHROPIC_API_KEY       = <key>
```

**Optional (signups):**
```
SIGNUP_OPEN             = true           # set false to close registration
VECTRASPACE_BASE_URL    = https://your-app.onrender.com
```

---

## Local Development

```bash
pip install -r requirements.txt

# Create a .env file (see Environment Variables above)

# Run
python main.py                    # starts on http://localhost:8000

# Or with uvicorn auto-reload:
uvicorn main:app --reload
```

---

## Adding a New Page

1. Add the HTML constant to `templates_loader.py`
2. Add a route in `pages.py` (or a new router file)
3. Include the router in `main.py` → `create_app()`

---

## Headless / Scheduled Runs

```bash
python main.py --headless         # runs pipeline once, exits
```

Windows Task Scheduler XML:
```bash
python main.py --gen-task-xml     # writes VectraSpace_Task.xml
```

---

## Team

| Person | Role | Contact |
|--------|------|---------|
| **Truman Heaston** | Founder / CEO | trumanheaston@gmail.com |
| **Will Lovelace** | Marketing & Outreach | Will.s.lovelace@gmail.com |
| **Grant Gill** | Hardware Lead · 3D File Store Contributor | [jellycatgrant@gmail.com](mailto:jellycatgrant@gmail.com) |

**GitHub:** https://github.com/trumanheaston-lab/VectraSpace

---

## Tech Stack

- **FastAPI** — async web framework
- **Skyfield / SGP4** — orbital propagation  
- **NumPy / SciPy** — vectorised conjunction screening
- **CesiumJS** — 3D globe visualisation
- **SQLite** — conjunction database
- **Gunicorn + Uvicorn** — production ASGI server
- **Render** — deployment platform
