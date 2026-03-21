"""
VectraSpace v11 — trajectory.py
Suborbital trajectory simulator: physics engine, Pydantic models, API route, and HTML template.

INTEGRATION — add two lines inside create_app() in main.py, in the routers section:
    from trajectory import router as trajectory_router
    app.include_router(trajectory_router, prefix="/api/tools", tags=["tools"])
"""

import math
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

router = APIRouter()


# ════════════════════════════════════════════════════════════════════════════════
#  PHYSICS ENGINE
# ════════════════════════════════════════════════════════════════════════════════

WGS84_A  = 6_378_137.0
WGS84_F  = 1 / 298.257_223_563
WGS84_E2 = 2 * WGS84_F - WGS84_F ** 2
RHO0     = 1.225
H_SCALE  = 8_500.0
DT_DEFAULT = 0.05
MAX_STEPS  = 200_000
MIN_ALT    = -5.0


def _gravity(lat_rad: float, alt: float) -> float:
    sin2 = math.sin(lat_rad) ** 2
    g0   = 9.780_325 * (1 + 0.001_931_851_353 * sin2) / math.sqrt(1 - WGS84_E2 * sin2)
    N    = WGS84_A / math.sqrt(1 - WGS84_E2 * sin2)
    return g0 * (N / (N + max(alt, 0))) ** 2


def _rho(alt: float) -> float:
    return RHO0 * math.exp(-max(alt, 0) / H_SCALE)


def _geodetic_to_ecef(lat: float, lon: float, alt: float):
    sl, cl = math.sin(lat), math.cos(lat)
    sn, cn = math.sin(lon), math.cos(lon)
    N = WGS84_A / math.sqrt(1 - WGS84_E2 * sl * sl)
    return (N + alt) * cl * cn, (N + alt) * cl * sn, (N * (1 - WGS84_E2) + alt) * sl


def _ecef_to_geodetic(x: float, y: float, z: float):
    lon = math.atan2(y, x)
    p   = math.sqrt(x * x + y * y)
    lat = math.atan2(z, p * (1 - WGS84_E2))
    for _ in range(5):
        sl  = math.sin(lat)
        N   = WGS84_A / math.sqrt(1 - WGS84_E2 * sl * sl)
        lat = math.atan2(z + WGS84_E2 * N * sl, p)
    sl  = math.sin(lat)
    N   = WGS84_A / math.sqrt(1 - WGS84_E2 * sl * sl)
    cl  = math.cos(lat)
    alt = (p / cl - N) if abs(cl) > 1e-10 else abs(z) / abs(sl) - N * (1 - WGS84_E2)
    return lat, lon, alt


def _ned_to_ecef_unit(lat: float, lon: float, n: float, e: float, u: float):
    """Convert a (North, East, Up) vector to a normalised ECEF unit vector."""
    sl, cl = math.sin(lat), math.cos(lat)
    sn, cn = math.sin(lon), math.cos(lon)
    x = -sl * cn * n - sn * e + cl * cn * u
    y = -sl * sn * n + cn * e + cl * sn * u
    z =  cl      * n           + sl      * u
    mag = math.sqrt(x*x + y*y + z*z)
    return x / mag, y / mag, z / mag


@dataclass
class _State:
    x: float; y: float; z: float
    vx: float; vy: float; vz: float


def _accel(s: _State, mass, cd, area, thrust, tu):
    lat, _, alt = _ecef_to_geodetic(s.x, s.y, s.z)
    r  = math.sqrt(s.x*s.x + s.y*s.y + s.z*s.z)
    g  = _gravity(lat, alt)
    gx = -g * s.x / r;  gy = -g * s.y / r;  gz = -g * s.z / r

    vm = math.sqrt(s.vx*s.vx + s.vy*s.vy + s.vz*s.vz)
    if vm > 1e-6:
        drag = 0.5 * _rho(alt) * cd * area * vm / mass  # gives deceleration m/s²
        drx, dry, drz = -drag*s.vx, -drag*s.vy, -drag*s.vz
    else:
        drx = dry = drz = 0.0

    ta = thrust / mass if thrust > 0 else 0.0
    return gx+drx+ta*tu[0], gy+dry+ta*tu[1], gz+drz+ta*tu[2]


