# AquaScope

**Evidence-driven water network diagnosis and active investigation** — a hackathon prototype.

AquaScope takes pressure and flow telemetry from a water distribution network, decides whether
something abnormal is happening, weighs **competing explanations** (leak vs. sensor fault vs.
operational disturbance vs. abnormal demand), says how sure it is, **abstains** when the evidence
is insufficient, and — its signature feature — **recommends where to take one extra pressure
reading** to separate the leading explanations, then simulates that reading and updates the diagnosis.

**New here? Open the app and use the "Start here" tab — a 5-step guided walkthrough in plain English.
Demo script: [DEMO.md](DEMO.md).**

> All data are **simulated** with EPyT-Flow / EPANET on the Net1 benchmark network with injected
> model mismatch. Nothing here has been validated on real utility data. Not production software.

## Features (implemented and tested)

| Area | What exists |
|---|---|
| Simulation | EPyT-Flow 0.17.4 / EPANET on Net1 (3 days, 30-min telemetry). 10 scenario classes: normal, abrupt leak, incipient leak, sensor bias, drift, noise, stuck, pump disturbance, valve (throttled pipe) disturbance, demand anomaly. Seeded, bit-reproducible. Model mismatch 0 / 5 / 15 % (roughness, base demand, demand pattern). |
| Diagnosis A | Physics/residual: observed vs. nominal EPANET model, day-1 reference profile, signature matching against EPANET-simulated hypotheses (leak at each pipe, throttling at each pipe, demand surge at each node, pump), sensor-isolation and sensor-health evidence; localisation ranking. |
| Diagnosis B | Random Forest on 43 residual features, location-grouped splits, saved model, feature-schema check at load. |
| Diagnosis C | Hybrid logistic stacker over out-of-fold RF probabilities + physics scores. |
| Uncertainty | Temperature scaling on the validation split; validated abstention threshold; statuses `CONFIDENT`, `AMBIGUOUS_DIAGNOSIS`, `INSUFFICIENT_EVIDENCE`; supporting and contradicting evidence for each hypothesis. |
| Active investigation | Simulates the two leading physical explanations, predicts the true pressure at every junction, ranks junctions by noise-aware separation, recommends one (or says none is reliable), simulates the field reading from the hidden network state and updates the posterior, flagging readings that fit neither hypothesis. |
| Evaluation | Held-out test (600 scenarios, unseen leak/valve/demand locations): classification, calibration, risk-coverage, localisation, detection delay, false alarms/day, latency, investigation vs. random node, pre-registered architecture-selection rule. |
| Remediation | Corrective-action engine: diagnosis confirmation states (only investigation- or operator-confirmed faults unlock hydraulic actions), rule-based candidates (leak isolation of both split pipe halves, valve/pump restoration, maintenance tasks), structural pre-screen, pressure-driven EPANET simulation of every candidate, hard constraint checks, ranking, explanation, and verification on the hidden true network. **Simulation only — no control interface; never an operational instruction.** |
| UI | Next.js command centre. **Start here**: guided 5-step walkthrough (pick a problem → what AquaScope thinks → one extra reading → what to do → did it work) with 4 pre-checked examples. Expert views: Overview, Network explorer, Telemetry, Diagnosis, Investigation, Remediation, Experiments (scenario generator + live evaluation results). Everything shown comes from the API. |

## Architecture

```
frontend/ (Next.js 16, TypeScript, Tailwind 4, Recharts)  --REST-->  backend/ (FastAPI)
                                                                     ├─ simulation.py   EPyT-Flow scenarios, custom events
                                                                     ├─ features.py     residuals + 43 features
                                                                     ├─ physics.py      approach A + localisation
                                                                     ├─ models.py       approach B (RF), C (hybrid), calibration
                                                                     ├─ diagnosis.py    abstention, evidence, online detector
                                                                     ├─ investigation.py measurement recommendation
                                                                     ├─ dataset.py / tune_physics.py / evaluation.py
                                                                     └─ api.py
```
Details: [docs/architecture.md](docs/architecture.md).

## Installation (Windows, tested; Python 3.12, Node 24)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
```powershell
cd frontend
npm install
```
The Net1 `.inp` file is included in `backend/data/networks/`. Trained models and evaluation results
are included in `backend/data/artifacts/`. To rebuild everything from scratch (about 2 minutes):
```powershell
cd backend
.venv\Scripts\python -m aquascope.dataset
.venv\Scripts\python -m aquascope.tune_physics
.venv\Scripts\python -m aquascope.models
.venv\Scripts\python -m aquascope.evaluation
```

## Run

