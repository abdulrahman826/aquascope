# Remediation Intelligence Engine — fixes after M4, and final evaluation

All numbers: simulated EPANET Net1 scenarios, outcomes on the hidden *true simulated* network. Not real-world validation.

## Findings fixed
| M4 finding | Fix | Where |
|---|---|---|
| Pump benefit under-predicted by ~90 % → 17/60 confirmed pump faults got "no intervention" | Pump speed calibrated from **observed** pump-flow telemetry (bisection on simulated day-3 pump-on flow, nominal model); replaces the physics-signature magnitude | `rank.estimate_pump_speed` |
| Confident diagnoses could never be confirmed by measurement (no competing hypothesis) | **Verification measurement**: confident hypothesis vs strongest physically different alternative, **equal priors** (the model's own confidence cannot decide) | `verification.py` |
| 7.9 % of investigation confirmations wrong | **K-way check**: the confirmed state must hold ≥ 0.5 equal-prior posterior among the 6 leading physical explanations; selected on validation | `verification.kway_posterior`, `confirmation.confirm_measurement`, `select_policy.py` |

Pump calibration (9 cases, 0/5/15 % mismatch × severity 0/0.6/1): speed error ≤ 0.012; benefit prediction within 6 %.

### Confirmation policy selection (validation split only)
Target fixed before looking: wrong confirmations ≤ 3 %. Posterior / z thresholds alone could **not** meet it (best: 4.6 %
wrong while losing a third of confirmations): 16 of 20 remaining errors were readings fitted almost perfectly
(|z| < 2) by a *neighbouring* explanation outside the tested pair. Adding the K-way check met it:

| Policy (validation) | Confirmations | Wrong | Hydraulic-truth confirmations |
|---|---|---|---|
| posterior ≥ 0.95, z ≤ 3 (old) | 241 | 19 (7.9 %) | 97 |
| + K-way ≥ 0.5 (**selected**) | 165 | 0 (0 %) | 35 |

Selected: posterior ≥ 0.95, z ≤ 3, K-way ≥ 0.5 (`data/remediation/confirmation_policy.json`).

## Final evaluation — fresh held-out test set
* **Fresh test set:** 600 new scenarios (seeds ≥ 1,000,001, never used anywhere), same design as the original test split
  (held-out leak pipes 112/121/31, valve pipes 11/111, demand nodes 12/31; 20 per class per mismatch level).
* Code frozen before the run: MD5 of `aquascope/remediation/*.py` = `74874ba6e4c732000e74fb450adc5176` (unchanged
  after the run). One post-freeze edit: silencing EPANET's "pumps cannot deliver enough flow or head" warning inside
  pump-speed bisection probes (no behavioural change; 175/175 tests unchanged).

### Hydraulic-fault scenarios (n = 240)
| Policy | Acts | Resolved | Ineffective / harmful | Constraint violation when acting |
|---|---|---|---|---|
| **AquaScope end-to-end** | 35.0 % | **35.0 %** | **0.0 %** | **0 %** |
| AquaScope, correct confirmation | 100 % | **100 %** | 0 % | 0 % |
| Naive (act on top-1 diagnosis) | 95.4 % | 61.7 % | 33.8 % | 9.6 % |
| Random feasible isolation (mean) | 100 % | 8.2 % | 91.8 % | 4.4 % |
| Do nothing | 0 % | 0 % | 0 % | N/A |

By class, end-to-end resolved: abrupt leak 23.3 %, incipient 28.3 %, **pump 80.0 %** (was 0 %), valve 8.3 % (was 58.3 %).
By mismatch: 47.5 % (0 %), 42.5 % (5 %), 15.0 % (15 %), n = 80 each.
Non-hydraulic scenarios (n = 360): AquaScope attempted **0** hydraulic actions (naive: 0.6 %).
Confirmations correct: **263 / 264 (99.6 %)** (was 116 / 126, 92.1 %). Constraint-prediction misses: 0.
Benefit prediction median relative error (correct confirmation): abrupt +1.9 %, incipient −0.2 %, pump +0.1 %, valve −3.8 %.

### Comparison with the M4 run (original test split)
| | M4 (before fixes) | After fixes, original test (second look) | After fixes, **fresh test** |
|---|---|---|---|
| End-to-end resolved | 25.8 % | 31.7 % | **35.0 %** |
| End-to-end ineffective/harmful | 0.8 % | 0.4 % | **0.0 %** |
| Correct-confirmation resolved | 92.9 % | 100 % | **100 %** |
| Confirmation precision | 92.1 % | 99.6 % | **99.6 %** |

The original test split had been seen once (M4), so its post-fix numbers are a second look and not used as the headline.

## Trade-offs and remaining limitations
* Stricter confirmation costs coverage: valve faults are now rarely confirmed end-to-end (8.3 %), because readings
  near a throttled pipe are often explained equally well by a neighbouring explanation. Operator confirmation still
  unlocks them (100 % resolved with correct confirmation).
* End-to-end resolution falls with mismatch (47.5 % → 15.0 %): fewer readings are decisive when the model is wrong.
* 60 scenarios per class; no confidence intervals. One small network (Net1).
