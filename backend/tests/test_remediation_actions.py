"""Remediation M2: diagnosis confirmation states, gating and rule-based action generation."""
import json

import pytest

from aquascope import config as C
from aquascope.diagnosis import diagnose_scenario
from aquascope.investigation import MIN_SEPARATION, recommend, simulate_measurement
from aquascope.remediation import config as R
from aquascope.remediation import confirmation as K
from aquascope.remediation.actions import control_nodes, generate, prescreen_isolation
from aquascope.remediation.simulate import fault_from_spec, run
from aquascope.simulation import ScenarioSpec, run_scenario

HYD = "HYDRAULIC_INTERVENTION"


def _cd(status, cls, loc, alts=()):
    return K.ConfirmedDiagnosis(status, cls, loc, 0.9, "test", "test", alternative_locations=tuple(alts))


def _hyd(out):
    return [c for c in out["_objects"] if c.category == HYD]


@pytest.fixture(scope="module")
def leak121():
    """Held-out leak at pipe 121 (seed 108): ambiguous diagnosis, investigation at J-32 resolves it."""
    sc = run_scenario(ScenarioSpec(event_class="abrupt_leak", location="121", seed=108, severity=0.5, mismatch=0.05))
    d = diagnose_scenario(sc)
    r = recommend(sc, d)
    return sc, d, r, simulate_measurement(sc, r, r["recommended_node"])


# ---------------------------------------------------------------- confirmation states
def test_ambiguous_diagnosis_is_insufficient_evidence(leak121):
    _, d, _, _ = leak121
    assert d["status"] == "AMBIGUOUS_DIAGNOSIS"
    assert K.from_diagnosis(d).status == "INSUFFICIENT_EVIDENCE"


def test_confident_diagnosis_is_never_confirmed():
    sc = run_scenario(ScenarioSpec(event_class="sensor_bias", location="22", seed=5, severity=0.9, mismatch=0.0))
    d = diagnose_scenario(sc)
    assert d["status"] == "CONFIDENT"
    cd = K.from_diagnosis(d)
    assert cd.status == "CONFIDENT_BUT_UNVERIFIED" and not cd.unlocks_hydraulic


def test_investigation_confirms_when_decisive(leak121):
    sc, d, r, m = leak121
    cd = K.confirm_measurement(sc, d, r, m)
    assert cd.status == "CONFIRMED_BY_INVESTIGATION" and cd.unlocks_hydraulic
    assert cd.location == "121" and cd.event_class in C.LEAK_CLASSES
    assert cd.probability >= R.CONFIRMATION_MIN_POSTERIOR and cd.parameters   # fitted diameter carried along


def test_investigation_rejects_low_separation_node(leak121):
    sc, d, r, _ = leak121
    weak = next(c for c in r["candidates"] if c["separation"] < MIN_SEPARATION)
    cd = K.from_investigation(d, r, simulate_measurement(sc, r, weak["node"]))
    assert cd.status == "INSUFFICIENT_EVIDENCE" and "separates the hypotheses by only" in cd.reason


def test_investigation_rejects_inconsistent_or_weak_posterior(leak121):
    _, d, r, m = leak121
    bad = json.loads(json.dumps(m))
    bad["consistent_with_a_hypothesis"] = False
    assert "neither explanation fits" in K.from_investigation(d, r, bad).reason
    weak = json.loads(json.dumps(m))
    weak["posterior"] = [{"description": "a", "probability": 0.9}, {"description": "b", "probability": 0.1}]
    assert K.from_investigation(d, r, weak).status == "INSUFFICIENT_EVIDENCE"


def test_confirmation_requires_kway_check(leak121):
    _, d, r, m = leak121
    assert "K-way) check not run" in K.from_investigation(d, r, m).reason
    low = K.from_investigation(d, r, m, kway_posterior=0.2)
    assert low.status == "INSUFFICIENT_EVIDENCE" and "fits the reading comparably" in low.reason
    assert K.from_investigation(d, r, m, kway_posterior=0.9).status == "CONFIRMED_BY_INVESTIGATION"


def test_operator_confirmation_validation():
    cd = K.operator_confirmed("abrupt_leak", "121", "op-7", "crew saw water at surface")
    assert cd.status == "OPERATOR_CONFIRMED" and cd.unlocks_hydraulic and cd.source == "operator:op-7"
    assert "not verified by AquaScope" in cd.reason
    with pytest.raises(ValueError, match="operator_id"):
        K.operator_confirmed("abrupt_leak", "121", "  ", "x")
    with pytest.raises(ValueError, match="not valid"):
        K.operator_confirmed("abrupt_leak", "110", "op", "x")        # tank riser is not a leak location in the model
    with pytest.raises(ValueError, match="unknown event class"):
        K.operator_confirmed("earthquake", None, "op", "x")


# ---------------------------------------------------------------- gating
def test_insufficient_evidence_generates_no_hydraulic_actions():
    out = generate(_cd("INSUFFICIENT_EVIDENCE", None, None))
    assert [c.kind for c in out["_objects"]] == ["investigate_first", "none"] and not _hyd(out)


def test_unverified_leak_lists_isolation_locked():
    out = generate(_cd("CONFIDENT_BUT_UNVERIFIED", "abrupt_leak", "121", ["31"]))
    assert out["_objects"][0].kind == "investigate_first"
    hyd = _hyd(out)
    assert hyd and all(not c.unlocked for c in hyd)
    assert all(any("require CONFIRMED_BY_INVESTIGATION" in r for r in c.lock_reasons) for c in hyd)


