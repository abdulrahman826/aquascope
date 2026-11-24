"""Remediation M1: pressure-driven intervention simulation and outcome metrics.

Thresholds are justified next to each assertion (measured values in docs/remediation_m1.md).
"""
import numpy as np
import pytest

from aquascope import config as C
from aquascope.network import topology
from aquascope.remediation import config as R, simulate as RS
from aquascope.remediation.metrics import connectivity, evaluate, tank_geometry
from aquascope.remediation.simulate import Fault, Intervention, fault_from_spec, run, simulate_intervention
from aquascope.simulation import ScenarioSpec, run_scenario

MM = [0.0, 0.05, 0.15]
SEED = 11
NORMAL = Fault("normal", None, None)
ELEV = {n["id"]: n["elevation_m"] for n in topology()["nodes"]}


def _fault(cls, loc=None, mm=0.0, seed=SEED, sev=0.6):
    return fault_from_spec(ScenarioSpec(event_class=cls, seed=seed, severity=sev, mismatch=mm, location=loc))


def _after(r):
    return r["time"] >= R.DECISION_TIME + R.SETTLE


# ---------------------------------------------------------------- consistency with the existing engine
@pytest.mark.parametrize("mm", MM)
@pytest.mark.parametrize("cls,loc", [("normal", None), ("abrupt_leak", "121"), ("incipient_leak", "31"),
                                     ("valve_disturbance", "11"), ("pump_disturbance", "9"),
                                     ("demand_anomaly", "22"), ("sensor_bias", "22")])
def test_reproduces_existing_scenario_state(mm, cls, loc):
    """First 72 h of a 96 h pressure-driven run == hidden state of the existing scenario generator.
    Tolerance 5e-3 m: results are float32 (~1e-5 relative at ~100 m) and EPyT-Flow's leak-node split
    perturbs pressures by up to 1.8e-3 m (measured); pressure-driven vs demand-driven demand is identical
    here because every pressure (>= 95 m) exceeds the 20 m required pressure."""
    spec = ScenarioSpec(event_class=cls, seed=21, severity=0.6, mismatch=mm, location=loc)
    hidden = run_scenario(spec)["hidden_state"]["pressure"]
    r = run(fault_from_spec(spec), mismatch=mm, seed=21)
    for j in C.JUNCTIONS:
        assert np.abs(r["pressure"][j][:145] - np.asarray(hidden[j])).max() < 5e-3, j


# ---------------------------------------------------------------- normal operation
@pytest.mark.parametrize("mm", MM)
def test_normal_operation_passes_constraints(mm):
    e = evaluate(simulate_intervention(NORMAL, Intervention(), mismatch=mm, seed=SEED))
    a = e["after"]
    assert e["passed_configured_constraints"] and e["verdict"] == R.VERDICT_PASS
    assert a["leak_flow_mean_L_s"] == 0.0 and a["negative_pressure_samples"] == 0
    assert a["delivered_fraction"] > 0.9999          # all pressures >> 20 m -> full delivery under PDA
    assert a["min_pressure_m"] > 90                  # Net1 operates at ~95-130 m
    geo = tank_geometry()
    assert geo["min_level_m"] < a["tank_min_level_m"] <= a["tank_max_level_m"] <= geo["max_level_m"] + 1e-6
    assert e["connectivity"]["junctions_disconnected_from_any_source"] == []
    assert e["warnings"] == []


# ---------------------------------------------------------------- leak isolation
@pytest.mark.parametrize("mm", MM)
@pytest.mark.parametrize("cls,pipe", [("abrupt_leak", "121"), ("incipient_leak", "31"), ("abrupt_leak", "112")])
def test_leak_isolation_closes_both_split_halves(mm, cls, pipe):
    e = evaluate(simulate_intervention(_fault(cls, pipe, mm), Intervention("isolate_pipe", pipe), mismatch=mm, seed=SEED))
    assert e["intervention"]["closed_links"] == [pipe, f"leak_pipe_{pipe}"]
    assert e["before"]["leak_flow_mean_L_s"] > 1.0                 # leak present before (measured 9-12 L/s)
    assert e["after_without_intervention"]["leak_flow_mean_L_s"] > 1.0
    # after isolation the leak node is cut off; 1e-3 L/s = numerical zero for EPANET emitter flow
    assert abs(e["after"]["leak_flow_mean_L_s"]) < 1e-3
    assert e["effect_vs_no_intervention"]["leak_volume_avoided_m3"] > 50   # >1 L/s x 23 h = 83 m3
    assert e["after"]["delivered_fraction"] > 0.9999 and e["after"]["min_pressure_m"] > R.MIN_SERVICE_PRESSURE_M
    assert e["connectivity"]["junctions_disconnected_from_any_source"] == []   # loop pipes in Net1
    assert e["verdict"] == R.VERDICT_PASS
    assert e["intervention"]["physical_valve_availability"] == {pipe: "UNKNOWN"}
    assert R.ISOLATION_ASSUMPTION in e["labels"]


