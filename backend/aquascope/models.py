"""Approach B (Random Forest), Approach C (hybrid stacker), temperature calibration and abstention.

Data use:
  train -> RF fit; out-of-fold RF probabilities (GroupKFold by location/seed) -> hybrid stacker fit
  val   -> temperature calibration per approach, abstention threshold selection
  test  -> evaluation only (evaluation.py)
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold

from . import config as C, physics
from .dataset import load
from .features import feature_names
from .tune_physics import load_params

MODEL_PATH = C.ARTIFACT_DIR / "models.joblib"
APPROACHES = ["physics", "random_forest", "hybrid"]
TARGET_SELECTIVE_RISK = 0.05   # abstention threshold chosen on val to keep error among accepted <= 5%
RF_SEED = 42


def _groups(df) -> np.ndarray:
    return np.where(df.label.isin(list(C.LEAK_CLASSES | {"valve_disturbance", "demand_anomaly"})),
                    df.label.str.split("_").str[0] + ":" + df.location.astype(str), "seed:" + df.seed.astype(str))


def _rf() -> RandomForestClassifier:
    return RandomForestClassifier(n_estimators=400, min_samples_leaf=2, class_weight="balanced",
                                  random_state=RF_SEED, n_jobs=-1)


def _rf_proba(rf, X) -> np.ndarray:
    """Probabilities in C.CLASSES order regardless of rf.classes_ order."""
    p = rf.predict_proba(X)
    out = np.zeros((len(X), len(C.CLASSES)))
    for j, c in enumerate(rf.classes_):
        out[:, C.CLASSES.index(c)] = p[:, j]
    return out


def _stack_X(p_rf, p_phys) -> np.ndarray:
    return np.hstack([np.log(p_rf + 1e-3), np.log(p_phys + 1e-6)])


def _apply_T(p, T) -> np.ndarray:
    z = np.log(p + 1e-9) / T
    z = np.exp(z - z.max(1, keepdims=True))
    return z / z.sum(1, keepdims=True)


def _fit_T(p, y_idx) -> float:
    nll = lambda T: -np.mean(np.log(_apply_T(p, T)[np.arange(len(y_idx)), y_idx] + 1e-12))
    return float(minimize_scalar(nll, bounds=(0.05, 20), method="bounded").x)


def choose_threshold(p, y_idx, target=TARGET_SELECTIVE_RISK) -> float:
    """Smallest confidence threshold whose selective risk on these data is <= target."""
    conf, correct = p.max(1), p.argmax(1) == y_idx
    for t in np.round(np.arange(0.30, 0.995, 0.01), 2):
        m = conf >= t
        if m.sum() >= 10 and 1 - correct[m].mean() <= target:
            return float(t)
    return 0.99


def train() -> dict:
    df = load()
    F = feature_names()
    tr, va = df[df.split == "train"], df[df.split == "val"]
    pp = load_params()
    t0 = time.time()
    rf = _rf().fit(tr[F], tr.label)
    # out-of-fold RF probabilities on train for the stacker (no in-sample leakage)
    oof = np.zeros((len(tr), len(C.CLASSES)))
    for a, b in GroupKFold(n_splits=5).split(tr, tr.label, _groups(tr)):
        m = _rf().fit(tr.iloc[a][F], tr.iloc[a].label)
        oof[b] = _rf_proba(m, tr.iloc[b][F])
    phys_tr = physics.predict_matrix(tr, pp)
    stacker = LogisticRegression(C=0.5, max_iter=3000).fit(_stack_X(oof, phys_tr), tr.label)
    assert list(stacker.classes_) == sorted(C.CLASSES)
    # validation: calibration + thresholds
    y_va = va.label.map(C.CLASSES.index).to_numpy()
    raw_va = raw_probas(rf, stacker, va, pp)
    temps, thresholds = {}, {}
    for a in APPROACHES:
        temps[a] = _fit_T(raw_va[a], y_va)
        thresholds[a] = choose_threshold(_apply_T(raw_va[a], temps[a]), y_va)
    bundle = {"rf": rf, "stacker": stacker, "temperatures": temps, "thresholds": thresholds,
              "feature_names": F, "classes": C.CLASSES, "physics_params": pp,
              "n_train": len(tr), "n_val": len(va), "train_seconds": round(time.time() - t0, 1)}
    joblib.dump(bundle, MODEL_PATH)
    meta = {k: v for k, v in bundle.items() if k not in ("rf", "stacker", "physics_params")}
    (C.ARTIFACT_DIR / "model_meta.json").write_text(json.dumps(meta, indent=2))
    return bundle


def _stack_proba(stacker, Xs) -> np.ndarray:
    p = stacker.predict_proba(Xs)
    out = np.zeros_like(p)
    for j, c in enumerate(stacker.classes_):
        out[:, C.CLASSES.index(c)] = p[:, j]
    return out


def raw_probas(rf, stacker, df, pp) -> dict:
    p_rf = _rf_proba(rf, df[feature_names()])
    p_ph = physics.predict_matrix(df, pp)
    return {"physics": p_ph, "random_forest": p_rf, "hybrid": _stack_proba(stacker, _stack_X(p_rf, p_ph))}


_BUNDLE = None


def bundle() -> dict:
    global _BUNDLE
    if _BUNDLE is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError("trained models missing; run `python -m aquascope.models`")
        b = joblib.load(MODEL_PATH)
        if b["feature_names"] != feature_names() or b["classes"] != C.CLASSES:
            raise RuntimeError("feature schema of saved model does not match current code; retrain")
        _BUNDLE = b
    return _BUNDLE


def calibrated_probas(df) -> dict:
    b = bundle()
    raw = raw_probas(b["rf"], b["stacker"], df, b["physics_params"])
    return {a: _apply_T(raw[a], b["temperatures"][a]) for a in APPROACHES}


if __name__ == "__main__":
    b = train()
    print({k: b[k] for k in ("temperatures", "thresholds", "n_train", "n_val", "train_seconds")})