@pytest.mark.parametrize("status", R.HYDRAULIC_UNLOCK_STATUSES)
def test_confirmed_leak_unlocks_only_confirmed_location(status):
    out = generate(_cd(status, "incipient_leak", "121", ["31", "122"]))
    hyd = {c.target: c for c in _hyd(out)}
    assert hyd["121"].unlocked and hyd["121"].role == "primary"
    assert not hyd["31"].unlocked and not hyd["122"].unlocked
    assert all("alternative hypothesis" in hyd[a].lock_reasons[0] for a in ("31", "122"))
    assert not any(c.kind == "investigate_first" for c in out["_objects"])


@pytest.mark.parametrize("cls,loc", [("sensor_bias", "22"), ("sensor_drift", "11"), ("sensor_noise", "13"),
                                     ("sensor_stuck", "31"), ("demand_anomaly", "22")])
def test_non_hydraulic_classes_get_no_hydraulic_actions(cls, loc):
    out = generate(_cd("OPERATOR_CONFIRMED", cls, loc))
    assert not _hyd(out)
    kinds = {c.kind for c in out["_objects"]}
    assert ("sensor_maintenance" in kinds) if cls in C.SENSOR_CLASSES else ("field_inspection" in kinds)


def test_normal_gets_no_intervention():
    out = generate(_cd("CONFIRMED_BY_INVESTIGATION", "normal", None))
    assert {c.kind for c in out["_objects"]} == {"none", "continue_monitoring"}


# ---------------------------------------------------------------- structural pre-screen
def test_control_nodes_parsed_from_inp():
    assert control_nodes() == {"2": ["9"]}


def test_prescreen_rejects_exactly_the_structural_single_points():
    rejected = {p for p in C.PIPES if prescreen_isolation(p)}
    assert rejected == {"10", "110"}
    assert any("single point of failure" in r for r in prescreen_isolation("10"))
    assert any("[CONTROLS]" in r for r in prescreen_isolation("110"))


def test_screened_isolation_stays_locked_even_when_confirmed():
    out = generate(_cd("OPERATOR_CONFIRMED", "abrupt_leak", "10"))
    c = next(c for c in _hyd(out) if c.target == "10")
    assert c.screened_out and not c.unlocked and not c.lock_reasons


def test_prescreen_agrees_with_simulation():
    """Every pipe the static pre-screen rejects fails the M1 simulated constraints; spot-check a passing pipe."""
    from aquascope.remediation.metrics import evaluate
    from aquascope.remediation.simulate import Fault, Intervention, simulate_intervention
    normal = Fault("normal", None, None)
    for p, should_pass in (("10", False), ("110", False), ("121", True)):
        e = evaluate(simulate_intervention(normal, Intervention("isolate_pipe", p), mismatch=0.0, seed=11))
        assert e["passed_configured_constraints"] is should_pass, p


# ---------------------------------------------------------------- labels, language, simulator compatibility
def test_nothing_is_real_world_executable_and_no_safety_claims():
    for cd in (_cd("OPERATOR_CONFIRMED", "abrupt_leak", "121", ["31"]), _cd("OPERATOR_CONFIRMED", "valve_disturbance", "11"),
               _cd("OPERATOR_CONFIRMED", "pump_disturbance", "9"), _cd("INSUFFICIENT_EVIDENCE", None, None)):
        out = generate(cd)
        assert R.NOT_EXECUTABLE in out["disclaimer"]
        for c in out["_objects"]:
            assert c.real_world_executable is False and c.execution_status == R.NOT_EXECUTABLE
            text = " ".join([c.description, *c.labels, *c.lock_reasons, *c.screen_reasons]).lower()
            assert " safe" not in text and "safely" not in text
            if c.kind == "isolate_pipe":
                assert c.physical_valve_availability == "UNKNOWN" and R.ISOLATION_ASSUMPTION in c.labels


@pytest.mark.parametrize("cls,loc,kind", [("abrupt_leak", "121", "isolate_pipe"), ("valve_disturbance", "11", "restore_valve"),
                                          ("pump_disturbance", "9", "restore_pump")])
def test_unlocked_candidates_map_to_valid_simulations(cls, loc, kind):
    out = generate(_cd("CONFIRMED_BY_INVESTIGATION", cls, loc))
    c = next(c for c in out["_objects"] if c.kind == kind)
    assert c.unlocked and c.simulation_supported
    fault = fault_from_spec(ScenarioSpec(event_class=cls, location=loc, seed=11, severity=0.6, mismatch=0.0))
    r = run(fault, c.to_intervention(), mismatch=0.0, seed=11)    # M1 validates fault/intervention compatibility
    assert len(r["time"]) == R.HORIZON // C.REPORT_STEP + 1


def test_non_simulated_candidates_cannot_become_interventions():
    c = next(c for c in generate(_cd("OPERATOR_CONFIRMED", "sensor_bias", "22"))["_objects"] if c.kind == "sensor_maintenance")
    with pytest.raises(ValueError, match="not supported by the hydraulic simulator"):
        c.to_intervention()


def test_generation_is_deterministic():
    cd = _cd("CONFIRMED_BY_INVESTIGATION", "abrupt_leak", "121", ["31", "122"])
    a, b = generate(cd), generate(cd)
    assert a["candidates"] == b["candidates"]
