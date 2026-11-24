"""Remediation M4: evaluation machinery (policies, outcome accounting, aggregation, determinism)."""
import pandas as pd
import pytest

from aquascope.dataset import load
from aquascope.remediation.evaluate import POLICIES, TrueNetwork, _agg, process
from aquascope.remediation.simulate import Intervention
from aquascope.simulation import ScenarioSpec


@pytest.fixture(scope="module")
def val():
    return load().query("split == 'val'")


def _row(val, cls):
    return val[val.label == cls].iloc[0].to_dict()


def test_process_hydraulic_scenario_has_all_policies(val):
    r = process(_row(val, "valve_disturbance"))
    assert r["error"] is None
    pol = {x["policy"] for x in r["rows"]}
    assert set(POLICIES) <= pol
    by = {x["policy"]: x for x in r["rows"] if not x["policy"].endswith(":candidate")}
    assert by["oracle"]["resolved"] and not by["do_nothing"]["acted"]
    assert any(x["policy"].endswith(":candidate") and "predicted_benefit" in x for x in r["rows"])


def test_process_non_hydraulic_scenario(val):
    r = process(_row(val, "sensor_bias"))
    pol = {x["policy"] for x in r["rows"]}
    assert "engine_oracle_confirmation" not in pol and "random_feasible_mean" not in pol
    assert all(not x["acted"] for x in r["rows"] if x["policy"] in ("engine_pipeline", "oracle", "do_nothing"))


def test_process_is_deterministic(val):
    a, b = process(_row(val, "abrupt_leak")), process(_row(val, "abrupt_leak"))
    a["summary"].pop("runtime_s"); b["summary"].pop("runtime_s")
    assert a == b


def test_attempted_inapplicable_action_counts_as_ineffective():
    """'Re-opening' a valve on a network without a throttled valve changes nothing hydraulically but is still an
    unnecessary intervention (methodology fix made on the validation split before the test run)."""
    tn = TrueNetwork(ScenarioSpec(event_class="normal", seed=3, mismatch=0.0))
    o = tn.outcome(Intervention("restore_valve", "11"))
    assert o["applicable"] is False and o["acted"] is True and o["ineffective_or_harmful"] is True


def test_aggregation_rates():
    rows = pd.DataFrame([
        {"scenario_id": "a", "policy": "naive_top1", "acted": True, "resolved": True, "ineffective_or_harmful": False,
         "actual_passed_constraints": True, "actual_benefit": 10.0},
        {"scenario_id": "b", "policy": "naive_top1", "acted": True, "resolved": False, "ineffective_or_harmful": True,
         "actual_passed_constraints": False, "actual_benefit": 0.0},
        {"scenario_id": "c", "policy": "naive_top1", "acted": False, "resolved": False, "ineffective_or_harmful": False,
         "actual_passed_constraints": True, "actual_benefit": 0.0},
    ])
    out = _agg(rows, pd.Series({"a": 10.0, "b": 20.0, "c": 5.0}))["naive_top1"]
    assert out["n"] == 3 and out["intervention_rate"] == pytest.approx(2 / 3)
    assert out["resolved_rate"] == pytest.approx(1 / 3) and out["ineffective_or_harmful_rate"] == pytest.approx(1 / 3)
    assert out["constraint_violation_rate_when_acting"] == pytest.approx(0.5)
    assert out["mean_regret_fraction"] == pytest.approx((0 + 1 + 1) / 3)
