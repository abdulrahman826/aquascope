"""M4: reproducible remediation dataset + evaluation against baselines on the hidden TRUE network.

Scenarios: the existing AquaScope dataset split (read-only; leak/valve/demand test locations unseen in training).
For every scenario the existing engine diagnoses; then, on the true network (actual fault + mismatch):

Policies
  engine_pipeline            diagnosis -> (investigation if not confident) -> confirmation -> rank -> action
  engine_oracle_confirmation true class/location confirmed by an "operator" -> rank -> action  (hydraulic truths only;
                             isolates the remediation logic from diagnosis errors)
  naive_top1                 act on the unconfirmed top-1 diagnosis (isolate / restore), no gating, no constraint check
  random_feasible_mean       mean outcome over isolating each pipe that passes the structural pre-screen
  do_nothing                 no intervention
  oracle                     correct action for the true fault if it passes the constraints on the true network

Outputs (data/remediation/): <split>_rows.csv  - one labelled row per (scenario, policy/candidate, action) with
predicted (nominal) and actual (true) outcomes;  <split>_evaluation.json - aggregated metrics with sample sizes.
No learned model is trained here (M4 decision: exhaustive simulation-based ranking; dataset kept for future use).

  python -m aquascope.remediation.evaluate val    # development split
  python -m aquascope.remediation.evaluate test   # held-out split (run once, design frozen)
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .. import config as C
from ..dataset import load
from ..diagnosis import diagnose_scenario
from ..evaluation import _truth_key
from ..investigation import _state_key, recommend, simulate_measurement
from ..simulation import ScenarioSpec, SimulationError, run_scenario
from . import config as R
from . import confirmation as K
from .actions import prescreen_isolation
from .metrics import evaluate
from .rank import _mechanism, rank
from .simulate import Fault, Intervention, fault_from_spec, run
from .verification import kway_posterior, verification_measurement

OUT_DIR = C.DATA_DIR / "remediation"
HYDRAULIC_TRUTHS = C.LEAK_CLASSES | {"valve_disturbance", "pump_disturbance"}
RESOLVED_LEAK_FRACTION = 0.05      # leak counted as stopped if after-window flow < 5 % of no-action flow
RESOLVED_MECHANISM_M = 0.5         # valve/pump counted as resolved if < 0.5 m excess head loss / head deficit remains
FEASIBLE_ISOLATIONS = [p for p in C.PIPES if not prescreen_isolation(p)]


def _spec(row) -> ScenarioSpec:
    return ScenarioSpec(event_class=row["label"], seed=int(row["seed"]), severity=float(row["severity"]),
                        mismatch=float(row["mismatch"]),
                        location=None if pd.isna(row["location"]) else str(row["location"]))


class TrueNetwork:
    """Actual outcomes of interventions on the scenario's true network (cached per intervention)."""

    def __init__(self, spec: ScenarioSpec):
        self.fault = fault_from_spec(spec)
        self.kw = dict(mismatch=spec.mismatch, seed=spec.seed)
        self.without = run(self.fault, Intervention(), **self.kw)
        self.requested = run(self.fault, Intervention(), pressure_driven=False, **self.kw)["demand"]
        self.hydraulic = self.fault.event_class in HYDRAULIC_TRUTHS
        self.leak = self.fault.event_class in C.LEAK_CLASSES
        self.ref = run(Fault("normal", None, None), **self.kw) if self.hydraulic and not self.leak else None
        self.lo = R.DECISION_TIME + R.SETTLE
        self._cache = {}

    def outcome(self, iv: Intervention) -> dict:
        key = (iv.kind, iv.target)
        if key in self._cache:
            return self._cache[key]
        applicable = True
        if iv.kind == "none":
            w = self.without
        else:
            try:
                w = run(self.fault, iv, **self.kw)
            except ValueError:           # e.g. restore_valve when the true fault is not a throttled valve there
                applicable, w = False, self.without
        e = evaluate({"with": w, "without": self.without, "requested": self.requested})
        a, wo = e["after"], e["after_without_intervention"]
        benefit, resolved = None, None
        if self.leak:
            benefit = e["effect_vs_no_intervention"]["leak_volume_avoided_m3"]
            resolved = bool(a["negative_pressure_samples"] == 0 and
                            a["leak_flow_mean_L_s"] < RESOLVED_LEAK_FRACTION * max(wo["leak_flow_mean_L_s"], 1e-9))
        elif self.hydraulic:
            before = _mechanism(self.fault, self.without, self.ref, self.lo)
            after = _mechanism(self.fault, w, self.ref, self.lo)
            benefit, resolved = before - after, bool(abs(after) < RESOLVED_MECHANISM_M)
        # attempted counts even if the action has no hydraulic effect on the true network (e.g. "re-open" a valve that
        # is not throttled): it is still an unnecessary field intervention
        acted = iv.kind != "none"
        out = {"kind": iv.kind, "target": iv.target, "applicable": applicable, "acted": acted,
               "actual_passed_constraints": e["passed_configured_constraints"],
               "actual_benefit": benefit, "resolved": resolved,
               "ineffective_or_harmful": bool(acted and (not self.hydraulic or not resolved
                                                         or not e["passed_configured_constraints"])),
               "actual_min_pressure_m": a["min_pressure_m"], "actual_delivered_fraction": a["delivered_fraction"],
               "actual_leak_flow_L_s": a["leak_flow_mean_L_s"], "actual_tank_empty_samples": a["tank_empty_samples"],
               "actual_negative_pressure_samples": a["negative_pressure_samples"]}
        self._cache[key] = out
        return out


