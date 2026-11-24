"""Simulation-based ranking of candidate actions (M3).

For a confirmed diagnosis, the fault is reconstructed on the NOMINAL model (what an operator would have) and every
simulation-supported candidate - eligible or not - is simulated with M1 and evaluated with M1 metrics.

Eligibility (hard, never traded off against benefit):
  unlocked (M2 confirmation + confirmed location) AND not screened out (M2 structural pre-screen)
  AND passed every configured simulation constraint (M1 metrics).
Among eligible interventions, ranking is lexicographic:
  1. primary benefit vs no intervention = removal of the fault's direct physical mechanism: leak volume avoided
     (leaks), excess head loss removed on the throttled pipe (valve), pump head deficit removed vs the healthy curve
     (pump). Reference laws are fitted on the no-fault nominal run;
  2. smaller demand deficit;  3. larger minimum pressure margin above the service pressure.
An intervention is recommended only if its benefit exceeds the configured minimum; otherwise no intervention is.
"""
from __future__ import annotations

import warnings
from typing import Optional

import numpy as np

from .. import config as C
from ..investigation import _start_estimate
from ..network import topology
from ..physics import REF_SEVERITY, fitted_params
from ..simulation import severity_params
from . import config as R
from .actions import ActionCandidate, generate
from .confirmation import ConfirmedDiagnosis
from .metrics import evaluate
from .simulate import Fault, Intervention, run

# classes with a simulated corrective action (demand anomalies and sensor faults have none)
HYDRAULIC_CLASSES = C.LEAK_CLASSES | {"valve_disturbance", "pump_disturbance"}


def _pump_on_flow(flow: np.ndarray, time: np.ndarray, lo: int, hi: int) -> Optional[float]:
    m = (time >= lo) & (time < hi) & (flow > 1.0)
    return float(np.median(flow[m])) if m.sum() >= 4 else None


def estimate_pump_speed(telemetry: dict, start: int) -> tuple[Optional[float], str]:
    """Calibrate the pump speed of a confirmed pump fault from OBSERVED telemetry (flow sensor on pump 9): bisection on
    speed so that the simulated day-3 median pump-on flow (nominal model) matches the observed one. Replaces the physics
    signature fit, which under-estimated the speed loss (M3/M4 finding)."""
    t = np.asarray(telemetry["time"], dtype=int)
    q_obs = _pump_on_flow(np.asarray(telemetry["flow"][C.PUMP_ID], dtype=float), t, 2 * C.DAY, 3 * C.DAY)
    if q_obs is None:
        return None, "pump speed not calibrated: too few pump-on samples on day 3"
    def q_sim(sp):
        # trial speeds near the 0.5 search bound can exceed the pump curve; EPANET then warns "Pumps cannot deliver
        # enough flow or head". Expected for bisection probes only, so that single message is silenced here.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*Pumps cannot deliver enough flow or head.*")
            r = run(Fault("pump_disturbance", C.PUMP_ID, start, (("speed", sp),)), mismatch=0.0, seed=0)
        return _pump_on_flow(r["flow"][C.PUMP_ID], r["time"], 2 * C.DAY, 3 * C.DAY) or 0.0
    lo, hi = 0.5, 1.0
    if q_obs >= q_sim(hi):
        return 1.0, f"pump speed calibrated to 1.0 (observed pump-on flow {q_obs:.1f} L/s >= nominal): no speed loss"
    for _ in range(9):                        # pump-on flow increases with speed on Net1
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if q_sim(mid) < q_obs else (lo, mid)
    sp = round((lo + hi) / 2, 3)
    return sp, f"pump speed calibrated to {sp} from observed day-3 pump-on flow {q_obs:.1f} L/s (9-step bisection)"


