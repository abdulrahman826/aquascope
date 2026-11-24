"""Scenario generation on Net1 using EPyT-Flow (EPANET).

Hydraulic events (leaks, pump, valve, demand) are injected into the EPANET simulation.
Sensor faults are measurement-layer events: they are applied with EPyT-Flow's sensor-fault
models to the clean simulated readings, which keeps the clean "true" state available for
the virtual field-measurement used by the investigation module.
"""
from __future__ import annotations

import hashlib
import warnings
import json
import math
import time
from functools import lru_cache
from typing import Literal, Optional

import numpy as np
from epanet_plus import EpanetConstants
from pydantic import BaseModel, Field, field_validator, model_validator

from epyt_flow.data.networks import load_inp
from epyt_flow.simulation import ScenarioSimulator
from epyt_flow.simulation.events import (
    AbruptLeakage, IncipientLeakage, PumpSpeedEvent, SensorFaultConstant, SensorFaultDrift,
    SensorFault, SystemEvent,
)
from epyt_flow.uncertainty import ModelUncertainty, RelativeUniformUncertainty

from . import config as C

warnings.filterwarnings("ignore", message="No pattern for pump")


class SimulationError(RuntimeError):
    """Raised when EPANET fails; never replaced by fabricated data."""


# ---------------------------------------------------------------- custom EPyT-Flow events
class PipeThrottleEvent(SystemEvent):
    """Partial closure of an in-line isolation valve on a pipe (Net1 has no valve elements),
    represented as an added minor-loss coefficient on that pipe while active."""

    def __init__(self, link_id: str, minor_loss: float, **kwds):
        self.link_id, self.minor_loss, self._orig = link_id, float(minor_loss), None
        super().__init__(**kwds)

    def init(self, epanet_api) -> None:
        super().init(epanet_api)
        self._idx = epanet_api.getlinkindex(self.link_id)
        self._orig = epanet_api.getlinkvalue(self._idx, EpanetConstants.EN_MINORLOSS)

    def apply(self, cur_time: int) -> None:
        self._epanet_api.setlinkvalue(self._idx, EpanetConstants.EN_MINORLOSS, self.minor_loss)

    def exit(self, cur_time) -> None:
        self._epanet_api.setlinkvalue(self._idx, EpanetConstants.EN_MINORLOSS, self._orig)


class DemandSurgeEvent(SystemEvent):
    """Abnormal consumption: base demand of one junction multiplied while active."""

    def __init__(self, node_id: str, multiplier: float, **kwds):
        self.node_id, self.multiplier = node_id, float(multiplier)
        super().__init__(**kwds)

    def init(self, epanet_api) -> None:
        super().init(epanet_api)
        self._idx = epanet_api.getnodeindex(self.node_id)
        self._orig = epanet_api.getnodevalue(self._idx, EpanetConstants.EN_BASEDEMAND)

    def apply(self, cur_time: int) -> None:
        self._epanet_api.setnodevalue(self._idx, EpanetConstants.EN_BASEDEMAND, self._orig * self.multiplier)

    def exit(self, cur_time) -> None:
        self._epanet_api.setnodevalue(self._idx, EpanetConstants.EN_BASEDEMAND, self._orig)


class SensorFaultStuck(SensorFault):
    """Reading frozen at the last value before the fault starts."""

    def apply_sensor_fault(self, cur_multiplier, sensor_reading, cur_time):  # unused
        return sensor_reading

    def apply(self, sensor_readings: np.ndarray, sensor_readings_time: np.ndarray) -> np.ndarray:
        on = sensor_readings_time >= self.start_time
        if on.any():
            first = int(np.argmax(on))
            sensor_readings[on] = sensor_readings[max(first - 1, 0)]
        return sensor_readings


class SensorFaultGaussianSeeded(SensorFault):
    """EPyT-Flow's Gaussian sensor fault, but drawing from a seeded generator (reproducibility)."""

    def __init__(self, std: float, rng: np.random.Generator, **kwds):
        self._std, self._rng = std, rng
        super().__init__(**kwds)

    def apply_sensor_fault(self, cur_multiplier, sensor_reading, cur_time):
        return sensor_reading + cur_multiplier * self._rng.normal(0.0, self._std)


