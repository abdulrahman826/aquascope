"""Single-scenario diagnosis service: features -> 3 approaches -> selected approach -> abstention,
ranked hypotheses with supporting / contradicting evidence, and online detection."""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from . import config as C, physics
from .features import N, extract, residuals
from .models import APPROACHES, bundle, calibrated_probas

SELECTION_PATH = C.ARTIFACT_DIR / "selection.json"
EVAL_PATH = C.ARTIFACT_DIR / "evaluation.json"

LABELS = {
    "normal": "Normal operation", "abrupt_leak": "Abrupt pipe leak", "incipient_leak": "Incipient (growing) leak",
    "sensor_bias": "Sensor bias / offset", "sensor_drift": "Sensor drift", "sensor_noise": "Sensor noise",
    "sensor_stuck": "Stuck sensor", "pump_disturbance": "Pump disturbance",
    "valve_disturbance": "Valve disturbance (throttled pipe)", "demand_anomaly": "Abnormal demand",
}


def selected_approach() -> str:
    if SELECTION_PATH.exists():
        return json.loads(SELECTION_PATH.read_text())["selected"]
    return "hybrid"  # provisional until evaluation has run; reported as provisional


def detect(telemetry: dict, sigma_p: float, threshold: float = 3.0, sigma_c: float = 2.0) -> dict:
    """Online detector over the monitoring period: robust (3 h rolling median) residual z-scores
    for pressure shift and consumption, plus sensor-health checks (frozen / noisy readings)."""
    R = residuals(telemetry)
    d, dc, P = R["d"], R["dc"], R["P"]
    win = 6
    t = np.asarray(telemetry["time"][:N])
    roll_med = lambda x: pd.Series(x).rolling(win, min_periods=win).median().to_numpy()
    zp = np.nan_to_num(np.abs(np.vstack([roll_med(row) for row in d]))).max(axis=0) / sigma_p
    zc = np.abs(roll_med(dc)) / sigma_c
    dP = np.abs(np.diff(P, axis=1, prepend=P[:, :1]))
    frozen = np.vstack([pd.Series(r < 1e-9).rolling(win, min_periods=win).sum().to_numpy() for r in dP]).max(0) >= win
    ref_rough = np.std(np.diff(np.diff(P[:, :C.STEPS_PER_DAY], axis=1), axis=1), axis=1) + 1e-3
    rough = np.vstack([pd.Series(np.abs(np.diff(r, n=2, prepend=[r[0], r[0]]))).rolling(win, min_periods=win).median()
                       .to_numpy() / ref_rough[i] for i, r in enumerate(P)]).max(0)
    score = np.nan_to_num(np.maximum.reduce([zp, zc, np.where(frozen, threshold * 2, 0), rough]))
    alarm = score >= threshold
    alarm[:C.STEPS_PER_DAY] = False                      # reference day is not monitored
    first = int(np.argmax(alarm)) if alarm.any() else None
    rises = int(np.sum(np.diff(alarm.astype(int)) == 1))
    return {"alarm": bool(alarm.any()), "first_alarm_time": int(t[first]) if first is not None else None,
            "n_alarm_episodes": rises, "score": np.round(score, 3).tolist(), "threshold": threshold}


