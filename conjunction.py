"""
VectraSpace v11 — conjunction.py
Conjunction detection (vectorised), Pc (Foster-Alfano), CW maneuver advisory.
"""

import logging
import re as _re
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import chi2

from config import Config
from propagation import SatTrack

log = logging.getLogger("VectraSpace")


@dataclass
class ManeuverSuggestion:
    delta_v_rtn: list
    delta_v_magnitude: float
    burn_epoch_offset_min: float
    safe_dist_achieved_km: float
    method: str = "CW-LINEAR"
    advisory_note: str = "Advisory only. Verify with high-fidelity propagator before execution."
    feasible: bool = True


@dataclass
class Conjunction:
    sat1: str
    sat2: str
    regime1: str
    regime2: str
    min_dist_km: float
    time_min: float
    pc_estimate: float
    covariance_source: str = "assumed"
    debris: bool = False
    maneuver: Optional[object] = None


# ── Pc (Foster-Alfano) ────────────────────────────────────────────────────────

def estimate_pc_foster(miss_km, v_rel_km_s, sigma_along, sigma_cross, sigma_radial,
                       cov1=None, cov2=None):
    source = "assumed"
    if cov1 is not None and cov2 is not None:
        combined = cov1 + cov2
        s_a = float(np.sqrt(max(combined[1, 1], 1e-12)))
        s_c = float(np.sqrt(max(combined[2, 2], 1e-12)))
        s_r = float(np.sqrt(max(combined[0, 0], 1e-12)))
        source = "measured"
    else:
        s_a = sigma_along  * np.sqrt(2)
        s_c = sigma_cross  * np.sqrt(2)
        s_r = sigma_radial * np.sqrt(2)

    sigma_combined = np.sqrt((s_a**2 + s_c**2 + s_r**2) / 3)
    if sigma_combined < 1e-9:
        return 0.0, source
    r_hbr = 0.02
    x     = ((miss_km - r_hbr) / sigma_combined) ** 2
    pc    = float(1.0 - chi2.cdf(max(x, 0), df=3))
    return min(pc, 1.0), source


def _ellipsoid_overlap_possible(miss_km, sigma_along, sigma_cross,
                                 sigma_radial, n_sigma=5.0):
    max_sigma = np.sqrt(2) * max(sigma_along, sigma_cross, sigma_radial)
    return miss_km <= (n_sigma * max_sigma)


# ── Vectorised pair screening ─────────────────────────────────────────────────

def _chunked_min_distances(all_tracks, chunk_size) -> np.ndarray:
    n = len(all_tracks)
    min_dists = np.full((n, n), np.inf, dtype=np.float32)
    np.fill_diagonal(min_dists, 0.0)

    for i_start in range(0, n, chunk_size):
        i_end   = min(i_start + chunk_size, n)
        block_i = np.stack([all_tracks[k].positions for k in range(i_start, i_end)], axis=0)

        for j_start in range(i_start, n, chunk_size):
            j_end = min(j_start + chunk_size, n)
            if j_start == i_start:
                ci = block_i.shape[0]
                for ri in range(ci):
                    for rj in range(ri + 1, ci):
                        diffs = block_i[ri] - block_i[rj]
                        d = float(np.sqrt((diffs**2).sum(axis=1)).min())
                        gi, gj = i_start + ri, j_start + rj
                        min_dists[gi, gj] = d
                        min_dists[gj, gi] = d
            else:
                block_j = np.stack([all_tracks[k].positions for k in range(j_start, j_end)], axis=0)
                diffs  = block_i[:, np.newaxis, :, :] - block_j[np.newaxis, :, :, :]
                dists  = np.sqrt((diffs**2).sum(axis=3))
                bmins  = dists.min(axis=2)
                for ri in range(block_i.shape[0]):
                    for rj in range(block_j.shape[0]):
                        gi, gj = i_start + ri, j_start + rj
                        if gi < gj:
                            min_dists[gi, gj] = bmins[ri, rj]
                            min_dists[gj, gi] = bmins[ri, rj]
    return min_dists


def _refine_pair(t1: SatTrack, t2: SatTrack, coarse_min_idx, cfg) -> tuple:
    t_lo = max(0, coarse_min_idx - 1)
    t_hi = min(len(t1.times_min) - 1, coarse_min_idx + 1)

    def dist_at(t_frac):
        p1 = tuple(np.interp(t_frac, t1.times_min, t1.positions[:, k]) for k in range(3))
        p2 = tuple(np.interp(t_frac, t2.times_min, t2.positions[:, k]) for k in range(3))
        return np.sqrt(sum((a - b)**2 for a, b in zip(p1, p2)))

    result = minimize_scalar(dist_at,
                             bounds=(t1.times_min[t_lo], t1.times_min[t_hi]),
                             method="bounded")
    return result.fun, result.x


