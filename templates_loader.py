"""
VectraSpace — templates_loader.py
All HTML page constants. Loaded at import time.
Auth, login, logout, signup, and quiz UI removed. All routes public.
"""

DASHBOARD_HTML = '''<!DOCTYPE html>
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
    #sidebar { width: 100vw !important; min-width: unset !important; }
    #globe-header { display: none; }
    /* Inputs: bigger tap targets */
    .field input, .field select { font-size: 14px !important; padding: 10px 10px !important; min-height: 44px; }
    .field label { font-size: 9px; }
    /* Run button: full width, tall */
    #run-btn { width: 100% !important; min-height: 52px; font-size: 12px !important; letter-spacing: 2px; }
    /* Section titles: slightly bigger */
    .section-title { font-size: 9px; letter-spacing: 2px; }
    /* Tighter section padding */
    .section { padding: 10px 12px; }
    #scroll { padding: 8px; }
    #header { padding: 10px 12px; }
    /* Sign-in button full width on small sidebar */
    .signin-btn { width: 100%; justify-content: center; }
  }

  /* ── SIGN IN BUTTON ── */
  .signin-btn {
    display: inline-flex; align-items: center; gap: 7px;
    font-family: var(--mono); font-size: 9px; letter-spacing: 1.5px;
    text-transform: uppercase; text-decoration: none;
    padding: 8px 16px; border-radius: 5px;
    background: var(--accent); color: var(--bg);
    border: none; cursor: pointer; font-weight: 600;
    transition: background 0.2s, transform 0.15s;
    white-space: nowrap;
  }
  .signin-btn:hover { background: #6bb5ff; transform: translateY(-1px); }
  .signin-btn svg { width: 12px; height: 12px; flex-shrink: 0; }
  #user-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px; border-bottom: 1px solid var(--border);
    min-height: 42px; gap: 8px; flex-wrap: wrap;
  }
  #user-label { font-family: var(--mono); font-size: 9px; color: var(--muted); letter-spacing: 0.5px; }
  #user-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  #user-actions a {
    font-family: var(--mono); font-size: 8px; letter-spacing: 1px;
    color: var(--muted); text-decoration: none; padding: 4px 8px;
    border: 1px solid var(--border); border-radius: 3px;
    text-transform: uppercase; transition: all 0.15s;
  }
  #user-actions a:hover { color: var(--accent); border-color: rgba(74,158,255,0.4); }
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
        <a href="/" class="brand-name" style="text-decoration:none;" title="VectraSpace Home">Vectra<em>Space</em></a>
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
        </div>

      <!-- Detection Settings -->
      <div class="section">
        <div class="section-title">Satellites per Regime</div>
        <div style="display:flex;gap:8px;">
          <div class="field" style="flex:1;margin-bottom:0">
            <label>LEO</label>
            <input type="number" id="num_leo" placeholder="100" min="1" max="2000">
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
          Run is available
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
    userActions.innerHTML = adminLnk;
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
    userActions.innerHTML = '';
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
'''

SCENARIOS_HTML = r'''<!DOCTYPE html>
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
    <a href="/scenarios" class="tb-link active">Scenarios</a>
    <a href="/api/tools/trajectory" class="tb-link">Trajectory ↗</a>
    <a href="/calculator" class="tb-link">Calculator</a>
    <a href="/glossary" class="tb-link">Resources</a>
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
      const fyLine = mkLine(fyPts, 0x44ffaa, 1.0);
      sceneGroup.add(fyLine);
      animState._fyLine = fyLine;
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
        if (s._fyLine) s._fyLine.visible = true;
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
</html>'''

CALC_HTML = '''<!DOCTYPE html>
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
    <a href="/glossary" class="nav-link">Resources</a>
    <a href="/calculator" class="nav-link active">Calculator</a>
    <a href="/api/tools/trajectory" class="nav-link">Trajectory ↗</a>
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
</html>'''

GLOSSARY_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Resources — VectraSpace</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:ital,wght@0,400;0,500;1,400&family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#080c12;--ink2:#0d1320;--ink3:#131d2e;--panel:#0f1925;
  --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.13);
  --text:#ccd6e0;--muted:#8aaac5;--faint:#2a3d50;
  --accent:#4a9eff;--accent2:#7bc4ff;--green:#34d399;--amber:#f59e0b;--red:#f87171;--purple:#a78bfa;
  --serif:"Instrument Serif",Georgia,serif;
  --mono:"DM Mono",monospace;
  --sans:"Outfit",sans-serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--ink);color:var(--text);font-family:var(--sans);line-height:1.6;min-height:100vh;}

nav{position:fixed;top:0;left:0;right:0;z-index:100;height:60px;padding:0 40px;display:flex;align-items:center;justify-content:space-between;background:rgba(8,12,18,0.96);border-bottom:1px solid var(--border);backdrop-filter:blur(16px);}
.nav-brand{text-decoration:none;}
.nav-brand-name{font-family:var(--serif);font-size:17px;font-style:italic;color:#fff;letter-spacing:-0.2px;}
.nav-brand-name em{color:var(--accent);font-style:normal;}
.nav-right{display:flex;gap:8px;}
.nav-back{font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--muted);text-decoration:none;padding:7px 14px;border:1px solid var(--border);border-radius:4px;transition:all 0.2s;}
.nav-back:hover{color:var(--text);border-color:var(--border2);}