def evidence(cls: str, f: dict, ph: dict) -> tuple[list[str], list[str]]:
    """Human-readable supporting / contradicting evidence for a hypothesis, from measured indicators."""
    sp = ph["indicators"]["pressure_sigma_m"]
    sup, con = [], []
    shifts = {s: f[f"shift_{s}"] for s in C.PRESSURE_SENSORS}
    n_sig = sum(abs(v) > 2 * sp for v in shifts.values())
    lead = ph["indicators"]["lead_sensor"]
    cons, corr = f["cons_shift"], f["cons_pattern_corr"]
    shift_txt = ", ".join(f"{s}: {v:+.2f} m" for s, v in shifts.items())
    fam = C.CLASS_FAMILY[cls]
    if fam == "leak":
        best = ph["localization"]["leak"][0]
        (sup if cons > 1.0 else con).append(f"Network consumption changed by {cons:+.2f} L/s vs reference day")
        (sup if n_sig >= 2 else con).append(f"{n_sig} of {len(shifts)} pressure sensors shifted beyond 2σ ({shift_txt})")
        (sup if corr < 0.3 else con).append(f"Excess consumption vs demand-pattern correlation = {corr:.2f} "
                                            "(leaks are pressure-driven, roughly pattern-independent)")
        red = 1 - best["rss"] / max(ph["indicators"]["null_misfit"], 1e-9)
        (sup if red > 0.5 else con).append(f"Leak signature at pipe {best['location']} explains {100 * red:.0f}% of residual misfit")
        g = ph["indicators"]["growth_ratio"]
        if cls == "incipient_leak":
            (sup if g > 0.1 else con).append(f"Consumption excess growth ratio {g:.2f} (growing leak expected > 0.1)")
        else:
            (sup if g <= 0.1 else con).append(f"Consumption excess growth ratio {g:.2f} (step change expected ≤ 0.1)")
    elif fam == "sensor":
        (sup if f["iso_ratio"] > 2 else con).append(
            f"Deviation concentrated at sensor {lead}: isolation ratio {f['iso_ratio']:.1f} ({shift_txt})")
        (sup if abs(cons) < 1.0 else con).append(f"Consumption change {cons:+.2f} L/s (sensor faults do not change hydraulics)")
        if cls == "sensor_noise":
            (sup if f["noise_ratio_max"] > 0.5 else con).append(f"High-frequency roughness ratio (log) {f['noise_ratio_max']:.2f} vs reference day")
        if cls == "sensor_stuck":
            (sup if f["stuck_max"] > 0.3 else con).append(f"{100 * f['stuck_max']:.0f}% of monitoring-period readings frozen")
        if cls == "sensor_drift":
            s = ph["indicators"]["lead_sensor_slope_m_per_day"]
            (sup if s > 1.0 else con).append(f"Day-3 residual trend at sensor {lead}: {s:.2f} m/day")
        if cls == "sensor_bias":
            s = ph["indicators"]["lead_sensor_slope_m_per_day"]
            (sup if s <= 1.0 else con).append(f"Residual at sensor {lead} is flat on day 3 (trend {s:.2f} m/day)")
    elif cls == "pump_disturbance":
        (sup if abs(f["pump_on_frac_change"]) > 0.05 else con).append(f"Pump duty cycle changed by {100 * f['pump_on_frac_change']:+.0f} pts")
        (sup if n_sig >= 3 else con).append(f"Network-wide pressure shift ({shift_txt})")
    elif cls == "valve_disturbance":
        best = ph["localization"]["valve_disturbance"][0]
        red = 1 - best["rss"] / max(ph["indicators"]["null_misfit"], 1e-9)
        (sup if red > 0.5 else con).append(f"Throttling signature at pipe {best['location']} explains {100 * red:.0f}% of residual misfit")
        (sup if abs(cons) < 1.0 else con).append(f"Consumption change {cons:+.2f} L/s (throttling redistributes, does not add, flow)")
    elif cls == "demand_anomaly":
        (sup if cons > 1.0 else con).append(f"Network consumption changed by {cons:+.2f} L/s")
        (sup if corr > 0.3 else con).append(f"Excess consumption follows the demand pattern (corr {corr:.2f})")
    else:  # normal
        (sup if n_sig == 0 else con).append(f"{n_sig} pressure sensors shifted beyond 2σ = {2 * sp:.2f} m ({shift_txt})")
        (sup if abs(cons) < 1.0 else con).append(f"Consumption change {cons:+.2f} L/s")
        (sup if f["noise_ratio_max"] < 0.5 and f["stuck_max"] < 0.3 else con).append(
            f"Sensor health: roughness ratio {f['noise_ratio_max']:.2f}, frozen fraction {f['stuck_max']:.2f}")
    return sup, con


