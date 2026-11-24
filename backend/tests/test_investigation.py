import copy

import pytest

from aquascope import investigation as I
from aquascope.diagnosis import diagnose_scenario
from aquascope.simulation import ScenarioSpec, run_scenario


@pytest.fixture(scope="module")
def leak_case():
    sc = run_scenario(ScenarioSpec(event_class="abrupt_leak", seed=777, mismatch=0.05, severity=0.3, location="122"))
    return sc, diagnose_scenario(sc)


def test_candidates_ranked_by_separation(leak_case):
    sc, d = leak_case
    r = I.recommend(sc, d)
    seps = [c["separation"] for c in r["candidates"]]
    assert seps == sorted(seps, reverse=True) and len(seps) == 9
    if r["recommended_node"]:
        assert r["recommended_node"] == r["candidates"][0]["node"] and seps[0] >= I.MIN_SEPARATION
    assert len(r["hypotheses"]) == 2 and r["hypotheses"][0]["probability"] >= r["hypotheses"][1]["probability"]


def test_known_case_resolves_to_truth(leak_case):
    sc, d = leak_case
    r = I.recommend(sc, d)
    assert r["recommended_node"] == "32"
    m = I.simulate_measurement(sc, r, "32")
    assert m["resolved_location"] == "122" and m["consistent_with_a_hypothesis"]


def test_noise_aware_separation(leak_case, monkeypatch):
    sc, d = leak_case
    base = I.recommend(sc, d)["candidates"][0]["separation"]
    monkeypatch.setattr(I, "_model_sigma", lambda ref, mon: 10.0)   # much larger model uncertainty
    noisy = I.recommend(sc, d)
    assert noisy["candidates"][0]["separation"] < base / 3
    assert noisy["recommended_node"] is None and "No reliable" in noisy["explanation"]


def test_no_competing_hypothesis_handled(leak_case):
    sc, d = leak_case
    d2 = copy.deepcopy(d)
    d2["hypotheses"] = d2["hypotheses"][:1]
    d2["hypotheses"][0]["event_class"] = "normal"
    r = I.recommend(sc, d2)
    assert r["recommended_node"] is None and r["candidates"] == []


def test_recommendation_reproducible(leak_case):
    sc, d = leak_case
    assert I.recommend(sc, d) == I.recommend(sc, d)
    r = I.recommend(sc, d)
    assert I.simulate_measurement(sc, r, "32") == I.simulate_measurement(sc, r, "32")


def test_measurement_rejects_ineligible_node(leak_case):
    sc, d = leak_case
    with pytest.raises(ValueError):
        I.simulate_measurement(sc, I.recommend(sc, d), "999")