def test_closing_only_one_split_half_does_not_stop_the_leak(monkeypatch):
    """Why both halves are needed: EPyT-Flow splits pipe 121 into 121 + leak_pipe_121 around the leak node."""
    class OneHalf(RS.CloseLinksEvent):
        def __init__(self, link_ids, **kw):
            super().__init__([l for l in link_ids if not l.startswith("leak_pipe_")], **kw)
    monkeypatch.setattr(RS, "CloseLinksEvent", OneHalf)
    r = run(_fault("abrupt_leak", "121"), Intervention("isolate_pipe", "121"), mismatch=0.0, seed=SEED)
    assert r["leak_flow"][_after(r)].mean() > 1.0


@pytest.mark.parametrize("mm", MM)
def test_leak_flow_equals_mass_balance(mm):
    """Leak flow measured at the EPyT-Flow leak node == supply (pump + tank outflow) - junction consumption.
    Tolerance 0.05 L/s on a ~10 L/s leak (float32 flows, per-sample rounding)."""
    r = run(_fault("abrupt_leak", "121", mm), mismatch=mm, seed=SEED)
    supply = r["flow"][C.PUMP_ID] + r["flow"]["110"]
    cons = sum(r["demand"][j] for j in C.JUNCTIONS)
    m = _after(r)
    assert abs((supply - cons)[m].mean() - r["leak_flow"][m].mean()) < 0.05


@pytest.mark.parametrize("mm", MM)
def test_isolating_the_only_main_fails_constraints(mm):
    """Pipe 10 is the single link from the pump: graph check shows junctions 11-32 on tank-only supply; simulation shows the tank
    empties and service collapses. Negative pressures are flagged, not hidden."""
    e = evaluate(simulate_intervention(_fault("abrupt_leak", "121", mm), Intervention("isolate_pipe", "10"),
                                       mismatch=mm, seed=SEED))
    downstream = [j for j in C.JUNCTIONS if j != "10"]   # junction 10 stays on the pump side
    assert e["connectivity"]["junctions_supplied_only_by_tank"] == downstream
    assert not e["passed_configured_constraints"] and e["verdict"] == R.VERDICT_FAIL
    assert e["after"]["delivered_fraction"] < 0.5 and e["after"]["tank_empty_samples"] > 0
    assert e["after"]["min_pressure_m"] < 0 and e["warnings"]
    assert e["effect_vs_no_intervention"]["leak_volume_avoided_m3"] is None   # non-physical, not reported


@pytest.mark.parametrize("mm", MM)
def test_isolating_tank_riser_fails_although_graph_is_connected(mm):
    """Pipe 110 isolation leaves every junction connected to the reservoir (graph check passes) but Net1's pump is
    switched by tank level, so the pump never restarts: only the hydraulic simulation reveals the supply loss."""
    e = evaluate(simulate_intervention(NORMAL, Intervention("isolate_pipe", "110"), mismatch=mm, seed=SEED))
    assert e["constraint_checks"][0]["passed"]
    assert e["after"]["delivered_fraction"] < 0.5 and not e["passed_configured_constraints"]


# ---------------------------------------------------------------- valve restoration
def _excess_headloss(r, k, mask):
    hl = (r["pressure"]["11"] + ELEV["11"]) - (r["pressure"]["12"] + ELEV["12"])
    q = np.abs(r["flow"]["11"])
    return hl[mask] - k * q[mask] ** 1.852


@pytest.mark.parametrize("mm", MM)
def test_valve_restoration_removes_added_headloss(mm):
    """Mechanism check on pipe 11 (nodes 11 -> 12): fit the pipe's Hazen-Williams law hl = k q^1.852 on the
    no-fault run (same mismatch seed), then measure the excess head loss. Throttled: ~11-12 m (configured
    3 + 7*0.6 = 7.2 m at nominal mean velocity; higher at pump-on flows). Restored: median < 0.2 m (measured
    0.03-0.08 m; residual from flow-dependent fitting error). Whole-network pressures do NOT return exactly to the
    no-fault run because tank level and pump phase carry the disturbance's history (documented)."""
    ref = run(NORMAL, mismatch=mm, seed=SEED)
    mref = np.abs(ref["flow"]["11"]) > 5
    k = np.median(((ref["pressure"]["11"] + ELEV["11"]) - (ref["pressure"]["12"] + ELEV["12"]))[mref]
                  / np.abs(ref["flow"]["11"][mref]) ** 1.852)
    f = _fault("valve_disturbance", "11", mm)
    restored = run(f, Intervention("restore_valve", "11"), mismatch=mm, seed=SEED)
    throttled = run(f, mismatch=mm, seed=SEED)
    sel = lambda r: _after(r) & (np.abs(r["flow"]["11"]) > 5)
    assert np.median(_excess_headloss(throttled, k, sel(throttled))) > 5.0
    assert abs(np.median(_excess_headloss(restored, k, sel(restored)))) < 0.2
    e = evaluate(simulate_intervention(f, Intervention("restore_valve", "11"), mismatch=mm, seed=SEED))
    assert e["verdict"] == R.VERDICT_PASS and e["intervention"]["physical_valve_availability"] is None