def decide(p: np.ndarray, threshold: float) -> tuple[str, str]:
    o = np.argsort(-p)
    p1, p2 = p[o[0]], p[o[1]]
    if p1 >= threshold:
        return "CONFIDENT", f"Leading probability {p1:.2f} ≥ validated threshold {threshold:.2f}"
    if p1 + p2 >= threshold:
        return ("AMBIGUOUS_DIAGNOSIS", f"Evidence splits between {LABELS[C.CLASSES[o[0]]]} ({p1:.2f}) and "
                f"{LABELS[C.CLASSES[o[1]]]} ({p2:.2f}); neither reaches threshold {threshold:.2f}")
    return ("INSUFFICIENT_EVIDENCE", f"No hypothesis or pair of hypotheses reaches threshold {threshold:.2f} "
            f"(top: {p1:.2f}, {p2:.2f})")


def diagnose_scenario(sc: dict, approach: str | None = None) -> dict:
    t0 = time.perf_counter()
    approach = approach or selected_approach()
    if approach not in APPROACHES:
        raise ValueError(f"unknown approach '{approach}'")
    b = bundle()
    x, f = extract(sc["telemetry"])
    probs = calibrated_probas(pd.DataFrame([f]))
    ph = physics.diagnose(f, b["physics_params"])
    p = probs[approach][0]
    thr = b["thresholds"][approach]
    status, reason = decide(p, thr)
    order = np.argsort(-p)
    hyps = []
    for j in order[:5]:
        cls = C.CLASSES[j]
        sup, con = evidence(cls, f, ph)
        loc_list = physics.predicted_location(ph, cls)
        hyps.append({"event_class": cls, "label": LABELS[cls], "family": C.CLASS_FAMILY[cls],
                     "probability": round(float(p[j]), 4), "candidate_locations": loc_list[:3],
                     "supporting_evidence": sup, "contradicting_evidence": con})
    R = residuals(sc["telemetry"])
    det = detect(sc["telemetry"], ph["indicators"]["pressure_sigma_m"])
    calib = _calibration_note(approach)
    return {
        "scenario_id": sc["scenario_id"], "approach": approach, "approach_selection": _selection_note(),
        "status": status, "reason": reason, "threshold": thr,
        "probability_type": calib,
        "hypotheses": hyps,
        "all_approaches": {a: dict(zip(C.CLASSES, np.round(probs[a][0], 4).tolist())) for a in APPROACHES},
        "physics": {"localization": ph["localization"], "indicators": ph["indicators"]},
        "features": {k: round(float(v), 4) for k, v in f.items()},
        "detection": det,
        "residuals": {"time": sc["telemetry"]["time"][:N],
                      "pressure": {s: np.round(R["d"][i], 3).tolist() for i, s in enumerate(C.PRESSURE_SENSORS)},
                      "consumption": np.round(R["dc"], 3).tolist(),
                      "expected_pressure": {s: np.round(R["Pn"][i] + np.tile(R["base"][i], 3), 3).tolist()
                                            for i, s in enumerate(C.PRESSURE_SENSORS)}},
        "latency_ms": round(1000 * (time.perf_counter() - t0), 1),
        "disclaimer": "Simulation-based evidence on EPANET Net1; not validated on real utility data.",
    }


def _selection_note() -> dict:
    if SELECTION_PATH.exists():
        return json.loads(SELECTION_PATH.read_text())
    return {"selected": "hybrid", "provisional": True, "reason": "evaluation not yet run"}


def _calibration_note(approach: str) -> str:
    if EVAL_PATH.exists():
        ev = json.loads(EVAL_PATH.read_text())
        ece = ev["approaches"][approach]["overall"].get("ece")
        n = ev["approaches"][approach]["overall"]["n"]
        return (f"Probability calibrated by temperature scaling on the validation split; "
                f"test ECE = {ece:.3f} (n = {n} simulated scenarios)")
    return "Temperature-scaled on validation split; test calibration not yet evaluated"
