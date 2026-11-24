# Known limitations

## Simulation
* Single small benchmark network (Net1: 9 junctions, 12 pipes). Results will not transfer to large networks unchanged.
* Model mismatch is limited to uniform relative perturbation of roughness, base demand and demand
  pattern. No unmodelled topology errors, valve-status errors, time-varying stochastic demand, or
  transient (water-hammer) behaviour.
* "Valve disturbance" is a modelling surrogate (added minor loss on a pipe) because Net1 has no valves.
* EPyT-Flow 0.17.4 emits EPANET warning 240 ("nonexistent source") when inserting a leak node; it concerns
  water-quality sources (unused). It is filtered in the test config. Inserting the leak node shifts
  pre-leak pressures by ≤ 0.01 m.
* One event per scenario; no simultaneous faults. Events start 30–44 h; day 1 is assumed event-free.

## Dataset
* 2,250 simulated scenarios; per-class test n = 60 (20 per mismatch level). Confidence intervals are not
  reported; differences of ~0.01–0.02 macro-F1 between approaches are within sampling noise.
* Only 4 pressure sensors exist, so sensor-fault scenarios cannot be split by sensor location; they are split by seed.
* Test leak/valve/demand locations are unseen in training, but come from the same small network.

## Model assumptions
* Diagnosis analyses a complete 72-h record (day 3 must be post-event). It is a retrospective classifier;
  the online detector is separate.
* Physics approach parameters were tuned by random search on validation; its scores are poorly calibrated
  (ECE 0.29) and it never reaches the validated abstention threshold, so on its own it always abstains.
* Hybrid stacker and RF learn from simulated features only.
* Investigation predictions use a validation-fitted Gaussian model-error term; real model error can be larger.
* Probabilities are temperature-scaled on simulated validation data; their calibration on real data is unknown.

## Known failure cases (measured)
* Abrupt vs incipient leak: ~15 % confusion each way (both are the same physical state at day 3).
* Valve throttling on unseen pipes (e.g. pipe 11) creates a +6 m upstream pressure rise at one sensor and is
  sometimes diagnosed as sensor drift/bias (valve recall 0.80).
* Low-severity sensor noise is sometimes called normal (noise recall 0.87).
* Leak localisation top-1 is 42.5 % overall and 37.5 % at 15 % mismatch.
* Investigation only compares the two leading physical explanations. If the truth is ranked lower the
  reading fits neither (flagged by the > 3σ check), but the system does not then re-rank automatically.
* Hypotheses that imply the same network state (bias vs drift on one sensor) cannot be separated by a spot
  pressure reading; the system says so instead of recommending.
* 21.7 % of normal test scenarios raised at least one detector alarm (0.20 false alarms/day).

## Not validated
* No real utility data, SCADA formats, sensor placement or field measurements were used.
* No security hardening, authentication or multi-user behaviour; the API is for local development only.
* Frontend has no automated browser tests; the critical journey was exercised manually in a real browser.

## Remediation engine
* Simulation only; isolations are idealised (instant, complete closure); physical valve availability in Net1 is UNKNOWN.
  "Passed configured simulation constraints" never means safe or executable.
* Constraints: connectivity, 20 m minimum service pressure, 99 % delivery, tank must not empty. No maximum-pressure,
  water-quality, transient or energy-cost constraints.
* End-to-end action rate is low (35 % of hydraulic faults on the fresh test set; valve faults 8.3 %; 15 % at 15 %
  mismatch) because confirmation requires a decisive reading. Operator confirmation is the alternative route.
* Confirmations are 99.6 % correct on the fresh test set (263/264), not 100 %; a wrong confirmation can lead to
  isolating the wrong pipe (visible only in simulation verification).
* Leak repair, pressure management and demand restriction are not simulated as interventions.
* Ranking assumes one fault at a time; only Net1; 60 scenarios per class.
