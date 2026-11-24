"""Select PhysicsParams by random search on the VALIDATION split (macro F1). Never touches test."""
import json
from dataclasses import asdict, fields

import numpy as np
from sklearn.metrics import f1_score

from . import config as C, physics
from .dataset import load

OUT = C.ARTIFACT_DIR / "physics_params.json"


def tune(n_iter: int = 150, seed: int = 0) -> physics.PhysicsParams:
    va = load().query("split == 'val'")
    rng = np.random.default_rng(seed)
    best, best_f1 = physics.PhysicsParams(), -1.0
    for i in range(n_iter):
        cand = best if i == 0 else physics.PhysicsParams(**{
            f.name: float(getattr(best, f.name) * np.exp(rng.normal(0, 0.35))) for f in fields(physics.PhysicsParams)})
        pred = np.array(C.CLASSES)[physics.predict_matrix(va, cand).argmax(1)]
        f1 = f1_score(va.label, pred, average="macro")
        if f1 > best_f1:
            best, best_f1 = cand, f1
            print(f"iter {i}: val macro-F1 {f1:.3f}")
    OUT.write_text(json.dumps({"params": asdict(best), "val_macro_f1": best_f1, "n_val": len(va)}, indent=2))
    return best


def load_params() -> physics.PhysicsParams:
    if OUT.exists():
        return physics.PhysicsParams(**json.loads(OUT.read_text())["params"])
    return physics.PhysicsParams()


if __name__ == "__main__":
    tune()
