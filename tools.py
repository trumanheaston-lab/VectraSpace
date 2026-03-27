# tools.py
# =============================================================================
# Simple helper for inserting a “Tools & Features” block into the landing page.
# =============================================================================

TOOLS_HTML = """
<div class="tools-section" style="margin-top:2rem">
  <h2 style="font-weight:700;margin-bottom:.75rem;color:#fff">Tools & Features</h2>
  <ul style="list-style:none;padding-left:0;margin:0">
    <li style="margin-bottom:.75rem">🚀 <strong>Orbital Propagation</strong> – Run fast SGP4 propagations for any catalogue.</li>
    <li style="margin-bottom:.75rem">🛡️ <strong>Conjunction Detection</strong> – Predict close‑encounters and generate
        collision‑magnitude estimates.</li>
    <li style="margin-bottom:.75rem">🧪 <strong>Debris Generation</strong> – Use NASA SBM to produce realistic fragment clouds.</li>
    <li style="margin-bottom:.75rem">📩 <strong>Alerting</strong> – Email / webhook / Pushover notifications on risking events.</li>
    <li style="margin-bottom:.75rem">📊 <strong>Data Export</strong> – CSV / GeoJSON / ODBC for downstream analysis.</li>
  </ul>
</div>
"""
