"""
VectraSpace v11 — pipeline.py
Orchestrates the full scan: TLE fetch → propagate → detect → alert → log.
"""

import datetime
import logging
import time
from typing import Optional

from config import Config, CFG
from tle import fetch_tles, filter_by_regime
from propagation import propagate_satellites
from conjunction import check_conjunctions
from database import init_db, log_conjunctions_to_db, fetch_covariance_cache
from alerts import send_alerts, send_propagation_complete

log = logging.getLogger("VectraSpace")


def run_pipeline(cfg: Config,
                 covariance_cache: Optional[dict] = None,
                 run_mode: str = "interactive",
                 user_id: Optional[str] = None,
                 user_prefs: Optional[dict] = None) -> dict:
    """
    Full detection pipeline.

    Returns:
        {
          "tracks":       list[SatTrack],
          "conjunctions": list[Conjunction],
          "run_time":     ISO-8601 string,
          "ts":           Skyfield timescale,
        }
    """
    t_start = time.time()

    satellites, ts = fetch_tles(cfg)
    buckets        = filter_by_regime(satellites, ts)

    regime_counts = {
        "LEO": cfg.num_leo,
        "MEO": cfg.num_meo,
        "GEO": cfg.num_geo,
    }

    all_tracks = []
    for regime, sats in buckets.items():
        count  = regime_counts.get(regime, cfg.num_satellites_per_regime)
        subset = sats[:count]
        tracks, _ = propagate_satellites(subset, regime, cfg, ts)
        all_tracks.extend(tracks)
        log.info(f"  {regime}: {len(tracks)} tracks")

    log.info(f"Total: {len(all_tracks)} tracks")

    conjunctions = check_conjunctions(all_tracks, cfg, ts,
                                      covariance_cache=covariance_cache)

    con      = init_db(cfg)
    run_time = datetime.datetime.utcnow().isoformat()
    log_conjunctions_to_db(conjunctions, con, run_time, user_id=user_id)

    duration = time.time() - t_start
    send_alerts(conjunctions, cfg,
                total_sats=len(all_tracks), user_prefs=user_prefs)
    send_propagation_complete(len(all_tracks), conjunctions,
                              duration, cfg, user_prefs=user_prefs)

    return {
        "tracks":       all_tracks,
        "conjunctions": conjunctions,
        "run_time":     run_time,
        "ts":           ts,
    }
