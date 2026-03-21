"""
VectraSpace v11 — templates_loader_patch.py

Drop this file in the repo root alongside templates_loader.py.
It monkey-patches LANDING_HTML to add the Trajectory Simulator button
to the tools strip and nav — no other files need changing.

INTEGRATION: Already done if you have the updated pages.py that imports from here.
If not, in pages.py change:
    from templates_loader import (LANDING_HTML, ...)
to:
    from templates_loader import (LANDING_HTML as _LANDING_HTML_BASE, ...)
    from templates_loader_patch import LANDING_HTML

Or — simpler — just apply the patch directly inside templates_loader.py by
adding these two lines at the very bottom:

    from templates_loader_patch import _apply_trajectory_patch
    LANDING_HTML = _apply_trajectory_patch(LANDING_HTML)
"""

# ── Trajectory Simulator tool card ───────────────────────────────────────────
_TRAJECTORY_TOOL_CARD = """
      <a href="/api/tools/trajectory" class="tool-card">
        <div class="tool-card-icon green">🚀</div>
        <div class="tool-card-body">
          <div class="tool-card-title">Trajectory Simulator</div>
          <div class="tool-card-desc">RK4 suborbital flight · WGS84 gravity · Cesium globe</div>
        </div>
      </a>"""

# ── Nav desktop link ──────────────────────────────────────────────────────────
_TRAJECTORY_NAV_LINK = '<li><a href="/api/tools/trajectory">Trajectory</a></li>'

# ── Nav mobile link ───────────────────────────────────────────────────────────
_TRAJECTORY_MOBILE_LINK = '<a href="/api/tools/trajectory">Trajectory Simulator <span>→</span></a>'


def _apply_trajectory_patch(html: str) -> str:
    """Insert trajectory simulator entry points into LANDING_HTML."""

    # 1. Add to tools strip — insert before the closing </div> of .tools-strip
    #    Anchor: the glossary/resources card (last card currently)
    TOOLS_ANCHOR = """      <a href="/glossary" class="tool-card">
        <div class="tool-card-icon purple">📖</div>
        <div class="tool-card-body">
          <div class="tool-card-title">Resources</div>
          <div class="tool-card-desc">50+ terms · searchable · deep-link ready</div>
        </div>
      </a>"""

    if TOOLS_ANCHOR in html and _TRAJECTORY_TOOL_CARD not in html:
        html = html.replace(
            TOOLS_ANCHOR,
            _TRAJECTORY_TOOL_CARD + "\n" + TOOLS_ANCHOR,
        )

    # 2. Add to desktop nav — insert after Calculator link
    DESKTOP_ANCHOR = '<li><a href="/calculator">Calculator</a></li>'
    if DESKTOP_ANCHOR in html and _TRAJECTORY_NAV_LINK not in html:
        html = html.replace(
            DESKTOP_ANCHOR,
            DESKTOP_ANCHOR + "\n    " + _TRAJECTORY_NAV_LINK,
        )

    # 3. Add to mobile nav — insert after Calculator mobile link
    MOBILE_ANCHOR = '<a href="/calculator">Calculator <span>→</span></a>'
    if MOBILE_ANCHOR in html and _TRAJECTORY_MOBILE_LINK not in html:
        html = html.replace(
            MOBILE_ANCHOR,
            MOBILE_ANCHOR + "\n  " + _TRAJECTORY_MOBILE_LINK,
        )

    return html


# ── Auto-apply when imported ──────────────────────────────────────────────────
try:
    import templates_loader as _tl
    _tl.LANDING_HTML = _apply_trajectory_patch(_tl.LANDING_HTML)
except Exception as _e:
    import logging
    logging.getLogger("VectraSpace").warning(
        f"templates_loader_patch: could not auto-patch LANDING_HTML: {_e}"
    )
