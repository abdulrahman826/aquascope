"""Remediation REST endpoints (M5). Mounted by aquascope.api with a single include_router line.

  POST /api/remediation/{sid}/confirm   {"mode": "auto"} or {"mode": "operator", event_class, location, operator_id, note}
  POST /api/remediation/{sid}/plan      candidate actions, simulated ranking, explanation (uses the stored confirmation)
  POST /api/remediation/{sid}/verify    apply the recommendation to the hidden true simulated network; before/after

All outputs are simulation results on EPANET Net1 — never operational instructions; there is no control interface.
"""
from __future__ import annotations

import json
import math
from typing import Literal, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config as C
from ..diagnosis import diagnose_scenario
from ..simulation import ScenarioSpec
from . import config as R
from . import confirmation as K
from .evaluate import _pipeline_confirmation
from .explain import explain
from .rank import rank
from .simulate import Intervention, fault_from_spec, run
from .verify import verify as verify_on_true

router = APIRouter(prefix="/api/remediation", tags=["remediation"])


def _plain(obj):
    """JSON-safe copy (numpy scalars / arrays, tuples)."""
    def conv(o):
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o).__name__)
    # rank.py uses -inf as a sort sentinel for "no trustworthy benefit"; JSON has no inf/NaN, so report them as null.
    def finite(o):
        if isinstance(o, float):
            return o if math.isfinite(o) else None
        if isinstance(o, dict):
            return {k: finite(v) for k, v in o.items()}
        if isinstance(o, list):
            return [finite(v) for v in o]
        return o
    return finite(json.loads(json.dumps(obj, default=conv)))


def _ctx():
    from ..api import SIM_LOCK, _get      # lazy: shared scenario store and EPANET lock of the main API
    return SIM_LOCK, _get


def _cd_dict(cd: K.ConfirmedDiagnosis, path: str) -> dict:
    return {"status": cd.status, "event_class": cd.event_class, "location": cd.location,
            "probability": cd.probability, "source": cd.source, "reason": cd.reason, "route": path,
            "parameters": dict(cd.parameters), "alternative_locations": list(cd.alternative_locations),
            "evidence": list(cd.evidence), "hydraulic_interventions_unlocked": cd.unlocks_hydraulic}


class ConfirmRequest(BaseModel):
    mode: Literal["auto", "operator"] = "auto"
    event_class: Optional[str] = None
    location: Optional[str] = None
    operator_id: Optional[str] = None
    note: str = ""


@router.post("/{sid}/confirm")
def confirm(sid: str, req: ConfirmRequest = ConfirmRequest()):
    lock, get = _ctx()
    sc = get(sid)
    with lock:
        d = diagnose_scenario(sc)
        if req.mode == "operator":
            try:
                cd = K.operator_confirmed(req.event_class or "", req.location, req.operator_id or "", req.note)
            except ValueError as e:
                raise HTTPException(422, str(e))
            path = "operator"
        else:
            cd, path, _ = _pipeline_confirmation(sc, d)
    sc["_remediation"] = {"confirmation": cd, "route": path}
    return _plain(_cd_dict(cd, path) | {"scenario_id": sid, "diagnosis_status": d["status"],
                                        "label": R.SIMULATION_LABEL})


@router.post("/{sid}/plan")
def plan(sid: str):
    lock, get = _ctx()
    sc = get(sid)
    state = sc.get("_remediation")
    if state is None:
        raise HTTPException(409, "run POST /api/remediation/{id}/confirm first")
    with lock:
        d = diagnose_scenario(sc)
        r = rank(state["confirmation"], d, telemetry=sc["telemetry"])
    state["plan"] = r
    ranked = [{k: v for k, v in row.items() if k != "evaluation"} | (
        {"after": row["evaluation"]["after"], "constraint_checks": row["evaluation"]["constraint_checks"],
         "labels": row["evaluation"]["labels"], "warnings": row["evaluation"]["warnings"],
         "physical_valve_availability": row["evaluation"]["intervention"]["physical_valve_availability"]}
        if "evaluation" in row else {}) for row in r["ranked"]]
    return _plain({"scenario_id": sid, "confirmation": _cd_dict(state["confirmation"], state["route"]),
                   "candidates": r["candidates"], "ranked": ranked, "recommendation": r["recommendation"],
                   "hypothesis_fault": r.get("hypothesis_fault"), "notes": r.get("notes", []),
                   "benefit_threshold": r.get("benefit_threshold"), "preview_only": r.get("preview_only", False),
                   "explanation": explain(r), "disclaimer": r["disclaimer"],
                   "execution_status": R.NOT_EXECUTABLE})


def _series(run_: dict) -> dict:
    t = run_["time"]
    keep = slice(2 * C.STEPS_PER_DAY, None)            # day 3 onwards: before and after the decision time
    P = np.array([run_["pressure"][j] for j in C.JUNCTIONS])
    return {"time_h": (t[keep] / 3600).tolist(), "min_pressure_m": P.min(0)[keep].round(3).tolist(),
            "mean_pressure_m": P.mean(0)[keep].round(3).tolist(),
            "leak_flow_L_s": np.round(run_["leak_flow"][keep], 3).tolist(),
            "tank_volume_m3": np.round(run_["tank_volume_m3"][keep], 1).tolist()}


@router.post("/{sid}/verify")
def verify(sid: str):
    lock, get = _ctx()
    sc = get(sid)
    state = sc.get("_remediation")
    if state is None or "plan" not in state:
        raise HTTPException(409, "run /confirm and /plan before /verify")
    spec = ScenarioSpec(**sc["spec"])
    r = state["plan"]
    rec = r["recommendation"]
    iv = Intervention(rec["kind"], rec["target"]) if rec["kind"] in ("isolate_pipe", "restore_valve", "restore_pump") \
        else Intervention()
    with lock:
        v = verify_on_true(r, spec)
        series = None
        if v.get("applicable"):
            f = fault_from_spec(spec)
            kw = dict(mismatch=spec.mismatch, seed=spec.seed)
            without = run(f, Intervention(), **kw)
            series = {"without": _series(without),
                      "with": _series(without if iv.kind == "none" else run(f, iv, **kw)),
                      "intervention_time_h": R.DECISION_TIME / 3600}
    return _plain({"scenario_id": sid, **v, "series": series})