/* HERO */
.hero{padding:108px 48px 52px;max-width:1100px;margin:0 auto;}
.eyebrow{font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--green);margin-bottom:14px;display:flex;align-items:center;gap:10px;}
.eyebrow::before{content:"";width:14px;height:1px;background:var(--green);}
.hero-title{font-family:var(--serif);font-size:clamp(36px,5vw,62px);color:#fff;font-weight:400;line-height:1.08;letter-spacing:-0.5px;margin-bottom:14px;}
.hero-title em{font-style:italic;color:var(--accent2);}
.hero-sub{font-size:15px;color:var(--muted);max-width:540px;line-height:1.8;}

/* SEARCH + FILTER */
.controls{max-width:1100px;margin:0 auto;padding:0 48px 36px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
.search-wrap{position:relative;flex:1;min-width:200px;}
.si{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--faint);pointer-events:none;}
.search-input{width:100%;background:var(--ink2);border:1px solid var(--border);border-radius:8px;padding:10px 13px 10px 40px;font-family:var(--mono);font-size:11px;color:var(--text);outline:none;transition:border-color 0.2s;letter-spacing:0.3px;}
.search-input::placeholder{color:var(--faint);}
.search-input:focus{border-color:rgba(74,158,255,0.35);}
.filters{display:flex;gap:5px;flex-wrap:wrap;}
.fbtn{font-family:var(--mono);font-size:8px;letter-spacing:1px;text-transform:uppercase;padding:6px 13px;border-radius:20px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;transition:all 0.15s;white-space:nowrap;}
.fbtn:hover{border-color:var(--border2);color:var(--text);}
.fbtn.on{color:#fff;}
.fbtn[data-cat="all"].on{border-color:var(--accent);background:rgba(74,158,255,0.1);color:var(--accent);}
.fbtn[data-cat="data"].on{border-color:var(--accent);background:rgba(74,158,255,0.1);color:var(--accent);}
.fbtn[data-cat="tools"].on{border-color:var(--green);background:rgba(52,211,153,0.1);color:var(--green);}
.fbtn[data-cat="papers"].on{border-color:var(--amber);background:rgba(245,158,11,0.1);color:var(--amber);}
.fbtn[data-cat="standards"].on{border-color:var(--red);background:rgba(248,113,113,0.1);color:var(--red);}
.fbtn[data-cat="courses"].on{border-color:var(--purple);background:rgba(167,139,250,0.1);color:var(--purple);}

/* META */
.meta{max-width:1100px;margin:0 auto;padding:0 48px 20px;font-family:var(--mono);font-size:9px;letter-spacing:1px;color:var(--faint);}

/* GRID */
.grid{max-width:1100px;margin:0 auto;padding:0 48px 100px;display:grid;grid-template-columns:repeat(3,1fr);gap:16px;}

/* CARD */
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;transition:border-color 0.2s,transform 0.18s;text-decoration:none;color:inherit;}
.card:hover{border-color:var(--border2);transform:translateY(-2px);}
.card-top{padding:20px 22px 16px;flex:1;display:flex;flex-direction:column;gap:10px;}
.card-meta{display:flex;align-items:center;justify-content:space-between;gap:8px;}
.tag{font-family:var(--mono);font-size:7px;letter-spacing:1.5px;text-transform:uppercase;padding:3px 9px;border-radius:10px;border:1px solid;white-space:nowrap;flex-shrink:0;}
.tag-data{color:var(--accent);border-color:rgba(74,158,255,0.25);background:rgba(74,158,255,0.07);}
.tag-tools{color:var(--green);border-color:rgba(52,211,153,0.25);background:rgba(52,211,153,0.07);}
.tag-papers{color:var(--amber);border-color:rgba(245,158,11,0.25);background:rgba(245,158,11,0.07);}
.tag-standards{color:var(--red);border-color:rgba(248,113,113,0.25);background:rgba(248,113,113,0.07);}
.tag-courses{color:var(--purple);border-color:rgba(167,139,250,0.25);background:rgba(167,139,250,0.07);}
.card-org{font-family:var(--mono);font-size:8px;letter-spacing:0.5px;color:var(--faint);}
.card-title{font-family:var(--serif);font-size:17px;font-style:italic;color:#fff;line-height:1.35;}
.card-desc{font-size:12px;color:var(--muted);line-height:1.7;}
.card-foot{padding:12px 22px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;}
.card-link-label{font-family:var(--mono);font-size:8px;letter-spacing:1px;text-transform:uppercase;color:var(--accent);display:flex;align-items:center;gap:5px;}
.card-link-label svg{transition:transform 0.15s;}
.card:hover .card-link-label svg{transform:translateX(3px);}
.card-free{font-family:var(--mono);font-size:7px;letter-spacing:1px;color:var(--green);background:rgba(52,211,153,0.07);border:1px solid rgba(52,211,153,0.2);padding:2px 7px;border-radius:8px;}

/* EMPTY */
.empty{grid-column:1/-1;text-align:center;padding:70px 0;font-family:var(--mono);font-size:11px;letter-spacing:1px;color:var(--faint);}

/* SECTION DIVIDER */
.section-label{grid-column:1/-1;display:flex;align-items:center;gap:14px;padding-top:8px;}
.section-label-text{font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;}
.section-label-line{flex:1;height:1px;background:var(--border);}

@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr);}}
@media(max-width:580px){
  nav{padding:0 20px;}
  .hero,.controls,.meta,.grid{padding-left:20px;padding-right:20px;}
  .hero{padding-top:88px;}
  .grid{grid-template-columns:1fr;}
}
</style>
</head>
<body>
<nav>
  <a href="/" class="nav-brand"><span class="nav-brand-name">Vectra<em>Space</em></span></a>
  <div class="nav-right">
    <a href="/" class="nav-back">&larr; Hub</a>
    <a href="/dashboard" class="nav-back">Dashboard &rarr;</a>
  </div>
</nav>

<div class="hero">
  <div class="eyebrow">Curated Library</div>
  <h1 class="hero-title">Space Safety <em>Resources</em></h1>
  <p class="hero-sub">Hand-picked datasets, tools, papers, standards, and courses for orbital mechanics researchers, satellite operators, and space safety professionals.</p>
</div>

<div class="controls">
  <div class="search-wrap">
    <svg class="si" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    <input class="search-input" id="rs-search" type="text" placeholder="Search resources, organisations, topics..." autocomplete="off" spellcheck="false">
  </div>
  <div class="filters">
    <button class="fbtn on" data-cat="all">All</button>
    <button class="fbtn" data-cat="data">Data &amp; Catalogs</button>
    <button class="fbtn" data-cat="tools">Tools</button>
    <button class="fbtn" data-cat="papers">Papers</button>
    <button class="fbtn" data-cat="standards">Standards</button>
    <button class="fbtn" data-cat="courses">Courses</button>
  </div>
</div>

<div class="meta" id="rs-meta"></div>
<div class="grid" id="rs-grid"></div>

