import pytest
from fastapi.testclient import TestClient

from aquascope import api
from aquascope.simulation import SimulationError


@pytest.fixture(scope="module")
def client():
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["models_loaded"] is True


def test_network(client):
    n = client.get("/api/network").json()
    assert len(n["nodes"]) == 11 and len(n["links"]) == 13


def test_full_workflow(client):
    r = client.post("/api/scenarios/run", json={"event_class": "abrupt_leak", "seed": 777, "severity": 0.3,
                                               "mismatch": 0.05, "location": "122"})
    assert r.status_code == 200
    sid = r.json()["scenario_id"]
    assert "hidden_state" not in r.json()
    t = client.get(f"/api/telemetry/{sid}").json()
    assert "hidden_state" not in t and len(t["telemetry"]["time"]) == 145
    d = client.post(f"/api/diagnosis/{sid}", json={}).json()
    assert d["hypotheses"][0]["family"] == "leak"
    assert client.post(f"/api/diagnosis/{sid}", json={"approach": "physics"}).json()["approach"] == "physics"
    inv = client.post(f"/api/investigation/{sid}").json()
    m = client.post(f"/api/investigation/{sid}/measure", json={"node": inv["recommended_node"]})
    assert m.status_code == 200 and "posterior" in m.json()
    assert client.get(f"/api/scenarios/{sid}/ground_truth").json()["location"] == "122"


@pytest.mark.parametrize("body", [{"event_class": "abrupt_leak", "location": "999"}, {"event_class": "nope"},
                                  {"event_class": "normal", "severity": -1}])
def test_invalid_requests(client, body):
    r = client.post("/api/scenarios/run", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_invalid_approach(client):
    sid = client.post("/api/scenarios/run", json={"event_class": "normal", "seed": 1}).json()["scenario_id"]
    assert client.post(f"/api/diagnosis/{sid}", json={"approach": "magic"}).status_code == 422


def test_missing_scenario(client):
    for r in (client.get("/api/telemetry/does-not-exist"), client.post("/api/diagnosis/does-not-exist", json={}),
              client.post("/api/investigation/does-not-exist")):
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_path_traversal_rejected(client):
    assert client.get("/api/telemetry/..%2F..%2Fconfig").status_code == 404


def test_measure_before_investigation(client):
    sid = client.post("/api/scenarios/run", json={"event_class": "normal", "seed": 2}).json()["scenario_id"]
    api._SCENARIOS[sid].pop("_last_recommendation", None)
    r = client.post(f"/api/investigation/{sid}/measure", json={"node": "12"})
    assert r.status_code == 409


def test_internal_simulation_error(client, monkeypatch):
    def boom(spec):
        raise SimulationError("EPANET simulation failed: injected test failure")
    monkeypatch.setattr(api, "run_scenario", boom)
    r = client.post("/api/scenarios/run", json={"event_class": "normal"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "simulation_failed"


def test_unexpected_error_hides_internals(client, monkeypatch):
    monkeypatch.setattr(api, "topology", lambda: 1 / 0)
    r = client.get("/api/network")
    assert r.status_code == 500 and "ZeroDivision" not in r.text
