# Remediation Intelligence Engine — M0 / M1 report

Status: **M0 and M1 complete. M2+ (action generation, gating, ranking, dataset, API, UI) NOT started.**
All results below are simulations on EPANET Net1 (EPyT-Flow 0.17.4). Nothing here is an operational instruction.

## M0 — regression snapshot of the existing engine

`backend/scripts/regression_snapshot.py --check` re-runs the 49 existing tests, regenerates the dataset and the
held-out evaluation **into a temporary directory** (existing artifacts are read, never written) and compares with
`backend/data/regression/baseline.json`.

| Check | Result |
|---|---|
| Existing backend tests | 49 passed |
| Dataset regenerated MD5 | `b378dc4d41a9f7cdd708fee7939dc23d` = stored |
| Evaluation canonical MD5 (stored / reproduced) | `f52031872079dd17fca13d71e8500aed` / identical |
| Detector threshold, architecture selection | reproduced identically |
| Artifact MD5s (models.joblib `d73df24e…`, evaluation.json `387ba7c7…`, …) | unchanged after M1 |

Canonical evaluation hash excludes wall-clock fields (timestamps, runtimes, latencies) and rounds floats to 12
significant digits. Justification (measured): RandomForest `predict_proba` with `n_jobs=-1` sums trees in a
thread-dependent order, giving last-bit differences (e.g. ECE 0.07081198292388088 vs 0.0708119829238809).

## M1 — what was built

| File | Purpose |
|---|---|
| `aquascope/remediation/config.py` | horizon (72 h + 24 h), pressure-driven demand model, named constraints, verdict wording, valve availability (UNKNOWN by default), isolation assumption text |
| `aquascope/remediation/simulate.py` | fault reconstruction (same classes/params/mismatch seed as the existing generator), interventions `none / isolate_pipe / restore_valve / restore_pump`, pressure-driven EPANET runs |
| `aquascope/remediation/metrics.py` | before/after windows, connectivity graph check, outcome metrics, constraint checks, verdict, labels, warnings |
| `tests/test_remediation.py` | 58 tests |

No existing module was modified; the package only calls existing public functions (`resolve`, `severity_params`,
`valve_minor_loss`, `_base_config`, event classes, `topology`, `_sections`).

### Constraints (configured, `remediation/config.py`)
all junctions connected to a source (graph) · min junction pressure ≥ 20 m · delivered/requested ≥ 99 % · tank never
empties — each evaluated over the "after" window [73 h, 96 h]. Verdict strings:
`PASSED CONFIGURED SIMULATION CONSTRAINTS — NOT VALIDATED FOR REAL-WORLD EXECUTION` /
`FAILED CONFIGURED SIMULATION CONSTRAINTS — SIMULATION RESULT ONLY`.

## Simulation results (seed 11, severity 0.6, intervention at 72 h, metrics over 73–96 h)

| Fault | Intervention | Mismatch | Leak before (L/s) | Leak after: with / without (L/s) | Leak avoided (m³) | Min P after (m) | Delivered after | Tank min level (m) | Disconnected / tank-only junctions | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| normal | none | 0% | 0.00 | 0.00 / 0.00 | 0.0 | 105.3 | 100.0% | 33.59 | 0 / 0 | PASS |
| normal | none | 5% | 0.00 | 0.00 / 0.00 | 0.0 | 104.6 | 100.0% | 33.63 | 0 / 0 | PASS |
| normal | none | 15% | 0.00 | 0.00 / 0.00 | 0.0 | 103.7 | 100.0% | 33.60 | 0 / 0 | PASS |
| abrupt_leak@121 | isolate_pipe 121 | 0% | 12.04 | 0.00 / 11.98 | 1013.5 | 71.2 | 100.0% | 33.53 | 0 / 0 | PASS |
| abrupt_leak@121 | isolate_pipe 121 | 5% | 12.02 | 0.00 / 11.96 | 1012.0 | 71.3 | 100.0% | 33.64 | 0 / 0 | PASS |
| abrupt_leak@121 | isolate_pipe 121 | 15% | 11.97 | 0.00 / 11.94 | 1010.2 | 72.2 | 100.0% | 33.56 | 0 / 0 | PASS |
| incipient_leak@31 | isolate_pipe 31 | 0% | 11.21 | 0.00 / 11.60 | 981.4 | 100.1 | 100.0% | 33.69 | 0 / 0 | PASS |
| incipient_leak@31 | isolate_pipe 31 | 5% | 11.14 | 0.00 / 11.58 | 979.3 | 98.9 | 100.0% | 33.75 | 0 / 0 | PASS |
| incipient_leak@31 | isolate_pipe 31 | 15% | 11.07 | 0.00 / 11.53 | 975.3 | 96.7 | 100.0% | 33.67 | 0 / 0 | PASS |
| abrupt_leak@112 | isolate_pipe 112 | 0% | 12.41 | 0.00 / 12.35 | 1044.5 | 94.6 | 100.0% | 33.53 | 0 / 0 | PASS |
| abrupt_leak@112 | isolate_pipe 112 | 5% | 12.38 | 0.00 / 12.32 | 1042.3 | 92.9 | 100.0% | 33.64 | 0 / 0 | PASS |
| abrupt_leak@112 | isolate_pipe 112 | 15% | 12.31 | 0.00 / 12.28 | 1039.3 | 90.4 | 100.0% | 33.58 | 0 / 0 | PASS |
| abrupt_leak@121 | isolate_pipe 10 | 0% | 12.04 | −0.56 / 11.98 | not reported* | −8.2 | 14.5% | 30.48 (empty) | 0 / 8 | FAIL |
| abrupt_leak@121 | isolate_pipe 10 | 5% | 12.02 | −0.57 / 11.96 | not reported* | −8.3 | 14.5% | 30.48 (empty) | 0 / 8 | FAIL |
| abrupt_leak@121 | isolate_pipe 10 | 15% | 11.97 | −0.31 / 11.94 | not reported* | −8.3 | 16.9% | 30.48 (empty) | 0 / 8 | FAIL |
| normal | isolate_pipe 110 | 0% | 0.00 | 0.00 / 0.00 | 0.0 | −8.7 | 0.0% | 35.47 | 0 / 0 | FAIL |
| normal | isolate_pipe 110 | 5% | 0.00 | 0.00 / 0.00 | 0.0 | −8.7 | 0.0% | 36.10 | 0 / 0 | FAIL |
| normal | isolate_pipe 110 | 15% | 0.00 | 0.00 / 0.00 | 0.0 | −8.7 | 0.0% | 36.92 | 0 / 0 | FAIL |
| valve_disturbance@11 | restore_valve 11 | 0% | 0.00 | 0.00 / 0.00 | 0.0 | 103.2 | 100.0% | 33.75 | 0 / 0 | PASS |
| valve_disturbance@11 | restore_valve 11 | 5% | 0.00 | 0.00 / 0.00 | 0.0 | 103.7 | 100.0% | 33.60 | 0 / 0 | PASS |
| valve_disturbance@11 | restore_valve 11 | 15% | 0.00 | 0.00 / 0.00 | 0.0 | 103.0 | 100.0% | 33.74 | 0 / 0 | PASS |
| pump_disturbance@9 | restore_pump 9 | 0% | 0.00 | 0.00 / 0.00 | 0.0 | 113.9 | 100.0% | 40.52 | 0 / 0 | PASS |
| pump_disturbance@9 | restore_pump 9 | 5% | 0.00 | 0.00 / 0.00 | 0.0 | 113.9 | 100.0% | 40.21 | 0 / 0 | PASS |
| pump_disturbance@9 | restore_pump 9 | 15% | 0.00 | 0.00 / 0.00 | 0.0 | 112.5 | 100.0% | 39.58 | 0 / 0 | PASS |