def hypothesis_fault(cd: ConfirmedDiagnosis, diag: Optional[dict] = None,
                     telemetry: Optional[dict] = None) -> tuple[Optional[Fault], list[str]]:
    """Fault to simulate on the nominal model + notes on how its magnitude/start were obtained."""
    if cd.event_class not in HYDRAULIC_CLASSES:
        return None, []
    sim_cls = "abrupt_leak" if cd.event_class in C.LEAK_CLASSES else cd.event_class   # leak modelled at fitted size
    notes = []
    if cd.parameters:
        params = dict(cd.parameters)
        notes.append(f"magnitude from investigation fit: {params}")
    else:
        key = "leak" if cd.event_class in C.LEAK_CLASSES else cd.event_class
        fit = next((f for f in (diag or {}).get("physics", {}).get("localization", {}).get(key, [])
                    if f["location"] == cd.location), None)
        if fit is not None:
            params = fitted_params(cd.event_class, fit["scale"])
            notes.append(f"magnitude from physics signature fit at {cd.location}: {params}")
        else:
            params = severity_params(sim_cls, REF_SEVERITY)
            notes.append(f"ASSUMED magnitude (no fit available): reference severity {REF_SEVERITY} -> {params}")
    if diag is not None:
        start = _start_estimate(diag)
        notes.append(f"start time {start / 3600:.1f} h from detector alarm (clipped to 30-44 h)")
    else:
        start = R.DEFAULT_START_TIME
        notes.append(f"start time {start / 3600:.1f} h assumed (no diagnosis provided)")
    if sim_cls == "pump_disturbance" and telemetry is not None:
        sp, note = estimate_pump_speed(telemetry, start)
        notes.append(note)
        if sp is not None:
            params = {"speed": min(sp, 0.999)}
    keep = {"abrupt_leak": ("diameter_m",), "valve_disturbance": ("headloss_m",), "pump_disturbance": ("speed",),
            }[sim_cls]
    return Fault(sim_cls, cd.location, start, tuple(sorted((k, float(params[k])) for k in keep))), notes


def _deviation(run_: dict, ref: dict, lo: int) -> float:
    """Mean |P - no-fault P| over junctions (reported only: tank level and pump phase carry a fault's history, so
    a repaired network need not move closer to the no-fault run within 24 h - measured in M1)."""
    m = run_["time"] >= lo
    return float(np.mean([np.abs(run_["pressure"][j][m] - ref["pressure"][j][m]).mean() for j in C.JUNCTIONS]))


_ELEV = {n["id"]: n["elevation_m"] for n in topology()["nodes"]}
_ENDS = {l["id"]: (l["from"], l["to"]) for l in topology()["links"]}


def _pipe_excess_headloss(run_: dict, ref: dict, pipe: str, lo: int) -> float:
    """Median head loss on `pipe` in excess of its Hazen-Williams law k*|q|^1.852, k fitted on the no-fault run."""
    a, b = _ENDS[pipe]
    hl = lambda r: (r["pressure"][a] + _ELEV[a]) - (r["pressure"][b] + _ELEV[b])
    q = lambda r: np.abs(r["flow"][pipe])
    mr = q(ref) > 1.0
    k = np.median(hl(ref)[mr] / q(ref)[mr] ** 1.852)
    m = (run_["time"] >= lo) & (q(run_) > 1.0)
    return float(np.median(hl(run_)[m] - k * q(run_)[m] ** 1.852)) if m.any() else 0.0


def _pump_head_deficit(run_: dict, ref: dict, lo: int) -> float:
    """Median shortfall of pump head below the healthy head-flow curve h = A - B q^2 fitted on the no-fault run."""
    res = next(n for n in topology()["nodes"] if n["type"] == "reservoir")
    a, b = _ENDS[C.PUMP_ID]
    head = lambda r: r["pressure"][b] + _ELEV[b] - res["elevation_m"] if b in r["pressure"] else None
    on = lambda r: r["flow"][C.PUMP_ID] > 1.0
    slope, icpt = np.polyfit(ref["flow"][C.PUMP_ID][on(ref)] ** 2, head(ref)[on(ref)], 1)
    m = (run_["time"] >= lo) & on(run_)
    q = run_["flow"][C.PUMP_ID][m]
    return float(np.median((icpt + slope * q ** 2) - head(run_)[m])) if m.any() else 0.0


def _mechanism(fault: Fault, run_: dict, ref: dict, lo: int) -> float:
    if fault.event_class == "valve_disturbance":
        return _pipe_excess_headloss(run_, ref, fault.location, lo)
    return _pump_head_deficit(run_, ref, lo)