<script>
var RESOURCES = [

  /* ── DATA & CATALOGS ─────────────────────────────────────── */
  { cat:"data", org:"CelesTrak / Dr. T.S. Kelso", free:true,
    title:"GP Element Sets (TLE Catalog)",
    desc:"The most widely used source of publicly available TLE data. Provides GP element sets for 20,000+ tracked objects in multiple formats (TLE, JSON, CSV) updated several times daily. Essential for any SGP4-based propagation work.",
    url:"https://celestrak.org/NORAD/elements/" },

  { cat:"data", org:"18th Space Defense Squadron", free:true,
    title:"Space-Track.org Satellite Catalog",
    desc:"The authoritative US government satellite catalog maintained by 18 SDS. Provides TLEs, conjunction data messages (CDMs), launch and decay notifications. Requires free account registration. The source for official SSN tracking data.",
    url:"https://www.space-track.org" },

  { cat:"data", org:"ESA DISCOS", free:false,
    title:"ESA DISCOS Database",
    desc:"ESA's Database and Information System Characterising Objects in Space. Comprehensive physical and orbital characteristics for space objects — dimensions, mass, shape, material composition. Used for accurate area-to-mass ratio calculations in drag and SRP modeling.",
    url:"https://discosweb.esoc.esa.int" },

  { cat:"data", org:"NASA CARA", free:true,
    title:"Conjunction Assessment Risk Analysis (CARA)",
    desc:"NASA's public conjunction assessment service providing close approach screening for NASA assets. Publishes methodology papers and probability of collision calculation approaches used as a reference standard across the industry.",
    url:"https://nasa.gov/conjunction-assessment" },

  { cat:"data", org:"LeoLabs", free:false,
    title:"LeoLabs Space Object Database",
    desc:"Commercial radar network tracking LEO objects with higher update rates and smaller trackable sizes than SSN. Provides conjunction screening, decay predictions, and maneuver support for commercial operators.",
    url:"https://leolabs.space" },

  { cat:"data", org:"Aerospace Corp / CCAR", free:true,
    title:"SOCRATES Conjunction Screening",
    desc:"Satellite Orbital Conjunction Reports Assessing Threatening Encounters in Space. Daily screening of the entire satellite catalog using publicly available TLEs. Ranks the top conjunction events by probability of collision.",
    url:"https://celestrak.org/SOCRATES/" },

  /* ── TOOLS ──────────────────────────────────────────────── */
  { cat:"tools", org:"AGI / Ansys", free:false,
    title:"Systems Tool Kit (STK)",
    desc:"The industry-standard orbital analysis and space mission engineering software. Models satellite coverage, conjunction analysis, sensor performance, and link budgets. Used by most government and commercial satellite operators globally.",
    url:"https://www.ansys.com/products/missions/ansys-stk" },

  { cat:"tools", org:"NASA / Vallado", free:true,
    title:"SGP4 Reference Implementation",
    desc:"The canonical C++, Python, and MATLAB implementation of the SGP4/SDP4 propagator by David Vallado at Aerospace Corp. The definitive reference for implementing TLE-based orbit propagation consistent with USSPACECOM standards.",
    url:"https://celestrak.org/software/vallado-sw.php" },

  { cat:"tools", org:"Open Source", free:true,
    title:"Skyfield (Python)",
    desc:"High-accuracy Python library for computing positions of stars, planets, and Earth satellites. Uses modern IERS Earth orientation data and supports TLE propagation via SGP4. The best Python library for precise satellite positional astronomy.",
    url:"https://rhodesmill.org/skyfield/" },

  { cat:"tools", org:"ESA", free:true,
    title:"DRAMA — Debris Risk Assessment",
    desc:"ESA's Debris Risk Assessment and Mitigation Analysis tool. Computes casualty risk for uncontrolled reentries, orbital lifetime estimates, and collision avoidance maneuver analysis. Used for compliance with space debris mitigation guidelines.",
    url:"https://sdup.esoc.esa.int/drama/" },

  { cat:"tools", org:"NASA Goddard", free:true,
    title:"GMAT — General Mission Analysis Tool",
    desc:"NASA's open-source mission design and navigation software. Supports high-fidelity trajectory optimization, maneuver planning, formation flying analysis, and conjunction assessment. Used for mission planning from LEO to deep space.",
    url:"https://software.nasa.gov/software/GSC-17177-1" },

  { cat:"tools", org:"Open Source / Helge Eichhorn", free:true,
    title:"Orekit (Java/Python)",
    desc:"A highly accurate open-source space dynamics library supporting all major orbit propagators (numerical, SGP4, Eckstein-Hechler), coordinate frame transformations, attitude modeling, and event detection. The reference for high-fidelity propagation in research.",
    url:"https://www.orekit.org" },

  /* ── PAPERS ─────────────────────────────────────────────── */
  { cat:"papers", org:"Kessler & Cour-Palais (1978)", free:false,
    title:"Collision Frequency of Artificial Satellites",
    desc:"The foundational paper introducing what became known as the Kessler Syndrome — the concept of a self-sustaining debris cascade in LEO. Arguably the most important paper in the history of space debris research. Published in Journal of Geophysical Research.",
    url:"https://doi.org/10.1029/JA083iA06p02637" },

  { cat:"papers", org:"Alfriend et al. (1999)", free:false,
    title:"Probability of Collision Error Analysis",
    desc:"Defines the probability of collision formulation used as the industry standard for conjunction assessment. Introduces the combined covariance approach and the 2D Pc computation method still used by 18 SDS and NASA CARA today.",
    url:"https://doi.org/10.1023/A:1008168728822" },

  { cat:"papers", org:"Vallado & Crawford (2008)", free:true,
    title:"SGP4 Orbit Determination",
    desc:"Comprehensive treatment of SGP4 orbit determination from observations, mean element generation, and TLE fitting. Essential reading for anyone implementing or validating SGP4 propagation. Freely available via AIAA.",
    url:"https://celestrak.org/publications/AIAA/2008-6770/" },

  { cat:"papers", org:"Letizia et al. (ESA, 2019)", free:true,
    title:"Extending the Continuum Approach for Debris Evolution",
    desc:"Presents ESA's DELTA model for long-term debris environment evolution using a continuum (density-based) approach rather than Monte Carlo object-by-object simulation. Provides a computationally tractable method for century-scale debris projections.",
    url:"https://doi.org/10.1016/j.actaastro.2019.01.039" },

  { cat:"papers", org:"Oltrogge & Alfano (2019)", free:true,
    title:"The Universal Pc Algorithm",
    desc:"Proposes a unified probability of collision calculation framework reconciling multiple prior Pc methods. Addresses numerical edge cases in near-miss geometries where the classical Alfriend formulation breaks down. Widely cited in recent conjunction analysis work.",
    url:"https://doi.org/10.1007/s10569-019-9927-z" },

  /* ── STANDARDS ──────────────────────────────────────────── */
  { cat:"standards", org:"IADC", free:true,
    title:"IADC Space Debris Mitigation Guidelines",
    desc:"The Inter-Agency Space Debris Coordination Committee guidelines — the internationally agreed baseline for debris mitigation. Covers protected orbital regions, passivation requirements, and the 25-year deorbit rule for LEO. The reference standard for compliance.",
    url:"https://www.iadc-home.org/documents_public/view/id/82" },

  { cat:"standards", org:"ISO", free:false,
    title:"ISO 24113 — Space Debris Mitigation",
    desc:"The formal ISO standard codifying space debris mitigation requirements for space systems. Provides normative requirements for mission design, operations, and end-of-life disposal. Referenced in national space law frameworks across multiple jurisdictions.",
    url:"https://www.iso.org/standard/72383.html" },

  { cat:"standards", org:"CCSDS", free:true,
    title:"Conjunction Data Message (CDM) Standard",
    desc:"The CCSDS 508.0-B standard defines the format of Conjunction Data Messages used by 18 SDS, NASA CARA, and commercial screening services. Understanding the CDM format is essential for automated conjunction assessment pipelines.",
    url:"https://public.ccsds.org/Pubs/508x0b1e2c2.pdf" },

  { cat:"standards", org:"FCC / ITU", free:true,
    title:"FCC Orbital Debris Mitigation Rules",
    desc:"The US Federal Communications Commission's orbital debris mitigation rules (47 CFR Part 25), updated in 2022 to require a 5-year post-mission disposal rule for satellites below 2000 km. Required reading for any US-licensed satellite operator.",
    url:"https://www.fcc.gov/document/fcc-updates-orbital-debris-mitigation-rules" },

  { cat:"standards", org:"NASA", free:true,
    title:"NASA-STD-8719.14B — Process for Limiting Orbital Debris",
    desc:"NASA's internal standard for limiting orbital debris generation, used as a design requirement for all NASA missions. More stringent than IADC guidelines in several areas. Provides detailed requirements for mission planning, debris assessment, and reporting.",
    url:"https://standards.nasa.gov/standard/nasa/nasa-std-871914" },

  /* ── COURSES & LEARNING ─────────────────────────────────── */
  { cat:"courses", org:"MIT OpenCourseWare", free:true,
    title:"16.346 — Astrodynamics",
    desc:"MIT's graduate astrodynamics course covering orbital mechanics, Lambert's problem, orbit determination, and spacecraft navigation. Lecture notes and problem sets freely available. One of the most rigorous freely accessible astrodynamics curricula online.",
    url:"https://ocw.mit.edu/courses/16-346-astrodynamics-fall-2008/" },

  { cat:"courses", org:"ESA Academy", free:true,
    title:"ESA Space Debris Training Course",
    desc:"ESA's dedicated training program on space debris — environment models, mitigation measures, debris removal technologies, and regulatory landscape. Offered periodically as in-person and online formats. Directly relevant to VectraSpace's domain.",
    url:"https://www.esa.int/Enabling_Support/Space_Engineering_Technology/Space_Debris_Training_Course" },

  { cat:"courses", org:"Coursera / University of Colorado", free:false,
    title:"Spacecraft Dynamics and Control Specialization",
    desc:"Four-course specialization covering attitude dynamics, kinematics, control systems, and mission simulation. Highly rated, mathematically rigorous, and directly applicable to satellite conjunction and maneuver planning contexts.",
    url:"https://www.coursera.org/specializations/spacecraft-dynamics-control" },

  { cat:"courses", org:"AGI / Ansys", free:true,
    title:"STK Fundamentals Online Training",
    desc:"Free self-paced online training for Systems Tool Kit (STK), the industry-standard orbital analysis platform. Covers satellite access analysis, coverage, conjunction analysis, and sensor modeling. Certification available upon completion.",
    url:"https://training.ansys.com/odl/stk_fundamentals/" },

  { cat:"courses", org:"Wertz & Larson (Textbook)", free:false,
    title:"Space Mission Engineering: The New SMAD",
    desc:"The reference textbook for space systems engineering — covers orbit design, propulsion, power, communications, and mission operations. Universally recommended as the first technical book for anyone entering the space industry.",
    url:"https://www.smad.com" },

];

var activeFilter = "all";
var searchQuery  = "";

