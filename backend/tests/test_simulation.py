import numpy as np
import pytest
from pydantic import ValidationError

from aquascope import config as C
from aquascope.simulation import ScenarioSpec, SimulationError, hydraulic_run, nominal, run_scenario


def _sc(cls, **kw):
    return run_scenario(ScenarioSpec(event_class=cls, seed=kw.pop("seed", 3), mismatch=kw.pop("mismatch", 0.0),
                                     pressure_noise=kw.pop("pressure_noise", 0.0), **kw))


def test_normal_scenario_schema():
    sc = _sc("normal")
    for k in ("scenario_id", "seed", "network_id", "ground_truth", "telemetry", "sensor_config", "model_mismatch",
              "metadata", "hidden_state"):
        assert k in sc
    t = sc["telemetry"]
    assert len(t["time"]) == C.SIM_DURATION // C.REPORT_STEP + 1
    assert set(t["pressure"]) == set(C.PRESSURE_SENSORS) and set(t["flow"]) == set(C.FLOW_SENSORS)
    assert sc["ground_truth"]["event_class"] == "normal" and sc["ground_truth"]["start_time"] is None
    assert sum(sc["ground_truth"]["per_step_event_active"]) == 0


@pytest.mark.parametrize("cls", C.CLASSES[1:])
def test_each_event_class_ground_truth(cls):
    sc = _sc(cls, severity=0.7)
    gt = sc["ground_truth"]
    assert gt["event_class"] == cls and gt["family"] == C.CLASS_FAMILY[cls]
    assert C.EVENT_START_MIN <= gt["start_time"] <= C.EVENT_START_MAX
    lab = np.array(gt["per_step_event_active"]); t = np.array(sc["telemetry"]["time"])
    assert (lab == (t >= gt["start_time"])).all()
    # the event must change observations after onset (physically distinguishable from nominal)
    nom = nominal()
    i = gt["start_time"] // C.REPORT_STEP
    dev = max(np.abs(np.array(sc["telemetry"]["pressure"][s])[i:] - nom["pressure"][s][i:]).max() for s in C.PRESSURE_SENSORS)
    dq = np.abs(np.array(sc["telemetry"]["flow"]["9"])[i:] - nom["flow"]["9"][i:]).max()
    assert dev > 0.05 or dq > 0.5, f"{cls} produced no observable change"
    # before onset, telemetry equals the nominal model (no mismatch, no noise); leak events split the pipe
    # at a new node in EPyT-Flow, which shifts pressures by <= 0.01 m even before the leak opens
    assert np.allclose(np.array(sc["telemetry"]["pressure"]["22"])[: i - 1], nom["pressure"]["22"][: i - 1], atol=0.02)


def test_sensor_faults_do_not_change_hydraulics():
    sc = _sc("sensor_bias", location="22", severity=1.0)
    nom = nominal()
    for n in C.JUNCTIONS:
        assert np.allclose(sc["hidden_state"]["pressure"][n], nom["pressure"][n])
    assert not np.allclose(sc["telemetry"]["pressure"]["22"], nom["pressure"]["22"])


def test_leak_lowers_true_pressure():
    sc = _sc("abrupt_leak", location="122", severity=1.0)
    end = np.array(sc["hidden_state"]["pressure"]["32"])[-10:].mean()
    assert end < nominal()["pressure"]["32"][-10:].mean() - 1.0


def test_reproducible_same_seed_and_distinct_seeds():
    a = _sc("sensor_noise", seed=5, mismatch=0.15, pressure_noise=0.1)
    b = _sc("sensor_noise", seed=5, mismatch=0.15, pressure_noise=0.1)
    c = _sc("sensor_noise", seed=6, mismatch=0.15, pressure_noise=0.1)
    assert a["telemetry"] == b["telemetry"] and a["scenario_id"] == b["scenario_id"]
    assert a["telemetry"]["pressure"]["11"] != c["telemetry"]["pressure"]["11"]


@pytest.mark.parametrize("bad", [
    {"event_class": "abrupt_leak", "location": "999"},
    {"event_class": "sensor_bias", "location": "12"},        # 12 has no sensor
    {"event_class": "abrupt_leak", "severity": 1.5},
    {"event_class": "abrupt_leak", "start_time": 108001},    # not aligned to report step
    {"event_class": "abrupt_leak", "start_time": 3600},      # inside reference day
    {"event_class": "earthquake"},
])
def test_invalid_configuration_rejected(bad):
    with pytest.raises(ValidationError):
        ScenarioSpec(**bad)


def test_simulation_error_is_raised_not_fabricated():
    class Broken:
        start_time, end_time = 0, 10
    with pytest.raises(SimulationError):
        hydraulic_run([Broken()])