def rank(cd: ConfirmedDiagnosis, diag: Optional[dict] = None, generated: Optional[dict] = None,
         telemetry: Optional[dict] = None) -> dict:
    gen = generated or generate(cd)
    objs: list[ActionCandidate] = gen["_objects"]
    base = {"confirmation": gen["confirmation"], "candidates": gen["candidates"], "disclaimer": gen["disclaimer"],
            "model": "nominal Net1 model (no mismatch), pressure-driven", "ranked": [], "notes": []}
    if cd.status == "INSUFFICIENT_EVIDENCE":
        return base | {"recommendation": {"kind": "investigate_first", "target": None,
                                          "reason": f"insufficient evidence: {cd.reason}"}}
    fault, notes = hypothesis_fault(cd, diag, telemetry)
    base["notes"] = notes
    if fault is None:
        what = "no fault" if cd.event_class == "normal" else f"{cd.event_class} has no hydraulic corrective action in the model"
        return base | {"recommendation": {"kind": "none", "target": None,
                                          "reason": f"No hydraulic intervention: {what}. See non-hydraulic tasks."}}
    kw = dict(mismatch=R.PREDICTION_MISMATCH, seed=0)
    without = run(fault, Intervention(), **kw)
    requested = run(fault, Intervention(), pressure_driven=False, **kw)["demand"]
    ref = run(Fault("normal", None, None), **kw)
    lo = R.DECISION_TIME + R.SETTLE
    dev_without = _deviation(without, ref, lo)
    leak = cd.event_class in C.LEAK_CLASSES
    metric = ("leak_volume_avoided_m3" if leak else "excess_headloss_removed_m" if fault.event_class == "valve_disturbance"
              else "pump_head_deficit_removed_m")
    mech_without = None if leak else _mechanism(fault, without, ref, lo)
    rows = []
    for c in objs:
        if not c.simulation_supported or c.category not in ("HYDRAULIC_INTERVENTION", "BASELINE"):
            continue
        try:
            w = without if c.kind == "none" else run(fault, c.to_intervention(), **kw)
        except ValueError as e:                      # e.g. restore_valve for a different pipe: not applicable
            rows.append({"id": c.id, "kind": c.kind, "target": c.target, "eligible": False,
                         "ineligible_reasons": [f"not applicable to the hypothesised fault: {e}"]})
            continue
        e = evaluate({"with": w, "without": without, "requested": requested})
        avoided = e["effect_vs_no_intervention"]["leak_volume_avoided_m3"]
        dev_red = dev_without - _deviation(w, ref, lo)
        benefit = ((avoided if avoided is not None else float("-inf")) if leak
                   else mech_without - _mechanism(fault, w, ref, lo))
        reasons = (c.lock_reasons + c.screen_reasons
                   + ([] if e["passed_configured_constraints"] else
                      [f"fails simulated constraint: {k['name']} (value {k['value']})" for k in e["constraint_checks"] if not k["passed"]]))
        rows.append({
            "id": c.id, "kind": c.kind, "target": c.target, "role": c.role, "category": c.category,
            "eligible": (c.unlocked and not c.screened_out and e["passed_configured_constraints"]),
            "ineligible_reasons": reasons, "benefit_metric": metric,
            "benefit": benefit, "leak_volume_avoided_m3": avoided, "pressure_deviation_reduction_m": dev_red,
            "demand_deficit_m3": e["after"]["demand_deficit_m3"],
            "min_pressure_margin_m": e["after"]["min_pressure_m"] - R.MIN_SERVICE_PRESSURE_M,
            "verdict": e["verdict"], "evaluation": e,
        })
    key = lambda r: (r["eligible"], r.get("benefit", float("-inf")), -r.get("demand_deficit_m3", np.inf),
                     r.get("min_pressure_margin_m", float("-inf")))
    rows.sort(key=key, reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    threshold = {"leak_volume_avoided_m3": R.MIN_LEAK_VOLUME_AVOIDED_M3,
                 "excess_headloss_removed_m": R.MIN_EXCESS_HEADLOSS_REMOVED_M,
                 "pump_head_deficit_removed_m": R.MIN_PUMP_HEAD_DEFICIT_REMOVED_M}[metric]
    best = next((r for r in rows if r["eligible"] and r["kind"] != "none"), None)
    if not cd.unlocks_hydraulic:
        rec = {"kind": "investigate_first", "target": None,
               "reason": "diagnosis is CONFIDENT_BUT_UNVERIFIED: simulated outcomes below are an informational preview; "
                         "confirm by investigation or operator before any hydraulic intervention is recommended"}
    elif best is None:
        rec = {"kind": "none", "target": None,
               "reason": "no candidate intervention is eligible (locked, screened out, or failing simulated constraints)"}
    elif best["benefit"] <= threshold:
        rec = {"kind": "none", "target": None,
               "reason": f"best eligible intervention {best['id']} predicts benefit {best['benefit']:.3f} "
                         f"<= configured minimum {threshold} ({best['benefit_metric']})"}
    else:
        rec = {"kind": best["kind"], "target": best["target"], "id": best["id"], "verdict": best["verdict"],
               "reason": f"highest predicted {best['benefit_metric']} ({best['benefit']:.2f}) among eligible "
                         "interventions that passed all configured simulation constraints"}
    return base | {"hypothesis_fault": {"event_class": fault.event_class, "location": fault.location,
                                        "start_time_h": fault.start_time / 3600, "parameters": fault.p},
                   "benefit_threshold": threshold, "ranked": rows, "recommendation": rec,
                   "preview_only": not cd.unlocks_hydraulic}
