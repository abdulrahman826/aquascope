"""Approach A: physics / residual-based diagnosis.

Each hydraulic hypothesis (leak at pipe p, valve throttling at pipe p, demand surge at node n,
pump degradation) is simulated on the nominal EPANET model. Its *signature* is the same feature
subset the observed data produces (day-3 pressure shifts, consumption shift, pump duty change).
The observed signature is fitted against each hypothesis with a non-negative scale; sensor faults
are fitted as a single-sensor deviation. Fit residuals become Gaussian log-likelihood scores with
a complexity penalty; temporal/sensor-health evidence splits families into sub-classes.
Thresholds live in PhysicsParams and are selected on the validation split only.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache

import numpy as np

from . import config as C
from .features import extract
from .simulation import hypothesis_run, nominal, severity_params

SIG_KEYS = [f"shift_{s}" for s in C.PRESSURE_SENSORS] + ["cons_shift", "pump_on_frac_change"]
REF_START = 36 * 3600
REF_SEVERITY = 0.5


@dataclass
class PhysicsParams:
    sigma_p0: float = 0.3      # m, pressure-shift uncertainty floor
    sigma_p_ref: float = 0.5   # multiplier on reference residual level (model-mismatch proxy)
    sigma_c: float = 1.5       # L/s, consumption-shift uncertainty
    sigma_f: float = 0.08      # pump duty-cycle change uncertainty
    penalty: float = 3.0       # log-score cost of a non-null hypothesis
    growth_ratio0: float = 0.12  # cons_growth / cons_shift above this -> incipient
    noise_ratio0: float = 0.6  # log roughness ratio above this -> sensor noise
    stuck0: float = 0.3        # fraction of frozen samples -> stuck
    drift_slope0: float = 1.2  # m/day on day 3 at isolated sensor -> drift
    pattern_corr0: float = 0.3  # consumption excess correlated with demand pattern -> demand anomaly
    evidence_gain: float = 6.0


def _telemetry_from_run(run: dict) -> dict:
    return {"pressure": {s: run["pressure"][s] for s in C.PRESSURE_SENSORS},
            "flow": {f: run["flow"][f] for f in C.FLOW_SENSORS}}


def hydraulic_hypotheses() -> list[tuple[str, str]]:
    return ([("leak", p) for p in C.LEAK_PIPES] + [("valve_disturbance", p) for p in C.VALVE_PIPES]
            + [("demand_anomaly", n) for n in C.DEMAND_NODES] + [("pump_disturbance", C.PUMP_ID)])


def _sim_class(kind: str) -> str:
    return "abrupt_leak" if kind == "leak" else kind


@lru_cache(maxsize=1)
def signature_library() -> dict[tuple[str, str], np.ndarray]:
    lib = {}
    for kind, loc in hydraulic_hypotheses():
        cls = _sim_class(kind)
        run = hypothesis_run(cls, loc, REF_START, severity_params(cls, REF_SEVERITY))
        _, f = extract(_telemetry_from_run(run))
        lib[(kind, loc)] = np.array([f[k] for k in SIG_KEYS])
    return lib


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def diagnose(feats: dict, params: PhysicsParams = PhysicsParams()) -> dict:
    """Return per-class log-scores, softmax scores (UNCALIBRATED), localisation and evidence."""
    p = params
    y = np.array([feats[k] for k in SIG_KEYS])
    sp = p.sigma_p0 + p.sigma_p_ref * feats["ref_residual_level"]
    w = np.array([1 / sp] * len(C.PRESSURE_SENSORS) + [1 / p.sigma_c, 1 / p.sigma_f])
    yw = y * w
    null_rss = float(yw @ yw)
    fits: dict[str, list] = {"leak": [], "valve_disturbance": [], "demand_anomaly": [], "pump_disturbance": []}
    for (kind, loc), g in signature_library().items():
        gw = g * w
        a = max(0.0, float(yw @ gw) / float(gw @ gw + 1e-12))
        a = min(a, 4.0)
        rss = float(np.sum((yw - a * gw) ** 2))
        fits[kind].append({"location": loc, "scale": a, "rss": rss, "loglik": -0.5 * rss - p.penalty})
    for k in fits:
        fits[k].sort(key=lambda d: -d["loglik"])
    # single-sensor fault: free-sign deviation at one sensor
    sens = []
    for i, s in enumerate(C.PRESSURE_SENSORS):
        rss = null_rss - yw[i] ** 2
        sens.append({"location": s, "scale": float(y[i]), "rss": rss, "loglik": -0.5 * rss - p.penalty})
    sens.sort(key=lambda d: -d["loglik"])

    g = p.evidence_gain
    growth = feats["cons_growth"] / (abs(feats["cons_shift"]) + 0.5)
    corr = feats["cons_pattern_corr"]
    lead = sens[0]["location"]
    slope = abs(feats[f"slope_{lead}"])
    noise = feats["noise_ratio_max"]
    stuck = feats["stuck_max"]
    lg = lambda x: float(np.log(_sigmoid(x) + 1e-9))
    L0 = -0.5 * null_rss
    leak_L, sens_L = fits["leak"][0]["loglik"], sens[0]["loglik"]
    healthy = lg(g * (p.noise_ratio0 - noise)) + lg(g * (p.stuck0 - stuck))
    scores = {
        "normal": L0 + healthy,
        "abrupt_leak": leak_L + lg(g * (p.growth_ratio0 - growth)) + lg(g * (p.pattern_corr0 - corr)),
        "incipient_leak": leak_L + lg(g * (growth - p.growth_ratio0)) + lg(g * (p.pattern_corr0 - corr)),
        "demand_anomaly": fits["demand_anomaly"][0]["loglik"] + lg(g * (corr - p.pattern_corr0)),
        "valve_disturbance": fits["valve_disturbance"][0]["loglik"],
        "pump_disturbance": fits["pump_disturbance"][0]["loglik"],
        "sensor_bias": sens_L + lg(g * (p.drift_slope0 - slope) / p.drift_slope0) + healthy,
        "sensor_drift": sens_L + lg(g * (slope - p.drift_slope0) / p.drift_slope0) + healthy,
        # noise / stuck faults need not shift the median: sensor-health evidence decides
        "sensor_noise": max(L0, sens_L) + lg(g * (noise - p.noise_ratio0)) + lg(g * (p.stuck0 - stuck)),
        "sensor_stuck": max(L0, sens_L) + lg(g * (stuck - p.stuck0)),
    }
    s = np.array([scores[c] for c in C.CLASSES])
    prob = np.exp(s - s.max()); prob /= prob.sum()
    return {
        "scores": dict(zip(C.CLASSES, prob.round(6).tolist())),
        "log_scores": {k: round(float(v), 3) for k, v in scores.items()},
        "localization": {
            "leak": fits["leak"][:5], "valve_disturbance": fits["valve_disturbance"][:5],
            "demand_anomaly": fits["demand_anomaly"][:5], "pump_disturbance": fits["pump_disturbance"][:1],
            "sensor": sens},
        "indicators": {"growth_ratio": float(growth), "pattern_corr": float(corr), "lead_sensor": lead,
                       "lead_sensor_slope_m_per_day": float(slope), "noise_ratio_max": float(noise),
                       "stuck_max": float(stuck), "pressure_sigma_m": float(sp),
                       "null_misfit": float(null_rss)},
        "params": asdict(p),
    }


def predict_matrix(df, params: PhysicsParams = PhysicsParams()) -> np.ndarray:
    """Physics scores for a feature dataframe (rows) -> (n, n_classes)."""
    rows = df.to_dict("records")
    return np.array([[diagnose(r, params)["scores"][c] for c in C.CLASSES] for r in rows])


def predicted_location(diag: dict, cls: str) -> list[str]:
    loc = diag["localization"]
    key = "leak" if cls in C.LEAK_CLASSES else ("sensor" if cls in C.SENSOR_CLASSES else cls)
    return [d["location"] for d in loc.get(key, [])]


def fitted_params(cls: str, scale: float) -> dict:
    """Map a fitted signature scale back to physical event parameters (approximate)."""
    ref = severity_params(_sim_class("leak" if cls in C.LEAK_CLASSES else cls), REF_SEVERITY)
    a = float(np.clip(scale, 0.05, 4.0))
    if cls in C.LEAK_CLASSES:          # leak flow ~ area ~ d^2
        return {"diameter_m": ref["diameter_m"] * np.sqrt(a)}
    if cls == "valve_disturbance":
        return {"headloss_m": ref["headloss_m"] * a}
    if cls == "demand_anomaly":
        return {"multiplier": 1 + (ref["multiplier"] - 1) * a}
    if cls == "pump_disturbance":
        return {"speed": float(np.clip(1 - (1 - ref["speed"]) * a, 0.5, 0.99))}
    return {}


def self_check():
    nom = nominal()
    _, f = extract(_telemetry_from_run(nom))
    d = diagnose(f)
    assert max(d["scores"], key=d["scores"].get) == "normal", d["scores"]
    return d