function tagClass(cat){ return "tag tag-" + cat; }
function tagLabel(cat){
  return {data:"Data & Catalogs", tools:"Tools", papers:"Papers",
          standards:"Standards", courses:"Courses"}[cat] || cat;
}

function arrowSVG(){
  return '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>';
}

function render(){
  var q = searchQuery.toLowerCase();
  var filtered = RESOURCES.filter(function(r){
    if(activeFilter !== "all" && r.cat !== activeFilter) return false;
    if(!q) return true;
    return r.title.toLowerCase().indexOf(q) !== -1
        || r.desc.toLowerCase().indexOf(q) !== -1
        || r.org.toLowerCase().indexOf(q) !== -1;
  });

  var grid = document.getElementById("rs-grid");
  var meta = document.getElementById("rs-meta");
  meta.textContent = filtered.length + " resource" + (filtered.length !== 1 ? "s" : "");

  if(filtered.length === 0){
    grid.innerHTML = '<div class="empty">No resources match your search.</div>';
    return;
  }

  /* Group by category in a defined order */
  var ORDER = ["data","tools","papers","standards","courses"];
  var groups = {};
  ORDER.forEach(function(c){ groups[c] = []; });
  filtered.forEach(function(r){ if(groups[r.cat]) groups[r.cat].push(r); });

  var html = "";
  ORDER.forEach(function(cat){
    var items = groups[cat];
    if(!items.length) return;
    /* Section divider if showing all */
    if(activeFilter === "all"){
      html += '<div class="section-label">'
            + '<span class="section-label-text" style="color:var(--' +
              {data:"accent",tools:"green",papers:"amber",standards:"red",courses:"purple"}[cat]
            + ')">' + tagLabel(cat) + '</span>'
            + '<span class="section-label-line"></span>'
            + '</div>';
    }
    items.forEach(function(r){
      html += '<a class="card" href="' + r.url + '" target="_blank" rel="noopener">'
            + '<div class="card-top">'
            + '<div class="card-meta">'
            + '<span class="' + tagClass(r.cat) + '">' + tagLabel(r.cat) + '</span>'
            + '<span class="card-org">' + r.org + '</span>'
            + '</div>'
            + '<div class="card-title">' + r.title + '</div>'
            + '<div class="card-desc">' + r.desc + '</div>'
            + '</div>'
            + '<div class="card-foot">'
            + '<span class="card-link-label">Open resource ' + arrowSVG() + '</span>'
            + (r.free ? '<span class="card-free">Free</span>' : '<span style="font-family:var(--mono);font-size:7px;letter-spacing:1px;color:var(--faint);">Paid / Login</span>')
            + '</div>'
            + '</a>';
    });
  });

  grid.innerHTML = html;
}

/* Search */
document.getElementById("rs-search").addEventListener("input", function(){
  searchQuery = this.value.trim();
  render();
});

/* Filters */
document.querySelectorAll(".fbtn[data-cat]").forEach(function(btn){
  btn.addEventListener("click", function(){
    document.querySelectorAll(".fbtn[data-cat]").forEach(function(b){ b.classList.remove("on"); });
    btn.classList.add("on");
    activeFilter = btn.dataset.cat;
    render();
  });
});

render();
</script>
</body>
</html>'''

RESEARCH_HTML = '''<!DOCTYPE html>
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
  const pcLabels = ['<1e-8','1e-8 to
1e-6','1e-6 to
1e-4','1e-4 to
1e-2','>1e-2'];
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
  const hdr = 'index,object1,object2,tca_utc,miss_dist_km,pc_estimate,rel_velocity_kms
';
  const rows = conjData.map((c,i) => [
    i+1,
    c.sat1 ?? c.name1 ?? '', c.sat2 ?? c.name2 ?? '',
    c.tca_utc ?? c.time ?? '',
    (c.min_dist_km ?? 0).toFixed(4),
    (c.pc_estimate ?? c.pc ?? 0).toExponential(4),
    (c.v_rel ?? 0).toFixed(4)
  ].join(',')).join('
');
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
</html>'''

EDU_ORBITAL_HTML = '''<!DOCTYPE html>
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
  <div style="display:flex;gap:8px;"><a href="/#deep-dives" class="nav-back">← All Chapters</a><a href="/glossary" class="nav-back">Resources</a><a href="/calculator" class="nav-back">Calculator</a></div>
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

</script>
</body>
</html>

'''

EDU_COLLISION_HTML = '''<!DOCTYPE html>
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
  <div style="display:flex;gap:8px;"><a href="/#deep-dives" class="nav-back">← All Chapters</a><a href="/glossary" class="nav-back">Resources</a><a href="/calculator" class="nav-back">Calculator</a></div>
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

'''

EDU_PERTURBATIONS_HTML = '''<!DOCTYPE html>
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
  <div style="display:flex;gap:8px;"><a href="/#learn" class="nav-back">← All Chapters</a><a href="/glossary" class="nav-back">Resources</a><a href="/calculator" class="nav-back">Calculator</a></div>
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

'''

EDU_DEBRIS_HTML = '''<!DOCTYPE html>
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
  
.chapter-nav { grid-template-columns:1fr; }
}
</style>
</head>
<body>

<div id="progress-bar"></div>

<nav>
  <a href="/" class="nav-brand"><span class="nav-brand-name">Vectra<em>Space</em></span></a>
  <div style="display:flex;gap:8px;"><a href="/#learn" class="nav-back">← All Chapters</a><a href="/glossary" class="nav-back">Resources</a><a href="/calculator" class="nav-back">Calculator</a></div>
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

'''

ADMIN_HTML = '''<!DOCTYPE html>
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
      <!-- renderAnalytics() populates this -->
      <div style="padding:40px;text-align:center;font-family:Share Tech Mono,monospace;font-size:9px;color:#3a5a75;letter-spacing:2px;">LOADING ANALYTICS...</div>
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

    // Render analytics section
    renderAnalytics(d.umami_url || '', d.umami_id || '');

  } catch(e) {
    console.error('Admin load failed:', e);
  }
}

