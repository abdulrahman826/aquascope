"""AquaScope REST API (FastAPI)."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import config as C
from .diagnosis import EVAL_PATH, LABELS, diagnose_scenario, selected_approach
from .investigation import recommend, simulate_measurement
from .models import APPROACHES, bundle
from .network import topology
from .simulation import ScenarioSpec, SimulationError, run_scenario, valid_locations

log = logging.getLogger("aquascope")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="AquaScope API", version="0.1.0",
              description="Evidence-driven water network diagnosis (EPyT-Flow / EPANET Net1 simulation)")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                   allow_methods=["*"], allow_headers=["*"])

SIM_LOCK = threading.Lock()   # ponytail: one global EPANET lock; toolkit is not assumed thread-safe
_SCENARIOS: dict[str, dict] = {}
_EVAL = {"running": False, "error": None, "started": None}


# ------------------------------------------------------------------ errors
def _err(status: int, code: str, msg: str):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": msg}})


@app.exception_handler(HTTPException)
async def _http(_: Request, e: HTTPException):
    return _err(e.status_code, {404: "not_found", 422: "invalid_request", 409: "conflict"}.get(e.status_code, "error"),
                str(e.detail))


@app.exception_handler(SimulationError)
async def _sim(_: Request, e: SimulationError):
    log.error("simulation error: %s", e)
    return _err(502, "simulation_failed", str(e))


@app.exception_handler(Exception)
async def _any(_: Request, e: Exception):
    log.exception("unhandled error")
    return _err(500, "internal_error", "Internal error; see server log.")


from fastapi.exceptions import RequestValidationError  # noqa: E402


@app.exception_handler(RequestValidationError)
async def _val(_: Request, e: RequestValidationError):
    msgs = "; ".join(f"{'.'.join(str(x) for x in er['loc'][1:])}: {er['msg']}" for er in e.errors())
    return _err(422, "invalid_request", msgs)


# ------------------------------------------------------------------ helpers
def _get(sid: str) -> dict:
    sc = _SCENARIOS.get(sid)
    if sc is None:
        p = C.SCENARIO_DIR / f"{sid}.json"
        if not p.exists() or "/" in sid or "\\" in sid or ".." in sid:
            raise HTTPException(404, f"scenario '{sid}' not found")
        sc = json.loads(p.read_text())
        _SCENARIOS[sid] = sc
    return sc


def _summary(sc: dict) -> dict:
    return {"scenario_id": sc["scenario_id"], "spec": sc["spec"], "network_id": sc["network_id"],
            "simulation_mode": sc["simulation_mode"], "created": sc.get("created"),
            "metadata": sc["metadata"], "model_mismatch": sc["model_mismatch"], "sensor_config": sc["sensor_config"]}


# ------------------------------------------------------------------ endpoints
@app.get("/api/health")
def health():
    try:
        b = bundle()
        models_ok, sel = True, selected_approach()
    except Exception as e:  # report, do not hide
        models_ok, sel = False, str(e)
    return {"status": "ok" if models_ok else "degraded", "simulation_mode": C.SIMULATION_MODE,
            "network": C.NETWORK_ID, "models_loaded": models_ok, "selected_approach": sel,
            "evaluation_available": EVAL_PATH.exists()}


@app.get("/api/network")
def network():
    return topology()


@app.get("/api/options")
def options():
    return {"classes": [{"id": c, "label": LABELS[c], "family": C.CLASS_FAMILY[c], "locations": valid_locations(c)}
                        for c in C.CLASSES],
            "mismatch_levels": C.MISMATCH_LEVELS, "approaches": APPROACHES,
            "event_start_range_s": [C.EVENT_START_MIN, C.EVENT_START_MAX], "report_step_s": C.REPORT_STEP}


@app.get("/api/scenarios")
def list_scenarios():
    items = []
    for p in sorted(C.SCENARIO_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        sc = _SCENARIOS.get(p.stem) or json.loads(p.read_text())
        items.append(_summary(sc))
    return {"scenarios": items}


@app.post("/api/scenarios/run")
def run(spec: ScenarioSpec):
    with SIM_LOCK:
        sc = run_scenario(spec)
    sc["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _SCENARIOS[sc["scenario_id"]] = sc
    (C.SCENARIO_DIR / f"{sc['scenario_id']}.json").write_text(json.dumps(sc))
    log.info("scenario %s generated in %.2fs", sc["scenario_id"], sc["metadata"]["runtime_s"])
    return _summary(sc)


@app.get("/api/telemetry/{sid}")
def telemetry(sid: str):
    sc = _get(sid)
    from .simulation import nominal
    with SIM_LOCK:
        nom = nominal()
    n = len(sc["telemetry"]["time"])
    return {"scenario_id": sid, "telemetry": sc["telemetry"], "units": sc["sensor_config"]["units"],
            "nominal_model": {"pressure": {s: nom["pressure"][s][:n].tolist() for s in C.PRESSURE_SENSORS},
                              "flow": {f: nom["flow"][f][:n].tolist() for f in C.FLOW_SENSORS}},
            "reference_period_s": sc["metadata"]["reference_period_s"]}


@app.get("/api/scenarios/{sid}/ground_truth")
def ground_truth(sid: str):
    gt = dict(_get(sid)["ground_truth"])
    gt.pop("per_step_event_active", None)
    return gt


class DiagnosisRequest(BaseModel):
    approach: Optional[Literal["physics", "random_forest", "hybrid"]] = None


@app.post("/api/diagnosis/{sid}")
def diagnosis(sid: str, req: DiagnosisRequest = DiagnosisRequest()):
    sc = _get(sid)
    with SIM_LOCK:
        return diagnose_scenario(sc, req.approach)


@app.post("/api/investigation/{sid}")
def investigation(sid: str, req: DiagnosisRequest = DiagnosisRequest()):
    sc = _get(sid)
    with SIM_LOCK:
        d = diagnose_scenario(sc, req.approach)
        rec = recommend(sc, d)
    sc["_last_recommendation"] = rec
    return rec | {"diagnosis_status": d["status"], "approach": d["approach"]}


class MeasureRequest(BaseModel):
    node: str


@app.post("/api/investigation/{sid}/measure")
def measure(sid: str, req: MeasureRequest):
    sc = _get(sid)
    rec = sc.get("_last_recommendation")
    if rec is None:
        raise HTTPException(409, "run POST /api/investigation/{id} before taking a measurement")
    if not rec["candidates"]:
        raise HTTPException(409, "no competing hypotheses to test for this scenario")
    if req.node not in C.JUNCTIONS:
        raise HTTPException(422, f"node must be one of {C.JUNCTIONS}")
    return simulate_measurement(sc, rec, req.node)


def _eval_job():
    try:
        from .evaluation import run as run_eval
        run_eval(workers=4)
        _EVAL["error"] = None
    except Exception as e:
        log.exception("evaluation failed")
        _EVAL["error"] = f"{type(e).__name__}: {e}"
    finally:
        _EVAL["running"] = False


@app.post("/api/evaluation/run")
def evaluation_run():
    if _EVAL["running"]:
        raise HTTPException(409, "evaluation already running")
    _EVAL.update(running=True, error=None, started=time.strftime("%Y-%m-%d %H:%M:%S"))
    threading.Thread(target=_eval_job, daemon=True).start()
    return {"status": "started", "started": _EVAL["started"],
            "note": "Re-evaluates frozen models on the held-out test split (~1 min). Poll /api/evaluation/results."}


@app.get("/api/evaluation/results")
def evaluation_results():
    res = json.loads(EVAL_PATH.read_text()) if EVAL_PATH.exists() else None
    return {"running": _EVAL["running"], "error": _EVAL["error"], "started": _EVAL["started"], "results": res}


@app.on_event("startup")
def _warm():
    try:
        with SIM_LOCK:
            from .physics import signature_library
            bundle(); signature_library()
        log.info("models and hypothesis signature library loaded")
    except Exception:
        log.exception("startup warm-up failed (health will report degraded)")


from .remediation.router import router as remediation_router  # noqa: E402  (remediation module, M5)
app.include_router(remediation_router)
