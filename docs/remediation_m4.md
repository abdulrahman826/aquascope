# Remediation Intelligence Engine — M4 report (dataset + evaluation against baselines)

Status: **M4 complete. M5 (API/UI) and M6 (docs) NOT started.** All outcomes are on the hidden *true simulated*
network (actual fault + model mismatch). This is simulation evidence on EPANET Net1, not real-world validation.

## Method
* **Scenarios:** the existing AquaScope dataset splits, read-only. Validation (450) used for development; held-out
  test (600; leak test pipes 112/121/31, valve test pipes 11/111 unseen in training) run once after freezing the code
  (MD5 of `aquascope/remediation/*.py` = `a139f8b223ee5d0ed9053cb8ad36367d`, recorded before the test run and unchanged after).
* **Policies:** engine_pipeline (existing diagnosis → investigation if available → confirmation → rank) ·
  engine_oracle_confirmation (true fault confirmed; isolates remediation from diagnosis errors) · naive_top1 (act on
  unconfirmed top-1 diagnosis, no gating, no constraint check) · random_feasible_mean (mean over isolating each of the
  10 pre-screen-passing pipes) · do_nothing · oracle (correct action if it passes the constraints on the true network).
* **Outcome definitions:** *resolved* = leak flow after < 5 % of no-action flow (no negative pressures), or < 0.5 m excess
  head loss / pump head deficit left. *Ineffective or harmful* = a hydraulic action was attempted and the fault was not
  resolved, or the network had no hydraulic fault, or a configured constraint failed. *Regret* = 1 − benefit/oracle benefit.
* **Dataset:** `data/remediation/test_rows.csv` — 1,026 labelled candidate rows (predicted on nominal model vs actual on
  true network) plus policy rows; `test_scenarios.csv` per scenario; `test_evaluation.json` aggregates.
  **Reproducible:** two runs of the test split gave byte-identical rows (MD5 `ad3a34284526813ec70e57fc79146bf6`).
* **Methodology fix made on validation, before the test run:** an attempted action with no hydraulic effect on the
  true network (e.g. "re-open" a valve that is not throttled) was initially counted as "not acted"; it is now counted as
  an attempted, ineffective intervention.
* **No ML model trained.** Exhaustive simulation of ≤ 5 candidates per case takes ~0.3–0.6 s; the dataset is kept for
  a possible future surrogate model, which would need its own validation.

## Results — held-out test, hydraulic-fault scenarios (n = 240: 60 abrupt leak, 60 incipient, 60 valve, 60 pump)
| Policy | Intervention rate | Resolved | Ineffective / harmful | Constraint violation when acting | Mean regret |
|---|---|---|---|---|---|
| **engine_pipeline** | 26.7 % | 25.8 % | **0.8 %** (2) | **0 %** | 0.741 |
| engine_oracle_confirmation | 92.9 % | **92.9 %** | 0 % | 0 % | 0.071 |
| naive_top1 | 95.0 % | 64.2 % | 30.8 % | 8.8 % | 0.306 (n = 230) |
| random_feasible_mean | 100 % | 7.9 % | 92.1 % | 4.4 % | 1.037 |
| do_nothing | 0 % | 0 % | 0 % | N/A | 1.000 |
| oracle | 100 % | 100 % | 0 % | 0 % | 0 |

By class (resolved / ineffective-or-harmful):
| Class | engine_pipeline | engine_oracle_confirmation | naive_top1 |
|---|---|---|---|
| abrupt leak | 26.7 % / 1.7 % | 100 % / 0 % | 50.0 % / 50.0 % |
| incipient leak | 18.3 % / 1.7 % | 100 % / 0 % | 35.0 % / 65.0 % |
| valve | 58.3 % / 0 % | 100 % / 0 % | 71.7 % / 8.3 % |
| pump | **0 % / 0 %** | **71.7 %** / 0 % | 100 % / 0 % |

By mismatch, engine_pipeline resolved 41.2 % (0 %), 23.7 % (5 %), 12.5 % (15 %) — n = 80 each; ineffective/harmful
1.3 % / 0 % / 1.3 %. engine_oracle_confirmation: 91.2 % / 90.0 % / 97.5 %.

## Results — held-out test, non-hydraulic scenarios (n = 360: normal, 4 sensor faults, demand; 60 each)
engine_pipeline attempted a hydraulic action in 1 / 360 (0.3 %: sensor noise @31 investigation-confirmed as a valve
disturbance @11 → "restore valve", ineffective). naive_top1: 3 / 360 (0.8 %).

## Engine internals (test, all 600)
* Confirmation states: CONFIDENT_BUT_UNVERIFIED 386 · CONFIRMED_BY_INVESTIGATION 126 · INSUFFICIENT_EVIDENCE 88.
* Why not confirmed: no competing hypothesis to measure 284 · no reliable measurement location 166 · reading did not
  meet the policy 24.
* Investigation confirmations correct (class family + location): **116 / 126 (92.1 %)**. Wrong confirmations that led
  to a hydraulic action: 3 (leak @31 → isolate 121; leak @31 → isolate 122; sensor noise → restore valve 11).
* Pipeline on hydraulic truths: investigate first 176 · restore valve 35 · isolate 29.
* Constraint-prediction misses (predicted pass, actual fail): **0** (pipeline and oracle-confirmation).
* Predicted vs actual benefit, median relative error (oracle-confirmation): abrupt leak +3.1 % (n = 60), incipient
  +0.6 % (60), valve −2.2 % (60), **pump −90.4 % (43)**. Pump: **17 / 60 confirmed pump faults received "no
  intervention"** because the under-predicted benefit fell below the 0.5 m minimum (validation: 9 / 45).

## Interpretation
1. **The remediation logic itself works when the diagnosis is confirmed correctly:** 92.9 % resolved, zero ineffective
   actions, zero constraint violations; all of the shortfall is the pump magnitude problem.
2. **The end-to-end engine is conservative by design:** it acts in 26.7 % of hydraulic-fault cases and is
   ineffective/harmful in 0.8 %, versus naive action (64.2 % resolved but 30.8 % ineffective/harmful, 8.8 % constraint
   violations). The main limit is confirmation coverage, not ranking.
3. **Structural confirmation gap:** a *confident* diagnosis has no competing hypothesis, so active investigation cannot
   confirm it (284 cases). Every confident pump diagnosis therefore stays "investigate first" (pump pipeline resolved 0 %).
   Without operator confirmation, confident diagnoses never unlock action.
4. Investigation confirmations are wrong 7.9 % of the time; with a 0.95 posterior policy this is a measured, not
   theoretical, false-confirmation rate.

## Open decisions for you (not changed by me)
* Pump magnitude: use a conservative benefit for repair-type actions (e.g. sign of the confirmed fault rather than the
  fitted magnitude), or improve the pump magnitude estimate — either needs re-evaluation on a fresh run.
* A confirmation route for confident diagnoses (e.g. a verification measurement against "normal / no fault"),
  or keep requiring operator confirmation.
* Whether 92 % investigation-confirmation precision is acceptable at the 0.95 posterior policy.
* Sample sizes are 60 per class; no confidence intervals are reported.
