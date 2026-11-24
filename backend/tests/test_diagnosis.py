import numpy as np
import pandas as pd
import pytest

from aquascope import config as C, models
from aquascope.diagnosis import decide, diagnose_scenario
from aquascope.features import extract, feature_names
from aquascope.simulation import ScenarioSpec, run_scenario


def _sc(cls, **kw):
    return run_scenario(ScenarioSpec(event_class=cls, seed=kw.pop("seed", 21), mismatch=kw.pop("mismatch", 0.0), **kw))


def test_feature_dimensions():
    x, f = extract(_sc("normal")["telemetry"])
    assert x.shape == (len(feature_names()),) and np.isfinite(x).all()
    assert list(f) == feature_names() or set(f) == set(feature_names())


def test_model_loads_and_schema_validated(monkeypatch):
    b = models.bundle()
    assert b["feature_names"] == feature_names() and b["classes"] == C.CLASSES
    monkeypatch.setattr(models, "_BUNDLE", None)
    monkeypatch.setattr(models, "feature_names", lambda: ["something_else"])
    with pytest.raises(RuntimeError, match="schema"):
        models.bundle()
    monkeypatch.setattr(models, "_BUNDLE", None)


def test_prediction_schema():
    d = diagnose_scenario(_sc("abrupt_leak", location="122", severity=0.8))
    assert d["status"] in {"CONFIDENT", "AMBIGUOUS_DIAGNOSIS", "INSUFFICIENT_EVIDENCE"}
    assert set(d["all_approaches"]) == {"physics", "random_forest", "hybrid"}
    for a, p in d["all_approaches"].items():
        assert abs(sum(p.values()) - 1) < 1e-3, a
    h = d["hypotheses"]
    assert [x["probability"] for x in h] == sorted([x["probability"] for x in h], reverse=True)
    for x in h:
        assert {"supporting_evidence", "contradicting_evidence", "candidate_locations"} <= set(x)


def test_missing_or_short_sensor_rejected():
    sc = _sc("normal")
    tel = {"time": sc["telemetry"]["time"], "pressure": dict(sc["telemetry"]["pressure"]), "flow": sc["telemetry"]["flow"]}
    del tel["pressure"]["22"]
    with pytest.raises(ValueError, match="missing pressure sensor '22'"):
        extract(tel)
    tel["pressure"]["22"] = [1.0] * 10
    with pytest.raises(ValueError, match="need"):
        extract(tel)


def test_abstention_logic():
    p = np.zeros(len(C.CLASSES))
    p[:3] = [0.9, 0.05, 0.05]
    assert decide(p, 0.8)[0] == "CONFIDENT"
    p[:3] = [0.5, 0.4, 0.1]
    assert decide(p, 0.8)[0] == "AMBIGUOUS_DIAGNOSIS"
    p = np.full(len(C.CLASSES), 1 / len(C.CLASSES))
    assert decide(p, 0.8)[0] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("cls,loc,family", [("sensor_bias", "22", "sensor"), ("abrupt_leak", "122", "leak"),
                                            ("sensor_stuck", "13", "sensor"), ("abrupt_leak", "31", "leak")])
def test_sensor_fault_vs_leak_family(cls, loc, family):
    d = diagnose_scenario(_sc(cls, location=loc, severity=0.9))
    assert d["hypotheses"][0]["family"] == family, d["hypotheses"][:2]


def test_selected_approach_matches_rule_output():
    import json
    from aquascope.diagnosis import SELECTION_PATH
    if not SELECTION_PATH.exists():
        pytest.skip("evaluation not run")
    sel = json.loads(SELECTION_PATH.read_text())
    expected = "hybrid" if all(sel["checks"].values()) else sel["best_single"]
    assert sel["selected"] == expected


def test_threshold_selection_uses_target():
    rng = np.random.default_rng(0)
    p = rng.dirichlet(np.ones(4), 400)
    y = p.argmax(1)  # always correct -> lowest threshold accepted
    assert models.choose_threshold(p, y) == 0.30
    assert models.choose_threshold(p, (y + 1) % 4) == 0.99  # always wrong -> abstain
