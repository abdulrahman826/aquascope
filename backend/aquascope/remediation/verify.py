"""Verification of a ranked recommendation on the TRUE simulated network (M3).

Prediction (rank.py) uses the nominal model and the hypothesised fault. Verification re-runs the chosen action on
the scenario's true network: the actual injected fault and model mismatch, which are hidden from diagnosis. This
is only possible in simulation; it measures how well model-based predictions transfer under mismatch and whether
the confirmed diagnosis was right. It is NOT a real-world validation.
"""
from __future__ import annotations

from ..simulation import ScenarioSpec
from . import config as R
from .metrics import evaluate
from .simulate import Fault, Intervention, fault_from_spec, run

_KEYS = ("min_pressure_m", "delivered_fraction", "leak_flow_mean_L_s", "demand_deficit_m3", "tank_min_level_m")


def verify(ranked: dict, spec: ScenarioSpec) -> dict:
    rec = ranked["recommendation"]
    true_fault = fault_from_spec(spec)
    iv = Intervention(rec["kind"], rec["target"]) if rec["kind"] not in ("investigate_first",) else Intervention()
    if iv.kind not in ("none", "isolate_pipe", "restore_valve", "restore_pump"):
        iv = Intervention()
    kw = dict(mismatch=spec.mismatch, seed=spec.seed)
    without = run(true_fault, Intervention(), **kw)
    requested = run(true_fault, Intervention(), pressure_driven=False, **kw)["demand"]
    try:
        with_ = without if iv.kind == "none" else run(true_fault, iv, **kw)
    except ValueError as e:     # e.g. restore_valve when the true fault is not a throttled valve there
        return {"label": "SIMULATION VERIFICATION AGAINST HIDDEN TRUE NETWORK — NOT REAL-WORLD VALIDATION",
                "applicable": False, "reason": f"recommended action cannot act on the true fault: {e}",
                "true_fault": {"event_class": true_fault.event_class, "location": true_fault.location}}
    actual = evaluate({"with": with_, "without": without, "requested": requested})
    pred_row = next((r for r in ranked["ranked"] if r["id"] == rec.get("id")), None)
    comparison = {}
    if pred_row:
        p = pred_row["evaluation"]["after"]
        comparison = {k: {"predicted": p[k], "actual": actual["after"][k], "error": actual["after"][k] - p[k]} for k in _KEYS}
    predicted_pass = pred_row["evaluation"]["passed_configured_constraints"] if pred_row else None
    actual_pass = actual["passed_configured_constraints"]
    mechanism = None
    if iv.kind in ("restore_valve", "restore_pump"):
        from .rank import _mechanism            # same mechanism metric as the ranking, on the true network
        ref = run(Fault("normal", None, None), **kw)
        lo = R.DECISION_TIME + R.SETTLE
        actual_benefit = _mechanism(true_fault, without, ref, lo) - _mechanism(true_fault, with_, ref, lo)
        mechanism = {"metric": pred_row["benefit_metric"] if pred_row else None,
                     "predicted_benefit": pred_row["benefit"] if pred_row else None, "actual_benefit": actual_benefit,
                     "remaining_after_m": _mechanism(true_fault, with_, ref, lo)}
    fault_removed = None
    if iv.kind == "isolate_pipe":
        fault_removed = actual["after"]["leak_flow_mean_L_s"] < 0.05 * max(actual["after_without_intervention"]["leak_flow_mean_L_s"], 1e-9)
    return {
        "label": "SIMULATION VERIFICATION AGAINST HIDDEN TRUE NETWORK — NOT REAL-WORLD VALIDATION",
        "applicable": True, "action": {"kind": iv.kind, "target": iv.target},
        "true_fault": {"event_class": true_fault.event_class, "location": true_fault.location},
        "mismatch": spec.mismatch,
        "diagnosis_matched_truth": (ranked.get("hypothesis_fault") or {}).get("location") == true_fault.location
        if ranked.get("hypothesis_fault") else None,
        "predicted_vs_actual_after": comparison,
        "predicted_passed_constraints": predicted_pass, "actual_passed_constraints": actual_pass,
        "constraint_prediction_miss": bool(predicted_pass and not actual_pass),
        "leak_stopped": fault_removed, "mechanism_benefit": mechanism,
        "predicted_leak_volume_avoided_m3": pred_row["leak_volume_avoided_m3"] if pred_row else None,
        "actual_leak_volume_avoided_m3": actual["effect_vs_no_intervention"]["leak_volume_avoided_m3"],
        "before": actual["before"], "after": actual["after"], "after_without_intervention": actual["after_without_intervention"],
        "actual_verdict": actual["verdict"], "warnings": actual["warnings"],
        "disclaimer": f"{R.SIMULATION_LABEL}. {R.NOT_EXECUTABLE}.",
    }