function renderAnalytics(umami_url, umami_id) {
  const section = document.getElementById('analytics-section');
  if (!section) return;
  if (umami_id) {
    // Extract website slug for the share URL
    const shareBase = 'https://cloud.umami.is/share/' + umami_id + '/vectraspace';
    section.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px;">
        <div style="font-family:'Share Tech Mono',monospace;font-size:9px;letter-spacing:2px;color:var(--accent3);text-transform:uppercase;">
          ● Umami Analytics · Live
        </div>
        <a href="${shareBase}" target="_blank"
           style="font-family:'Share Tech Mono',monospace;font-size:8px;letter-spacing:1px;
                  color:var(--accent);text-decoration:none;padding:4px 10px;
                  border:1px solid var(--accent);border-radius:3px;text-transform:uppercase;
                  transition:all 0.2s;"
           onmouseover="this.style.background='rgba(0,212,255,0.1)'"
           onmouseout="this.style.background='transparent'">
          Open Full Dashboard →
        </a>
      </div>
      <iframe
        src="${shareBase}"
        style="width:100%;height:600px;border:none;border-radius:6px;background:var(--bg2);"
        loading="lazy"
        title="Umami Analytics">
      </iframe>`;
  } else {
    section.innerHTML = `
      <div class="analytics-placeholder">
        <div class="icon">📊</div>
        <p>Umami Analytics Not Configured</p>
        <p style="font-size:10px;color:var(--text);opacity:0.6;margin:8px 0 16px;font-family:sans-serif;">
          Add free website analytics in 2 minutes. Tracks visits, pageviews, countries, devices — no cookies, no GDPR issues.
        </p>
        <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">
          <a href="https://cloud.umami.is/signup" target="_blank"
             style="font-family:'Share Tech Mono',monospace;font-size:10px;letter-spacing:2px;
                    padding:10px 20px;border:1px solid var(--accent);border-radius:3px;
                    color:var(--accent);text-decoration:none;text-transform:uppercase;">
            → Create Free Umami Account
          </a>
        </div>
        <div class="umami-script-box" style="margin-top:16px;">
          Set UMAMI_WEBSITE_ID env var on Render, then redeploy.
        </div>
      </div>`;
  }
}

function copyUmamiInstructions() {
  const text = 'UMAMI_SCRIPT_URL=https://cloud.umami.is/script.js
UMAMI_WEBSITE_ID=your-website-id-here';
  navigator.clipboard.writeText(text).catch(() => {});
}

loadAdmin();
</script>
</body>
</html>'''

_LANDING_BASE = '''<!DOCTYPE html>
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
  height: 60px; padding: 0 40px;
  display: flex; align-items: center; gap: 0;
  transition: background 0.4s, border-color 0.4s;
  border-bottom: 1px solid transparent;
}
.nav-right {
  display: flex; align-items: center; gap: 8px; margin-left: auto;
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
  display: flex; gap: 20px; list-style: none; align-items: center;
  flex: 1; justify-content: center;
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
.nav-signin {
  font-family: var(--mono); font-size: 10px; letter-spacing: 1.5px;
  text-transform: uppercase; text-decoration: none;
  padding: 8px 16px; border: 1px solid var(--border);
  border-radius: 3px; color: var(--muted);
  transition: all 0.2s; white-space: nowrap;
}
.nav-signin:hover { color: var(--text); border-color: var(--border2); }

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
  margin-top: 48px; position: relative; z-index: 1; overflow: hidden;
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

/* ── CTA SECTION ── */
#cta { padding: 100px 0; }
.cta-box {
  position: relative; overflow: hidden;
  background: linear-gradient(135deg, rgba(74,158,255,0.08) 0%, rgba(74,158,255,0.02) 100%);
  border: 1px solid rgba(74,158,255,0.2); border-radius: 20px;
  padding: 80px 64px; text-align: center;
}
.cta-glow {
  position: absolute; top: -80px; left: 50%; transform: translateX(-50%);
  width: 500px; height: 300px; border-radius: 50%;
  background: radial-gradient(ellipse, rgba(74,158,255,0.12) 0%, transparent 70%);
  pointer-events: none;
}
.cta-eyebrow {
  font-family: var(--mono); font-size: 10px; letter-spacing: 3px;
  text-transform: uppercase; color: var(--accent); margin-bottom: 20px;
}
.cta-title {
  font-family: var(--serif); font-size: clamp(30px, 4vw, 52px);
  font-weight: 400; color: #fff; line-height: 1.15; letter-spacing: -0.5px;
  margin-bottom: 20px;
}
.cta-title em { font-style: italic; color: var(--accent2); }
.cta-body {
  font-size: 16px; color: var(--muted); line-height: 1.8;
  max-width: 580px; margin: 0 auto 40px;
}
.cta-buttons {
  display: flex; gap: 14px; justify-content: center;
  flex-wrap: wrap; align-items: center;
}

/* ── FOOTER ── */
footer {
  padding: 60px 48px 40px;
  border-top: 1px solid var(--border);
  position: relative; z-index: 1;
}
.footer-top {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 40px; margin-bottom: 40px; flex-wrap: wrap;
}
.footer-brand {
  font-family: var(--serif); font-size: 18px; font-style: italic;
  color: #fff; letter-spacing: -0.2px; text-decoration: none;
}
.footer-brand em { color: var(--accent); font-style: normal; }
.footer-links {
  display: flex; gap: 6px 24px; flex-wrap: wrap; list-style: none;
  max-width: 420px;
}
.footer-links a {
  font-family: var(--mono); font-size: 10px; letter-spacing: 1px;
  text-transform: uppercase; color: var(--muted); text-decoration: none;
  transition: color 0.2s;
}
.footer-links a:hover { color: var(--text); }
.footer-contact {
  font-family: var(--mono); font-size: 10px; letter-spacing: 0.5px;
  color: var(--muted); line-height: 1.8;
}
.footer-contact a { color: var(--accent); text-decoration: none; }
.footer-contact a:hover { text-decoration: underline; }
.footer-copy {
  font-family: var(--mono); font-size: 10px; letter-spacing: 1px;
  color: var(--faint); border-top: 1px solid var(--border); padding-top: 20px;
}

@media (max-width: 600px) {
  .satod-stats { grid-template-columns: 1fr; }
  .satod-stat { border-right: none; border-bottom: 1px solid var(--border); }
  .satod-stat:last-child { border-bottom: none; }
  .satod-header { padding: 24px 20px 16px; }
  .satod-footer, .satod-operator { padding-left: 20px; padding-right: 20px; }
}

/* ═══════════════════════════════════════════════════
   RESPONSIVE — TABLET  (≤960px)
   ═══════════════════════════════════════════════════ */
@media (max-width: 960px) {
  nav { padding: 0 16px; }
  .nav-links { display: none; }
  .nav-hamburger { display: flex; }
  .nav-cta { display: none; }
  .nav-signin { display: none; }
  .section-wrap { padding: 0 20px; }
  .mission-grid, .kessler-inner { grid-template-columns: 1fr; gap: 40px; }
  .ssa-pillars { grid-template-columns: 1fr 1fr; }
  .sim-grid { grid-template-columns: 1fr 1fr; }
  .chapters-grid { grid-template-columns: 1fr; }
  .data-metrics { grid-template-columns: repeat(2, 1fr); }
  .mission-visual { height: 260px; }
  footer { flex-direction: column; gap: 20px; text-align: center; }
  .kessler-data { position: static; }
  #contact .container { padding: 0 20px !important; }
  #contact .reveal > div { padding: 40px 32px !important; }
  /* Why cards — 2 col on tablet */
  #why .why-grid { grid-template-columns: repeat(2, 1fr) !important; }
  /* Howto strip — stack on tablet */
  #howto .howto-inner { flex-direction: column !important; gap: 0 !important; }
  #howto .howto-step { border-right: none !important; border-bottom: 1px solid var(--border) !important; padding: 20px 0 !important; }
  #howto .howto-step:last-child { border-bottom: none !important; }
}

/* ═══════════════════════════════════════════════════
   RESPONSIVE — MOBILE  (≤600px)
   ═══════════════════════════════════════════════════ */
@media (max-width: 600px) {
  /* ── Nav ── */
  nav { padding: 0 16px; height: 54px; }

  /* ── Hero ── */
  #hero { padding: 88px 16px 60px; min-height: auto; }
  .hero-orbit-system { display: none; }
  .hero-title { font-size: clamp(38px, 11vw, 56px); letter-spacing: -1px; }
  .hero-title-line2 { font-size: clamp(22px, 7vw, 32px); }
  .hero-desc { font-size: 15px; margin: 20px auto 32px; }
  /* Social proof strip — 2x2 grid on mobile */
  .hero-proof { flex-wrap: wrap !important; gap: 16px !important; }
  .hero-proof-divider { display: none !important; }
  .hero-proof-item { min-width: calc(50% - 8px) !important; }
  .hero-actions { flex-direction: column; gap: 10px; width: 100%; }
  .btn-primary-hero, .btn-secondary-hero { width: 100%; text-align: center; }
  .hero-scroll { display: none; }

  /* ── Ticker ── */
  #tle-ticker { display: none; }
  .ticker-bar { margin-top: 16px; }

  /* ── Section wrap ── */
  .section-wrap { padding: 0 16px; }

  /* ── Why cards — single column ── */
  #why { padding: 60px 0 40px !important; }
  #why .why-grid { grid-template-columns: 1fr !important; gap: 14px !important; }

  /* ── Howto strip ── */
  #howto { padding: 12px 0 60px !important; }
  #howto .howto-inner {
    flex-direction: column !important;
    padding: 28px 20px !important;
    gap: 0 !important;
  }
  #howto .howto-step {
    border-right: none !important;
    border-bottom: 1px solid var(--border) !important;
    padding: 20px 0 !important;
  }
  #howto .howto-step:last-child { border-bottom: none !important; padding-bottom: 0 !important; }

  /* ── Chapters ── */
  .chapters-grid { grid-template-columns: 1fr; gap: 14px; }
  .chapter-card-body { padding: 20px 16px; }
  .chapter-footer { padding: 12px 16px; }
  .chapter-topics { gap: 5px; }
  .topic-pill { font-size: 8px; padding: 3px 8px; }

  /* ── Simulation grid ── */
  .sim-grid { grid-template-columns: 1fr; }
  .tools-strip { flex-direction: column; }
  .tool-card { min-width: 0; }

  /* ── Data metrics ── */
  .data-metrics { grid-template-columns: 1fr 1fr; }

  /* ── CTA ── */
  .cta-box { padding: 40px 20px; }
  .cta-title { font-size: clamp(22px, 6vw, 34px); }
  .cta-body { font-size: 14px; }
  .cta-buttons { flex-direction: column; gap: 10px; }
  .cta-buttons a { width: 100%; text-align: center; }

  /* ── SSA pillars ── */
  .ssa-pillars { grid-template-columns: 1fr; }

  /* ── Contact ── */
  #contact { padding: 60px 0 !important; }
  /* Team cards: horizontal scroll on mobile */
  .team-grid {
    grid-template-columns: 1fr 1fr !important;
    gap: 12px !important;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    padding-bottom: 8px;
  }
  .team-grid > div { min-width: 240px; }
  #contact .container { padding: 0 16px !important; }
  #contact .reveal > div { padding: 28px 16px !important; }
  .contact-bio-row { flex-direction: column !important; gap: 20px !important; align-items: center !important; text-align: center !important; }
  #contact .reveal [style*="font-size:26px"] { font-size: 18px !important; }
  #contact .reveal [style*="font-size:clamp(22px"] { font-size: 18px !important; }
  #contact .reveal [style*="max-width:520px"] { max-width: 100% !important; }
  #contact a[href^="mailto"] { width: 100%; justify-content: center !important; font-size: 9px !important; padding: 12px 12px !important; box-sizing: border-box; }
  /* Team cards — stack on mobile */
  #contact .reveal > div > div[style*="grid-template-columns:1fr 1fr"] {
    grid-template-columns: 1fr !important;
  }

  /* ── Footer ── */
  footer { padding: 28px 16px; }
  .footer-top { flex-direction: column; align-items: flex-start; gap: 24px; margin-bottom: 24px; }
  .footer-top { flex-direction: column; align-items: center; text-align: center; }
  .footer-links { flex-wrap: wrap; justify-content: center; gap: 6px 14px; }
  .footer-contact { text-align: center; }
  .footer-copy { font-size: 10px; text-align: center; }
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
        <li><a href="/glossary">Resources</a></li>
    <li><a href="/calculator">Calculator</a></li>
    <li><a href="#contact">Contact</a></li>
    <li><a href="/api/tools/trajectory">Trajectory ↗</a></li>
  </ul>
  <div class="nav-right">
        <a href="/dashboard" class="nav-cta">Dashboard →</a>
  </div>
  <button class="nav-hamburger" id="nav-hamburger" onclick="toggleMobileNav()" aria-label="Menu">
    <span></span><span></span><span></span>
  </button>
</nav>
<div id="mobile-nav">
  <a href="#mission">Mission <span>→</span></a>
  <a href="#learn">Chapters <span>→</span></a>
  <a href="/scenarios">Scenarios <span>→</span></a>
  <a href="/glossary">Resources <span>→</span></a>
  <a href="/calculator">Calculator <span>→</span></a>
  <a href="#contact">Contact <span>→</span></a>
  <a href="/api/tools/trajectory">Trajectory Simulator <span>↗</span></a>
    <a href="/dashboard" class="cta-link">Open Dashboard →</a>
</div>

<!-- HERO -->
<section id="hero">
  <div class="hero-orbit-system">
    <div class="orbit-ring orbit-ring-1"><div class="orbit-sat" style="color:#4a9eff;"></div></div>
    <div class="orbit-ring orbit-ring-2"><div class="orbit-sat orbit-sat-2" style="top:-4px;left:calc(50% - 4px);"></div></div>
    <div class="orbit-ring orbit-ring-3"><div class="orbit-sat orbit-sat-3" style="top:calc(50% - 4px);left:-4px;"></div></div>
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
    <strong>27,000+ tracked objects.</strong> A debris field that could trigger an irreversible
    cascade. VectraSpace gives you the physics, the data, and the tools to understand it —
    from Kepler to Kessler, in four chapters.
  </p>

  <!-- Social proof strip -->
  <div class="hero-proof" style="display:flex;gap:32px;margin:28px 0 36px;flex-wrap:wrap;">
    <div class="hero-proof-item" style="display:flex;flex-direction:column;gap:2px;">
      <span style="font-family:var(--serif);font-size:28px;font-style:italic;color:#fff;letter-spacing:-1px;">4</span>
      <span style="font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);">Technical Chapters</span>
    </div>
    <div class="hero-proof-divider" style="width:1px;background:var(--border);"></div>
    <div class="hero-proof-item" style="display:flex;flex-direction:column;gap:2px;">
      <span style="font-family:var(--serif);font-size:28px;font-style:italic;color:#fff;letter-spacing:-1px;">27k+</span>
      <span style="font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);">Tracked Objects</span>
    </div>
    <div class="hero-proof-divider" style="width:1px;background:var(--border);"></div>
    <div class="hero-proof-item" style="display:flex;flex-direction:column;gap:2px;">
      <span style="font-family:var(--serif);font-size:28px;font-style:italic;color:var(--green);letter-spacing:-1px;">Live</span>
      <span style="font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);">SGP4 Scanner</span>
    </div>
    <div class="hero-proof-divider" style="width:1px;background:var(--border);"></div>
    <div class="hero-proof-item" style="display:flex;flex-direction:column;gap:2px;">
      <span style="font-family:var(--serif);font-size:28px;font-style:italic;color:var(--amber);letter-spacing:-1px;">Free</span>
      <span style="font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);">No account needed</span>
    </div>
  </div>

  <div class="hero-actions">
    <a href="#learn" class="btn-primary-hero">Start Learning &darr;</a>
    <a href="/dashboard" class="btn-secondary-hero">Live Scanner &rarr;</a>
  </div>

  <a href="#why" class="hero-scroll">
    <div class="scroll-line"></div>
    Scroll
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

<!-- MISSION / WHY IT MATTERS -->
<div id="mission" style="position:relative;top:-60px;pointer-events:none;"></div>
<section id="why"  style="padding:80px 0 60px;">
  <div class="section-wrap">
    <!-- ── WHY WE EXIST ── -->
    <div class="reveal" style="text-align:center;margin-bottom:56px;">
      <div class="section-label" style="justify-content:center;">// Our Mission</div>
      <h2 class="section-title" style="margin-bottom:20px;">Built because the physics<br><em>deserves to be understood</em></h2>
      <p style="font-size:15px;color:var(--muted);max-width:600px;margin:0 auto 40px;line-height:1.85;">
        VectraSpace exists because orbital safety is one of the most consequential engineering
        problems of our generation — and almost no one outside the industry understands it.
        We built a platform where anyone can engage with the real mathematics: not simplified
        metaphors, but the actual SGP4 propagation, Foster-Alfano probability of collision,
        and Kessler cascade physics that real SSA operators use every day.
      </p>
      <div style="display:flex;gap:32px;justify-content:center;flex-wrap:wrap;margin-bottom:48px;">
        <div style="display:flex;flex-direction:column;align-items:center;gap:6px;">
          <span style="font-family:var(--serif);font-size:36px;font-style:italic;color:var(--accent);">Free</span>
          <span style="font-family:var(--mono);font-size:8px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);">Always &amp; Forever</span>
        </div>
        <div style="width:1px;background:var(--border);"></div>
        <div style="display:flex;flex-direction:column;align-items:center;gap:6px;">
          <span style="font-family:var(--serif);font-size:36px;font-style:italic;color:var(--green);">Real</span>
          <span style="font-family:var(--mono);font-size:8px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);">Physics &amp; Data</span>
        </div>
        <div style="width:1px;background:var(--border);"></div>
        <div style="display:flex;flex-direction:column;align-items:center;gap:6px;">
          <span style="font-family:var(--serif);font-size:36px;font-style:italic;color:var(--amber);">Open</span>
          <span style="font-family:var(--mono);font-size:8px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);">No Account Needed</span>
        </div>
      </div>
      <div class="section-label" style="justify-content:center;margin-bottom:8px;">// Why orbital safety matters</div>
      <h2 class="section-title">The orbital environment<br>is <em>running out of time</em></h2>
    </div>
    <div class="why-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px;">

      <div class="reveal" style="background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:32px 28px;position:relative;overflow:hidden;">
        <div style="position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent),transparent);"></div>
        <div style="font-family:var(--serif);font-size:48px;font-style:italic;color:var(--accent);letter-spacing:-2px;margin-bottom:10px;">27,000+</div>
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:12px;">Tracked Objects</div>
        <p style="font-size:13px;color:var(--muted);line-height:1.75;">The US Space Surveillance Network tracks 27,000+ objects larger than 10cm. Hundreds of thousands of smaller fragments — invisible to radar — travel at 7.8 km/s through crowded orbital shells.</p>
      </div>

      <div class="reveal reveal-delay-1" style="background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:32px 28px;position:relative;overflow:hidden;">
        <div style="position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--amber),transparent);"></div>
        <div style="font-family:var(--serif);font-size:48px;font-style:italic;color:var(--amber);letter-spacing:-2px;margin-bottom:10px;">10×</div>
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:12px;">Collision Energy Multiplier</div>
        <p style="font-size:13px;color:var(--muted);line-height:1.75;">Orbital velocities of ~7.8 km/s mean a 10 cm debris fragment carries the kinetic energy of a hand grenade. Even a 1 cm fragment can destroy a satellite — and generate thousands more pieces.</p>
      </div>

      <div class="reveal reveal-delay-2" style="background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:32px 28px;position:relative;overflow:hidden;">
        <div style="position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--red),transparent);"></div>
        <div style="font-family:var(--serif);font-size:48px;font-style:italic;color:var(--red);letter-spacing:-2px;margin-bottom:10px;">Cascade</div>
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:12px;">Kessler Syndrome Risk</div>
        <p style="font-size:13px;color:var(--muted);line-height:1.75;">Above a critical density threshold, collisions generate debris faster than drag removes it. The cascade becomes self-sustaining — rendering entire orbital shells permanently unusable.</p>
      </div>

    </div>
  </div>
</section>

<!-- 3-step onboarding strip -->
<section id="howto" style="padding:20px 0 80px;">
  <div class="section-wrap">
    <div class="reveal howto-inner" style="background:var(--ink2);border:1px solid var(--border);border-radius:16px;padding:40px 48px;display:flex;align-items:center;gap:0;flex-wrap:wrap;">
      <div class="howto-step" style="flex:1;min-width:160px;padding:0 24px 0 0;border-right:1px solid var(--border);">
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--accent);margin-bottom:10px;">Step 01</div>
        <div style="font-family:var(--serif);font-size:20px;font-style:italic;color:#fff;margin-bottom:6px;">Read the Chapters</div>
        <div style="font-size:12px;color:var(--muted);line-height:1.7;">Four technical deep dives — orbital mechanics, collision prediction, perturbations, debris modeling.</div>
      </div>
      <div class="howto-step" style="flex:1;min-width:160px;padding:0 24px;border-right:1px solid var(--border);">
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--green);margin-bottom:10px;">Step 02</div>
        <div style="font-family:var(--serif);font-size:20px;font-style:italic;color:#fff;margin-bottom:6px;">Run a Simulation</div>
        <div style="font-size:12px;color:var(--muted);line-height:1.7;">Explore the Kessler cascade, Iridium–Cosmos collision, and ASAT events in interactive 3D.</div>
      </div>
      <div class="howto-step" style="flex:1;min-width:160px;padding:0 0 0 24px;">
        <div style="font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--amber);margin-bottom:10px;">Step 03</div>
        <div style="font-family:var(--serif);font-size:20px;font-style:italic;color:#fff;margin-bottom:6px;">Scan Live Orbits</div>
        <div style="font-size:12px;color:var(--muted);line-height:1.7;">Run the live SGP4 conjunction scanner on real TLE data and generate a downloadable CDM report.</div>
      </div>
    </div>
  </div>
</section>
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
          <div class="tool-card-title">Resources</div>
          <div class="tool-card-desc">50+ terms · searchable · deep-link ready</div>
        </div>
      </a>
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
  <!-- TLE Ticker moved below stats card -->
</section>

<div style="padding: 0 48px 48px; position:relative; z-index:1; max-width:1280px; margin:0 auto;">
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

<section id="contact" style="padding:100px 0; position:relative; z-index:1;">
  <div class="container" style="max-width:1080px; margin:0 auto; padding:0 48px;">

    <!-- section eyebrow -->
    <div style="font-family:var(--mono); font-size:10px; letter-spacing:3px; color:var(--green); text-transform:uppercase; margin-bottom:14px; display:flex; align-items:center; gap:10px;">
      <span style="display:inline-block; width:14px; height:1px; background:var(--green);"></span>The Team
    </div>
    <div style="font-family:var(--serif); font-size:clamp(28px,3.5vw,44px); color:#fff; font-weight:400; line-height:1.15; letter-spacing:-0.4px; margin-bottom:52px;">
      The people behind<br><em style="font-style:italic; color:var(--accent2);">VectraSpace</em>
    </div>

    <!-- two-card grid -->
    <div class="team-grid" style="display:grid; grid-template-columns:1fr 1fr; gap:24px; margin-bottom:60px;">

      <!-- ── Truman card ── -->
      <div class="reveal" style="background:var(--panel); border:1px solid var(--border); border-radius:16px; overflow:hidden; position:relative;">
        <div style="height:2px; background:linear-gradient(90deg, var(--accent) 0%, var(--green) 60%, transparent 100%);"></div>
        <div style="padding:40px 44px;">
          <div style="font-family:var(--mono); font-size:9px; letter-spacing:3px; color:var(--green); text-transform:uppercase; margin-bottom:24px;">Founder &amp; Builder</div>
          <div style="display:flex; gap:20px; align-items:flex-start; margin-bottom:24px;">
            <div style="flex-shrink:0; width:60px; height:60px; border-radius:50%; background:linear-gradient(135deg, var(--accent) 0%, var(--green) 100%); display:flex; align-items:center; justify-content:center; font-family:var(--serif); font-size:24px; color:#fff; box-shadow:0 0 0 3px var(--border), 0 0 24px rgba(74,158,255,0.2);">T</div>
            <div>
              <div style="font-family:var(--serif); font-size:22px; color:#fff; font-weight:400; margin-bottom:4px; letter-spacing:-0.2px;">Truman Heaston</div>
              <div style="font-family:var(--mono); font-size:9px; letter-spacing:2px; color:var(--accent); text-transform:uppercase;">Builder · Student · Orbital Mechanics Nerd</div>
            </div>
          </div>
          <p style="font-size:14px; color:var(--muted); line-height:1.8; margin:0 0 28px;">
            Passionate about space, orbital mechanics, and the belief that great education can change the world. VectraSpace started as a personal obsession — I wanted to understand the real math behind satellite conjunction events, so I built the platform I wished existed.
          </p>
          <a href="mailto:trumanheaston@gmail.com"
             style="display:inline-flex; align-items:center; gap:8px; padding:11px 22px;
                    background:var(--accent); color:#fff; border-radius:6px;
                    font-family:var(--mono); font-size:10px; letter-spacing:2px; text-transform:uppercase;
                    text-decoration:none; transition:all 0.2s; font-weight:500;
                    box-shadow:0 4px 20px rgba(74,158,255,0.25);"
             onmouseover="this.style.background='#6ab4ff'; this.style.transform='translateY(-2px)';"
             onmouseout="this.style.background='var(--accent)'; this.style.transform='';">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
            trumanheaston@gmail.com
          </a>
          <div style="position:absolute; bottom:-60px; right:-60px; width:280px; height:280px; background:radial-gradient(ellipse, rgba(74,158,255,0.05) 0%, transparent 70%); pointer-events:none;"></div>
        </div>
      </div>

      <!-- ── Will card ── -->
      <div class="reveal" style="background:var(--panel); border:1px solid var(--border); border-radius:16px; overflow:hidden; position:relative;">
        <div style="height:2px; background:linear-gradient(90deg, var(--amber) 0%, var(--green) 60%, transparent 100%);"></div>
        <div style="padding:40px 44px;">
          <div style="font-family:var(--mono); font-size:9px; letter-spacing:3px; color:var(--amber); text-transform:uppercase; margin-bottom:24px;">Marketing &amp; Outreach</div>
          <div style="display:flex; gap:20px; align-items:flex-start; margin-bottom:24px;">
            <div style="flex-shrink:0; width:60px; height:60px; border-radius:50%; background:linear-gradient(135deg, var(--amber) 0%, var(--green) 100%); display:flex; align-items:center; justify-content:center; font-family:var(--serif); font-size:24px; color:#fff; box-shadow:0 0 0 3px var(--border), 0 0 24px rgba(245,158,11,0.2);">W</div>
            <div>
              <div style="font-family:var(--serif); font-size:22px; color:#fff; font-weight:400; margin-bottom:4px; letter-spacing:-0.2px;">Will Lovelace</div>
              <div style="font-family:var(--mono); font-size:9px; letter-spacing:2px; color:var(--amber); text-transform:uppercase;">Marketing · Outreach · Growth</div>
            </div>
          </div>
          <p style="font-size:14px; color:var(--muted); line-height:1.8; margin:0 0 28px;">
            Leading marketing and outreach for VectraSpace — connecting the platform with researchers, educators, and operators across the space industry. If you're interested in partnerships, press, or collaboration opportunities, Will is your contact.
          </p>
          <a href="mailto:Will.s.lovelace@gmail.com"
             style="display:inline-flex; align-items:center; gap:8px; padding:11px 22px;
                    background:var(--amber); color:#000; border-radius:6px;
                    font-family:var(--mono); font-size:10px; letter-spacing:2px; text-transform:uppercase;
                    text-decoration:none; transition:all 0.2s; font-weight:500;
                    box-shadow:0 4px 20px rgba(245,158,11,0.25);"
             onmouseover="this.style.background='#fbbf24'; this.style.transform='translateY(-2px)';"
             onmouseout="this.style.background='var(--amber)'; this.style.transform='';">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
            Will.s.lovelace@gmail.com
          </a>
          <div style="position:absolute; bottom:-60px; right:-60px; width:280px; height:280px; background:radial-gradient(ellipse, rgba(245,158,11,0.05) 0%, transparent 70%); pointer-events:none;"></div>
        </div>
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

'''

# ── Patch: GitHub link + Grant Gill card ─────────────────────────────────────
_GITHUB_FOOTER_LINK = (
    '<li><a href="https://github.com/trumanheaston-lab/VectraSpace" '
    'target="_blank" rel="noopener">GitHub</a></li>'
)

_GRANT_CARD = """
      <!-- ── Grant card ── -->
      <div class="reveal" style="background:var(--panel); border:1px solid var(--border); border-radius:16px; overflow:hidden; position:relative;">
        <div style="height:2px; background:linear-gradient(90deg, var(--green) 0%, var(--accent) 60%, transparent 100%);"></div>
        <div style="padding:40px 44px;">
          <div style="font-family:var(--mono); font-size:9px; letter-spacing:3px; color:var(--green); text-transform:uppercase; margin-bottom:24px;">Hardware Lead</div>
          <div style="display:flex; gap:20px; align-items:flex-start; margin-bottom:24px;">
            <div style="flex-shrink:0; width:60px; height:60px; border-radius:50%; background:linear-gradient(135deg, var(--green) 0%, var(--accent) 100%); display:flex; align-items:center; justify-content:center; font-family:var(--serif); font-size:24px; color:#fff; box-shadow:0 0 0 3px var(--border), 0 0 24px rgba(52,211,153,0.2);">G</div>
            <div>
              <div style="font-family:var(--serif); font-size:22px; color:#fff; font-weight:400; margin-bottom:4px; letter-spacing:-0.2px;">Grant Gill</div>
              <div style="font-family:var(--mono); font-size:9px; letter-spacing:2px; color:var(--green); text-transform:uppercase;">Hardware Lead · 3D File Store Contributor</div>
            </div>
          </div>
          <p style="font-size:14px; color:var(--muted); line-height:1.8; margin:0 0 28px;">
            Hardware lead and 3D file store contributor for VectraSpace.
          </p>
          <a href="mailto:jellycatgrant@gmail.com"
             style="display:inline-flex; align-items:center; gap:8px; padding:11px 22px;
                    background:var(--green); color:#000; border-radius:6px;
                    font-family:var(--mono); font-size:10px; letter-spacing:2px; text-transform:uppercase;
                    text-decoration:none; transition:all 0.2s; font-weight:500;">
            jellycatgrant@gmail.com
          </a>
        </div>
      </div>
"""


def _patch_landing(html: str) -> str:
    FOOTER_ANCHOR = '<li><a href="/education/orbital-mechanics">Orbital Mechanics</a></li>'
    if FOOTER_ANCHOR in html:
        html = html.replace(
            FOOTER_ANCHOR,
            _GITHUB_FOOTER_LINK + "\n      " + FOOTER_ANCHOR,
        )
    WILL_CARD_END = "<!-- get in touch strip -->"
    if WILL_CARD_END in html:
        html = html.replace(
            WILL_CARD_END,
            _GRANT_CARD + "\n    " + WILL_CARD_END,
        )
    MOBILE_NAV_ANCHOR = '<a href="#contact">Contact <span>→</span></a>'
    GITHUB_MOBILE = (
        '<a href="https://github.com/trumanheaston-lab/VectraSpace" '
        'target="_blank" rel="noopener">GitHub <span>↗</span></a>'
    )
    if MOBILE_NAV_ANCHOR in html and GITHUB_MOBILE not in html:
        html = html.replace(
            MOBILE_NAV_ANCHOR,
            MOBILE_NAV_ANCHOR + "\n  " + GITHUB_MOBILE,
        )
    DESKTOP_NAV_ANCHOR = '<li><a href="#contact">Contact</a></li>'
    GITHUB_DESKTOP = (
        '<li><a href="https://github.com/trumanheaston-lab/VectraSpace" '
        'target="_blank" rel="noopener">GitHub</a></li>'
    )
    if DESKTOP_NAV_ANCHOR in html and GITHUB_DESKTOP not in html:
        html = html.replace(
            DESKTOP_NAV_ANCHOR,
            DESKTOP_NAV_ANCHOR + "\n    " + GITHUB_DESKTOP,
        )
    return html


LANDING_HTML = _patch_landing(_LANDING_BASE)