Backend (port **8077**):
```powershell
cd backend; .venv\Scripts\python -m uvicorn aquascope.api:app --port 8077
```
Frontend (port 3000):
```powershell
cd frontend; npm run dev
```
Open http://localhost:3000. To point the UI at another backend URL set `NEXT_PUBLIC_API_BASE`.

## API

Interactive docs at http://localhost:8077/docs. Errors are JSON `{"error": {"code", "message"}}`
(422 invalid input, 404 unknown scenario, 409 wrong order, 502 simulation failure, 500 internal).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | backend, models, selected approach |
| GET | `/api/network` | Net1 topology (nodes, links, sensors) |
| GET | `/api/options` | event classes, valid locations, mismatch levels |
| GET | `/api/scenarios` | recent scenarios |
| POST | `/api/scenarios/run` | simulate a scenario (`event_class, seed, severity, mismatch, pressure_noise, location?, start_time?`) |
| GET | `/api/telemetry/{id}` | sensor telemetry + nominal model |
| GET | `/api/scenarios/{id}/ground_truth` | reveal the injected event (simulation only) |
| POST | `/api/diagnosis/{id}` | diagnosis (`{"approach": "physics"|"random_forest"|"hybrid"}` optional) |
| POST | `/api/investigation/{id}` | measurement recommendation |
| POST | `/api/investigation/{id}/measure` | simulate a reading at `{"node": "32"}` and update |
| POST | `/api/evaluation/run` | re-run held-out evaluation in the background |
| GET | `/api/evaluation/results` | latest evaluation results |
| POST | `/api/remediation/{id}/confirm` | `{"mode":"auto"}` (measurement) or `{"mode":"operator", event_class, location, operator_id, note}` |
| POST | `/api/remediation/{id}/plan` | candidate actions, simulated ranking, explanation |
| POST | `/api/remediation/{id}/verify` | apply the recommendation to the hidden true simulated network; before/after |

## Tests

```powershell
cd backend; .venv\Scripts\python -m pytest -q
```
```powershell
cd frontend; npx eslint src; npx next build
```
181 backend tests: 49 for the diagnosis engine (simulation, diagnosis, investigation, API) and 132 for the remediation engine. Frontend lint + type-checked build.
Full-journey sweep through the live API (every class × mismatch × 3 seeds, plus the 4 demo examples), backend running:
`.venv\Scripts\python scripts\sweep_journey.py 3` (writes `data/sweep_journey.csv`; `0` checks only the demo examples).
Regression gate proving the diagnosis engine is unchanged (run in `backend`): `.venv\Scripts\python scripts\regression_snapshot.py --check`.
The end-to-end browser journey was exercised manually through the browser pane (see BUILD_STATUS.md).

## Headline results (held-out test, simulated, n = 600; 200 per mismatch level)

| Approach | Macro-F1 | F1 @ 15 % mismatch | ECE | Coverage at threshold | Selective risk |
|---|---|---|---|---|---|
| A Physics | 0.804 | 0.742 | 0.291 | 0 % (always abstains) | N/A |
| **B Random Forest (selected)** | **0.923** | **0.900** | **0.047** | 79.8 % | 0.8 % |
| C Hybrid | 0.938 | 0.915 | 0.079 | 82.8 % | 2.4 % |

Hybrid gained only +0.015 macro-F1 at 15 % mismatch (rule requires +0.05) and was less calibrated, so the
pre-registered rule selected the Random Forest. Active investigation: on 134 test cases where the truth
was one of the two investigated explanations, the recommended junction resolved to the truth **98.5 %**
of the time vs **81.7 %** for a random eligible junction (76.9 % before any reading).
Full results: [docs/experiments.md](docs/experiments.md). Limitations: [docs/known_limitations.md](docs/known_limitations.md).

## Remediation Intelligence Engine (results)

Fresh held-out test set (600 never-used scenarios; 240 with hydraulic faults), outcomes on the hidden true simulated network:

| Policy | Resolved | Ineffective / harmful | Constraint violations when acting |
|---|---|---|---|
| **AquaScope end-to-end** | 35.0 % | **0.0 %** | **0 %** |
| AquaScope with correct confirmation | 100 % | 0 % | 0 % |
| Naive (act on top-1 diagnosis) | 61.7 % | 33.8 % | 9.6 % |

The engine is deliberately conservative: it acts only after a decisive confirming measurement (99.6 % of confirmations
correct) or an operator confirmation. Details: [docs/remediation_fixes.md](docs/remediation_fixes.md) and the milestone
reports `docs/remediation_m1.md` … `docs/remediation_m4.md`. Rebuild: `python -m aquascope.remediation.evaluate fresh_test`.
