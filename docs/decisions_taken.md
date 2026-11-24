# Decisions taken

| Decision | Reason | Alternatives rejected |
|---|---|---|
| Real EPyT-Flow 0.17.4 / EPANET, no synthetic mode | Installed and verified on Python 3.12/Windows; Net1 runs in ~0.1 s | Synthetic demo mode (not needed) |
| Net1 `.inp` stored in repo | EPyT-Flow's primary download URL timed out; backup URL is the official EPyT repo | Download at runtime |
| CMH flow units internally, L/s in outputs | EPyT-Flow leak emitter computation only supports CMH/CFS | LPS (raises in EPyT-Flow) |
| Explicit `end_time` on events | EPyT-Flow `AbruptLeakage.init` overflows on `end_time=None` (math.inf) | — |
| Sensor faults applied to clean readings with EPyT-Flow fault models | Keeps the clean true state for virtual field measurements; physically identical (faults don't change hydraulics) | Faults inside the simulator (true state lost) |
| Custom seeded Gaussian and stuck-at-last-value faults | EPyT-Flow Gaussian fault uses the global RNG (not reproducible); it only offers stuck-at-zero | — |
| Valve disturbance = added minor loss on a pipe | Net1 has no valve elements; adding one would change the benchmark | `ValveStateEvent` (no valves) |
| 4 pressure sensors | Leaves 5 junctions for portable gauges and creates genuine ambiguity | Sensors everywhere (nothing to investigate) |
| Day-1 reference period | Absorbs stationary model mismatch without labels | Raw model residual (mismatch dominates) |
| Split leak/valve/demand by location | Spec requirement; tests generalisation to unseen pipes/nodes | Random split (optimistic) |
| Hybrid = logistic stacker on out-of-fold RF + physics | Transparent, testable, no in-sample leakage | Averaging scores |
| Temperature scaling on validation | One parameter, robust with 450 validation scenarios | Isotonic (too little data per class) |
| Abstention target ≤ 5 % selective risk on validation | Prefers abstention over unsupported diagnosis | Fixed 0.5 threshold |
| Architecture rule from spec, coded before evaluation | Prevents post-hoc selection | Choosing the best-looking test number |
| Investigation merges same-network-state hypotheses | A spot reading cannot distinguish bias from drift at one sensor; the UI says so | Recommending a useless measurement |
| Sensor-interpolated predictions for investigation | Day-1 offset gave 2.3 m error at 15 % mismatch (validation); interpolation of current residuals 1.0 m, at 5 % 0.06 m vs 0.98 m | Day-1 offset only |
| Separation criterion, not information gain | Transparent and fast; labelled honestly | Full Bayesian EIG (time) |
| Backend port 8077 | Port 8000 was occupied on the dev machine | — |
| One global EPANET lock in the API | The toolkit is not assumed thread-safe | Per-request processes |
| Client-rendered Next.js UI with plain `fetch` | Every view is driven by user actions against the API | SWR/React Query (unneeded dependency) |
| No database | JSON scenario files + CSV/JSON artifacts suffice | SQLite/Postgres |
| LightGBM not used | RF already strong; no verified need | — |
| Remediation as an independent package (`aquascope/remediation/`), one `include_router` line in `api.py` | Existing diagnosis engine must not change; regression gate proves it | Editing diagnosis/investigation modules |
| Pressure-driven analysis for interventions | Demand-driven analysis reported −5×10⁷ m and 100 % delivery when supply was cut | DDA |
| Close both `<pipe>` and `leak_pipe_<pipe>` | EPyT-Flow splits a leaking pipe; one half left > 1 L/s leaking | Closing the original id only |
| Confirmation states; only investigation/operator confirmation unlocks hydraulic actions | A confident model prediction is not a confirmed fault | Acting on confident predictions |
| Benefit = removal of the fault's physical mechanism | "Distance to no-fault network" penalised pump repair because tank/pump phase keep the fault's history | Network pressure deviation |
| Pump speed calibrated from observed pump flow | Physics fit under-estimated speed loss (benefit −90 %) | Physics signature magnitude |
| Verification measurement with equal priors | Confident diagnoses had no competing hypothesis to measure against | Operator-only confirmation |
| K-way confirmation check (≥ 0.5 among 6 explanations), selected on validation | Thresholds alone could not get wrong confirmations ≤ 3 % | Stricter posterior/z only |
| Fresh test set for the final remediation numbers | Original test split had been seen once (M4) | Re-using the original test split as headline |
| No ML ranker | ≤ 5 candidates, ~0.5 s exhaustive simulation; dataset kept for future surrogates | Supervised ranking |