def _rk4(s: _State, dt, mass, cd, area, thrust, tu):
    def d(s, thr):
        ax, ay, az = _accel(s, mass, cd, area, thr, tu)
        return s.vx, s.vy, s.vz, ax, ay, az

    d0 = d(s, thrust)
    s1 = _State(s.x+.5*dt*d0[0], s.y+.5*dt*d0[1], s.z+.5*dt*d0[2],
                s.vx+.5*dt*d0[3], s.vy+.5*dt*d0[4], s.vz+.5*dt*d0[5])
    d1 = d(s1, thrust)
    s2 = _State(s.x+.5*dt*d1[0], s.y+.5*dt*d1[1], s.z+.5*dt*d1[2],
                s.vx+.5*dt*d1[3], s.vy+.5*dt*d1[4], s.vz+.5*dt*d1[5])
    d2 = d(s2, thrust)
    s3 = _State(s.x+dt*d2[0], s.y+dt*d2[1], s.z+dt*d2[2],
                s.vx+dt*d2[3], s.vy+dt*d2[4], s.vz+dt*d2[5])
    d3 = d(s3, 0.0)

    return _State(
        s.x  + dt/6*(d0[0]+2*d1[0]+2*d2[0]+d3[0]),
        s.y  + dt/6*(d0[1]+2*d1[1]+2*d2[1]+d3[1]),
        s.z  + dt/6*(d0[2]+2*d1[2]+2*d2[2]+d3[2]),
        s.vx + dt/6*(d0[3]+2*d1[3]+2*d2[3]+d3[3]),
        s.vy + dt/6*(d0[4]+2*d1[4]+2*d2[4]+d3[4]),
        s.vz + dt/6*(d0[5]+2*d1[5]+2*d2[5]+d3[5]),
    )


