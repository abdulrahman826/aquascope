"""Active diagnostic investigation: where should one extra pressure reading be taken?

1. Expand the diagnosis into concrete physical hypotheses (class + location + fitted magnitude).
2. Merge hypotheses that imply the same physical state (e.g. bias vs drift at the same sensor:
   a single spot reading cannot tell them apart).
3. For the two leading physical states, predict the TRUE pressure at every junction during the
   measurement window (last 2 h of the record):
     - monitored node whose sensor is healthy under the hypothesis -> its own recent reading;
     - otherwise -> nominal EPANET model with the hypothesised event injected (re-simulated with
       the fitted magnitude) + current model-mismatch correction, interpolated (inverse network
       distance) from the residuals of sensors that are healthy under that hypothesis.
4. Noise-aware separation score S(n) = |mu_1(n) - mu_2(n)| / sqrt(sigma_meas^2 + sigma_model(n)^2).
   Under Gaussian errors, a nearest-prediction decision is correct with probability Phi(S/2).
   This is a transparent pressure-separation criterion, NOT full Bayesian expected information gain.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

from . import config as C, physics
from .features import N, residuals
from .network import node_distance_m
from .simulation import hypothesis_run

W = slice(N - 4, N)            # measurement window: last 4 samples (2 h)
MIN_SEPARATION = 2.0           # S >= 2  <=>  >= 84% chance a single reading picks the right state
GAUGE_NOISE_M = 0.1            # portable gauge noise (std, per reading)
MIN_ALT_PROBABILITY = 0.05     # below this the alternative is not a real competitor


def _start_estimate(diag: dict) -> int:
    t = diag["detection"]["first_alarm_time"]
    if t is None or not (C.EVENT_START_MIN <= t <= C.EVENT_START_MAX + 6 * 3600):
        t = 36 * 3600
    t = min(max(t, C.EVENT_START_MIN), C.EVENT_START_MAX)
    return int(t // C.REPORT_STEP * C.REPORT_STEP)


def candidate_hypotheses(diag: dict) -> list[dict]:
    loc = diag["physics"]["localization"]
    out = []
    for h in diag["hypotheses"]:
        cls, p = h["event_class"], h["probability"]
        key = "leak" if cls in C.LEAK_CLASSES else ("sensor" if cls in C.SENSOR_CLASSES else cls)
        fits = loc.get(key, [])[:3] if cls != "normal" else []
        if not fits:
            out.append({"event_class": cls, "location": None, "scale": 0.0, "probability": p})
            continue
        ll = np.array([f["loglik"] for f in fits])
        w = np.exp(ll - ll.max()); w /= w.sum()
        for f, wi in zip(fits, w):
            out.append({"event_class": cls, "location": f["location"], "scale": f["scale"], "probability": p * wi})
    out.sort(key=lambda d: -d["probability"])
    return out


def _state_key(h: dict) -> tuple:
    cls = h["event_class"]
    if cls == "normal":
        return ("nominal", ())
    if cls in C.SENSOR_CLASSES:
        return ("nominal", (h["location"],))
    fam = "leak" if cls in C.LEAK_CLASSES else cls
    return (fam, h["location"])


def physical_states(diag: dict) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for h in candidate_hypotheses(diag):
        k = _state_key(h)
        g = groups.setdefault(k, {"key": k, "members": [], "probability": 0.0})
        g["members"].append(h)
        g["probability"] += h["probability"]
    states = sorted(groups.values(), key=lambda g: -g["probability"])
    for g in states:
        g["lead"] = max(g["members"], key=lambda h: h["probability"])
        g["description"] = _describe(g["lead"], sorted({m["event_class"] for m in g["members"]}))
    return states


def _describe(h: dict, names: list[str]) -> str:
    from .diagnosis import LABELS
    if len(names) > 1 and set(names) <= C.SENSOR_CLASSES:
        return f"Faulty sensor {h['location']} ({' / '.join(n.split('_')[1] for n in names)})"
    lab = " / ".join(LABELS[n] for n in names)
    if h["location"] is None:
        return lab
    cls = h["event_class"]
    key = "leak" if cls in C.LEAK_CLASSES else ("sensor" if cls in C.SENSOR_CLASSES else cls)
    where = {"sensor": "sensor", "leak": "pipe", "valve_disturbance": "pipe", "demand_anomaly": "node",
             "pump_disturbance": "pump"}
    return f"{lab} at {where.get(key, '')} {h['location']}"


def _model_sigma(ref_level: float, monitored: bool) -> float:
    """Model-prediction error (std, m) of the sensor-interpolated prediction. Quadratic in the
    reference-day residual level; coefficients fitted (x1.2 safety) on validation normal scenarios."""
    return 0.05 + (2.4 if monitored else 2.9) * ref_level ** 2


def _predict(state: dict, sc: dict, R: dict, start: int, ref_level: float) -> tuple[dict, dict]:
    h = state["lead"]
    cls = h["event_class"]
    faulty = set(state["key"][1]) if state["key"][0] == "nominal" else set()
    sim_cls = "abrupt_leak" if cls in C.LEAK_CLASSES else cls
    run = hypothesis_run(sim_cls, h["location"], start, physics.fitted_params(cls, h["scale"]))
    model = {n: float(np.mean(np.asarray(run["pressure"][n])[W])) for n in C.JUNCTIONS}
    reading = {s: float(np.mean(R["P"][i, W])) for i, s in enumerate(C.PRESSURE_SENSORS)}
    healthy = [s for s in C.PRESSURE_SENSORS if s not in faulty]
    err = {s: reading[s] - model[s] for s in healthy}   # current mismatch seen by healthy sensors under h
    meas = GAUGE_NOISE_M / np.sqrt(W.stop - W.start)
    mu, sd = {}, {}
    for n in C.JUNCTIONS:
        if n in healthy:
            mu[n] = reading[n]
            sd[n] = float(np.hypot(meas, sc["sensor_config"]["pressure_noise_std_m"] / 2))
            continue
        src = [s for s in healthy if s != n]
        d = np.array([node_distance_m(n, s) for s in src]) + 1.0
        w = (1 / d) / (1 / d).sum()
        mu[n] = model[n] + float(np.dot(w, [err[s] for s in src]))
        sd[n] = float(np.hypot(meas, _model_sigma(ref_level, n in C.PRESSURE_SENSORS)))
    return mu, sd


def recommend(sc: dict, diag: dict) -> dict:
    R = residuals(sc["telemetry"])
    states = physical_states(diag)
    base = {"scenario_id": sc["scenario_id"], "method": "noise-aware pressure separation (not Bayesian information gain)",
            "measurement_window": "last 2 h of record (4 readings averaged)", "gauge_noise_std_m": GAUGE_NOISE_M,
            "min_separation": MIN_SEPARATION, "limitations": [
                "Model-error std is fitted on simulated validation scenarios; real networks may differ.",
                "Fitted event magnitudes are approximate (signature scaling).",
                "Considers only the two leading physical explanations; if the truth is ranked lower, "
                "the reading will fit neither (flagged after measurement)."]}
    if len(states) < 2:
        return base | {"recommended_node": None, "hypotheses": [], "candidates": [],
                       "explanation": "Only one physical explanation remains; no extra measurement needed."}
    h1, h2 = states[0], states[1]
    if h2["probability"] < MIN_ALT_PROBABILITY:
        return base | {"recommended_node": None, "hypotheses": [], "candidates": [],
                       "explanation": (f"'{h1['description']}' accounts for {100 * h1['probability']:.0f}% of the probability; "
                                       f"the strongest physically different alternative has {100 * h2['probability']:.1f}%. "
                                       "Remaining ambiguity (if any) is between explanations that imply the same network "
                                       "state, which a spot pressure reading cannot separate.")}
    start = _start_estimate(diag)
    ref_level = diag["features"]["ref_residual_level"]
    mu1, sd1 = _predict(h1, sc, R, start, ref_level)
    mu2, sd2 = _predict(h2, sc, R, start, ref_level)
    cands = []
    for n in C.JUNCTIONS:
        s = float(np.sqrt((sd1[n] ** 2 + sd2[n] ** 2) / 2))
        S = abs(mu1[n] - mu2[n]) / s
        cands.append({"node": n, "monitored": n in C.PRESSURE_SENSORS,
                      "predicted_h1_m": round(mu1[n], 3), "predicted_h2_m": round(mu2[n], 3),
                      "difference_m": round(mu1[n] - mu2[n], 3), "uncertainty_m": round(s, 3),
                      "separation": round(S, 2), "p_correct_single_reading": round(float(norm.cdf(S / 2)), 3)})
    cands.sort(key=lambda c: -c["separation"])
    best = cands[0]
    hyps = [{"rank": i + 1, "description": h["description"], "probability": round(float(h["probability"]), 4),
             "event_class": h["lead"]["event_class"], "location": h["lead"]["location"],
             "fitted_parameters": {k: round(float(v), 4) for k, v in
                                   physics.fitted_params(h["lead"]["event_class"], h["lead"]["scale"]).items()}}
            for i, h in enumerate((h1, h2))]
    if best["separation"] < MIN_SEPARATION:
        return base | {"recommended_node": None, "hypotheses": hyps, "candidates": cands,
                       "explanation": (f"No reliable additional measurement location identified: best node {best['node']} "
                                       f"separates the hypotheses by only {best['separation']:.1f} sigma (< {MIN_SEPARATION}). "
                                       "The competing explanations imply nearly the same pressures everywhere, relative "
                                       "to measurement and model uncertainty.")}
    kind = ("co-located verification gauge next to the existing sensor" if best["monitored"]
            else "portable gauge at an unmonitored junction")
    higher = "first" if best["difference_m"] > 0 else "second"
    expl = (f"Measure pressure at junction {best['node']} ({kind}). Under '{h1['description']}' the expected reading is "
            f"{best['predicted_h1_m']:.2f} m; under '{h2['description']}' it is {best['predicted_h2_m']:.2f} m. "
            f"The {abs(best['difference_m']):.2f} m gap is {best['separation']:.1f}x the combined measurement/model "
            f"uncertainty ({best['uncertainty_m']:.2f} m), so one reading picks the right explanation with "
            f"~{100 * best['p_correct_single_reading']:.0f}% probability (Gaussian assumption); "
            f"the {higher} hypothesis predicts the higher value.")
    return base | {"recommended_node": best["node"], "hypotheses": hyps, "candidates": cands, "explanation": expl}


def simulate_measurement(sc: dict, rec: dict, node: str) -> dict:
    """Take the virtual field reading from the hidden simulated state and update the two hypotheses."""
    cand = next((c for c in rec["candidates"] if c["node"] == node), None)
    if cand is None:
        raise ValueError(f"node '{node}' is not an eligible measurement location")
    rng = np.random.default_rng([sc["seed"], int(node), 404])
    true = float(np.mean(np.asarray(sc["hidden_state"]["pressure"][node], dtype=float)[W]))
    reading = true + float(rng.normal(0, GAUGE_NOISE_M / np.sqrt(W.stop - W.start)))
    h1, h2 = rec["hypotheses"]
    s = cand["uncertainty_m"]
    l1 = norm.logpdf(reading, cand["predicted_h1_m"], s) + np.log(max(h1["probability"], 1e-9))
    l2 = norm.logpdf(reading, cand["predicted_h2_m"], s) + np.log(max(h2["probability"], 1e-9))
    p1 = float(1 / (1 + np.exp(np.clip(l2 - l1, -700, 700))))
    winner = h1 if p1 >= 0.5 else h2
    z1, z2 = (reading - cand["predicted_h1_m"]) / s, (reading - cand["predicted_h2_m"]) / s
    consistent = min(abs(z1), abs(z2)) <= 3.0
    return {"node": node, "reading_m": round(reading, 3), "predicted_h1_m": cand["predicted_h1_m"],
            "predicted_h2_m": cand["predicted_h2_m"],
            "posterior": [{"description": h1["description"], "probability": round(p1, 4)},
                          {"description": h2["description"], "probability": round(1 - p1, 4)}],
            "resolved_to": winner["description"], "resolved_event_class": winner["event_class"],
            "resolved_location": winner["location"],
            "z_scores": [round(float(z1), 2), round(float(z2), 2)], "consistent_with_a_hypothesis": bool(consistent),
            "warning": None if consistent else ("Reading is more than 3 sigma from BOTH predictions: neither leading "
                                                "explanation fits; treat the diagnosis as unresolved and consider "
                                                "lower-ranked hypotheses."),
            "note": "Posterior over the two leading explanations only (priors renormalised); "
                    "reading drawn from the hidden simulated network state plus gauge noise."}
