"""Fixes after M4: telemetry pump-speed calibration, verification measurement, K-way confirmation, policy, fresh test set."""
import pandas as pd
import pytest

from aquascope.dataset import load
from aquascope.diagnosis import diagnose_scenario
from aquascope.investigation import _state_key, simulate_measurement
from aquascope.remediation import config as R
from aquascope.remediation import confirmation as K
from aquascope.remediation.evaluate import FRESH_SEED_BASE, fresh_test_rows
from aquascope.remediation.rank import estimate_pump_speed
from aquascope.remediation.select_policy import grid
from aquascope.remediation.verification import kway_posterior, verification_measurement
from aquascope.simulation import ScenarioSpec, run_scenario


@pytest.mark.parametrize("mm", [0.0, 0.05, 0.15])
@pytest.mark.parametrize("sev", [0.0, 0.6, 1.0])
def test_pump_speed_calibration(mm, sev):
    """Calibrated speed within 0.02 of truth (measured max error 0.012 across these 9 cases)."""
    sc = run_scenario(ScenarioSpec(event_class="pump_disturbance", seed=31, severity=sev, mismatch=mm))
    sp, note = estimate_pump_speed(sc["telemetry"], sc["ground_truth"]["start_time"])
    assert abs(sp - sc["ground_truth"]["physical_parameters"]["speed"]) < 0.02 and "calibrated" in note


def test_pump_speed_on_healthy_network_is_nominal():
    sc = run_scenario(ScenarioSpec(event_class="normal", seed=31, mismatch=0.0))
    sp, _ = estimate_pump_speed(sc["telemetry"], 36 * 3600)
    assert sp >= 0.99


@pytest.fixture(scope="module")
def confident_pump():
    sc = run_scenario(ScenarioSpec(event_class="pump_disturbance", seed=31, severity=0.6, mismatch=0.05))
    return sc, diagnose_scenario(sc)


def test_verification_measurement_equal_priors(confident_pump):
    sc, d = confident_pump
    v = verification_measurement(sc, d)
    assert len(v["hypotheses"]) == 2 and all(h["probability"] == 0.5 for h in v["hypotheses"])
    assert v["hypotheses"][0]["event_class"] == d["hypotheses"][0]["event_class"]
    seps = [c["separation"] for c in v["candidates"]]
    assert seps == sorted(seps, reverse=True)
    if v["recommended_node"]:
        m = simulate_measurement(sc, v, v["recommended_node"])
        cd = K.confirm_measurement(sc, d, v, m)
        assert cd.status in ("CONFIRMED_BY_INVESTIGATION", "INSUFFICIENT_EVIDENCE")


def test_kway_posterior_is_a_probability(confident_pump):
    sc, d = confident_pump
    win = _state_key({"event_class": "pump_disturbance", "location": "9"})
    k = kway_posterior(sc, d, "10", 100.0, win)
    assert 2 <= k["n_states"] <= 6 and 0.0 <= k["posterior"] <= 1.0
    assert kway_posterior(sc, d, "10", 100.0, ("leak", "nonexistent"))["posterior"] == 0.0


def test_policy_grid_and_configured_policy():
    s = pd.DataFrame([
        {"meas_route": "investigation", "meas_posterior": 0.999, "meas_abs_z_winner": 0.5, "meas_separation": 4.0,
         "meas_consistent": True, "meas_kway_posterior": 0.9, "meas_state_correct": True, "true_class": "abrupt_leak"},
        {"meas_route": "investigation", "meas_posterior": 0.999, "meas_abs_z_winner": 0.5, "meas_separation": 4.0,
         "meas_consistent": True, "meas_kway_posterior": 0.3, "meas_state_correct": False, "true_class": "abrupt_leak"},
    ])
    g = grid(s)
    row = g[(g.min_posterior == 0.95) & (g.max_z == 3.0) & (g.min_kway_posterior == 0.5)].iloc[0]
    assert row.n_confirmed == 1 and row.n_wrong == 0
    assert R.CONFIRMATION_MIN_KWAY_POSTERIOR == 0.5        # value selected on validation (docs/remediation_fixes.md)


def test_fresh_test_set_is_disjoint_and_reproducible():
    rows = fresh_test_rows()
    seeds = {r["seed"] for r in rows}
    assert len(rows) == 600 and len(seeds) == 600 and min(seeds) >= FRESH_SEED_BASE
    assert not seeds & set(load().seed)
    leak_locs = {r["location"] for r in rows if r["label"] in ("abrupt_leak", "incipient_leak")}
    assert leak_locs == {"112", "121", "31"}                   # held-out test pipes only
    assert rows == fresh_test_rows()
