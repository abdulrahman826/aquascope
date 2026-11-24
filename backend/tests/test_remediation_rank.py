"""Remediation M3: ranking, explanation and verification on the hidden true network."""
import pytest

from aquascope.diagnosis import diagnose_scenario
from aquascope.investigation import recommend, simulate_measurement
from aquascope.remediation import config as R
from aquascope.remediation import confirmation as K
from aquascope.remediation.explain import explain
from aquascope.remediation.rank import hypothesis_fault, rank
from aquascope.remediation.verify import verify
from aquascope.simulation import ScenarioSpec, run_scenario

MM = [0.0, 0.05, 0.15]


def _spec(cls, loc, mm, seed=31):
    return ScenarioSpec(event_class=cls, location=loc, seed=seed, severity=0.6, mismatch=mm)


def _diag(spec):
    return diagnose_scenario(run_scenario(spec))


def _op(cls, loc):
    return K.operator_confirmed(cls, loc, "op-1", "test confirmation")


# ---------------------------------------------------------------- correct action per class, verified on the true network
@pytest.mark.parametrize("mm", MM)
def test_confirmed_leak_isolation_recommended_and_verified(mm):
    spec = _spec("abrupt_leak", "121", mm)
    r = rank(_op("abrupt_leak", "121"), _diag(spec))
    assert r["recommendation"]["kind"] == "isolate_pipe" and r["recommendation"]["target"] == "121"
    assert r["recommendation"]["verdict"] == R.VERDICT_PASS
    v = verify(r, spec)
    assert v["leak_stopped"] and v["actual_passed_constraints"] and not v["constraint_prediction_miss"]
    # prediction transfer under mismatch: measured +5..+16 % over actual; 25 % bound documents that it stays close
    pred, act = v["predicted_leak_volume_avoided_m3"], v["actual_leak_volume_avoided_m3"]
    assert abs(pred - act) / act < 0.25


@pytest.mark.parametrize("mm", MM)
def test_confirmed_valve_restoration_recommended_and_verified(mm):
    spec = _spec("valve_disturbance", "11", mm)
    r = rank(_op("valve_disturbance", "11"), _diag(spec))
    assert r["recommendation"]["kind"] == "restore_valve"
    m = verify(r, spec)["mechanism_benefit"]
    assert m["remaining_after_m"] < 0.5                     # excess head loss essentially gone (measured 0.07-0.14 m)
    assert abs(m["predicted_benefit"] - m["actual_benefit"]) < 1.0   # measured 0.03-0.64 m


@pytest.mark.parametrize("mm", MM)
def test_confirmed_pump_restoration_recommended_and_verified(mm):
    """Recommendation is correct, but the predicted benefit is NOT accurate: the physics fit underestimates the
    speed loss (0.86-0.99 estimated vs 0.818 true), so predicted 2.9-36.7 m vs actual 47.8 m. Asserted as found."""
    spec = _spec("pump_disturbance", "9", mm)
    r = rank(_op("pump_disturbance", "9"), _diag(spec))
    assert r["recommendation"]["kind"] == "restore_pump"
    m = verify(r, spec)["mechanism_benefit"]
    assert abs(m["remaining_after_m"]) < 0.5
    assert m["predicted_benefit"] < m["actual_benefit"]     # known under-estimation (see docs/remediation_m3.md)


def test_investigation_confirmed_demo_case():
    spec = ScenarioSpec(event_class="abrupt_leak", location="121", seed=108, severity=0.5, mismatch=0.05)
    sc = run_scenario(spec)
    d = diagnose_scenario(sc)
    rec = recommend(sc, d)
    cd = K.confirm_measurement(sc, d, rec, simulate_measurement(sc, rec, rec["recommended_node"]))
    r = rank(cd, d)
    assert r["recommendation"]["target"] == "121"
    alts = [row for row in r["ranked"] if row["role"] == "alternative_location"]
    assert alts and all(not a["eligible"] for a in alts)
    assert verify(r, spec)["leak_stopped"]


