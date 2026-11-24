# Remediation Intelligence Engine — M2 report (confirmation, gating, action generation)

Status: **M2 complete. M3+ (ranking, explanation, verification on the true network, dataset, evaluation, API, UI)
NOT started.** No ranking or "best action" exists yet; M2 only produces gated candidate lists.

## Files
| File | Change |
|---|---|
| `aquascope/remediation/confirmation.py` | new — confirmation states and builders |
| `aquascope/remediation/actions.py` | new — structural pre-screen, rule-based generator, gating |
| `aquascope/remediation/config.py` | M1 file, extended — confirmation policy constants |
| `tests/test_remediation_actions.py` | new — 26 tests |
| `scripts/regression_snapshot.py` | M0 file — ignores all `tests/test_remediation*.py` so the gate covers exactly the 49 pre-existing tests |

No existing diagnosis / simulation / investigation module was modified (regression gate: PASS).

## Confirmation states
| State | Produced by | Hydraulic interventions |
|---|---|---|
| `INSUFFICIENT_EVIDENCE` | diagnosis AMBIGUOUS / INSUFFICIENT; or an investigation that did not resolve | none generated; only "investigate first" + no-action baseline |
| `CONFIDENT_BUT_UNVERIFIED` | diagnosis CONFIDENT (model only) | listed for information, all **locked** |
| `CONFIRMED_BY_INVESTIGATION` | simulated reading: node separation ≥ 2σ **and** consistent (≤ 3σ) **and** posterior ≥ 0.95 | unlocked at the confirmed location only |
| `OPERATOR_CONFIRMED` | named operator + note; validated only against the Net1 model (class/location exist), recorded as "not verified by AquaScope" | unlocked at the confirmed location only |

The 0.95 posterior is a configured policy, not tuned on data (no remediation dataset exists yet).

## Candidate generation (rules)
| Confirmed class | Hydraulic (simulated, M1) | Non-hydraulic (informational) |
|---|---|---|
| abrupt / incipient leak | isolate confirmed pipe (both split halves); isolate alternative-location pipes (always locked, comparators) | leak survey, schedule repair |
| valve disturbance | restore valve | valve inspection |
| pump disturbance | restore pump (repair outcome) | pump inspection |
| demand anomaly | — | field inspection / customer contact |
| sensor faults | — | sensor maintenance (flag, interpolate, recalibrate) |
| normal | — | continue monitoring |

Every list includes the no-action baseline. Every candidate carries `real_world_executable = False` and
`NOT VALIDATED FOR REAL-WORLD EXECUTION`; isolations carry physical valve availability `UNKNOWN` and the
idealised-isolation assumption label.

## Structural pre-screen (static, before any simulation)
Isolation of pipe *p* is screened out if, in the Net1 graph without *p*: a junction loses every source; a junction
is left on tank-only supply; or a node referenced by an EPANET `[CONTROLS]` rule is cut off from the reservoir.
Result over all 12 Net1 pipes: **rejects exactly 10 and 110**. Pipe 110 is caught only by the control rule (the tank
level switches pump 9). A test confirms these agree with the M1 simulation (10 and 110 fail the simulated
constraints; 121 passes). A screened-out isolation stays locked even when confirmed.

## Observed behaviour on real pipeline cases (illustrative, n = 5; not an evaluation)
| Scenario | Diagnosis → state | Investigation | Unlocked hydraulic action |
|---|---|---|---|
| leak @121 (held-out), seed 108, 5 % | AMBIGUOUS → INSUFFICIENT_EVIDENCE | J-32, posterior 1.00 → CONFIRMED | isolate 121 (31, 122 locked) |
| leak @122, seed 777, 5 % | AMBIGUOUS → INSUFFICIENT_EVIDENCE | posterior 0.880 < 0.95 → stays INSUFFICIENT | none (investigate first) |
| sensor bias @22, 0 % | CONFIDENT → CONFIDENT_BUT_UNVERIFIED | not needed | none (sensor maintenance only) |
| valve @11, 0 % | AMBIGUOUS → INSUFFICIENT_EVIDENCE | J-11, posterior 1.00 → CONFIRMED | restore valve 11 |
| leak @10, seed 3, 0 % | CONFIDENT → UNVERIFIED, but localised to pipe **11** (wrong) | — | none; pipe 10 also screened out |

The last row shows why confident ≠ confirmed: the model was confident about a leak but at the wrong pipe.

## Known limitations / risks carried into M3
* Investigation resolves the *physical state*; for a leak it reports the lead class of a merged group
  (e.g. `incipient_leak` for a true abrupt leak). Leak sub-type does not change the action.
* `OPERATOR_CONFIRMED` has no fitted magnitude; M3 must estimate one (or use the diagnosis fit) before predicting outcomes.
* Investigation compares two hypotheses only; a confirmation is conditional on the truth being one of them
  (guarded by the ≤ 3σ consistency check, not eliminated).
* Pre-screen covers graph connectivity and `[CONTROLS]` dependencies only; hydraulic adequacy (pressure, supply,
  tank) is judged by the M1 simulation in M3.
