"""Before / after hydraulic outcome metrics and configured-constraint checks for an intervention.

Outcome windows (on the reporting grid):
  before        = [t_int - 24 h, t_int)            from the intervention run (identical to the no-intervention run)
  after         = [t_int + 1 h, horizon]           from the intervention run
  after_without = same window, no-intervention run (what would have happened anyway)
"""
from __future__ import annotations

from functools import lru_cache
from math import pi

import networkx as nx
import numpy as np

from .. import config as C
from ..network import _sections, topology
from . import config as R
from .simulate import RESERVOIR_ID, TANK_ID

FT = 0.3048


@lru_cache(maxsize=1)
def tank_geometry() -> dict:
    row = next(r for r in _sections(C.NET1_INP)["TANKS"] if r[0] == TANK_ID)
    return {"area_m2": pi * (float(row[5]) * FT) ** 2 / 4, "min_level_m": float(row[3]) * FT,
            "max_level_m": float(row[4]) * FT}


def connectivity(closed_links: list[str]) -> dict:
    """Static graph check on the original Net1 topology with the closed pipes removed (a closed leak half
    `leak_pipe_<id>` belongs to pipe <id>, which is removed as a whole)."""
    g = nx.Graph()
    removed = {l.removeprefix("leak_pipe_") for l in closed_links}
    for l in topology()["links"]:
        g.add_node(l["from"]); g.add_node(l["to"])
        if l["id"] not in removed:
            g.add_edge(l["from"], l["to"])
    to_res = nx.node_connected_component(g, RESERVOIR_ID)
    to_tank = nx.node_connected_component(g, TANK_ID)
    return {"closed_pipes": sorted(removed),
            "junctions_disconnected_from_any_source": [j for j in C.JUNCTIONS if j not in to_res and j not in to_tank],
            "junctions_supplied_only_by_tank": [j for j in C.JUNCTIONS if j in to_tank and j not in to_res]}


def outcome(run: dict, requested: dict, lo: int, hi: int) -> dict:
    t = run["time"]
    m = (t >= lo) & (t <= hi)
    if not m.any():
        raise ValueError("empty outcome window")
    P = np.array([run["pressure"][j][m] for j in C.JUNCTIONS])
    D = np.array([run["demand"][j][m] for j in C.JUNCTIONS])
    Q = np.array([requested[j][m] for j in C.JUNCTIONS])
    dt = C.REPORT_STEP
    geo = tank_geometry()
    level = run["tank_volume_m3"][m] / geo["area_m2"]
    k = np.unravel_index(np.argmin(P), P.shape)
    leak = run["leak_flow"][m]
    return {
        "window_h": [lo / 3600, hi / 3600], "n_samples": int(m.sum()),
        "min_pressure_m": float(P.min()), "min_pressure_junction": C.JUNCTIONS[k[0]],
        "junctions_below_min_service_pressure": [j for i, j in enumerate(C.JUNCTIONS)
                                                 if (P[i] < R.MIN_SERVICE_PRESSURE_M).any()],
        "mean_pressure_m": {j: float(P[i].mean()) for i, j in enumerate(C.JUNCTIONS)},
        "requested_L_s": float(Q.sum(0).mean()), "delivered_L_s": float(D.sum(0).mean()),
        "delivered_fraction": float(D.sum() / Q.sum()) if Q.sum() > 0 else 1.0,
        "demand_deficit_m3": float(np.clip(Q - D, 0, None).sum() * dt / 1000),
        "leak_flow_mean_L_s": float(leak.mean()), "leak_volume_m3": float(leak.sum() * dt / 1000),
        "tank_min_level_m": float(level.min()), "tank_max_level_m": float(level.max()),
        "tank_empty_samples": int((level <= geo["min_level_m"] + 0.01).sum()),
        # EPANET emitter (leak) flow and pressure-driven demand are not physical at negative pressure
        "negative_pressure_samples": int((P < 0).sum()),
    }