def _measure(sc: dict, d: dict, rec: dict, route: str) -> tuple[K.ConfirmedDiagnosis, dict]:
    """Take the recommended reading; return the confirmation and raw statistics (for offline policy selection)."""
    m = simulate_measurement(sc, rec, rec["recommended_node"])
    wi = max(range(2), key=lambda i: m["posterior"][i]["probability"])
    cand = next(c for c in rec["candidates"] if c["node"] == m["node"])
    truth = _truth_key(sc["ground_truth"])
    raw = {"meas_route": route, "meas_node": m["node"], "meas_separation": cand["separation"],
           "meas_posterior": m["posterior"][wi]["probability"], "meas_abs_z_winner": abs(m["z_scores"][wi]),
           "meas_consistent": m["consistent_with_a_hypothesis"],
           "meas_state_correct": _state_key({"event_class": m["resolved_event_class"],
                                             "location": m["resolved_location"]}) == truth,
           "meas_truth_in_pair": truth in [_state_key(h) for h in rec["hypotheses"]]}
    kw = kway_posterior(sc, d, m["node"], m["reading_m"],
                        _state_key({"event_class": m["resolved_event_class"], "location": m["resolved_location"]}))
    raw.update(meas_kway_posterior=kw["posterior"], meas_kway_n_states=kw["n_states"])
    return K.from_investigation(d, rec, m, kw["posterior"]), raw


def _pipeline_confirmation(sc: dict, d: dict) -> tuple[K.ConfirmedDiagnosis, str, dict]:
    cd = K.from_diagnosis(d)
    rec = recommend(sc, d)
    if rec["recommended_node"]:
        ci, raw = _measure(sc, d, rec, "investigation")
        if ci.status == "CONFIRMED_BY_INVESTIGATION":
            return ci, "investigation_confirmed", raw
        return cd, "investigation_not_confirmed", raw
    if cd.status != "CONFIDENT_BUT_UNVERIFIED":
        return cd, "no_reliable_measurement" if rec["hypotheses"] else "no_competing_hypothesis", {}
    ver = verification_measurement(sc, d)            # fix for the confirmation gap of confident diagnoses
    if not ver["recommended_node"]:
        return cd, "verification_no_reliable_location", {}
    ci, raw = _measure(sc, d, ver, "verification")
    if ci.status == "CONFIRMED_BY_INVESTIGATION":
        return ci, "verification_confirmed", raw
    return cd, "verification_not_confirmed", raw


def _naive(d: dict) -> Intervention:
    lead = d["hypotheses"][0]
    loc = lead["candidate_locations"][0] if lead["candidate_locations"] else None
    if lead["event_class"] in C.LEAK_CLASSES and loc in C.PIPES:
        return Intervention("isolate_pipe", loc)
    if lead["event_class"] == "valve_disturbance" and loc in C.PIPES:
        return Intervention("restore_valve", loc)
    if lead["event_class"] == "pump_disturbance":
        return Intervention("restore_pump", C.PUMP_ID)
    return Intervention()