# ---------------------------------------------------------------- scenario specification
EventClass = Literal[tuple(C.CLASSES)]


class ScenarioSpec(BaseModel):
    event_class: EventClass = "normal"
    seed: int = Field(0, ge=0, le=2**31 - 1)
    severity: float = Field(0.5, ge=0.0, le=1.0)
    mismatch: float = Field(0.05, ge=0.0, le=0.3)
    pressure_noise: float = Field(C.DEFAULT_PRESSURE_NOISE_M, ge=0.0, le=2.0)
    location: Optional[str] = None
    start_time: Optional[int] = Field(None, ge=C.EVENT_START_MIN, le=C.EVENT_START_MAX)

    @field_validator("start_time")
    @classmethod
    def _aligned(cls, v):
        if v is not None and v % C.REPORT_STEP:
            raise ValueError(f"start_time must be a multiple of {C.REPORT_STEP} s")
        return v

    @model_validator(mode="after")
    def _check_location(self):
        valid = valid_locations(self.event_class)
        if self.location is not None and self.location not in valid:
            raise ValueError(f"location '{self.location}' invalid for {self.event_class}; expected one of {valid}")
        return self


def valid_locations(event_class: str) -> list[str]:
    fam = C.CLASS_FAMILY[event_class]
    if event_class in C.LEAK_CLASSES:
        return C.LEAK_PIPES
    if event_class == "valve_disturbance":
        return C.VALVE_PIPES
    if fam == "sensor":
        return C.PRESSURE_SENSORS
    if event_class == "pump_disturbance":
        return [C.PUMP_ID]
    if event_class == "demand_anomaly":
        return C.DEMAND_NODES
    return []


def resolve(spec: ScenarioSpec) -> ScenarioSpec:
    """Fill unspecified location/start deterministically from the seed."""
    rng = np.random.default_rng([spec.seed, 7])
    locs = valid_locations(spec.event_class)
    loc = spec.location or (str(rng.choice(locs)) if locs else None)
    n_slots = (C.EVENT_START_MAX - C.EVENT_START_MIN) // C.REPORT_STEP
    start = spec.start_time or (C.EVENT_START_MIN + int(rng.integers(0, n_slots + 1)) * C.REPORT_STEP)
    if spec.event_class == "normal":
        loc, start = None, None
    return spec.model_copy(update={"location": loc, "start_time": start})


def severity_params(event_class: str, s: float) -> dict:
    """Physical magnitude for a severity in [0, 1]. Ranges chosen from Net1 hydraulics (see docs)."""
    return {
        "normal": {},
        "abrupt_leak": {"diameter_m": 0.012 + 0.018 * s},
        "incipient_leak": {"diameter_m": 0.012 + 0.018 * s, "ramp_hours": 18 - 8 * s},
        "sensor_bias": {"shift_m": 1.0 + 3.0 * s},
        "sensor_drift": {"drift_m_per_day": 2.0 + 4.0 * s},
        "sensor_noise": {"std_m": 0.6 + 1.4 * s},
        "sensor_stuck": {},
        "pump_disturbance": {"speed": 0.92 - 0.17 * s},
        "valve_disturbance": {"headloss_m": 3.0 + 7.0 * s},
        "demand_anomaly": {"multiplier": 1.5 + 1.5 * s},
    }[event_class]


# ---------------------------------------------------------------- core simulation
@lru_cache(maxsize=1)
def _base_config():
    if not C.NET1_INP.exists():
        raise SimulationError(f"Network file missing: {C.NET1_INP}")
    return load_inp(str(C.NET1_INP), flow_units_id=C.FLOW_UNITS_CMH)


