"""Remediation M5: REST endpoints."""
import pytest
from fastapi.testclient import TestClient

from aquascope import api
from aquascope.remediation import config as R


@pytest.fixture(scope="module")
def client():
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c


def _scenario(client, **spec):
    r = client.post("/api/scenarios/run", json=spec)
    assert r.status_code == 200
    return r.json()["scenario_id"]


def test_auto_confirm_plan_verify_leak(client):
    sid = _scenario(client, event_class="abrupt_leak", location="121", seed=108, severity=0.5, mismatch=0.05)
    c = client.post(f"/api/remediation/{sid}/confirm", json={"mode": "auto"}).json()
    assert c["status"] in ("CONFIRMED_BY_INVESTIGATION", "CONFIDENT_BUT_UNVERIFIED", "INSUFFICIENT_EVIDENCE")
    p = client.post(f"/api/remediation/{sid}/plan").json()
    assert p["execution_status"] == R.NOT_EXECUTABLE and "explanation" in p
    for row in p["ranked"]:
        assert "evaluation" not in row
    v = client.post(f"/api/remediation/{sid}/verify")
    assert v.status_code == 200
    assert "NOT REAL-WORLD VALIDATION" in v.json()["label"]


def test_operator_confirm_valve_end_to_end(client):
    sid = _scenario(client, event_class="valve_disturbance", location="11", seed=31, severity=0.6, mismatch=0.0)
    c = client.post(f"/api/remediation/{sid}/confirm", json={"mode": "operator", "event_class": "valve_disturbance",
                                                             "location": "11", "operator_id": "op-1", "note": "x"})
    assert c.status_code == 200 and c.json()["hydraulic_interventions_unlocked"]
    p = client.post(f"/api/remediation/{sid}/plan").json()
    assert p["recommendation"]["kind"] == "restore_valve"
    assert p["recommendation"]["verdict"] == R.VERDICT_PASS
    v = client.post(f"/api/remediation/{sid}/verify").json()
    assert v["applicable"] and v["mechanism_benefit"]["remaining_after_m"] < 0.5
    s = v["series"]
    assert len(s["with"]["time_h"]) == len(s["without"]["time_h"]) > 10 and s["intervention_time_h"] == 72


def test_operator_confirm_validation_errors(client):
    sid = _scenario(client, event_class="normal", seed=2)
    r = client.post(f"/api/remediation/{sid}/confirm", json={"mode": "operator", "event_class": "abrupt_leak",
                                                             "location": "110", "operator_id": "op"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    r = client.post(f"/api/remediation/{sid}/confirm", json={"mode": "operator", "event_class": "abrupt_leak",
                                                             "location": "121", "operator_id": ""})
    assert r.status_code == 422
    assert client.post(f"/api/remediation/{sid}/confirm", json={"mode": "magic"}).status_code == 422


def test_order_and_missing_scenario(client):
    sid = _scenario(client, event_class="normal", seed=3)
    api._SCENARIOS[sid].pop("_remediation", None)
    assert client.post(f"/api/remediation/{sid}/plan").status_code == 409
    assert client.post(f"/api/remediation/{sid}/verify").status_code == 409
    assert client.post("/api/remediation/does-not-exist/confirm", json={}).status_code == 404


def test_insufficient_evidence_plan_recommends_investigation(client):
    sid = _scenario(client, event_class="normal", seed=4, mismatch=0.15)
    client.post(f"/api/remediation/{sid}/confirm", json={"mode": "auto"})
    p = client.post(f"/api/remediation/{sid}/plan").json()
    assert p["recommendation"]["kind"] in ("investigate_first", "none")
    assert all(r["kind"] in ("none",) or not r["eligible"] for r in p["ranked"])


def test_plan_with_unverified_leak_preview_is_json_safe(client):
    # Regression (found by scripts/sweep_journey.py): a confident-but-unverified leak whose candidate isolation drives
    # pressures negative gets benefit = -inf (sort sentinel) -> plan returned HTTP 500. Must be 200 with null instead.
    sid = _scenario(client, event_class="abrupt_leak", location="10", seed=5000, severity=0.6, mismatch=0.0,
                    pressure_noise=0.05)
    assert client.post(f"/api/remediation/{sid}/confirm", json={"mode": "auto"}).status_code == 200
    p = client.post(f"/api/remediation/{sid}/plan")
    assert p.status_code == 200
    assert any(r.get("benefit", 0) is None for r in p.json()["ranked"])
    assert client.post(f"/api/remediation/{sid}/verify").status_code == 200