def evaluate(sim: dict) -> dict:
    w, wo, req = sim["with"], sim["without"], sim["requested"]
    t_int, hz = w["t_int"], w["horizon"]
    before = outcome(w, req, t_int - R.BEFORE_WINDOW, t_int - 1)
    after = outcome(w, req, t_int + R.SETTLE, hz)
    after_without = outcome(wo, req, t_int + R.SETTLE, hz)
    conn = connectivity(w["closed_links"])
    cons = R.CONSTRAINTS
    checks = [
        {"name": "all junctions connected to a source (graph check)",
         "value": conn["junctions_disconnected_from_any_source"], "limit": "none disconnected",
         "passed": not conn["junctions_disconnected_from_any_source"]},
        {"name": f"min junction pressure >= {cons['min_service_pressure_m']} m (after window)",
         "value": round(after["min_pressure_m"], 2), "limit": cons["min_service_pressure_m"],
         "passed": after["min_pressure_m"] >= cons["min_service_pressure_m"]},
        {"name": f"delivered/requested demand >= {cons['min_delivered_fraction']:.0%} (after window)",
         "value": round(after["delivered_fraction"], 4), "limit": cons["min_delivered_fraction"],
         "passed": after["delivered_fraction"] >= cons["min_delivered_fraction"]},
        {"name": "tank does not empty (after window)", "value": after["tank_empty_samples"], "limit": 0,
         "passed": after["tank_empty_samples"] == 0},
    ]
    passed = all(c["passed"] for c in checks)
    iv = w["intervention"]
    labels = [R.SIMULATION_LABEL]
    if iv.kind == "isolate_pipe":
        labels.append(R.ISOLATION_ASSUMPTION)
    if iv.kind == "restore_valve":
        labels.append("Valve restoration returns the throttled pipe to its original minor loss. Net1 has no valve "
                      "elements; the throttling itself is a modelling surrogate for a partly closed valve.")
    if iv.kind == "restore_pump":
        labels.append("Pump restoration = nominal speed 1.0, i.e. the outcome of repair/maintenance, not a control "
                      "setpoint. The pump stays under Net1's tank-level on/off control.")
    return {
        "intervention": {"kind": iv.kind, "target": iv.target, "time_h": t_int / 3600,
                         "closed_links": w["closed_links"],
                         "physical_valve_availability": ({p: R.valve_availability(p) for p in conn["closed_pipes"]}
                                                         if iv.kind == "isolate_pipe" else None)},
        "fault": {"event_class": w["fault"].event_class, "location": w["fault"].location},
        "mismatch": w["mismatch"], "seed": w["seed"], "demand_model": "pressure-driven (EPANET PDA)",
        "before": before, "after": after, "after_without_intervention": after_without,
        "effect_vs_no_intervention": {
            # not reported when negative pressures make emitter flow non-physical
            "leak_volume_avoided_m3": (None if after["negative_pressure_samples"] or after_without["negative_pressure_samples"]
                                       else after_without["leak_volume_m3"] - after["leak_volume_m3"]),
            "demand_deficit_change_m3": after["demand_deficit_m3"] - after_without["demand_deficit_m3"],
            "min_pressure_change_m": after["min_pressure_m"] - after_without["min_pressure_m"],
            "mean_pressure_change_m": {j: after["mean_pressure_m"][j] - after_without["mean_pressure_m"][j]
                                       for j in C.JUNCTIONS},
        },
        "connectivity": conn, "constraint_checks": checks,
        "verdict": R.VERDICT_PASS if passed else R.VERDICT_FAIL, "passed_configured_constraints": passed,
        "labels": labels,
        "warnings": [f"{name} window has {o['negative_pressure_samples']} junction-samples with negative pressure: "
                     "leak (emitter) flow and delivered demand there are not physically meaningful"
                     for name, o in (("after", after), ("after_without_intervention", after_without))
                     if o["negative_pressure_samples"]],
    }
