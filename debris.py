"""
VectraSpace v11 — debris.py
NASA Standard Breakup Model fragment generation.
"""

import logging
import math

import numpy as np

log = logging.getLogger("VectraSpace")


def generate_debris_cloud(parent_track, event_type: str, n_debris: int, ts) -> list:
    n_debris = min(int(n_debris), 200)
    mu_lv, sigma_lv = (0.1, 0.5) if event_type == "EXPLOSION" else (0.3, 0.7)
    parent_pos = parent_track.positions[0]
    rng = np.random.default_rng(seed=42)
    debris_tracks = []

    for k in range(n_debris):
        lc     = rng.uniform(0.01, 0.5)
        dv_mag = 10 ** rng.normal(mu_lv + 0.5 * math.log10(lc), sigma_lv)
        dv_mag = float(np.clip(dv_mag, 0.001, 5.0))

        direction  = rng.normal(0, 1, 3)
        direction /= np.linalg.norm(direction) + 1e-12
        dv_eci     = direction * dv_mag

        synthetic = parent_track.positions.copy()
        for t_idx in range(len(synthetic)):
            dt = parent_track.times_min[t_idx] * 60
            synthetic[t_idx] = parent_track.positions[t_idx] + dv_eci * dt

        # Use a simple struct-like object so we don't need dataclass import here
        track = type("SatTrack", (), {
            "name":      f"DEBRIS-{99000 + k:05d}",
            "regime":    parent_track.regime,
            "times_min": parent_track.times_min,
            "positions": synthetic,
            "is_debris": True,
        })()
        debris_tracks.append(track)

    log.info(f"Generated {len(debris_tracks)} debris from {parent_track.name} ({event_type})")
    return debris_tracks