# ---------------------------------------------------------------- hard rules
def test_hard_constraints_are_never_overridden(monkeypatch):
    """Raise the service-pressure constraint to 80 m: isolating 121 (min pressure ~70 m) has by far the largest benefit
    but now fails a constraint, so it must not be recommended."""
    monkeypatch.setitem(R.CONSTRAINTS, "min_service_pressure_m", 80.0)
    r = rank(_op("abrupt_leak", "121"))
    row = next(x for x in r["ranked"] if x["id"] == "isolate_pipe:121")
    assert row["benefit"] > 500 and not row["eligible"]
    assert any("fails simulated constraint" in s for s in row["ineligible_reasons"])
    assert r["recommendation"]["kind"] == "none"


def test_benefit_below_threshold_means_no_intervention(monkeypatch):
    monkeypatch.setattr(R, "MIN_LEAK_VOLUME_AVOIDED_M3", 1e6)
    r = rank(_op("abrupt_leak", "121"))
    assert r["recommendation"]["kind"] == "none" and "configured minimum" in r["recommendation"]["reason"]


def test_unverified_diagnosis_is_preview_only():
    cd = K.ConfirmedDiagnosis("CONFIDENT_BUT_UNVERIFIED", "abrupt_leak", "121", 0.9, "diagnosis", "model only")
    r = rank(cd)
    assert r["preview_only"] and r["recommendation"]["kind"] == "investigate_first"
    assert all(not x["eligible"] for x in r["ranked"] if x["kind"] != "none")


def test_insufficient_evidence_runs_no_simulation():
    r = rank(K.ConfirmedDiagnosis("INSUFFICIENT_EVIDENCE", None, None, None, "diagnosis", "ambiguous"))
    assert r["recommendation"]["kind"] == "investigate_first" and r["ranked"] == []


@pytest.mark.parametrize("cls,loc", [("sensor_bias", "22"), ("demand_anomaly", "22"), ("normal", None)])
def test_classes_without_hydraulic_action(cls, loc):
    r = rank(_op(cls, loc))
    assert r["recommendation"]["kind"] == "none" and r["ranked"] == []


def test_wrong_confirmed_location_is_exposed_by_verification():
    """Operator confirms pipe 31 but the leak is at 121: the model-based recommendation follows the confirmation,
    and true-network verification shows the leak continues."""
    spec = _spec("abrupt_leak", "121", 0.05, seed=108)
    r = rank(_op("abrupt_leak", "31"), _diag(spec))
    assert r["recommendation"]["target"] == "31"
    v = verify(r, spec)
    assert v["leak_stopped"] is False and v["diagnosis_matched_truth"] is False


def test_verification_reports_inapplicable_action():
    spec = _spec("abrupt_leak", "121", 0.0)
    v = verify(rank(_op("valve_disturbance", "11")), spec)
    assert v["applicable"] is False and "cannot act on the true fault" in v["reason"]


# ---------------------------------------------------------------- determinism, assumptions, language
def test_ranking_is_deterministic():
    a, b = rank(_op("abrupt_leak", "121")), rank(_op("abrupt_leak", "121"))
    strip = lambda r: [{k: v for k, v in x.items() if k != "evaluation"} for x in r["ranked"]]
    assert strip(a) == strip(b) and a["recommendation"] == b["recommendation"]


def test_assumed_magnitude_is_labelled():
    _, notes = hypothesis_fault(_op("abrupt_leak", "121"))
    assert any(n.startswith("ASSUMED magnitude") for n in notes)
    assert any("assumed (no diagnosis provided)" in n for n in notes)


def test_explanation_content_and_language():
    x = explain(rank(_op("abrupt_leak", "121")))
    text = " ".join(x["summary"]) + " " + x["disclaimer"]
    assert R.VERDICT_PASS in text and R.ISOLATION_ASSUMPTION in text and "UNKNOWN" in text
    assert R.NOT_EXECUTABLE in x["disclaimer"]
    assert " safe" not in text.lower() and "safely" not in text.lower()
    assert x["constraint_table"] and all(row["passed"] for row in x["constraint_table"])
