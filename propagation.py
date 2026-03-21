"""
VectraSpace v11 — propagation.py
SGP4 batch propagation → SatTrack dataclass.
"""

import logging
from dataclasses import dataclass

import numpy as np

from config import Config

log = logging.getLogger("VectraSpace")


@dataclass
class SatTrack:
    name: str
    regime: str
    times_min: np.ndarray
    positions: np.ndarray   # shape (N, 3) — ECI km
    is_debris: bool = False


def propagate_satellites(sat_list: list, regime: str,
                         cfg: Config, ts) -> tuple[list, object]:
    now       = ts.now()
    num_steps = int(cfg.time_window_hours * 60 / cfg.coarse_step_minutes)
    dt_days   = cfg.coarse_step_minutes / 1440.0
    jd_array  = now.tt + np.arange(num_steps) * dt_days
    times     = ts.tt_jd(jd_array)
    times_min = np.arange(num_steps) * cfg.coarse_step_minutes

    tracks = []
    for sat in sat_list:
        try:
            pos_km = sat.at(times).position.km
            tracks.append(SatTrack(
                name=sat.name,
                regime=regime,
                times_min=times_min,
                positions=pos_km.T,
            ))
        except Exception as e:
            log.debug(f"Skipping {sat.name}: {e}")

    log.info(f"  Propagated {len(tracks)}/{len(sat_list)} {regime} satellites")
    return tracks, times
