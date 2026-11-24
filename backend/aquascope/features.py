"""Residual analysis and feature extraction.

Residual r_s(t) = observed - nominal EPANET model. Day 1 is the reference period (assumed
event-free); its residual profile b_s(time-of-day) absorbs stationary model mismatch.
Day-matched residual d_s(t) = r_s(t) - b_s(t mod day). Events start between 30 h and 44 h,
so day 3 (48-72 h) is entirely post-event; features use only telemetry, never labels.
"""
from __future__ import annotations

import numpy as np

from . import config as C
from .simulation import nominal

N = 3 * C.STEPS_PER_DAY  # 144 samples (drop the final 72 h sample)
D1 = slice(0, C.STEPS_PER_DAY)
D2 = slice(C.STEPS_PER_DAY, 2 * C.STEPS_PER_DAY)
D3 = slice(2 * C.STEPS_PER_DAY, N)
MON = slice(C.STEPS_PER_DAY, N)


def _arr(d: dict, keys) -> np.ndarray:
    return np.array([np.asarray(d[k], dtype=float)[:N] for k in keys])


def residuals(telemetry: dict) -> dict:
    """Return arrays (sensors x time) needed by features, physics and the UI."""
    for s in C.PRESSURE_SENSORS:
        if s not in telemetry["pressure"]:
            raise ValueError(f"missing pressure sensor '{s}'")
        if len(telemetry["pressure"][s]) < N:
            raise ValueError(f"pressure sensor '{s}' has {len(telemetry['pressure'][s])} samples; need {N}")
    for f in C.FLOW_SENSORS:
        if f not in telemetry["flow"] or len(telemetry["flow"][f]) < N:
            raise ValueError(f"missing or short flow sensor '{f}'")
    nom = nominal()
    P = _arr(telemetry["pressure"], C.PRESSURE_SENSORS)
    if not np.isfinite(P).all():
        raise ValueError("non-finite pressure readings")
    Pn = _arr(nom["pressure"], C.PRESSURE_SENSORS)
    r = P - Pn
    base = r[:, D1]
    d = r - np.tile(base, (1, 3))
    q = _arr(telemetry["flow"], C.FLOW_SENSORS)       # [pump 9, tank pipe 110]
    qn = _arr(nom["flow"], C.FLOW_SENSORS)
    cons, cons_n = q.sum(0), qn.sum(0)                 # network consumption = pump + tank outflow
    dc = (cons - cons_n) - np.tile((cons - cons_n)[D1], 3)
    return {"P": P, "Pn": Pn, "r": r, "d": d, "base": base, "q": q, "qn": qn,
            "cons": cons, "cons_n": cons_n, "dc": dc}


def _slope_per_day(y: np.ndarray) -> float:
    x = np.arange(len(y)) / C.STEPS_PER_DAY
    return float(np.polyfit(x, y, 1)[0])


def extract(telemetry: dict) -> tuple[np.ndarray, dict]:
    R = residuals(telemetry)
    P, d, dc, q = R["P"], R["d"], R["dc"], R["q"]
    f: dict[str, float] = {}
    # reference-period variability (scale of normal mismatch + noise)
    ref_level = float(np.median(np.abs(R["r"][:, D1])))
    f["ref_residual_level"] = ref_level
    m3 = np.median(d[:, D3], axis=1)                                   # day-3 shift per sensor
    m2 = np.median(d[:, 2 * C.STEPS_PER_DAY - 8: 2 * C.STEPS_PER_DAY], axis=1)  # last 4 h of day 2
    for i, s in enumerate(C.PRESSURE_SENSORS):
        others = np.delete(m3, i)
        f[f"shift_{s}"] = float(m3[i])
        f[f"iso_{s}"] = float(m3[i] - np.median(others))
        dP1 = np.diff(P[i, D1]); dP3 = np.diff(P[i, D3])
        # roughness: high-frequency content relative to the reference day
        f[f"noise_ratio_{s}"] = float(np.log((np.std(np.diff(dP3)) + 1e-3) / (np.std(np.diff(dP1)) + 1e-3)))
        f[f"stuck_{s}"] = float(np.mean(np.abs(np.diff(P[i, MON])) < 1e-9))
        f[f"slope_{s}"] = _slope_per_day(d[i, D3])  # step faults are flat on day 3; drifts are not
        f[f"growth_{s}"] = float(m3[i] - m2[i])
    iso = np.array([f[f"iso_{s}"] for s in C.PRESSURE_SENSORS])
    order = np.argsort(-np.abs(iso))
    f["iso_max_abs"] = float(np.abs(iso[order[0]]))
    f["iso_ratio"] = float(np.abs(iso[order[0]]) / (np.abs(iso[order[1]]) + 0.1))
    f["shift_mean"] = float(m3.mean())
    f["shift_abs_mean"] = float(np.abs(m3).mean())
    f["shift_sign_consistency"] = float(np.abs(np.sign(m3).sum()) / len(m3))
    f["shift_spread"] = float(m3.max() - m3.min())
    k = order[0]
    f["slope_at_iso_sensor"] = float(np.abs(f[f"slope_{C.PRESSURE_SENSORS[k]}"]))
    f["noise_ratio_max"] = max(f[f"noise_ratio_{s}"] for s in C.PRESSURE_SENSORS)
    f["stuck_max"] = max(f[f"stuck_{s}"] for s in C.PRESSURE_SENSORS)
    f["growth_abs_max"] = max(abs(f[f"growth_{s}"]) for s in C.PRESSURE_SENSORS)
    # residual persistence: fraction of day 3 where the leading-sensor residual keeps its sign
    lead = d[k, D3]
    f["persistence"] = float(np.mean(np.sign(lead) == np.sign(np.median(lead))))
    # flow / mass balance
    f["cons_shift"] = float(np.median(dc[D3]))
    f["cons_shift_rel"] = float(np.median(dc[D3]) / np.mean(R["cons_n"]))
    f["cons_growth"] = float(np.median(dc[D3]) - np.median(dc[2 * C.STEPS_PER_DAY - 8: 2 * C.STEPS_PER_DAY]))
    extra = dc[D3]
    cn = R["cons_n"][D3]
    f["cons_pattern_corr"] = float(np.corrcoef(extra, cn)[0, 1]) if np.std(extra) > 1e-6 else 0.0
    pump_on1, pump_on3 = q[0, D1] > 1.0, q[0, D3] > 1.0
    f["pump_on_frac_change"] = float(pump_on3.mean() - pump_on1.mean())
    on_flow = lambda x, m: float(np.median(x[m])) if m.any() else 0.0
    f["pump_on_flow_change"] = on_flow(q[0, D3], pump_on3) - on_flow(q[0, D1], pump_on1)
    f["tank_flow_shift"] = float(np.median(q[1, D3]) - np.median(q[1, D1]))
    names = feature_names()
    return np.array([f[n] for n in names], dtype=float), f


def feature_names() -> list[str]:
    per = []
    for s in C.PRESSURE_SENSORS:
        per += [f"shift_{s}", f"iso_{s}", f"noise_ratio_{s}", f"stuck_{s}", f"slope_{s}", f"growth_{s}"]
    return ["ref_residual_level"] + per + [
        "iso_max_abs", "iso_ratio", "shift_mean", "shift_abs_mean", "shift_sign_consistency", "shift_spread",
        "slope_at_iso_sensor", "noise_ratio_max", "stuck_max", "growth_abs_max", "persistence",
        "cons_shift", "cons_shift_rel", "cons_growth", "cons_pattern_corr",
        "pump_on_frac_change", "pump_on_flow_change", "tank_flow_shift"]
