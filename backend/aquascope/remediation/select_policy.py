"""Select the confirmation policy on the VALIDATION split only (remediation fix for the M4 false-confirmation finding).

Target fixed before looking at results: wrong confirmations <= 3 % of all confirmations (investigation + verification);
among policies meeting it, maximise the number of confirmations; ties -> stricter policy.
Reads data/remediation/val_scenarios.csv (raw measurement statistics recorded under the permissive 0.95 / 3 sigma policy).
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from .. import config as C
from ..investigation import MIN_SEPARATION

TARGET_WRONG_RATE = 0.03
POSTERIORS = [0.95, 0.99, 0.995, 0.999, 0.9999]
MAX_Z = [3.0, 2.5, 2.0, 1.5, 1.0]
KWAY = [0.0, 0.5, 0.8, 0.9, 0.95, 0.99]     # min equal-prior posterior among the K leading physical states
OUT = C.DATA_DIR / "remediation" / "confirmation_policy.json"


def grid(s: pd.DataFrame) -> pd.DataFrame:
    m = s[s.meas_route.notna()].copy()
    rows = []
    for p in POSTERIORS:
      for z in MAX_Z:
        for k in KWAY:
            ok = ((m.meas_posterior >= p) & (m.meas_abs_z_winner <= z) & (m.meas_separation >= MIN_SEPARATION)
                  & m.meas_consistent & (m.meas_kway_posterior >= k))
            c = m[ok]
            rows.append({"min_posterior": p, "max_z": z, "min_kway_posterior": k, "n_confirmed": len(c),
                         "n_wrong": int((~c.meas_state_correct.astype(bool)).sum()),
                         "wrong_rate": float(1 - c.meas_state_correct.astype(bool).mean()) if len(c) else 0.0,
                         "n_hydraulic_truth_confirmed": int(c.true_class.isin(
                             list(C.LEAK_CLASSES | {"valve_disturbance", "pump_disturbance"})).sum())})
    return pd.DataFrame(rows)


def select(split_csv=C.DATA_DIR / "remediation" / "val_scenarios.csv") -> dict:
    g = grid(pd.read_csv(split_csv))
    ok = g[g.wrong_rate <= TARGET_WRONG_RATE].sort_values(["n_confirmed", "min_kway_posterior", "min_posterior", "max_z"],
                                                           ascending=[False, False, False, True])
    if ok.empty:
        raise SystemExit("no policy meets the target on validation")
    best = ok.iloc[0].to_dict()
    res = {"selected": {"min_posterior": best["min_posterior"], "max_z": best["max_z"],
                        "min_kway_posterior": best["min_kway_posterior"]},
           "validation": best, "target_wrong_rate": TARGET_WRONG_RATE, "grid": g.to_dict("records"),
           "note": "selected on the validation split only"}
    OUT.write_text(json.dumps(res, indent=2, default=float))
    return res


if __name__ == "__main__":
    r = select()
    g = pd.DataFrame(r["grid"])
    print(g[g.wrong_rate <= TARGET_WRONG_RATE].sort_values("n_confirmed", ascending=False).head(8).to_string(index=False))
    print(g[(g.min_kway_posterior == 0.0) & (g.max_z == 3.0)].to_string(index=False))
    print("SELECTED:", r["selected"], r["validation"])