def _compute_maneuver(t1: SatTrack, t2: SatTrack,
                      time_min_tca: float, cfg) -> ManeuverSuggestion:
    if time_min_tca < 1.0:
        return ManeuverSuggestion(
            delta_v_rtn=[0,0,0], delta_v_magnitude=None,
            burn_epoch_offset_min=0.0, safe_dist_achieved_km=0.0,
            feasible=False, advisory_note="TCA too imminent (< 60s).",
        )
    idx0   = 0
    r_rel  = t1.positions[idx0] - t2.positions[idx0]
    if len(t1.positions) > 1:
        dt    = t1.times_min[1] * 60
        v_rel = (t1.positions[1] - t1.positions[0] - (t2.positions[1] - t2.positions[0])) / dt
    else:
        v_rel = np.zeros(3)

    r1     = np.linalg.norm(t1.positions[idx0])
    mu     = 398600.4418
    tau    = time_min_tca * 60
    target = cfg.maneuver_safe_dist_km

    r_rel_mag = float(np.linalg.norm(r_rel))
    r_unit    = r_rel / (r_rel_mag + 1e-9)
    needed    = max(0.0, target - r_rel_mag)
    dv_t      = needed / (2.0 * tau) if tau > 0 else 0.0
    dv_r      = -float(np.dot(v_rel, r_unit)) * 0.1
    dv_vec    = np.array([dv_r, dv_t, 0.0]) * 1000
    dv_mag    = float(np.linalg.norm(dv_vec))

    note = "Advisory only. Verify with high-fidelity propagator before execution."
    if dv_mag > 10.0:
        note = f"LARGE_DV ({dv_mag:.1f} m/s). {note}"

    return ManeuverSuggestion(
        delta_v_rtn=[round(float(dv_vec[0]),4), round(float(dv_vec[1]),4), round(float(dv_vec[2]),4)],
        delta_v_magnitude=round(dv_mag, 4),
        burn_epoch_offset_min=0.0,
        safe_dist_achieved_km=round(target, 3),
        feasible=True,
        advisory_note=note,
    )


# ── ISS / CSS same-station filter ────────────────────────────────────────────

_ISS_PERMANENT = {"ISS","ZARYA","ZVEZDA","UNITY","DESTINY","HARMONY",
                   "TRANQUILITY","SERENITY","COLUMBUS","KIBO","QUEST",
                   "PIRS","POISK","RASSVET","NAUKA","PRICHAL","BISHOP","NANORACKS"}
_ISS_VISITING  = {"SOYUZ","PROGRESS","CYGNUS","DRAGON","STARLINER","HTV","ATV"}
_CSS_PERMANENT = {"TIANHE","WENTIAN","MENGTIAN"}
_CSS_VISITING  = {"CSS","TIANZHOU","SHENZHOU"}


def _same_station(n1, n2):
    u1, u2 = n1.upper(), n2.upper()
    def iss_perm(n): return any(k in n for k in _ISS_PERMANENT)
    def iss_fam(n):  return any(k in n for k in _ISS_PERMANENT | _ISS_VISITING)
    def css_perm(n): return any(k in n for k in _CSS_PERMANENT)
    def css_fam(n):  return any(k in n for k in _CSS_PERMANENT | _CSS_VISITING)

    if iss_perm(u1) and iss_perm(u2): return True
    if iss_fam(u1)  and iss_fam(u2):  return True
    if css_perm(u1) and css_perm(u2): return True
    if css_fam(u1)  and css_fam(u2):  return True

    b1 = _re.sub(r'[\s\-_]?[\dA-Z]{1,2}$', '', u1).strip()
    b2 = _re.sub(r'[\s\-_]?[\dA-Z]{1,2}$', '', u2).strip()
    if b1 == b2 and len(b1) > 5:
        return True
    return False


# ── Main entry ────────────────────────────────────────────────────────────────

def check_conjunctions(all_tracks: list, cfg: Config, ts,
                       covariance_cache: Optional[dict] = None) -> list:
    conjunctions = []
    n  = len(all_tracks)
    log.info(f"Checking {n*(n-1)//2} pairs…")

    try:
        min_dist_matrix = _chunked_min_distances(all_tracks, cfg.vector_chunk_size)
    except MemoryError:
        log.warning("MemoryError in vectorised screening — falling back to loop")
        min_dist_matrix = None

    skipped = 0
    for i in range(n):
        t1 = all_tracks[i]
        for j in range(i + 1, n):
            t2 = all_tracks[j]

            if _same_station(t1.name, t2.name):
                skipped += 1
                continue

            if min_dist_matrix is not None:
                coarse = float(min_dist_matrix[i, j])
            else:
                diffs  = t1.positions - t2.positions
                coarse = float(np.sqrt((diffs**2).sum(axis=1)).min())

            if not _ellipsoid_overlap_possible(coarse, cfg.sigma_along,
                                               cfg.sigma_cross, cfg.sigma_radial):
                skipped += 1
                continue

            if coarse > cfg.refine_threshold_km:
                continue

            diffs     = t1.positions - t2.positions
            distances = np.sqrt((diffs**2).sum(axis=1))
            cidx      = int(np.argmin(distances))
            min_dist, min_time = _refine_pair(t1, t2, cidx, cfg)

            if min_dist < cfg.collision_alert_km:
                idx = min(cidx, len(t1.positions) - 2)
                dt  = cfg.coarse_step_minutes * 60
                v1  = np.linalg.norm(t1.positions[idx+1] - t1.positions[idx]) / dt
                v2  = np.linalg.norm(t2.positions[idx+1] - t2.positions[idx]) / dt
                v_rel = abs(v1 - v2) + 0.5

                cov1 = covariance_cache.get(t1.name) if covariance_cache else None
                cov2 = covariance_cache.get(t2.name) if covariance_cache else None
                pc, cov_source = estimate_pc_foster(min_dist, v_rel,
                                                     cfg.sigma_along, cfg.sigma_cross,
                                                     cfg.sigma_radial, cov1, cov2)
                maneuver  = _compute_maneuver(t1, t2, min_time, cfg)
                is_debris = getattr(t1, "is_debris", False) or getattr(t2, "is_debris", False)

                conjunctions.append(Conjunction(
                    sat1=t1.name, sat2=t2.name,
                    regime1=t1.regime, regime2=t2.regime,
                    min_dist_km=min_dist, time_min=min_time,
                    pc_estimate=pc, covariance_source=cov_source,
                    debris=is_debris, maneuver=maneuver,
                ))

    conjunctions.sort(key=lambda c: c.min_dist_km)
    log.info(f"Found {len(conjunctions)} conjunctions — {skipped} pairs skipped")
    return conjunctions
