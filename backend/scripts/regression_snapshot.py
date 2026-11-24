"""Regression gate for the existing AquaScope engine (Remediation M0).

  python scripts/regression_snapshot.py --record   # write data/regression/baseline.json
  python scripts/regression_snapshot.py --check    # recompute and compare against the baseline

Reproduces the dataset and the held-out evaluation into a temporary directory (existing artifacts are
only read, never written) and compares a canonical hash of the evaluation that excludes wall-clock
fields (timestamps, runtimes, latencies), which legitimately vary between runs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASELINE = ROOT / "data" / "regression" / "baseline.json"
VOLATILE = ("generated_at", "runtime_s", "latency")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _round(x):
    """Floats rounded to 12 significant digits: RandomForest.predict_proba (n_jobs=-1) sums tree outputs in a
    thread-dependent order, producing last-bit differences (~1e-17) in ECE/Brier between identical runs."""
    if isinstance(x, float):
        return float(f"{x:.12g}")
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_round(v) for v in x]
    return x


def canonical_eval(ev: dict) -> str:
    ev = copy.deepcopy(ev)
    for k in VOLATILE:
        ev.pop(k, None)
    ev.get("selection", {}).pop("p95_latency_s", None)
    return hashlib.md5(json.dumps(_round(ev), sort_keys=True).encode()).hexdigest()


def snapshot() -> dict:
    from aquascope import config as C, dataset, evaluation
    art = C.ARTIFACT_DIR
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests",
                            "--ignore-glob=tests/test_remediation*.py"], cwd=ROOT, capture_output=True, text=True)
    summary = tests.stdout.strip().splitlines()[-1] if tests.stdout.strip() else tests.stderr[-300:]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        dataset.DATASET_CSV = tmp / "dataset.csv"          # regenerate into temp, compare bytes
        dataset.build()
        regenerated_dataset = md5(tmp / "dataset.csv")
        dataset.DATASET_CSV = art / "dataset.csv"          # evaluate on the committed dataset (read-only)
        evaluation.EVAL_PATH = tmp / "evaluation.json"
        evaluation.SELECTION_PATH = tmp / "selection.json"
        evaluation.DETECTOR_PATH = tmp / "detector.json"
        reproduced = evaluation.run()
        reproduced = json.loads(json.dumps(reproduced, default=float))
        det_same = (tmp / "detector.json").read_text() == (art / "detector.json").read_text()
        sel_new = json.loads((tmp / "selection.json").read_text())
    stored = json.loads((art / "evaluation.json").read_text())
    sel_old = json.loads((art / "selection.json").read_text())
    for s in (sel_new, sel_old):
        s.pop("p95_latency_s", None)
    return {
        "existing_tests": {"returncode": tests.returncode, "summary": summary},
        "artifact_md5": {p.name: md5(p) for p in sorted(art.iterdir()) if p.is_file()},
        "dataset_regenerated_md5": regenerated_dataset,
        "evaluation_canonical_md5_stored": canonical_eval(stored),
        "evaluation_canonical_md5_reproduced": canonical_eval(reproduced),
        "detector_reproduced_identical": det_same,
        "selection_reproduced_identical": sel_new == sel_old,
    }


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    snap = snapshot()
    ok = (snap["existing_tests"]["returncode"] == 0
          and snap["dataset_regenerated_md5"] == snap["artifact_md5"]["dataset.csv"]
          and snap["evaluation_canonical_md5_reproduced"] == snap["evaluation_canonical_md5_stored"]
          and snap["detector_reproduced_identical"] and snap["selection_reproduced_identical"])
    snap["reproduced_ok"] = ok
    print(json.dumps(snap, indent=2))
    if mode == "--record":
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(snap, indent=2))
        print(f"baseline written to {BASELINE}")
    else:
        base = json.loads(BASELINE.read_text())
        diffs = [k for k in ("artifact_md5", "dataset_regenerated_md5", "evaluation_canonical_md5_reproduced")
                 if base[k] != snap[k]]
        ok = ok and not diffs
        print("REGRESSION CHECK:", "PASS" if ok else f"FAIL {diffs}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