def simulate_trajectory(mass, cd, area, total_impulse, burn_time,
                         launch_angle, azimuth, lat_deg, lon_deg,
                         launch_alt=0.0, dt=DT_DEFAULT):
    thrust = total_impulse / burn_time
    lat0, lon0 = math.radians(lat_deg), math.radians(lon_deg)
    el = math.radians(90.0 - launch_angle)
    az = math.radians(azimuth)
    tu = _ned_to_ecef_unit(lat0, lon0,
                            math.cos(el) * math.cos(az),
                            math.cos(el) * math.sin(az),
                            math.sin(el))

    x0, y0, z0 = _geodetic_to_ecef(lat0, lon0, launch_alt)
    state = _State(x0, y0, z0, 0, 0, 0)

    traj, t, apogee, max_vel = [], 0.0, launch_alt, 0.0
    for step in range(MAX_STEPS):
        lat, lon, alt = _ecef_to_geodetic(state.x, state.y, state.z)
        vel = math.sqrt(state.vx**2 + state.vy**2 + state.vz**2)
        rec_every = max(1, int(1.0 / dt)) if t > burn_time else 1
        if step % rec_every == 0:
            traj.append({"t": round(t, 3), "lat": math.degrees(lat),
                         "lon": math.degrees(lon), "alt": round(alt, 2),
                         "velocity": round(vel, 3)})
        if alt > apogee: apogee = alt
        if vel > max_vel: max_vel = vel
        if alt < MIN_ALT and t > 0.5: break
        state = _rk4(state, dt, mass, cd, area,
                     thrust if t < burn_time else 0.0, tu)
        t += dt

    last = traj[-1] if traj else {"lat": lat_deg, "lon": lon_deg}
    ll, lo = last["lat"], last["lon"]
    dlat = math.radians(ll - lat_deg)
    dlon = math.radians(lo - lon_deg)
    a_gc = (math.sin(dlat/2)**2 +
            math.cos(lat0) * math.cos(math.radians(ll)) * math.sin(dlon/2)**2)
    range_km = 2 * WGS84_A * math.asin(math.sqrt(max(0, a_gc))) / 1000

    return {
        "trajectory": traj,
        "summary": {
            "apogee_m":        round(apogee, 1),
            "landing_lat":     round(ll, 6),
            "landing_lon":     round(lo, 6),
            "max_velocity_ms": round(max_vel, 2),
            "flight_time_s":   round(t, 2),
            "range_km":        round(range_km, 4),
            "burn_time_s":     burn_time,
            "total_points":    len(traj),
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
#  PYDANTIC MODELS
# ════════════════════════════════════════════════════════════════════════════════

class TrajectoryRequest(BaseModel):
    mass:          float = Field(..., gt=0, le=500)
    drag_coeff:    float = Field(..., gt=0, le=5.0)
    area:          float = Field(..., gt=0, le=10.0)
    total_impulse: float = Field(..., gt=0, le=40_960)
    burn_time:     float = Field(..., gt=0, le=30)
    launch_angle:  float = Field(default=0.0, ge=0, le=85)
    azimuth:       float = Field(default=0.0, ge=0, lt=360)
    lat:           float = Field(..., ge=-90, le=90)
    lon:           float = Field(..., ge=-180, le=180)
    launch_alt:    float = Field(default=0.0, ge=-500, le=5000)
    dt:            float = Field(default=0.05, ge=0.01, le=0.5)

    @model_validator(mode="after")
    def check_twr(self):
        avg_thrust = self.total_impulse / self.burn_time
        weight     = self.mass * 9.81
        if avg_thrust < weight * 1.1:
            raise ValueError(
                f"Thrust-to-weight too low: avg thrust {avg_thrust:.1f} N "
                f"must be >= 1.1x weight ({weight * 1.1:.1f} N)."
            )
        return self


# ════════════════════════════════════════════════════════════════════════════════
#  API ROUTES
# ════════════════════════════════════════════════════════════════════════════════

@router.get("/trajectory", response_class=HTMLResponse)
async def trajectory_page():
    return HTMLResponse(HTML_TRAJECTORY)


@router.post("/trajectory/simulate")
async def run_simulation(params: TrajectoryRequest):
    try:
        result = simulate_trajectory(
            mass=params.mass, cd=params.drag_coeff, area=params.area,
            total_impulse=params.total_impulse, burn_time=params.burn_time,
            launch_angle=params.launch_angle, azimuth=params.azimuth,
            lat_deg=params.lat, lon_deg=params.lon,
            launch_alt=params.launch_alt, dt=params.dt,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation error: {e}")
    return result


# ════════════════════════════════════════════════════════════════════════════════
#  HTML TEMPLATE
# ════════════════════════════════════════════════════════════════════════════════

HTML_TRAJECTORY = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Trajectory Simulator — VectraSpace</title>
  <script src="https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Cesium.js"></script>
  <link href="https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Widgets/widgets.css" rel="stylesheet" />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg-0: #050a0f; --bg-1: #090f17; --bg-2: #0a1520; --bg-3: #0a1929;
      --border: #0d2137; --accent: #00d4ff; --accent-dim: #0099bb;
      --green: #00ff88; --danger: #ff4444;
      --text: #c8dff0; --muted: #4a6a85; --white: #ffffff;
      --font: 'Courier New', Courier, monospace;
    }
    html, body { height: 100%; background: var(--bg-0); color: var(--text); font-family: var(--font); overflow: hidden; }
    #app { display: flex; height: 100vh; }
    #sidebar { width: 360px; min-width: 320px; height: 100vh; background: var(--bg-1);
      border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; flex-shrink: 0; }
    #globe-wrap { flex: 1; position: relative; background: var(--bg-0); }
    #cesium-container { width: 100%; height: 100%; }
    .sidebar-header { background: linear-gradient(135deg, #0a1929 0%, #0d2137 100%);
      padding: 20px 24px 16px; border-bottom: 2px solid var(--accent); flex-shrink: 0; }
    .badge { font-size: 9px; color: var(--accent); letter-spacing: 4px; text-transform: uppercase; margin-bottom: 6px; }
    .sidebar-header h1 { font-size: 16px; color: var(--white); font-weight: 700; letter-spacing: 1px; margin-bottom: 3px; }
    .sidebar-header .sub { font-size: 10px; color: var(--muted); letter-spacing: 1px; }
    #form-body { flex: 1; overflow-y: auto; padding: 16px 20px;
      scrollbar-width: thin; scrollbar-color: var(--border) transparent; }
    .section-title { font-size: 9px; color: var(--accent); letter-spacing: 3px; text-transform: uppercase;
      margin: 18px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
    .section-title:first-child { margin-top: 4px; }
    .field { margin-bottom: 10px; }
    .field label { display: block; font-size: 9px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); margin-bottom: 4px; }
    .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    input[type="number"] { width: 100%; background: var(--bg-0); border: 1px solid var(--border);
      border-radius: 3px; color: var(--white); font-family: var(--font); font-size: 13px;
      padding: 7px 10px; transition: border-color 0.15s; -moz-appearance: textfield; }
    input[type="number"]::-webkit-inner-spin-button { -webkit-appearance: none; }
    input:focus { outline: none; border-color: var(--accent); }
    .hint { font-size: 9px; color: var(--muted); margin-top: 3px; letter-spacing: 1px; }
    .preset-row { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 12px; }
    .preset-btn { background: var(--bg-0); border: 1px solid var(--border); border-radius: 2px;
      padding: 4px 9px; font-family: var(--font); font-size: 10px; color: var(--muted);
      letter-spacing: 1px; cursor: pointer; transition: all 0.12s; }
    .preset-btn:hover, .preset-btn.active { border-color: var(--accent); color: var(--accent); background: rgba(0,212,255,0.08); }
    #btn-run { width: 100%; margin-top: 16px; background: var(--accent); color: #050a0f;
      border: none; border-radius: 3px; font-family: var(--font); font-weight: 700;
      font-size: 11px; letter-spacing: 3px; text-transform: uppercase; padding: 13px;
      cursor: pointer; transition: background 0.15s; }
    #btn-run:hover { background: var(--accent-dim); }
    #btn-run:disabled { background: var(--bg-3); color: var(--muted); cursor: not-allowed; }
    #results { flex-shrink: 0; background: var(--bg-0); border-top: 2px solid var(--border); padding: 14px 20px; display: none; }
    .results-title { font-size: 9px; color: var(--green); letter-spacing: 3px; text-transform: uppercase;
      margin-bottom: 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
    .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 8px; }
    .stat-card { background: var(--bg-2); border: 1px solid var(--border); border-radius: 3px; padding: 9px 12px; }
    .stat-card .lbl { font-size: 8px; color: var(--muted); letter-spacing: 2px; text-transform: uppercase; margin-bottom: 3px; }
    .stat-card .val { font-size: 16px; font-weight: 700; color: var(--accent); }
    .stat-card .unit { font-size: 9px; color: var(--muted); margin-left: 2px; }
    #btn-csv { width: 100%; background: transparent; border: 1px solid var(--border); border-radius: 3px;
      color: var(--muted); font-family: var(--font); font-size: 9px; letter-spacing: 2px;
      text-transform: uppercase; padding: 8px; cursor: pointer; transition: all 0.15s; margin-top: 8px; }
    #btn-csv:hover { border-color: var(--accent); color: var(--accent); }
    #globe-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
      flex-direction: column; gap: 14px; background: rgba(5,10,15,0.7);
      opacity: 0; pointer-events: none; transition: opacity 0.2s; z-index: 5; }
    #globe-overlay.active { opacity: 1; pointer-events: all; }
    .spinner { width: 32px; height: 32px; border: 2px solid var(--border);
      border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite; }
    .overlay-label { font-size: 9px; color: var(--accent); letter-spacing: 3px; text-transform: uppercase; }
    @keyframes spin { to { transform: rotate(360deg); } }
    #hud { position: absolute; top: 16px; left: 16px; background: rgba(9,15,23,0.88);
      border: 1px solid var(--border); border-radius: 3px; padding: 12px 16px;
      pointer-events: none; display: none; z-index: 4; }
    #hud .hud-badge { font-size: 8px; color: var(--accent); letter-spacing: 3px; text-transform: uppercase; margin-bottom: 6px; }
    #hud .hud-row { font-size: 11px; color: var(--text); margin-bottom: 2px; }
    #hud .hud-row span { color: var(--white); }
    #toast { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%);
      background: var(--bg-2); border: 1px solid var(--danger); border-radius: 3px;
      padding: 10px 20px; font-size: 11px; color: var(--danger); letter-spacing: 1px;
      opacity: 0; pointer-events: none; transition: opacity 0.2s; z-index: 10; white-space: nowrap; }
    #toast.show { opacity: 1; }
    .cesium-credit-logoContainer, .cesium-credit-textContainer { display: none !important; }
  </style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <div class="sidebar-header">
      <div class="badge">VectraSpace // Mission Control</div>
      <h1>&#11014; Trajectory Simulator</h1>
      <div class="sub">Suborbital RK4 &mdash; WGS84 Gravity &mdash; Exp. Atmosphere</div>
    </div>
    <div id="form-body">
      <div class="section-title">Rocket Properties</div>
      <div class="row-2">
        <div class="field">
          <label>Mass (kg)</label>
          <input type="number" id="mass" value="2.5" step="0.1" min="0.1" max="500" />
          <div class="hint">total wet mass</div>
        </div>
        <div class="field">
          <label>Cd</label>
          <input type="number" id="drag_coeff" value="0.45" step="0.01" min="0.05" max="5" />
          <div class="hint">drag coefficient</div>
        </div>
      </div>
      <div class="field">
        <label>Ref. Area (m&#178;)</label>
        <input type="number" id="area" value="0.00636" step="0.0001" min="0.0001" max="10" />
        <div class="hint">cross-section &mdash; &#960;r&#178; for cylindrical body</div>
      </div>
      <div class="section-title">Motor</div>
      <div class="preset-row">
        <button class="preset-btn" data-impulse="2.5"  data-burn="0.5">A</button>
        <button class="preset-btn" data-impulse="5"    data-burn="0.6">B</button>
        <button class="preset-btn" data-impulse="10"   data-burn="0.8">C</button>
        <button class="preset-btn" data-impulse="20"   data-burn="1.0">D</button>
        <button class="preset-btn" data-impulse="40"   data-burn="1.5">E</button>
        <button class="preset-btn" data-impulse="80"   data-burn="2.0">F</button>
        <button class="preset-btn" data-impulse="160"  data-burn="2.2">G</button>
        <button class="preset-btn" data-impulse="320"  data-burn="2.5">H</button>
        <button class="preset-btn" data-impulse="640"  data-burn="3.2">I</button>
        <button class="preset-btn" data-impulse="1280" data-burn="3.8">J</button>
        <button class="preset-btn" data-impulse="2560" data-burn="4.5">K</button>
      </div>
      <div class="row-2">
        <div class="field">
          <label>Total Impulse (N&#183;s)</label>
          <input type="number" id="total_impulse" value="320" step="1" min="0.1" max="40960" />
        </div>
        <div class="field">
          <label>Burn Time (s)</label>
          <input type="number" id="burn_time" value="2.5" step="0.1" min="0.1" max="30" />
        </div>
      </div>
      <div class="section-title">Launch Geometry</div>
      <div class="row-2">
        <div class="field">
          <label>Angle from Vertical (&#176;)</label>
          <input type="number" id="launch_angle" value="5" step="0.5" min="0" max="85" />
          <div class="hint">0 = straight up</div>
        </div>
        <div class="field">
          <label>Azimuth (&#176;)</label>
          <input type="number" id="azimuth" value="0" step="1" min="0" max="359" />
          <div class="hint">clockwise from N</div>
        </div>
      </div>
      <div class="section-title">Launch Site</div>
      <div class="row-2">
        <div class="field">
          <label>Latitude (&#176;)</label>
          <input type="number" id="lat" value="34.05" step="0.0001" min="-90" max="90" />
        </div>
        <div class="field">
          <label>Longitude (&#176;)</label>
          <input type="number" id="lon" value="-117.45" step="0.0001" min="-180" max="180" />
        </div>
      </div>
      <div class="field">
        <label>Site Altitude (m ASL)</label>
        <input type="number" id="launch_alt" value="450" step="10" min="-500" max="5000" />
      </div>
      <button id="btn-run">&#9654; Run Simulation</button>
    </div>

    <div id="results">
      <div class="results-title">&#10003; Simulation Complete</div>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="lbl">Apogee</div>
          <div class="val" id="r-apogee">&mdash;</div>
          <div class="unit">m</div>
        </div>
        <div class="stat-card">
          <div class="lbl">Max Velocity</div>
          <div class="val" id="r-vel">&mdash;</div>
          <div class="unit">m/s</div>
        </div>
        <div class="stat-card">
          <div class="lbl">Flight Time</div>
          <div class="val" id="r-time">&mdash;</div>
          <div class="unit">s</div>
        </div>
        <div class="stat-card">
          <div class="lbl">Surface Range</div>
          <div class="val" id="r-range">&mdash;</div>
          <div class="unit">km</div>
        </div>
      </div>
      <div class="stat-card" style="margin-bottom:0">
        <div class="lbl">Landing Zone</div>
        <div style="font-size:11px;color:var(--white);margin-top:3px;" id="r-landing">&mdash;</div>
      </div>
      <button id="btn-csv">&#8595; Export CSV</button>
    </div>
  </div>

  <div id="globe-wrap">
    <div id="cesium-container"></div>
    <div id="hud">
      <div class="hud-badge">Last Trajectory</div>
      <div class="hud-row">Apogee: <span id="hud-apogee">&mdash;</span></div>
      <div class="hud-row">T-flight: <span id="hud-time">&mdash;</span></div>
      <div class="hud-row">V-max: <span id="hud-vel">&mdash;</span></div>
    </div>
    <div id="globe-overlay">
      <div class="spinner"></div>
      <div class="overlay-label">Computing Trajectory</div>
    </div>
    <div id="toast"></div>
  </div>
</div>

<script>
Cesium.Ion.defaultAccessToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJlMzRmMGI5Ni1hMTM0LTQxMjgtODgzMy04ZGYxN2UzNzYyN2MiLCJpZCI6MzkyNzg4LCJpYXQiOjE3NzE2OTU4OTF9.lulZ9jWB9A_XCxfui1FpcGmC7A7B49znZpcwn7yg530';

const viewer = new Cesium.Viewer('cesium-container', {
  terrainProvider: Cesium.createWorldTerrain(),
  baseLayerPicker: false, navigationHelpButton: false, homeButton: false,
  sceneModePicker: false, geocoder: false, animation: false, timeline: false,
  fullscreenButton: false, infoBox: false, selectionIndicator: false,
  creditContainer: document.createElement('div'),
});
viewer.scene.globe.enableLighting = true;

let lastTrajectory = null, lastSummary = null, cesiumEntities = [];

document.querySelectorAll('.preset-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.getElementById('total_impulse').value = btn.dataset.impulse;
    document.getElementById('burn_time').value     = btn.dataset.burn;
    document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 5000);
}
function setLoading(on) {
  document.getElementById('globe-overlay').classList.toggle('active', on);
  document.getElementById('btn-run').disabled = on;
}
function clearEntities() {
  cesiumEntities.forEach(e => viewer.entities.remove(e));
  cesiumEntities = [];
}

document.getElementById('btn-run').addEventListener('click', async () => {
  const get = id => parseFloat(document.getElementById(id).value);
  const payload = {
    mass: get('mass'), drag_coeff: get('drag_coeff'), area: get('area'),
    total_impulse: get('total_impulse'), burn_time: get('burn_time'),
    launch_angle: get('launch_angle'), azimuth: get('azimuth'),
    lat: get('lat'), lon: get('lon'), launch_alt: get('launch_alt'),
  };
  for (const [k, v] of Object.entries(payload)) {
    if (isNaN(v)) { showToast('Invalid value for ' + k); return; }
  }
  setLoading(true); clearEntities();
  try {
    const res = await fetch('/api/tools/trajectory/simulate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'HTTP ' + res.status);
    }
    const data = await res.json();
    lastTrajectory = data.trajectory;
    lastSummary    = data.summary;
    renderTrajectory(data.trajectory, data.summary, payload);
    showResults(data.summary);
  } catch(e) {
    showToast('Simulation error: ' + e.message);
  } finally {
    setLoading(false);
  }
});

function renderTrajectory(traj, summary, params) {
  clearEntities();
  const positions = traj.map(p => Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.alt));

  cesiumEntities.push(viewer.entities.add({
    polyline: {
      positions, width: 2,
      material: new Cesium.PolylineGlowMaterialProperty({
        glowPower: 0.15,
        color: Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.9),
      }),
    },
  }));

  cesiumEntities.push(viewer.entities.add({
    position: Cesium.Cartesian3.fromDegrees(params.lon, params.lat, params.launch_alt),
    point: { pixelSize: 8, color: Cesium.Color.fromCssColorString('#00ff88'), outlineColor: Cesium.Color.WHITE, outlineWidth: 1 },
    label: { text: 'LAUNCH', font: '10px Courier New', fillColor: Cesium.Color.fromCssColorString('#00ff88'),
      pixelOffset: new Cesium.Cartesian2(0, -20), style: Cesium.LabelStyle.FILL },
  }));

  const apoPt = traj.reduce((a, b) => b.alt > a.alt ? b : a, traj[0]);
  cesiumEntities.push(viewer.entities.add({
    position: Cesium.Cartesian3.fromDegrees(apoPt.lon, apoPt.lat, apoPt.alt),
    point: { pixelSize: 7, color: Cesium.Color.fromCssColorString('#00d4ff'), outlineColor: Cesium.Color.WHITE, outlineWidth: 1 },
    label: { text: 'APOGEE\\n' + Math.round(summary.apogee_m).toLocaleString() + ' m',
      font: '10px Courier New', fillColor: Cesium.Color.fromCssColorString('#00d4ff'),
      pixelOffset: new Cesium.Cartesian2(0, -22), style: Cesium.LabelStyle.FILL },
  }));

  cesiumEntities.push(viewer.entities.add({
    position: Cesium.Cartesian3.fromDegrees(summary.landing_lon, summary.landing_lat, 0),
    point: { pixelSize: 8, color: Cesium.Color.fromCssColorString('#ff4444'), outlineColor: Cesium.Color.WHITE, outlineWidth: 1 },
    label: { text: 'LANDING', font: '10px Courier New', fillColor: Cesium.Color.fromCssColorString('#ff4444'),
      pixelOffset: new Cesium.Cartesian2(0, -20), style: Cesium.LabelStyle.FILL },
  }));

  viewer.flyTo(cesiumEntities[0], {
    offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-35), Math.max(summary.apogee_m * 8, 50000)),
    duration: 2.0,
  });

  document.getElementById('hud').style.display = 'block';
  document.getElementById('hud-apogee').textContent = Math.round(summary.apogee_m).toLocaleString() + ' m';
  document.getElementById('hud-time').textContent   = summary.flight_time_s.toFixed(1) + ' s';
  document.getElementById('hud-vel').textContent    = summary.max_velocity_ms.toFixed(1) + ' m/s';
}

function showResults(s) {
  document.getElementById('results').style.display = 'block';
  document.getElementById('r-apogee').textContent  = Math.round(s.apogee_m).toLocaleString();
  document.getElementById('r-vel').textContent     = s.max_velocity_ms.toFixed(1);
  document.getElementById('r-time').textContent    = s.flight_time_s.toFixed(1);
  document.getElementById('r-range').textContent   = s.range_km.toFixed(3);
  document.getElementById('r-landing').textContent = s.landing_lat.toFixed(5) + '\u00b0N  ' + s.landing_lon.toFixed(5) + '\u00b0E';
}

document.getElementById('btn-csv').addEventListener('click', () => {
  if (!lastTrajectory) return;
  const rows = lastTrajectory.map(p => p.t+','+p.lat+','+p.lon+','+p.alt+','+p.velocity).join('\n');
  const blob = new Blob(['time_s,lat_deg,lon_deg,alt_m,velocity_ms\n' + rows], {type: 'text/csv'});
  const a = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(blob),
    download: 'vectraspace_trajectory_' + Date.now() + '.csv'
  });
  a.click(); URL.revokeObjectURL(a.href);
});
</script>
</body>
</html>"""
