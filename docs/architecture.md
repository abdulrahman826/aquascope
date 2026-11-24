# AquaScope architecture

## Components and data flow

```
Experiments UI ──POST /scenarios/run──► simulation.run_scenario ──► EPyT-Flow ScenarioSimulator (EPANET)
                                              │  hydraulic events injected in EPANET
                                              │  sensor faults applied to clean readings (EPyT-Flow fault models)
                                              ▼
                                   scenario JSON (data/scenarios/<id>.json)
                                   telemetry (4 pressure + 2 flow sensors) | hidden full-network state | ground truth
                                              │
Diagnosis UI ──POST /diagnosis──► features.extract ──► physics.diagnose ─┐
                                      │                                  ├─► models: RF, hybrid, temperature scaling
                                      │                                  ┘        │
                                      └─► diagnosis.detect (online alarm)          ▼
                                                                   decide(): CONFIDENT / AMBIGUOUS / INSUFFICIENT
                                                                   + evidence for/against each hypothesis
Investigation UI ──POST /investigation──► investigation.recommend ──► hypothesis_run (EPANET re-simulation)
                 ──POST /measure──► simulate_measurement (reads hidden state + gauge noise) ──► posterior update
```

The hidden full-network state is never used by diagnosis or recommendation; only by the virtual field
measurement and by the evaluation.

## Simulation design (`simulation.py`)

* Network: EPANET Net1 (9 junctions, 12 pipes, 1 pump, 1 tank, 1 reservoir), loaded with EPyT-Flow
  `load_inp` in CMH units (EPyT-Flow leaks only support CMH/CFS). Pressures in m, flows reported in L/s.
* 72 h simulation, 15-min hydraulic step, 30-min reporting (145 samples). Day 1 is an event-free
  reference period; events start at a seeded random time between 30 h and 44 h.
* Sensors: pressure at junctions 11, 13, 22, 31; flow at pump 9 and tank pipe 110. The other five
  junctions (10, 12, 21, 23, 32) are where a portable gauge could be placed.
* Events (all EPyT-Flow classes unless noted):
  * abrupt leak — `AbruptLeakage` (diameter 12–30 mm by severity)
  * incipient leak — `IncipientLeakage` (same diameters, 10–18 h ramp)
  * sensor bias / drift — `SensorFaultConstant` / `SensorFaultDrift` (±1–4 m, ±2–6 m/day)
  * sensor noise — Gaussian fault, re-implemented on a *seeded* generator (EPyT-Flow's uses the global numpy RNG)
  * stuck sensor — custom `SensorFault` subclass (frozen at last good value; EPyT-Flow only has stuck-at-zero)
  * pump disturbance — `PumpSpeedEvent` (speed 0.92 → 0.75)
  * valve disturbance — custom `SystemEvent`: Net1 has no valve elements, so a partially closed in-line
    isolation valve is modelled as an added minor-loss coefficient on a pipe, sized to give 3–10 m head
    loss at nominal velocity (only pipes with enough flow for throttling to matter)
  * demand anomaly — custom `SystemEvent`: base demand of one junction × 1.5–3
* Model mismatch: EPyT-Flow `ModelUncertainty` with relative uniform ±level on pipe roughness, base demand
  and demand-pattern values, seeded per scenario. The diagnostic model is always the unperturbed network.
* Measurement noise: Gaussian pressure noise (default 0.1 m, configurable) and 1 % flow noise, seeded.
* Failures raise `SimulationError` (HTTP 502); non-finite results are rejected. Nothing is fabricated.

## Residuals and features (`features.py`)

`r_s(t) = observed − nominal model`; the day-1 residual profile `b_s(time-of-day)` absorbs stationary
mismatch; `d_s(t) = r_s(t) − b_s(t mod 24h)`. Day 3 is fully post-event for every scenario. Features (43):
day-3 median shift and spatial isolation per sensor, high-frequency roughness ratio, frozen-sample
fraction, day-3 slope, growth, aggregates (isolation ratio, sign consistency, spread, persistence), and
mass-balance features (consumption = pump + tank outflow: shift, growth, correlation of the excess with the
demand pattern), pump duty-cycle change, tank flow shift. No feature uses labels or event timing.

## Diagnosis approaches

* **A — Physics (`physics.py`)**: a signature library is built by simulating every hydraulic hypothesis
  (11 leak pipes, 7 throttle pipes, 8 demand nodes, pump) on the nominal model and passing it through
  the same feature extractor. The observed signature (4 pressure shifts, consumption shift, pump duty
  change) is fitted to each with a non-negative scale; misfit → Gaussian log-likelihood with a complexity
  penalty. Sensor faults are fitted as a single-sensor deviation. Temporal and sensor-health evidence
  (growth, pattern correlation, slope, roughness, frozen fraction) split families into classes. Its 11
  parameters were chosen by random search on the validation split.
* **B — Random Forest (`models.py`)**: 400 trees, `min_samples_leaf=2`, balanced class weights, seed 42.
* **C — Hybrid**: multinomial logistic regression on `[log RF probs, log physics scores]`. RF inputs are
  out-of-fold (5-fold GroupKFold by location/seed) so the stacker never sees in-sample RF outputs.
* Each approach is temperature-scaled on validation; the abstention threshold is the smallest confidence
  with ≤ 5 % selective risk on validation. `decide()` returns CONFIDENT if p1 ≥ τ, AMBIGUOUS if p1 + p2 ≥ τ,
  otherwise INSUFFICIENT_EVIDENCE.
* **Online detector (`diagnosis.detect`)**: 3-h rolling-median residual z-score, consumption z-score,
  frozen-reading and roughness checks; threshold chosen on validation normal scenarios for ≤ 0.25 false
  alarms/day.

## Active investigation design (`investigation.py`)

1. Expand ranked classes into concrete hypotheses using the physics localisation (top 3 locations each,
   weighted by likelihood).
2. Merge hypotheses with the same physical network state (e.g. bias / drift / noise at sensor 22) — a spot
   reading cannot separate them, and saying so is part of the output.
3. For the two leading states, predict the true pressure at each junction over the last 2 h:
   a healthy monitored node → its own reading; otherwise → EPANET re-simulation with the fitted event +
   inverse-distance interpolation of the current model error seen by sensors that are healthy under that
   hypothesis.
4. Score `S(n) = |μ1 − μ2| / σ(n)`, with σ combining gauge noise and a model-error term fitted on validation
   normal scenarios (`0.05 + k·ref_level²`). Recommend the arg-max if `S ≥ 2` (≈ 84 % single-reading
   correctness under a Gaussian assumption), otherwise report that no reliable location exists.
5. `simulate_measurement` reads the hidden true state + gauge noise, computes the posterior over the two
   states, and warns when the reading is > 3σ from both predictions.

This is a transparent pressure-separation criterion, **not** Bayesian expected information gain.