def _oracle(tn: TrueNetwork) -> Intervention:
    f = tn.fault
    iv = (Intervention("isolate_pipe", f.location) if tn.leak else
          Intervention("restore_valve", f.location) if f.event_class == "valve_disturbance" else
          Intervention("restore_pump", C.PUMP_ID) if f.event_class == "pump_disturbance" else Intervention())
    if iv.kind != "none" and not tn.outcome(iv)["actual_passed_constraints"]:
        return Intervention()
    return iv


def _rec_iv(r: dict) -> Intervention:
    k = r["recommendation"]["kind"]
    return Intervention(k, r["recommendation"]["target"]) if k in ("isolate_pipe", "restore_valve", "restore_pump") else Intervention()


def process(row: dict) -> dict:
    t0 = time.perf_counter()
    spec = _spec(row)
    base = {"scenario_id": row["scenario_id"], "true_class": row["label"], "true_location": spec.location,
            "mismatch": spec.mismatch, "seed": spec.seed, "severity": spec.severity}
    try:
        sc = run_scenario(spec)
        d = diagnose_scenario(sc)
        tn = TrueNetwork(spec)
        cd, path, raw = _pipeline_confirmation(sc, d)
        rows, summary = [], dict(base, **raw, diagnosis_status=d["status"], diagnosis_top=d["hypotheses"][0]["event_class"],
                                 confirmation_status=cd.status, confirmation_path=path,
                                 confirmed_class=cd.event_class, confirmed_location=cd.location)

        def add(policy, iv, pred=None, extra=None):
            o = tn.outcome(iv)
            rows.append(dict(base, policy=policy, **o, **(pred or {}), **(extra or {})))
            return o

        def ranked_policy(name, r):
            rec = r["recommendation"]
            summary[f"{name}_recommendation"] = rec["kind"]
            summary[f"{name}_reason"] = rec["reason"]
            chosen = _rec_iv(r)
            for cand in r["ranked"]:            # labelled candidate rows: predicted (nominal) vs actual (true)
                if "evaluation" not in cand:
                    continue
                ev = cand["evaluation"]
                add(f"{name}:candidate", Intervention(cand["kind"], cand["target"] if cand["kind"] != "none" else None),
                    {"predicted_benefit": cand["benefit"], "benefit_metric": cand["benefit_metric"],
                     "predicted_passed_constraints": ev["passed_configured_constraints"],
                     "predicted_min_pressure_m": ev["after"]["min_pressure_m"], "eligible": cand["eligible"],
                     "predicted_rank": cand["rank"]}, {"chosen": (cand["kind"], cand.get("target")) == (chosen.kind, chosen.target)})
            pred = next((c for c in r["ranked"] if (c["kind"], c["target"]) == (chosen.kind, chosen.target)
                         and chosen.kind != "none"), None)
            o = add(name, chosen, {"predicted_benefit": pred["benefit"] if pred else None,
                                   "predicted_passed_constraints": pred["evaluation"]["passed_configured_constraints"] if pred else None,
                                   "recommendation_kind": rec["kind"]})
            summary[f"{name}_constraint_prediction_miss"] = bool(pred and pred["evaluation"]["passed_configured_constraints"]
                                                                 and not o["actual_passed_constraints"])

        ranked_policy("engine_pipeline", rank(cd, d, telemetry=sc["telemetry"]))
        if tn.hydraulic:
            ranked_policy("engine_oracle_confirmation",
                          rank(K.operator_confirmed(spec.event_class, spec.location, "oracle", "ground truth"), d,
                               telemetry=sc["telemetry"]))
        add("naive_top1", _naive(d))
        add("do_nothing", Intervention())
        add("oracle", _oracle(tn))
        if tn.hydraulic:
            outs = [tn.outcome(Intervention("isolate_pipe", p)) for p in FEASIBLE_ISOLATIONS]
            rows.append(dict(base, policy="random_feasible_mean", kind="isolate_pipe", target="mean_over_feasible",
                             applicable=True, acted=True,
                             actual_passed_constraints=float(np.mean([o["actual_passed_constraints"] for o in outs])),
                             actual_benefit=float(np.mean([o["actual_benefit"] if o["actual_benefit"] is not None else 0.0 for o in outs])),
                             resolved=float(np.mean([bool(o["resolved"]) for o in outs])),
                             ineffective_or_harmful=float(np.mean([o["ineffective_or_harmful"] for o in outs]))))
        summary["runtime_s"] = round(time.perf_counter() - t0, 2)
        return {"summary": summary, "rows": rows, "error": None}
    except (SimulationError, ValueError) as e:     # recorded, never silently dropped
        return {"summary": dict(base, error=f"{type(e).__name__}: {e}"), "rows": [], "error": str(e)}