def hydraulic_run(system_events: list = (), mismatch: float = 0.0, mismatch_seed: int = 0) -> dict:
    """Run EPANET via EPyT-Flow. Returns clean pressures at all junctions and flows at all pipes/pump."""
    try:
        with ScenarioSimulator(scenario_config=_base_config()) as sim:
            sim.set_general_parameters(simulation_duration=C.SIM_DURATION, hydraulic_time_step=C.HYD_STEP,
                                       reporting_time_step=C.REPORT_STEP)
            sim.set_pressure_sensors(C.JUNCTIONS)
            sim.set_flow_sensors(C.PIPES + [C.PUMP_ID])
            if mismatch > 0:
                u = lambda: RelativeUniformUncertainty(low=1 - mismatch, high=1 + mismatch)
                sim.set_model_uncertainty(ModelUncertainty(
                    global_pipe_roughness_uncertainty=u(), global_base_demand_uncertainty=u(),
                    global_demand_pattern_uncertainty=u(), seed=int(mismatch_seed)))
            for ev in system_events:
                if isinstance(ev, (AbruptLeakage, IncipientLeakage)):
                    sim.add_leakage(ev)
                elif isinstance(ev, PumpSpeedEvent):
                    sim.add_actuator_event(ev)
                else:
                    sim.add_system_event(ev)
            res = sim.run_simulation()
            p = np.asarray(res.get_data_pressures(), dtype=float)
            f = np.asarray(res.get_data_flows(), dtype=float) / 3.6  # m3/h -> L/s
            t = np.asarray(res.sensor_readings_time, dtype=int)
    except Exception as e:  # EPANET / EPyT errors surface as SimulationError
        raise SimulationError(f"EPANET simulation failed: {type(e).__name__}: {e}") from e
    if not np.isfinite(p).all() or not np.isfinite(f).all():
        raise SimulationError("EPANET returned non-finite values")
    links = C.PIPES + [C.PUMP_ID]
    return {"time": t, "pressure": {n: p[:, i] for i, n in enumerate(C.JUNCTIONS)},
            "flow": {l: f[:, i] for i, l in enumerate(links)}}


def hydraulic_events(event_class: str, location: str, start: int, params: dict) -> list:
    if event_class == "abrupt_leak":
        return [AbruptLeakage(link_id=location, diameter=float(params["diameter_m"]), start_time=start, end_time=C.SIM_DURATION)]
    if event_class == "incipient_leak":
        peak = min(start + int(params["ramp_hours"] * 3600), C.SIM_DURATION - C.REPORT_STEP)
        return [IncipientLeakage(link_id=location, diameter=float(params["diameter_m"]), start_time=start,
                                 end_time=C.SIM_DURATION, peak_time=peak)]
    if event_class == "pump_disturbance":
        return [PumpSpeedEvent(pump_id=location, pump_speed=float(params["speed"]), time=start)]
    if event_class == "valve_disturbance":
        return [PipeThrottleEvent(link_id=location, minor_loss=valve_minor_loss(location, params["headloss_m"]), start_time=start, end_time=C.SIM_DURATION)]
    if event_class == "demand_anomaly":
        return [DemandSurgeEvent(node_id=location, multiplier=params["multiplier"], start_time=start, end_time=C.SIM_DURATION)]
    return []


def valve_minor_loss(pipe: str, headloss_m: float) -> float:
    """Minor-loss coefficient that produces `headloss_m` at the pipe's nominal mean velocity."""
    q = float(np.abs(nominal()["flow"][pipe]).mean()) / 1000.0
    v = q / (math.pi * C.PIPE_DIAMETER_M[pipe] ** 2 / 4)
    return headloss_m / max(v * v / 19.62, 1e-4)


def sensor_fault(event_class: str, location: str, start: int, params: dict, rng) -> Optional[SensorFault]:
    sign = float(params.get("sign", 1.0))
    kw = {"sensor_id": location, "sensor_type": 1, "start_time": start, "end_time": C.SIM_DURATION}  # 1 = SENSOR_TYPE_NODE_PRESSURE
    if event_class == "sensor_bias":
        return SensorFaultConstant(constant_shift=float(params["shift_m"]) * sign, **kw)
    if event_class == "sensor_drift":
        return SensorFaultDrift(coef=float(params["drift_m_per_day"]) / C.DAY * sign, **kw)
    if event_class == "sensor_noise":
        return SensorFaultGaussianSeeded(std=float(params["std_m"]), rng=rng, **kw)
    if event_class == "sensor_stuck":
        return SensorFaultStuck(**kw)
    return None


def scenario_id(spec: ScenarioSpec) -> str:
    h = hashlib.sha1(json.dumps(spec.model_dump(), sort_keys=True).encode()).hexdigest()[:10]
    return f"{spec.event_class}-{h}"


