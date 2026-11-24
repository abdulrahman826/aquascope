"""Verification measurement for CONFIDENT_BUT_UNVERIFIED diagnoses (fix for the M4 confirmation gap).

The existing active investigation only runs when a competing physical explanation has >= 5 % probability, so a
confident diagnosis could never be confirmed by measurement. Here the confident hypothesis H1 is tested against the
strongest PHYSICALLY DIFFERENT alternative H0 (however unlikely the model thinks it is), with EQUAL PRIORS: the reading
alone must discriminate. Predictions, separation score and the virtual reading reuse the existing investigation
module (read-only); its MIN_SEPARATION applies.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

from .. import config as C
from ..features import residuals
from ..investigation import MIN_SEPARATION, _predict, _start_estimate, _state_key, physical_states
from ..physics import REF_SEVERITY, fitted_params
from ..simulation import severity_params

K_STATES = 6      # leading physical states checked against every confirming reading


def calibrated_states(sc: dict, diag: dict) -> list[dict]:
    """Physical states of the diagnosis; pump hypotheses use the telemetry-calibrated speed instead of the physics fit
    (the physics fit under-estimates pump speed loss - M3/M4 finding)."""
    states = physical_states(diag)
    if any(st["lead"]["event_class"] == "pump_disturbance" for st in states):
        from .rank import estimate_pump_speed
        sp, _ = estimate_pump_speed(sc["telemetry"], _start_estimate(diag))
        if sp is not None:
            ref = severity_params("pump_disturbance", REF_SEVERITY)["speed"]
            for st in states:
                if st["lead"]["event_class"] == "pump_disturbance":
                    st["lead"] = dict(st["lead"], scale=max((1 - sp) / (1 - ref), 0.05))
    return states


def kway_posterior(sc: dict, diag: dict, node: str, reading: float, winner: tuple) -> dict:
    """Equal-prior posterior of the confirmed state among the K leading physical states, given the reading at `node`.
    A neighbouring explanation that predicts the same reading splits the probability and blocks confirmation."""
    R = residuals(sc["telemetry"])
    start, ref_level = _start_estimate(diag), diag["features"]["ref_residual_level"]
    ll = {}
    for st in calibrated_states(sc, diag)[:K_STATES]:
        mu, sd = _predict(st, sc, R, start, ref_level)
        ll[st["key"]] = float(norm.logpdf(reading, mu[node], sd[node]))
    if winner not in ll:
        return {"n_states": len(ll), "winner_in_states": False, "posterior": 0.0}
    v = np.array(list(ll.values()))
    post = np.exp(v - v.max()); post /= post.sum()
    keys = list(ll)
    return {"n_states": len(ll), "winner_in_states": True, "posterior": float(post[keys.index(winner)]),
            "top_state_is_winner": keys[int(np.argmax(post))] == winner}


def verification_measurement(sc: dict, diag: dict) -> dict:
    states = calibrated_states(sc, diag)
    base = {"scenario_id": sc["scenario_id"], "method": "verification measurement, equal priors (pressure separation)",
            "min_separation": MIN_SEPARATION, "recommended_node": None, "hypotheses": [], "candidates": []}
    if len(states) < 2:
        return base | {"explanation": "no physically different alternative to test against"}
    h1, h0 = states[0], states[1]
    R = residuals(sc["telemetry"])
    start, ref_level = _start_estimate(diag), diag["features"]["ref_residual_level"]
    mu1, sd1 = _predict(h1, sc, R, start, ref_level)
    mu0, sd0 = _predict(h0, sc, R, start, ref_level)
    cands = []
    for n in C.JUNCTIONS:
        s = float(np.sqrt((sd1[n] ** 2 + sd0[n] ** 2) / 2))
        S = abs(mu1[n] - mu0[n]) / s
        cands.append({"node": n, "monitored": n in C.PRESSURE_SENSORS, "predicted_h1_m": round(mu1[n], 3),
                      "predicted_h2_m": round(mu0[n], 3), "difference_m": round(mu1[n] - mu0[n], 3),
                      "uncertainty_m": round(s, 3), "separation": round(S, 2),
                      "p_correct_single_reading": round(float(norm.cdf(S / 2)), 3)})
    cands.sort(key=lambda c: -c["separation"])
    hyps = [{"rank": i + 1, "description": h["description"], "probability": 0.5,          # equal priors
             "model_probability": round(float(h["probability"]), 4), "event_class": h["lead"]["event_class"],
             "location": h["lead"]["location"],
             "fitted_parameters": {k: round(float(v), 4) for k, v in
                                   fitted_params(h["lead"]["event_class"], h["lead"]["scale"]).items()}}
            for i, h in enumerate((h1, h0))]
    best = cands[0]
    if best["separation"] < MIN_SEPARATION:
        return base | {"hypotheses": hyps, "candidates": cands,
                       "explanation": f"no junction separates '{h1['description']}' from '{h0['description']}' by "
                                      f">= {MIN_SEPARATION} sigma (best {best['node']}: {best['separation']:.2f})"}
    return base | {"recommended_node": best["node"], "hypotheses": hyps, "candidates": cands,
                   "explanation": f"verify '{h1['description']}' against '{h0['description']}' with a reading at "
                                  f"junction {best['node']} ({best['separation']:.1f} sigma separation)"}