# ---------------------------------------------------------------- aggregation
POLICIES = ["engine_pipeline", "engine_oracle_confirmation", "naive_top1", "random_feasible_mean", "do_nothing", "oracle"]


def _agg(df: pd.DataFrame, oracle_benefit: pd.Series) -> dict:
    out = {}
    for p in POLICIES:
        g = df[df.policy == p]
        if g.empty:
            continue
        acted = g.acted.astype(float)
        ob = g.scenario_id.map(oracle_benefit)
        ok = ob.notna() & (ob > 0) & g.actual_benefit.notna()
        regret = (1 - (g.actual_benefit[ok] / ob[ok]).clip(upper=1.0)) if ok.any() else pd.Series(dtype=float)
        out[p] = {"n": int(len(g)), "intervention_rate": float(acted.mean()),
                  "resolved_rate": float(g.resolved.astype(float).mean()) if g.resolved.notna().any() else None,
                  "ineffective_or_harmful_rate": float(g.ineffective_or_harmful.astype(float).mean()),
                  "constraint_violation_rate_when_acting": (float((1 - g.actual_passed_constraints.astype(float))[acted > 0].mean())
                                                            if (acted > 0).any() else None),
                  "mean_regret_fraction": float(regret.mean()) if len(regret) else None, "n_regret": int(len(regret))}
    return out


def aggregate(summaries: list[dict], rows: list[dict]) -> dict:
    s, df = pd.DataFrame(summaries), pd.DataFrame(rows)
    hyd = s.true_class.isin(list(HYDRAULIC_TRUTHS))
    orc = df[df.policy == "oracle"].set_index("scenario_id").actual_benefit
    res = {"n_scenarios": int(len(s)), "n_errors": int(s.get("error", pd.Series(dtype=object)).notna().sum()),
           "hydraulic_fault_scenarios": {}, "non_hydraulic_scenarios": {}, "engine": {}}
    dh = df[df.scenario_id.isin(s[hyd].scenario_id)]
    res["hydraulic_fault_scenarios"]["all"] = _agg(dh, orc)
    for key, sub in [("by_class", "true_class"), ("by_mismatch", "mismatch")]:
        res["hydraulic_fault_scenarios"][key] = {str(k): _agg(dh[dh[sub] == k], orc) for k in sorted(dh[sub].unique())}
    dn = df[df.scenario_id.isin(s[~hyd].scenario_id)]
    res["non_hydraulic_scenarios"]["all"] = _agg(dn, orc)
    res["non_hydraulic_scenarios"]["by_class"] = {str(k): _agg(dn[dn.true_class == k], orc) for k in sorted(dn.true_class.unique())}
    e = res["engine"]
    e["confirmation_status_counts"] = s.confirmation_status.value_counts().to_dict()
    e["confirmation_path_counts"] = s.confirmation_path.value_counts().to_dict()
    e["pipeline_recommendation_counts_hydraulic_truth"] = s[hyd].engine_pipeline_recommendation.value_counts().to_dict()
    e["pipeline_recommendation_counts_non_hydraulic_truth"] = s[~hyd].engine_pipeline_recommendation.value_counts().to_dict()
    conf = s[s.confirmation_status.isin(R.HYDRAULIC_UNLOCK_STATUSES)]
    e["investigation_confirmations"] = {"n": int(len(conf)),
                                        "correct_class_family_and_location": int(sum(
                                            (C.CLASS_FAMILY.get(r.confirmed_class) == C.CLASS_FAMILY[r.true_class])
                                            and (r.confirmed_location == r.true_location) for r in conf.itertuples()))}
    e["oracle_confirmation_recommendation_counts_by_class"] = {
        str(k): v.engine_oracle_confirmation_recommendation.value_counts().to_dict() for k, v in s[hyd].groupby("true_class")}
    for name in ("engine_pipeline", "engine_oracle_confirmation"):
        col = f"{name}_constraint_prediction_miss"
        e[f"{name}_constraint_prediction_misses"] = int(s[col].fillna(False).astype(bool).sum()) if col in s else 0
    ch = df[(df.policy == "engine_oracle_confirmation") & df.predicted_benefit.notna() & df.actual_benefit.notna()]
    e["oracle_confirmation_prediction_error_by_class"] = {
        str(k): {"n": int(len(g)), "median_relative_error": float(((g.predicted_benefit - g.actual_benefit) / g.actual_benefit.abs().clip(lower=1e-6)).median())}
        for k, g in ch.groupby("true_class")}
    cand = df[df.policy.str.endswith(":candidate")]
    e["n_labelled_candidate_rows"] = int(len(cand))
    return res


