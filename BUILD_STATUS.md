# BUILD_STATUS — AquaScope

Last updated: 2026-11-23.

## Environment findings
* Windows 11, Git Bash + PowerShell. Python 3.12.4 (system), Node 24.18.0, npm 11.16.0. ~30 GB free disk.
* `C:\Users\rehan\aquascope` was empty; not a git repository. Nothing was overwritten.
* Backend venv: `backend/.venv` with EPyT-Flow 0.17.4, numpy 2.5, pandas 3.1, scikit-learn 1.9, scipy 1.18,
  FastAPI 0.165 (`backend/requirements.lock.txt` has the exact freeze).
* Port 8000 was already used by another local server → AquaScope backend uses **8077**.
* `create-next-app` initialised its own git repository with an initial commit in `frontend/`; that nested
  `.git` was deleted (no git history created by this project; nothing committed or pushed).

## Completed
* EPyT-Flow / EPANET Net1 simulation for 10 scenario classes, seeded and byte-reproducible; 0/5/15 % mismatch.
* Feature extraction, physics approach (signature library + localisation), Random Forest, hybrid stacker,
  temperature calibration, validated abstention, evidence for/against, online detector.
* Active investigation (recommendation, "no reliable location" handling, virtual measurement, posterior,
  inconsistency warning) and its evaluation against random node choice.
* Evaluation pipeline with location-grouped held-out test, pre-registered architecture rule (→ Random Forest).
* FastAPI backend (12 endpoints, JSON errors, CORS, EPANET lock). Next.js UI with 6 views wired to the API.
* Documentation: README, docs/architecture.md, docs/experiments.md, docs/decisions_taken.md, docs/known_limitations.md.

## Failed / not done
* No automated frontend/browser tests (journey was exercised manually; see below).
* LightGBM not tried. Bayesian expected information gain not implemented (separation score instead).
* No re-ranking beyond the top-2 hypotheses after an inconsistent measurement.
* No confidence intervals on metrics.

## Tests executed
* `backend`: `python -m pytest -q` → **49 passed** (simulation 20, diagnosis 11, investigation 6, API 12).
  Initially 2 failed (leak pre-onset tolerance; EPyT-Flow splits the pipe at the leak node, ≤ 0.01 m) — tolerance
  set to 0.02 m and documented.
* `frontend`: `npx eslint src` clean; `npx next build` succeeds with TypeScript check.
* Dataset regeneration reproducibility: identical MD5 before/after.
* Real-browser end-to-end (built-in browser pane, backend + `npm run dev`):
  load dashboard → network topology → Experiments: abrupt leak, pipe 121 (held-out), seed 108, severity 0.5,
  mismatch 5 % → telemetry charts → diagnosis AMBIGUOUS (leak family; pipe 31 leading, 121 alternative) →
  recommendation J-32 (3.8σ) → simulated reading 105.89 m → resolved to pipe 121 (true location). Fresh tab: no console errors.
  Backend stopped → UI shows a visible "Cannot reach AquaScope backend" error with the start command.
* Bugs found and fixed during integration: stale backend returned an old schema → UI crash (backend restarted,
  UI made tolerant); a Python rewrite truncated `investigation.py` (cp1252 encoding) → file restored in full;
  map legend overlapped nodes; mono font lacked σ glyph.

## Remaining blockers
None for running the demo. See docs/known_limitations.md for scientific limitations.

## Exact run instructions
```powershell
cd C:\Users\rehan\aquascope\backend
.venv\Scripts\python -m uvicorn aquascope.api:app --port 8077
```
```powershell
cd C:\Users\rehan\aquascope\frontend
npm run dev
```
Open http://localhost:3000 (API docs at http://localhost:8077/docs).

## Remediation Intelligence Engine (added 2026-11-23/24)
Milestones M0–M6 complete (docs: `docs/remediation_m1.md` … `m4.md`, `docs/remediation_fixes.md`).
* New package `backend/aquascope/remediation/` (config, simulate, metrics, confirmation, actions, rank, explain, verify,
  verification, evaluate, select_policy, router). Existing files changed: `aquascope/api.py` (+2 lines: include_router);
  frontend `page.tsx` (Remediation tab), `lib/api.ts` (types/calls); new `components/RemediationView.tsx`.
* Tests: **180 backend tests pass** (49 original + 131 remediation). Regression gate (`scripts/regression_snapshot.py
  --check`): PASS — original tests, dataset MD5 and evaluation reproduce exactly.
* Frontend: `npx eslint src` clean, `npx next build` OK. Browser journey verified: scenario → Remediation → confirm by
  measurement (CONFIRMED_BY_INVESTIGATION) → plan (isolate pipe 121, 864 m³ predicted) → verify on true network (leak
  stopped; predicted/actual min pressure 71.4/72.6 m); no console errors.
* EPyT-Flow 0.17.4 issue worked around: second `PumpSpeedEvent` on a pump crashes (float pattern index).

## Usability pass and full-journey sweep (2026-11-24)
* New default **Start here** tab (`frontend/src/components/GuidedDemo.tsx`): plain-English 5-step walkthrough with 4 demo
  examples; existing views kept as "Expert views". Display-only wording; no engine changes.
* `backend/scripts/sweep_journey.py` runs simulate → diagnose → investigate/measure → confirm → plan → verify through the
  HTTP API for all 10 classes × 3 mismatch levels × 3 seeds (+ the 4 demo examples, each checked for its expected recommendation).
* Bug found by the sweep and fixed: `/api/remediation/{id}/plan` returned HTTP 500 for 9/90 journeys (confident-but-unverified
  leaks whose preview isolation drove pressures negative): `rank.py`'s `-inf` sort sentinel for "no trustworthy benefit" is not
  valid JSON. Fixed at the API boundary (`router._plain` reports non-finite floats as null); ranking code unchanged.
  Regression test `test_plan_with_unverified_leak_preview_is_json_safe`. 181 tests pass.
* Demo script: `DEMO.md`.
