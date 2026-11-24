"""Reproducible scenario dataset with location-grouped train / validation / test splits.

Leak, valve and demand scenarios are split by *location* (a pipe/node used for testing is never
seen in training). Sensor-fault, pump and normal scenarios are split by scenario seed.
Every scenario has a unique seed, so each draws its own model-mismatch perturbation.
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import config as C
from .features import extract, feature_names
from .simulation import ScenarioSpec, run_scenario, valid_locations

PER_SPLIT = {"train": 40, "val": 15, "test": 20}   # per class per mismatch level
DATASET_CSV = C.ARTIFACT_DIR / "dataset.csv"
SPLIT_SEED = 2024


def location_groups() -> dict[str, dict[str, list[str]]]:
    rng = np.random.default_rng(SPLIT_SEED)
    out = {}
    for name, locs, n_test, n_val in [("leak", C.LEAK_PIPES, 3, 2), ("valve", C.VALVE_PIPES, 2, 1),
                                      ("demand", C.DEMAND_NODES, 2, 1)]:
        p = list(rng.permutation(locs))
        out[name] = {"test": sorted(p[:n_test]), "val": sorted(p[n_test:n_test + n_val]),
                     "train": sorted(p[n_test + n_val:])}
    return out


def _group_key(cls: str):
    if cls in C.LEAK_CLASSES:
        return "leak"
    return {"valve_disturbance": "valve", "demand_anomaly": "demand"}.get(cls)


def build_specs() -> list[tuple[str, ScenarioSpec]]:
    groups = location_groups()
    specs, seed = [], 1
    for split, n in PER_SPLIT.items():
        for cls in C.CLASSES:
            g = _group_key(cls)
            for mm in C.MISMATCH_LEVELS:
                for i in range(n):
                    rng = np.random.default_rng([seed, 99])
                    locs = groups[g][split] if g else valid_locations(cls)
                    loc = str(locs[i % len(locs)]) if locs else None
                    specs.append((split, ScenarioSpec(event_class=cls, seed=seed, severity=float(rng.uniform()),
                                                      mismatch=mm, location=loc)))
                    seed += 1
    return specs


def _one(item):
    split, spec = item
    sc = run_scenario(spec)
    x, _ = extract(sc["telemetry"])
    gt = sc["ground_truth"]
    return {"split": split, "scenario_id": sc["scenario_id"], "seed": spec.seed, "label": gt["event_class"],
            "location": gt["location"], "start_time": gt["start_time"], "severity": spec.severity,
            "mismatch": spec.mismatch, **dict(zip(feature_names(), x))}


def build(workers: int = 6) -> pd.DataFrame:
    specs = build_specs()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_one, specs, chunksize=20))
    df = pd.DataFrame(rows)
    df.to_csv(DATASET_CSV, index=False)
    return df


def load() -> pd.DataFrame:
    if not DATASET_CSV.exists():
        raise FileNotFoundError(f"{DATASET_CSV} missing; run `python -m aquascope.dataset`")
    return pd.read_csv(DATASET_CSV, dtype={"location": str})


if __name__ == "__main__":
    import time
    t = time.time()
    df = build(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
    print(df.groupby(["split", "label"]).size().unstack(0))
    print(f"{len(df)} scenarios in {time.time() - t:.1f}s -> {DATASET_CSV}")