FRESH_SEED_BASE = 1_000_001      # existing dataset uses seeds 1..2250; fresh seeds are never used elsewhere


def fresh_test_rows(per_cell: int = 20) -> list[dict]:
    """A NEW held-out set: same design as the existing test split (test locations, 3 mismatch levels, 20 per cell),
    but with seeds never used for training, validation, threshold selection or earlier evaluation."""
    from ..dataset import _group_key, location_groups
    from ..simulation import resolve, scenario_id, valid_locations
    groups, rows, seed = location_groups(), [], FRESH_SEED_BASE
    for cls in C.CLASSES:
        g = _group_key(cls)
        for mm in C.MISMATCH_LEVELS:
            for i in range(per_cell):
                rng = np.random.default_rng([seed, 99])
                locs = groups[g]["test"] if g else valid_locations(cls)
                spec = ScenarioSpec(event_class=cls, seed=seed, severity=float(rng.uniform()), mismatch=mm,
                                    location=str(locs[i % len(locs)]) if locs else None)
                rows.append({"split": "fresh_test", "scenario_id": scenario_id(resolve(spec)), "seed": seed,
                             "label": cls, "location": spec.location if spec.location else np.nan,
                             "severity": spec.severity, "mismatch": mm})
                seed += 1
    return rows


def main(split: str, workers: int = 6) -> dict:
    t0 = time.time()
    if split == "fresh_test":
        rows_in = fresh_test_rows()
    else:
        data = load()
        rows_in = data[data.split == split].to_dict("records")
    with ProcessPoolExecutor(workers) as ex:
        results = list(ex.map(process, rows_in, chunksize=4))
    summaries = [r["summary"] for r in results]
    rows = [x for r in results for x in r["rows"]]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_DIR / f"{split}_rows.csv", index=False)
    pd.DataFrame(summaries).to_csv(OUT_DIR / f"{split}_scenarios.csv", index=False)
    res = aggregate([s for s in summaries if not s.get("error")], rows)
    res.update({"split": split, "n_errors": sum(1 for s in summaries if s.get("error")),
                "errors": [s for s in summaries if s.get("error")][:20], "runtime_s": round(time.time() - t0, 1),
                "config": {"benefit_thresholds": {"leak_m3": R.MIN_LEAK_VOLUME_AVOIDED_M3,
                                                  "excess_headloss_m": R.MIN_EXCESS_HEADLOSS_REMOVED_M,
                                                  "pump_head_m": R.MIN_PUMP_HEAD_DEFICIT_REMOVED_M},
                           "confirmation_min_posterior": R.CONFIRMATION_MIN_POSTERIOR, "constraints": R.CONSTRAINTS,
                           "resolved_definitions": {"leak_fraction": RESOLVED_LEAK_FRACTION, "mechanism_m": RESOLVED_MECHANISM_M},
                           "feasible_isolations": FEASIBLE_ISOLATIONS},
                "disclaimer": "Simulated EPANET Net1 scenarios only; outcomes on the hidden true simulated network, not real-world validation."})
    (OUT_DIR / f"{split}_evaluation.json").write_text(json.dumps(res, indent=2, default=float))
    return res


if __name__ == "__main__":
    split = sys.argv[1] if len(sys.argv) > 1 else "val"
    if split not in ("val", "test", "fresh_test"):
        raise SystemExit("split must be 'val', 'test' or 'fresh_test'")
    r = main(split)
    print(json.dumps({k: r[k] for k in ("split", "n_scenarios", "n_errors", "runtime_s")}))
