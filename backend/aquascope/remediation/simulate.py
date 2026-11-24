"""Pressure-driven intervention simulation on Net1 (EPyT-Flow / EPANET).

A run = the (true or nominal) network + one fault + optionally one intervention at `t_int`,
simulated to `horizon` (default 96 h) under pressure-driven analysis. The fault is rebuilt from the same
classes, parameters and mismatch seed as the existing scenario generator, so the first 72 h reproduce the
existing scenario's hidden network state (verified by tests); only the duration and demand model differ.

Interventions supported (M1):
  none          — reference run
  isolate_pipe  — close a pipe; if the fault is a leak on that pipe, BOTH halves created by EPyT-Flow's
                  leak split (`<id>` and `leak_pipe_<id>`) are closed. Idealised isolation (modelling assumption).
  restore_valve — return a throttled pipe to its original minor loss (only for a valve_disturbance fault there)
  restore_pump  — return pump speed to nominal 1.0 (only for a pump_disturbance fault; = repair outcome)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np
from epanet_plus import EpanetConstants

from epyt_flow.simulation import ScenarioSimulator
from epyt_flow.simulation.events import AbruptLeakage, IncipientLeakage, PumpSpeedEvent, SystemEvent
from epyt_flow.uncertainty import ModelUncertainty, RelativeUniformUncertainty

from .. import config as C
from .. import simulation as S
from . import config as R

TANK_ID, RESERVOIR_ID = "2", "9"
Kind = Literal["none", "isolate_pipe", "restore_valve", "restore_pump"]


@dataclass(frozen=True)
class Fault:
    event_class: str
    location: Optional[str]
    start_time: Optional[int]
    params: tuple = ()                     # sorted (name, value) pairs

    @property
    def p(self) -> dict:
        return dict(self.params)


def fault_from_spec(spec: S.ScenarioSpec) -> Fault:
    """Same resolution (location, start, severity parameters) as the existing scenario generator."""
    r = S.resolve(spec)
    return Fault(r.event_class, r.location, r.start_time, tuple(sorted(S.severity_params(r.event_class, r.severity).items())))


@dataclass(frozen=True)
class Intervention:
    kind: Kind = "none"
    target: Optional[str] = None


class CloseLinksEvent(SystemEvent):
    """Holds the given links CLOSED from start_time (idealised isolation)."""

    def __init__(self, link_ids: list[str], **kwds):
        self.link_ids = list(link_ids)
        super().__init__(**kwds)

    def apply(self, cur_time: int) -> None:
        for l in self.link_ids:
            self._epanet_api.setlinkvalue(self._epanet_api.getlinkindex(l), EpanetConstants.EN_STATUS,
                                          EpanetConstants.EN_CLOSED)


class PumpSpeedRestoreEvent(PumpSpeedEvent):
    """EPyT-Flow 0.17.4 PumpSpeedEvent crashes when the pump already has a speed pattern (getlinkvalue returns
    the pattern index as float; setpattern needs int). Same logic with the index cast to int."""

    def apply(self, cur_time: int) -> None:
        pump_idx = self._epanet_api.get_link_idx(self.pump_id)
        pattern_idx = int(self._epanet_api.getlinkvalue(pump_idx, EpanetConstants.EN_LINKPATTERN))
        if pattern_idx == 0:
            raise S.SimulationError(f"pump '{self.pump_id}' has no speed pattern to restore")
        self._epanet_api.setpattern(pattern_idx, [self.pump_speed], 1)


def validate(fault: Fault, iv: Intervention, t_int: int, horizon: int) -> None:
    if iv.kind not in ("none", "isolate_pipe", "restore_valve", "restore_pump"):
        raise ValueError(f"unknown intervention '{iv.kind}'")
    if horizon % C.REPORT_STEP or horizon <= C.SIM_DURATION:
        raise ValueError("horizon must exceed 72 h and be a multiple of the reporting step")
    if iv.kind == "none":
        return
    if t_int % C.HYD_STEP or not (C.REFERENCE_END < t_int < horizon):
        raise ValueError(f"intervention time must be a multiple of {C.HYD_STEP} s inside (24 h, horizon)")
    if fault.start_time is not None and t_int <= fault.start_time:
        raise ValueError("intervention must come after the fault starts")
    if iv.kind == "isolate_pipe" and iv.target not in C.PIPES:
        raise ValueError(f"cannot isolate '{iv.target}': not a Net1 pipe {C.PIPES}")
    if iv.kind == "restore_valve" and not (fault.event_class == "valve_disturbance" and iv.target == fault.location):
        raise ValueError(f"no throttled valve to restore at pipe '{iv.target}' for fault {fault.event_class}@{fault.location}")
    if iv.kind == "restore_pump" and not (fault.event_class == "pump_disturbance" and iv.target == C.PUMP_ID):
        raise ValueError(f"no pump disturbance to restore at '{iv.target}' for fault {fault.event_class}")


def _fault_events(fault: Fault, iv: Intervention, t_int: int, horizon: int) -> list:
    c, loc, st, p = fault.event_class, fault.location, fault.start_time, fault.p
    if c == "abrupt_leak":
        return [AbruptLeakage(link_id=loc, diameter=float(p["diameter_m"]), start_time=st, end_time=horizon)]
    if c == "incipient_leak":
        return [IncipientLeakage(link_id=loc, diameter=float(p["diameter_m"]), start_time=st, end_time=horizon,
                                 peak_time=st + int(p["ramp_hours"] * 3600))]
    if c == "pump_disturbance":
        ev = [PumpSpeedEvent(pump_id=loc, pump_speed=float(p["speed"]), time=st)]
        if iv.kind == "restore_pump":
            ev.append(PumpSpeedRestoreEvent(pump_id=loc, pump_speed=1.0, time=t_int))
        return ev
    if c == "valve_disturbance":
        # exit() (restoring the original minor loss) runs at the first step with t > end_time, i.e. at t_int
        end = t_int - 1 if iv.kind == "restore_valve" else horizon
        return [S.PipeThrottleEvent(link_id=loc, minor_loss=S.valve_minor_loss(loc, p["headloss_m"]),
                                    start_time=st, end_time=end)]
    if c == "demand_anomaly":
        return [S.DemandSurgeEvent(node_id=loc, multiplier=p["multiplier"], start_time=st, end_time=horizon)]
    return []   # normal operation and sensor faults have no hydraulic effect


def run(fault: Fault, iv: Intervention = Intervention(), *, mismatch: float, seed: int,
        t_int: int = R.DECISION_TIME, horizon: int = R.HORIZON, pressure_driven: bool = True) -> dict:
    validate(fault, iv, t_int, horizon)
    try:
        with ScenarioSimulator(scenario_config=S._base_config()) as sim:
            kw = dict(simulation_duration=horizon, hydraulic_time_step=C.HYD_STEP, reporting_time_step=C.REPORT_STEP)
            if pressure_driven:
                kw["demand_model"] = R.DEMAND_MODEL
            sim.set_general_parameters(**kw)
            if mismatch > 0:   # identical construction to simulation.hydraulic_run -> same perturbation per seed
                u = lambda: RelativeUniformUncertainty(low=1 - mismatch, high=1 + mismatch)
                sim.set_model_uncertainty(ModelUncertainty(
                    global_pipe_roughness_uncertainty=u(), global_base_demand_uncertainty=u(),
                    global_demand_pattern_uncertainty=u(), seed=int(seed)))
            for ev in _fault_events(fault, iv, t_int, horizon):
                if isinstance(ev, (AbruptLeakage, IncipientLeakage)):
                    sim.add_leakage(ev)
                elif isinstance(ev, PumpSpeedEvent):
                    sim.add_actuator_event(ev)
                else:
                    sim.add_system_event(ev)
            links = sim.epanet_api.get_all_links_id()
            nodes = sim.epanet_api.get_all_nodes_id()
            closed = []
            if iv.kind == "isolate_pipe":
                closed = [iv.target] + [l for l in links if l == f"leak_pipe_{iv.target}"]
                if fault.event_class in C.LEAK_CLASSES and fault.location == iv.target and len(closed) != 2:
                    raise S.SimulationError(f"leak split halves of pipe {iv.target} not found: {links}")
                sim.add_system_event(CloseLinksEvent(closed, start_time=t_int, end_time=horizon))
            leak_nodes = [n for n in nodes if n.startswith("leak_node_")]
            extra_links = [l for l in links if l.startswith("leak_pipe_")]
            sim.set_pressure_sensors(C.JUNCTIONS)
            sim.set_demand_sensors(C.JUNCTIONS + leak_nodes)
            sim.set_flow_sensors(C.PIPES + [C.PUMP_ID] + extra_links)
            sim.set_tank_sensors([TANK_ID])
            res = sim.run_simulation()
            t = np.asarray(res.sensor_readings_time, dtype=int)
            p = np.asarray(res.get_data_pressures(), dtype=float)
            d = np.asarray(res.get_data_demands(), dtype=float) / 3.6      # m3/h -> L/s
            f = np.asarray(res.get_data_flows(), dtype=float) / 3.6
            v = np.asarray(res.get_data_tanks_water_volume(), dtype=float)[:, 0]
    except S.SimulationError:
        raise
    except Exception as e:
        raise S.SimulationError(f"EPANET intervention simulation failed: {type(e).__name__}: {e}") from e
    if not (np.isfinite(p).all() and np.isfinite(d).all() and np.isfinite(f).all() and np.isfinite(v).all()):
        raise S.SimulationError("EPANET returned non-finite values")
    nj = len(C.JUNCTIONS)
    return {
        "time": t, "pressure": {n: p[:, i] for i, n in enumerate(C.JUNCTIONS)},
        "demand": {n: d[:, i] for i, n in enumerate(C.JUNCTIONS)},
        "leak_flow": d[:, nj:].sum(axis=1) if leak_nodes else np.zeros(len(t)),
        "flow": {l: f[:, i] for i, l in enumerate(C.PIPES + [C.PUMP_ID] + extra_links)},
        "tank_volume_m3": v, "closed_links": closed, "pressure_driven": pressure_driven,
        "fault": fault, "intervention": iv, "t_int": t_int, "horizon": horizon, "mismatch": mismatch, "seed": seed,
    }


def simulate_intervention(fault: Fault, iv: Intervention, *, mismatch: float, seed: int,
                          t_int: int = R.DECISION_TIME, horizon: int = R.HORIZON) -> dict:
    """Intervention run, no-intervention reference (both pressure-driven) and requested demand.
    Requested demand comes from a demand-driven run of the same faulty network (DDA always delivers the
    full requested demand; its pressures are not used)."""
    kw = dict(mismatch=mismatch, seed=seed, t_int=t_int, horizon=horizon)
    return {"with": run(fault, iv, **kw), "without": run(fault, Intervention(), **kw),
            "requested": run(fault, Intervention(), pressure_driven=False, **kw)["demand"]}
