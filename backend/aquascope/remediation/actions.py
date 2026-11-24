"""Rule-based corrective-action generator (M2): ConfirmedDiagnosis -> candidate actions.

Each candidate states whether the hydraulic model can simulate it, whether it is UNLOCKED, and why (not).
  * Hydraulic interventions are unlocked only for CONFIRMED_BY_INVESTIGATION / OPERATOR_CONFIRMED, only at the
    confirmed location, and only if the structural pre-screen passes.
  * CONFIDENT_BUT_UNVERIFIED still lists hydraulic candidates, LOCKED, as information; INSUFFICIENT_EVIDENCE gets
    no hydraulic candidates at all, only "investigate first".
  * Nothing is ever real-world executable: every candidate carries NOT VALIDATED FOR REAL-WORLD EXECUTION.
Ranking and outcome simulation are M3 (not here).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Literal, Optional

import networkx as nx

from .. import config as C
from ..network import _sections, topology
from . import config as R
from .confirmation import ConfirmedDiagnosis
from .metrics import connectivity
from .simulate import RESERVOIR_ID, Intervention

Category = Literal["HYDRAULIC_INTERVENTION", "NON_HYDRAULIC", "BASELINE", "GATE"]
_SIMULATED = {"isolate_pipe", "restore_valve", "restore_pump", "none"}


@dataclass
class ActionCandidate:
    id: str
    kind: str
    target: Optional[str]
    category: Category
    role: str
    description: str
    simulation_supported: bool
    unlocked: bool
    lock_reasons: list = field(default_factory=list)
    screened_out: bool = False
    screen_reasons: list = field(default_factory=list)
    physical_valve_availability: Optional[str] = None
    real_world_executable: bool = False
    execution_status: str = R.NOT_EXECUTABLE
    labels: list = field(default_factory=list)

    def to_intervention(self) -> Intervention:
        if self.kind not in _SIMULATED:
            raise ValueError(f"'{self.kind}' is not supported by the hydraulic simulator")
        return Intervention(self.kind, self.target if self.kind != "none" else None)


# ---------------------------------------------------------------- structural pre-screen (static, before simulation)
@lru_cache(maxsize=1)
def control_nodes() -> dict[str, list[str]]:
    """Nodes whose state drives an EPANET control, from the .inp [CONTROLS] section: {node: [controlled links]}."""
    out: dict[str, list[str]] = {}
    for row in _sections(C.NET1_INP).get("CONTROLS", []):
        up = [x.upper() for x in row]
        if "NODE" in up and "LINK" in up:
            out.setdefault(row[up.index("NODE") + 1], set()).add(row[up.index("LINK") + 1])
    return {k: sorted(v) for k, v in out.items()}


def prescreen_isolation(pipe: str) -> list[str]:
    """Reasons an idealised isolation of `pipe` is structurally unacceptable in the model (empty = passes)."""
    reasons = []
    conn = connectivity([pipe])
    if conn["junctions_disconnected_from_any_source"]:
        reasons.append(f"disconnects junctions {conn['junctions_disconnected_from_any_source']} from every source")
    if conn["junctions_supplied_only_by_tank"]:
        reasons.append(f"leaves junctions {conn['junctions_supplied_only_by_tank']} supplied only by finite tank "
                       "storage (single point of failure)")
    g = nx.Graph([(l["from"], l["to"]) for l in topology()["links"] if l["id"] != pipe])
    g.add_nodes_from(n["id"] for n in topology()["nodes"])
    for node, links in control_nodes().items():
        if not nx.has_path(g, node, RESERVOIR_ID):
            reasons.append(f"cuts off node {node}, whose level/pressure controls link(s) {links} ([CONTROLS]); "
                           "the controlled pump would stop switching")
    return reasons


# ---------------------------------------------------------------- generator
def _hydraulic(cd: ConfirmedDiagnosis, kind: str, target: str, role: str, description: str,
               confirmed_location: bool) -> ActionCandidate:
    lock = []
    if not cd.unlocks_hydraulic:
        lock.append(f"diagnosis status {cd.status}: hydraulic interventions require CONFIRMED_BY_INVESTIGATION or "
                    "OPERATOR_CONFIRMED")
    if not confirmed_location:
        lock.append(f"location {target} is an alternative hypothesis, not the confirmed location {cd.location}")
    screen = prescreen_isolation(target) if kind == "isolate_pipe" else []
    labels = [R.SIMULATION_LABEL]
    if kind == "isolate_pipe":
        labels.append(R.ISOLATION_ASSUMPTION)
    return ActionCandidate(
        id=f"{kind}:{target}", kind=kind, target=target, category="HYDRAULIC_INTERVENTION", role=role,
        description=description, simulation_supported=True, unlocked=not lock and not screen, lock_reasons=lock,
        screened_out=bool(screen), screen_reasons=screen,
        physical_valve_availability=R.valve_availability(target) if kind == "isolate_pipe" else None, labels=labels)


def _info(kind: str, target: Optional[str], description: str) -> ActionCandidate:
    return ActionCandidate(id=f"{kind}:{target}", kind=kind, target=target, category="NON_HYDRAULIC",
                           role="informational", description=description, simulation_supported=False, unlocked=True,
                           labels=["Field / maintenance task; outcome not simulated by the hydraulic model."])


def _baseline() -> ActionCandidate:
    return ActionCandidate(id="none:None", kind="none", target=None, category="BASELINE", role="comparator",
                           description="No intervention (reference for every comparison).",
                           simulation_supported=True, unlocked=True, labels=[R.SIMULATION_LABEL])


def _investigate(reason: str) -> ActionCandidate:
    return ActionCandidate(id="investigate_first:None", kind="investigate_first", target=None, category="GATE",
                           role="gate", description=f"Investigate before any hydraulic intervention: {reason}",
                           simulation_supported=False, unlocked=True)


def generate(cd: ConfirmedDiagnosis) -> dict:
    cands: list[ActionCandidate] = []
    cls, loc = cd.event_class, cd.location
    if cd.status == "INSUFFICIENT_EVIDENCE":
        cands = [_investigate(cd.reason), _baseline()]
    elif cls == "normal":
        cands = [_baseline(), _info("continue_monitoring", None, "No fault diagnosed; continue monitoring.")]
    else:
        if not cd.unlocks_hydraulic:
            cands.append(_investigate("the diagnosis is model-confident but unverified; run the active "
                                      "investigation or obtain operator confirmation"))
        cands.append(_baseline())
        if cls in C.LEAK_CLASSES:
            cands.append(_hydraulic(cd, "isolate_pipe", loc, "primary",
                                    f"Isolate pipe {loc} (both halves around the leak) to stop the leak.", True))
            for alt in cd.alternative_locations:
                cands.append(_hydraulic(cd, "isolate_pipe", alt, "alternative_location",
                                        f"Isolate pipe {alt} (alternative leak location hypothesis).", False))
            cands.append(_info("leak_survey", loc, f"Acoustic / step-test survey of pipe {loc} to pinpoint the leak."))
            cands.append(_info("schedule_repair", loc, f"Schedule repair of pipe {loc} (repair not simulated in M1/M2)."))
        elif cls == "valve_disturbance":
            cands.append(_hydraulic(cd, "restore_valve", loc, "primary",
                                    f"Re-open the partly closed valve on pipe {loc} (restore original minor loss).", True))
            cands.append(_info("valve_inspection", loc, f"Inspect valve position / operation history on pipe {loc}."))
        elif cls == "pump_disturbance":
            cands.append(_hydraulic(cd, "restore_pump", loc, "primary",
                                    f"Repair pump {loc} to nominal speed (maintenance outcome, not a setpoint).", True))
            cands.append(_info("pump_inspection", loc, f"Inspect pump {loc} (impeller, drive, speed controller)."))
        elif cls == "demand_anomaly":
            cands.append(_info("field_inspection", loc, f"Inspect node {loc} for unmetered use, bursts on customer "
                                                         "side, or unusual consumption; contact the customer."))
        elif cls in C.SENSOR_CLASSES:
            cands.append(_info("sensor_maintenance", loc, f"Flag pressure sensor {loc}, use a model-interpolated "
                                                          "estimate meanwhile, schedule recalibration/replacement."))
    return {"confirmation": {k: v for k, v in asdict(cd).items() if k != "evidence"} | {"evidence": list(cd.evidence)},
            "hydraulic_interventions_unlocked": cd.unlocks_hydraulic,
            "candidates": [asdict(c) for c in cands], "_objects": cands,
            "disclaimer": f"{R.SIMULATION_LABEL}. Every candidate is {R.NOT_EXECUTABLE}."}