def run_scenario(spec: ScenarioSpec) -> dict:
    spec = resolve(spec)
    params = severity_params(spec.event_class, spec.severity)
    rng = np.random.default_rng([spec.seed, 11])
    t0 = time.perf_counter()
    hyd = hydraulic_run(hydraulic_events(spec.event_class, spec.location, spec.start_time, params),
                        spec.mismatch, mismatch_seed=spec.seed)
    times = hyd["time"]
    pressures = {}
    fault = sensor_fault(spec.event_class, spec.location, spec.start_time, params, rng) \
        if spec.event_class in C.SENSOR_CLASSES else None
    if fault is not None:
        params = params | {"sign": float(getattr(fault, "constant_shift", getattr(fault, "coef", 1.0)) > 0) * 2 - 1} \
            if spec.event_class in ("sensor_bias", "sensor_drift") else params
    for s in C.PRESSURE_SENSORS:
        clean = hyd["pressure"][s].copy()
        noisy = clean + rng.normal(0.0, spec.pressure_noise, clean.shape) if spec.pressure_noise > 0 else clean
        if fault is not None and s == spec.location:
            noisy = fault.apply(noisy.copy(), times)
        pressures[s] = noisy
    flows = {l: hyd["flow"][l] * (1 + rng.normal(0.0, C.DEFAULT_FLOW_NOISE_REL, times.shape)) for l in C.FLOW_SENSORS}
    labels = np.zeros(len(times), dtype=int)
    if spec.start_time is not None:
        labels[times >= spec.start_time] = 1
    return {
        "scenario_id": scenario_id(spec),
        "seed": spec.seed,
        "network_id": C.NETWORK_ID,
        "simulation_mode": C.SIMULATION_MODE,
        "spec": spec.model_dump(),
        "ground_truth": {"event_class": spec.event_class, "family": C.CLASS_FAMILY[spec.event_class],
                         "location": spec.location, "start_time": spec.start_time,
                         "severity": spec.severity, "physical_parameters": params,
                         "per_step_event_active": labels.tolist()},
        "sensor_config": {"pressure_sensors": C.PRESSURE_SENSORS, "flow_sensors": C.FLOW_SENSORS,
                          "pressure_noise_std_m": spec.pressure_noise, "flow_noise_rel": C.DEFAULT_FLOW_NOISE_REL,
                          "units": {"pressure": "m", "flow": "L/s"}},
        "model_mismatch": {"level": spec.mismatch, "perturbed": ["pipe roughness", "base demand", "demand pattern"],
                           "distribution": "uniform relative", "seed": spec.seed},
        "telemetry": {"time": times.tolist(), "pressure": {k: v.tolist() for k, v in pressures.items()},
                      "flow": {k: v.tolist() for k, v in flows.items()}},
        # Clean full-network state: hidden from diagnosis; used only for virtual field measurements.
        "hidden_state": {"pressure": {k: v.tolist() for k, v in hyd["pressure"].items()}},
        "metadata": {"duration_s": C.SIM_DURATION, "hydraulic_step_s": C.HYD_STEP, "report_step_s": C.REPORT_STEP,
                     "reference_period_s": [0, C.REFERENCE_END], "runtime_s": round(time.perf_counter() - t0, 3)},
    }


@lru_cache(maxsize=1)
def nominal() -> dict:
    """The diagnostic 'digital twin': nominal Net1 model, no mismatch, no events."""
    return hydraulic_run([], 0.0)


@lru_cache(maxsize=2048)
def _hyp_cached(event_class: str, location: str, start: int, key: tuple) -> dict:
    return hydraulic_run(hydraulic_events(event_class, location, start, dict(key)), 0.0)


def hypothesis_run(event_class: str, location: Optional[str], start: int, params: dict) -> dict:
    """Nominal model with a hypothesised hydraulic event injected (sensor faults -> nominal)."""
    if event_class in C.SENSOR_CLASSES or event_class == "normal":
        return nominal()
    key = tuple(sorted((k, round(float(v), 6)) for k, v in params.items()))
    return _hyp_cached(event_class, location, int(start), key)