# ---------------------------------------------------------------- pump restoration
def _pump_points(r, mask):
    m = mask & (r["flow"][C.PUMP_ID] > 1)
    return r["flow"][C.PUMP_ID][m], r["pressure"]["10"][m] + ELEV["10"] - 800 * 0.3048   # reservoir head 800 ft


@pytest.mark.parametrize("mm", MM)
def test_pump_restoration_returns_to_healthy_curve(mm):
    """Fit the healthy pump head-flow curve (h = A - B q^2) on the no-fault run; it fits exactly (residual 0.000 m).
    Restored pump: every pump-on point after 72 h lies on the healthy curve (< 0.05 m). Faulted pump: far below it."""
    ref = run(NORMAL, mismatch=mm, seed=SEED)
    q, h = _pump_points(ref, np.ones(len(ref["time"]), bool))
    b, a = np.polyfit(q ** 2, h, 1)
    assert np.abs(h - (a + b * q ** 2)).max() < 0.05
    f = _fault("pump_disturbance", "9", mm)
    rr = run(f, Intervention("restore_pump", "9"), mismatch=mm, seed=SEED)
    fr = run(f, mismatch=mm, seed=SEED)
    win = lambda r: r["time"] >= R.DECISION_TIME + C.HYD_STEP
    q2, h2 = _pump_points(rr, win(rr))
    q3, h3 = _pump_points(fr, win(fr))
    assert len(q2) > 10 and np.abs(h2 - (a + b * q2 ** 2)).max() < 0.05
    assert (h3 - (a + b * q3 ** 2)).max() < -5.0
    assert evaluate(simulate_intervention(f, Intervention("restore_pump", "9"), mismatch=mm, seed=SEED))[
        "passed_configured_constraints"]


# ---------------------------------------------------------------- validation, determinism, language
@pytest.mark.parametrize("fault,iv,msg", [
    (NORMAL, Intervention("isolate_pipe", "999"), "not a Net1 pipe"),
    (NORMAL, Intervention("restore_valve", "11"), "no throttled valve"),
    (Fault("valve_disturbance", "11", 120600, (("headloss_m", 5.0),)), Intervention("restore_valve", "12"), "no throttled valve"),
    (NORMAL, Intervention("restore_pump", "9"), "no pump disturbance"),
    (NORMAL, Intervention("teleport", "9"), "unknown intervention"),
])
def test_invalid_interventions_rejected(fault, iv, msg):
    with pytest.raises(ValueError, match=msg):
        run(fault, iv, mismatch=0.0, seed=SEED)


def test_invalid_timing_rejected():
    f = _fault("abrupt_leak", "121")
    with pytest.raises(ValueError, match="after the fault starts"):
        run(f, Intervention("isolate_pipe", "121"), mismatch=0.0, seed=SEED, t_int=f.start_time - 900)
    with pytest.raises(ValueError, match="multiple"):
        run(f, Intervention("isolate_pipe", "121"), mismatch=0.0, seed=SEED, t_int=R.DECISION_TIME + 1)


def test_deterministic():
    a = evaluate(simulate_intervention(_fault("abrupt_leak", "121", 0.15), Intervention("isolate_pipe", "121"),
                                       mismatch=0.15, seed=SEED))
    b = evaluate(simulate_intervention(_fault("abrupt_leak", "121", 0.15), Intervention("isolate_pipe", "121"),
                                       mismatch=0.15, seed=SEED))
    assert a == b


def test_safety_language_and_valve_availability():
    for v in (R.VERDICT_PASS, R.VERDICT_FAIL, R.SIMULATION_LABEL, R.ISOLATION_ASSUMPTION):
        assert "safe" not in v.lower()
    assert R.VERDICT_PASS == "PASSED CONFIGURED SIMULATION CONSTRAINTS — NOT VALIDATED FOR REAL-WORLD EXECUTION"
    assert all(R.valve_availability(p) == "UNKNOWN" for p in C.PIPES)


def test_connectivity_graph():
    assert connectivity([])["junctions_disconnected_from_any_source"] == []
    c = connectivity(["10"])
    assert c["junctions_supplied_only_by_tank"] == [j for j in C.JUNCTIONS if j != "10"]
    c = connectivity(["10", "110"])
    assert c["junctions_disconnected_from_any_source"] == [j for j in C.JUNCTIONS if j != "10"]
    assert connectivity(["leak_pipe_121", "121"])["closed_pipes"] == ["121"]
