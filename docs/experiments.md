# Experiments

All numbers below come from `backend/data/artifacts/evaluation.json`, produced by
`python -m aquascope.evaluation`. They are **simulation results on Net1**, not real-world accuracy.

## Scenario matrix

10 classes × 3 mismatch levels (0 %, 5 %, 15 %) × {train 40, validation 15, test 20} per cell
= 2,250 scenarios (1,200 / 450 / 600). Severity ~ U(0, 1), event start ~ U{30 h … 44 h}, unique seed per scenario
(so every scenario draws its own mismatch perturbation). Generation takes ~22 s with 6 processes.

## Dataset splits (no leakage)

* Leak, valve and demand scenarios are split **by location**; test locations are never seen in training:
  * leak pipes — train 10, 11, 111, 113, 122, 22 · validation 12, 21 · **test 112, 121, 31**
  * throttled pipes — train 10, 112, 12, 122 · validation 121 · **test 11, 111**
  * demand nodes — train 11, 13, 21, 22, 32 · validation 23 · **test 12, 31**
* Sensor-fault, pump and normal scenarios are split by seed (only 4 sensors / 1 pump exist).
* Each scenario yields one feature vector (no overlapping windows across splits).
* Physics parameters, temperatures, abstention thresholds and the detector threshold are fitted on
  validation only. The test split is touched only by `evaluation.py`.
* Reproducibility: regenerating the dataset produced a byte-identical CSV (MD5 `b378dc4d…`).

## Hypotheses tested

1. Residual + learned features separate the 10 classes under model mismatch.
2. A hybrid of physics and ML beats the best single approach by ≥ 0.05 macro-F1 at 15 % mismatch (pre-registered rule).
3. The recommended junction separates competing explanations better than a random eligible junction.

## Classification (test, n = 600; 200 per mismatch level)

| Approach | Accuracy | Macro-P | Macro-R | Macro-F1 | F1 @0 % | F1 @5 % | F1 @15 % | Brier | ECE |
|---|---|---|---|---|---|---|---|---|---|
| A Physics | 0.805 | 0.831 | 0.805 | 0.804 | 0.865 | 0.797 | 0.742 | 0.458 | 0.291 |
| B Random Forest | 0.923 | 0.926 | 0.923 | 0.923 | 0.934 | 0.935 | 0.900 | 0.110 | 0.047 |
| C Hybrid | 0.938 | 0.940 | 0.938 | 0.938 | 0.965 | 0.933 | 0.915 | 0.117 | 0.079 |

Random Forest per-class (precision / recall, n = 60 each): normal 0.91/0.97 · abrupt leak 0.84/0.85 ·
incipient leak 0.85/0.83 · bias 0.88/0.98 · drift 0.85/0.93 · noise 1.00/0.87 · stuck 1.00/1.00 ·
pump 1.00/1.00 · valve 0.94/0.80 · demand 1.00/1.00.

Main confusions (RF): abrupt ↔ incipient leak (~10 each way); valve disturbance on unseen pipes → sensor
drift/bias (a throttle near one sensor looks like a single-sensor offset); sensor noise → normal at low severity.

## Abstention and reliability (test)

| Approach | Threshold (val) | Coverage | Selective risk | Abstention rate |
|---|---|---|---|---|
| Physics | 0.99 | 0 % | N/A — never reaches the validated threshold | 100 % |
| Random Forest | 0.80 | 79.8 % | 0.8 % | 20.2 % |
| Hybrid | 0.68 | 82.8 % | 2.4 % | 17.2 % |

RF at 15 % mismatch: coverage 72.0 %, selective risk 1.4 %. Selected-approach status counts on test:
CONFIDENT 479, AMBIGUOUS_DIAGNOSIS 112, INSUFFICIENT_EVIDENCE 9. Risk–coverage curves are shown in the UI.

## Leak localisation (physics signature ranking over 11 pipes; test leak pipes unseen in training)

| Mismatch | n | Top-1 | Top-3 | Mean network distance of top-1 |
|---|---|---|---|---|
| 0 % | 40 | 50.0 % | 90.0 % | 161 m |
| 5 % | 40 | 40.0 % | 82.5 % | 201 m |
| 15 % | 40 | 37.5 % | 72.5 % | 644 m |
| all | 120 | 42.5 % | 81.7 % | 335 m |

Random baseline: top-1 9.1 %, top-3 27.3 %. With only 4 pressure sensors, single-candidate localisation is weak.

## Detection (online detector, threshold 2.5 chosen on validation)

* False alarms: 0.20 per day of normal operation (n = 60 normal test scenarios; 21.7 % had at least one alarm).
* Event detection rate 96.1 % (n = 540), 2.0 % alarmed before the true start (counted as not detected).
* Detection delay: median 2.5 h, P90 12 h. Per class median: abrupt leak 1.0 h, incipient 6.75 h, bias 2.0 h,
  drift 9.5 h, noise 3.0 h, stuck 2.5 h, pump 1.0 h, valve 6.0 h, demand 1.5 h.

## Latency

End-to-end `diagnose_scenario` (features, all three approaches, evidence, detector): median 59 ms,
p95 64 ms (n = 30, warm process). Recommendation adds two EPANET runs (~0.2 s).

## Architecture selection (rule fixed in code before evaluation)

Hybrid only if, at 15 % mismatch, macro-F1 ≥ best single + 0.05, ECE ≤ best single + 0.02, latency < 1 s.
Result: hybrid 0.915 vs RF 0.900 (+0.015 ✗), ECE 0.115 vs 0.071 (✗), latency ✓ → **Random Forest selected**.

## Active investigation vs random measurement location

Run on every test scenario with the selected approach. Cases counted when the true physical state is one
of the two investigated explanations and a recommendation (S ≥ 2) was made.

| Subset | n | Correct before reading | Recommended junction | Random eligible junction |
|---|---|---|---|---|
| all such cases | 134 | 76.9 % | **98.5 %** | 81.7 % |
| non-confident diagnoses only | 36 | 63.9 % | **100 %** | 74.3 % |

In 95 further cases the truth was in the pair but no junction reached S ≥ 2, and the system reported
"no reliable additional measurement location" instead of guessing. Cases where the truth was not among
the two leading explanations are not counted here (the reading then fits neither; flagged by the >3σ check).