\* negative pressures → emitter flow is non-physical; the benefit is suppressed and a warning is emitted.

### Mechanism checks (all mismatch levels)
* **Reproduction:** first 72 h of each 96 h pressure-driven run match the existing scenario hidden state within
  ≤ 1.8e-3 m (normal, both leak types, valve, pump, demand, sensor bias).
* **Leak isolation:** EPyT-Flow splits a leaking pipe into `<id>` + `leak_pipe_<id>`; both are closed. Closing only
  `<id>` leaves > 1 L/s leaking (test). Leak flow at the leak node equals the mass balance (within 0.05 L/s).
* **Valve restoration:** excess head loss on pipe 11 over its fitted Hazen-Williams law drops from ≈ 11–12 m
  (throttled) to 0.03–0.08 m (restored).
* **Pump restoration:** the healthy head–flow curve fitted on the no-fault run has residual 0.000 m; every restored
  pump-on point after 72 h lies on it (< 0.05 m); faulted points lie > 5 m below it.

## Assumptions
1. Idealised isolation (instant, complete closure) — labelled as a modelling assumption; physical valve
   availability is **UNKNOWN** for every Net1 pipe unless configured.
2. "Valve disturbance" and its restoration remain a modelling surrogate (added minor loss) — Net1 has no valves.
3. Pump restoration = repair outcome (speed 1.0), not a setpoint; the pump stays under Net1's tank-level control.
4. Pressure-driven analysis with required pressure 20 m (= minimum service pressure), minimum 0 m, exponent 0.5.
5. Intervention at 72 h (end of telemetry record); outcomes over 73–96 h (first hour excluded as settling).
6. Requested demand is taken from a demand-driven run of the same faulty network.

## Findings and remaining technical risks
* **Graph connectivity is insufficient.** Isolating tank riser 110 keeps every junction graph-connected to the
  reservoir, but Net1's pump is switched by tank level, so it never restarts and delivery falls to 0 %. Only the
  hydraulic simulation shows this.
* **System memory.** After a valve or pump is restored, network pressures do not return exactly to the no-fault
  run (0.3–2.2 m residual differences) because tank level and pump phase carry the disturbance's history. Tests
  therefore check the mechanism (head loss, pump curve), not equality with the no-fault run.
* **Pump fault speed (existing engine, unchanged):** speed inferred from the fitted curve via affinity laws is
  0.6–2.8 % above the configured value (e.g. 0.8483 vs 0.835), constant over time. Likely an artefact of the
  inference model rather than the event, but EPANET's exact speed-scaling convention was not confirmed.
* **EPyT-Flow 0.17.4 bug:** a second `PumpSpeedEvent` on the same pump crashes (pattern index returned as float).
  Worked around with `PumpSpeedRestoreEvent` (same logic, index cast to int).
* Net1 pressures (≈ 95–130 m) are far above typical service maxima; no maximum-pressure constraint is configured yet.
* Negative-pressure results (pipe 10 / 110 isolation) make emitter and demand quantities non-physical; they are
  flagged, and leak benefit is not reported.
* Diagnosis-confirmation states (CONFIDENT_BUT_UNVERIFIED / CONFIRMED_BY_INVESTIGATION / OPERATOR_CONFIRMED /
  INSUFFICIENT_EVIDENCE) and gating are **M2** — not implemented yet; M1 simulates given faults only.
