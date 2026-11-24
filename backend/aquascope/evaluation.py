"""Held-out evaluation of the three approaches + detection, localisation, latency, investigation.

All thresholds (physics params, temperatures, abstention, detector) are fixed on the validation
split before this runs. The architecture-selection rule below is fixed in code (spec §10).
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

from . import config as C, physics
from .dataset import load
from .diagnosis import EVAL_PATH, SELECTION_PATH, detect, diagnose_scenario
from .features import extract
from .investigation import recommend, simulate_measurement
from .models import APPROACHES, bundle, calibrated_probas
from .network import link_distance_m
from .simulation import ScenarioSpec, run_scenario

DETECTOR_PATH = C.ARTIFACT_DIR / "detector.json"
TARGET_FALSE_ALARMS_PER_DAY = 0.25


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    conf, pred = p.max(1), p.argmax(1)
    e, edges = 0.0, np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            e += m.mean() * abs((pred[m] == y[m]).mean() - conf[m].mean())
    return float(e)


def classification(p: np.ndarray, y: np.ndarray, threshold: float) -> dict:
    pred = p.argmax(1)
    labels = list(range(len(C.CLASSES)))
    pr, rc, f1, sup = precision_recall_fscore_support(y, pred, labels=labels, zero_division=0)
    onehot = np.eye(len(C.CLASSES))[y]
    conf = p.max(1)
    correct = pred == y
    order = np.argsort(-conf)
    cov = np.arange(1, len(y) + 1) / len(y)
    risk = 1 - np.cumsum(correct[order]) / np.arange(1, len(y) + 1)
    idx = np.unique(np.linspace(0, len(y) - 1, 21).astype(int))
    acc_m = conf >= threshold
    return {
        "n": int(len(y)), "accuracy": float(correct.mean()),
        "macro_precision": float(pr.mean()), "macro_recall": float(rc.mean()), "macro_f1": float(f1.mean()),
        "per_class": {c: {"precision": float(pr[i]), "recall": float(rc[i]), "f1": float(f1[i]), "n": int(sup[i])}
                      for i, c in enumerate(C.CLASSES)},
        "confusion_matrix": confusion_matrix(y, pred, labels=labels).tolist(),
        "brier": float(np.mean(np.sum((p - onehot) ** 2, axis=1))),
        "ece": ece(p, y),
        "risk_coverage": [{"coverage": float(cov[i]), "risk": float(risk[i])} for i in idx],
        "threshold": threshold,
        "coverage_at_threshold": float(acc_m.mean()),
        "abstention_rate": float(1 - acc_m.mean()),
        "selective_risk": float(1 - correct[acc_m].mean()) if acc_m.any() else None,
    }


def _sim(row) -> dict:
    return run_scenario(ScenarioSpec(event_class=row["label"], seed=int(row["seed"]), severity=float(row["severity"]),
                                     mismatch=float(row["mismatch"]),
                                     location=None if pd.isna(row["location"]) else str(row["location"])))


def _detect_row(args):
    row, thr = args
    sc = _sim(row)
    _, f = extract(sc["telemetry"])
    sp = physics.diagnose(f, bundle()["physics_params"])["indicators"]["pressure_sigma_m"]
    return [detect(sc["telemetry"], sp, threshold=t) | {"label": row["label"], "start": row["start_time"]}
            for t in thr]


def _strip(d):
    return {k: v for k, v in d.items() if k != "score"}


def detector_eval(df: pd.DataFrame, thresholds: list[float], workers: int) -> list[list[dict]]:
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_detect_row, [(r, thresholds) for r in df.to_dict("records")], chunksize=10))
    return [[_strip(x) for x in r] for r in res]


def tune_detector(workers: int) -> float:
    va = load().query("split == 'val' and label == 'normal'")
    grid = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0]
    res = detector_eval(va, grid, workers)
    for j, t in enumerate(grid):
        fa = np.mean([r[j]["n_alarm_episodes"] + (1 if r[j]["alarm"] and r[j]["n_alarm_episodes"] == 0 else 0)
                      for r in res]) / 2.0
        if fa <= TARGET_FALSE_ALARMS_PER_DAY:
            DETECTOR_PATH.write_text(json.dumps({"threshold": t, "val_false_alarms_per_day": fa, "n_val_normal": len(va)}))
            return t
    DETECTOR_PATH.write_text(json.dumps({"threshold": grid[-1], "note": "target not met on val"}))
    return grid[-1]


def detection_metrics(te: pd.DataFrame, thr: float, workers: int) -> dict:
    res = [r[0] for r in detector_eval(te, [thr], workers)]
    normal = [r for r in res if r["label"] == "normal"]
    events = [r for r in res if r["label"] != "normal"]
    fa = [r["n_alarm_episodes"] + (1 if r["alarm"] and r["n_alarm_episodes"] == 0 else 0) for r in normal]
    delays, detected, early = [], 0, 0
    for r in events:
        if r["first_alarm_time"] is None:
            continue
        if r["first_alarm_time"] < r["start"]:
            early += 1
            continue
        detected += 1
        delays.append((r["first_alarm_time"] - r["start"]) / 3600)
    per_class = {}
    for c in C.CLASSES[1:]:
        rc = [r for r in events if r["label"] == c]
        ok = [r for r in rc if r["first_alarm_time"] is not None and r["first_alarm_time"] >= r["start"]]
        per_class[c] = {"n": len(rc), "detection_rate": len(ok) / len(rc),
                        "median_delay_h": float(np.median([(r["first_alarm_time"] - r["start"]) / 3600 for r in ok])) if ok else None}
    return {"threshold": thr, "n_normal": len(normal), "n_events": len(events),
            "false_alarms_per_day": float(np.sum(fa) / (2.0 * len(normal))),
            "normal_scenarios_with_any_alarm": float(np.mean([r["alarm"] for r in normal])),
            "event_detection_rate": detected / len(events), "alarm_before_event_start": early / len(events),
            "median_detection_delay_h": float(np.median(delays)) if delays else None,
            "p90_detection_delay_h": float(np.percentile(delays, 90)) if delays else None,
            "per_class": per_class,
            "note": "Detection counted only if first alarm >= true start; earlier alarms counted as false/early."}


def localisation(te: pd.DataFrame) -> dict:
    lk = te[te.label.isin(list(C.LEAK_CLASSES))]
    pp = bundle()["physics_params"]
    out = {"all": [], 0.0: [], 0.05: [], 0.15: []}
    for r in lk.to_dict("records"):
        ranking = [d["location"] for d in physics.diagnose(r, pp)["localization"]["leak"]]
        t = str(r["location"])
        rec = (ranking[0] == t, t in ranking[:3], link_distance_m(ranking[0], t))
        out["all"].append(rec); out[float(r["mismatch"])].append(rec)
    summ = lambda v: {"n": len(v), "top1": float(np.mean([a for a, _, _ in v])), "top3": float(np.mean([b for _, b, _ in v])),
                      "mean_distance_m": float(np.mean([d for _, _, d in v])),
                      "median_distance_m": float(np.median([d for _, _, d in v]))} if v else None
    return {"method": "physics signature ranking over 11 candidate pipes (test pipes unseen in training)",
            "by_mismatch": {str(k): summ(v) for k, v in out.items()},
            "random_baseline_top1": 1 / len(C.LEAK_PIPES), "random_baseline_top3": 3 / len(C.LEAK_PIPES)}


def _truth_key(gt: dict):
    cls = gt["event_class"]
    if cls == "normal":
        return ("nominal", ())
    if cls in C.SENSOR_CLASSES:
        return ("nominal", (gt["location"],))
    return ("leak" if cls in C.LEAK_CLASSES else cls, gt["location"])


def _hyp_key(h: dict):
    from .investigation import _state_key
    return _state_key({"event_class": h["event_class"], "location": h["location"]})


def _investigate_row(row):
    sc = _sim(row)
    t0 = time.perf_counter()
    d = diagnose_scenario(sc)
    lat = time.perf_counter() - t0
    rec = recommend(sc, d)
    out = {"label": row["label"], "mismatch": row["mismatch"], "status": d["status"], "latency_s": lat,
           "recommended": rec["recommended_node"]}
    if not rec["hypotheses"]:
        return out
    keys = [_hyp_key(h) for h in rec["hypotheses"]]
    truth = _truth_key(sc["ground_truth"])
    out["truth_in_pair"] = truth in keys
    if not out["truth_in_pair"] or rec["recommended_node"] is None:
        return out
    ti = keys.index(truth)
    pick = lambda node: _hyp_key({"event_class": (m := simulate_measurement(sc, rec, node))["resolved_event_class"],
                                  "location": m["resolved_location"]}) == truth
    out["correct_recommended"] = pick(rec["recommended_node"])
    others = [c["node"] for c in rec["candidates"] if c["node"] != rec["recommended_node"]]
    out["correct_random_mean"] = float(np.mean([pick(n) for n in others]))
    out["prior_correct"] = ti == 0
    return out


def investigation_eval(te: pd.DataFrame, workers: int) -> tuple[dict, list]:
    with ProcessPoolExecutor(workers) as ex:
        rows = list(ex.map(_investigate_row, te.to_dict("records"), chunksize=10))
    with_rec = [r for r in rows if r.get("correct_recommended") is not None]
    no_rec = [r for r in rows if r.get("truth_in_pair") and r.get("recommended") is None]
    amb = [r for r in rows if r["status"] != "CONFIDENT"]
    res = {"n_test_scenarios": len(rows), "n_non_confident": len(amb),
           "n_with_recommendation_and_truth_in_pair": len(with_rec),
           "n_truth_in_pair_but_no_reliable_location": len(no_rec),
           "accuracy_before_measurement": float(np.mean([r["prior_correct"] for r in with_rec])) if with_rec else None,
           "accuracy_recommended_node": float(np.mean([r["correct_recommended"] for r in with_rec])) if with_rec else None,
           "accuracy_random_eligible_node": float(np.mean([r["correct_random_mean"] for r in with_rec])) if with_rec else None,
           "definition": ("Among test scenarios where the true physical state is one of the two investigated hypotheses and a "
                          "recommendation was made: fraction where the simulated reading resolves to the truth, using the "
                          "recommended node vs the mean over all other eligible nodes (random choice).")}
    nc = [r for r in with_rec if r["status"] != "CONFIDENT"]
    if nc:
        res["non_confident_subset"] = {"n": len(nc), "accuracy_before": float(np.mean([r["prior_correct"] for r in nc])),
                                       "accuracy_recommended": float(np.mean([r["correct_recommended"] for r in nc])),
                                       "accuracy_random": float(np.mean([r["correct_random_mean"] for r in nc]))}
    return res, rows


def select_architecture(approaches: dict, latency_s: float) -> dict:
    """Spec §10 rule (fixed before evaluation): hybrid only if, under 15% mismatch, macro-F1 >= best single + 0.05,
    ECE no more than 0.02 worse, latency < 1 s. Otherwise the stronger single approach (by 15% macro-F1)."""
    m = {a: approaches[a]["by_mismatch"]["0.15"] for a in APPROACHES}
    best_single = max(["physics", "random_forest"], key=lambda a: m[a]["macro_f1"])
    h = m["hybrid"]
    checks = {"macro_f1_gain_ge_0.05": h["macro_f1"] - m[best_single]["macro_f1"] >= 0.05,
              "ece_not_worse_by_0.02": h["ece"] - m[best_single]["ece"] <= 0.02,
              "latency_below_1s": latency_s < 1.0, "same_heldout_test": True}
    sel = "hybrid" if all(checks.values()) else best_single
    return {"selected": sel, "best_single": best_single, "checks": checks,
            "evidence_15pct": {a: {"macro_f1": m[a]["macro_f1"], "ece": m[a]["ece"], "n": m[a]["n"]} for a in APPROACHES},
            "p95_latency_s": latency_s, "rule": select_architecture.__doc__}


def run(workers: int = 6) -> dict:
    t0 = time.time()
    df = load()
    te = df[df.split == "test"].reset_index(drop=True)
    y = te.label.map(C.CLASSES.index).to_numpy()
    probs = calibrated_probas(te)
    thr = bundle()["thresholds"]
    approaches = {}
    for a in APPROACHES:
        approaches[a] = {"overall": classification(probs[a], y, thr[a]), "by_mismatch": {}}
        for mm in C.MISMATCH_LEVELS:
            m = (te.mismatch == mm).to_numpy()
            approaches[a]["by_mismatch"][str(mm)] = classification(probs[a][m], y[m], thr[a])
    det_thr = tune_detector(workers)
    detection = detection_metrics(te, det_thr, workers)
    # latency on a fixed sample, warm process, before selection
    sample = te.groupby("label").head(3).to_dict("records")
    diagnose_scenario(_sim(sample[0]), "hybrid")
    lat = []
    for r in sample:
        sc = _sim(r)
        t1 = time.perf_counter(); diagnose_scenario(sc, "hybrid"); lat.append(time.perf_counter() - t1)
    lat = np.array(lat)
    latency = {"n": len(lat), "median_ms": float(np.median(lat) * 1000), "p95_ms": float(np.percentile(lat, 95) * 1000),
               "note": "end-to-end diagnose_scenario (features, all 3 approaches, evidence, detection), warm process"}
    selection = select_architecture(approaches, latency["p95_ms"] / 1000)
    SELECTION_PATH.write_text(json.dumps(selection, indent=2))
    inv, rows = investigation_eval(te, workers)   # uses the selected approach
    status = pd.Series([r["status"] for r in rows]).value_counts().to_dict()
    result = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "simulation_mode": C.SIMULATION_MODE,
              "dataset": {"n_total": len(df), **df.split.value_counts().to_dict(),
                          "test_by_mismatch": te.mismatch.value_counts().sort_index().to_dict(),
                          "classes": C.CLASSES},
              "approaches": approaches, "localization": localisation(te), "detection": detection,
              "latency": latency, "investigation": inv, "selected_approach_status_counts": status,
              "selection": selection, "runtime_s": round(time.time() - t0, 1),
              "disclaimer": "All metrics are on simulated EPANET Net1 scenarios; no real utility data was used."}
    EVAL_PATH.write_text(json.dumps(result, indent=2, default=float))
    return result


if __name__ == "__main__":
    r = run()
    for a in APPROACHES:
        o = r["approaches"][a]
        print(a, {k: round(o["overall"][k], 3) for k in ("accuracy", "macro_f1", "ece", "brier", "coverage_at_threshold")},
              "F1@15%:", round(o["by_mismatch"]["0.15"]["macro_f1"], 3))
    print("selection:", r["selection"]["selected"], r["selection"]["checks"])
    print("localization:", r["localization"]["by_mismatch"]["all"])
    print("detection:", {k: r["detection"][k] for k in ("false_alarms_per_day", "event_detection_rate", "median_detection_delay_h")})
    print("investigation:", r["investigation"])
    print("latency:", r["latency"], "runtime", r["runtime_s"])
