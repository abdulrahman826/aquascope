# HackWithAditya 2026 — Level 1 form answers (copy-paste)

## Project title
AquaScope — evidence-driven leak and fault diagnosis for city water networks

## Problem statement
Water utilities lose a large share of treated water to leaks. When a pressure alarm goes off, operators usually cannot tell whether it is a real leak, a faulty sensor, a pump or valve problem, or just unusual demand, or where in the network it is. Crews dig in the wrong place, broken sensors trigger needless call-outs, and real leaks run longer. Existing alarm systems give a single guess with no sense of how sure they are and no advice on what to check next.

## Target users
Control-room operators and engineers at municipal water utilities and water boards (e.g. Jal Board / smart-city water departments) who monitor pressure and flow sensors, plus maintenance crews who need to know where to go before they dig.

## Abstract
AquaScope reads pressure and flow telemetry from a water network and works out what is wrong and where. It weighs competing explanations (leak, sensor fault, pump/valve problem, demand change), says how confident it is, and abstains instead of bluffing. When it is unsure, it recommends the one junction where a portable pressure gauge best separates the leading explanations, then updates the diagnosis with that reading. Only once a fault is confirmed does it suggest a corrective action (e.g. isolate a pipe, restore a pump), which it first tests in a hydraulic simulation against hard engineering constraints such as minimum customer pressure. It combines physics (EPANET hydraulic model residuals) with machine learning (Random Forest + calibrated hybrid model).

## Working prototype
Works today (full web app, FastAPI backend + Next.js dashboard):
- Simulates 10 situations on the EPANET Net1 benchmark network with realistic model errors: normal, sudden leak, slow-growing leak, 4 sensor faults, pump problem, valve problem, demand spike.
- Diagnosis with confidence and abstention: macro-F1 0.92 on 600 unseen test scenarios.
- "Where to measure next": the recommended extra reading settles the question 98.5% of the time vs 81.7% for a random spot.
- Corrective actions tested in simulation before being recommended: on 600 fresh scenarios, 0% harmful/ineffective actions and 0 constraint violations, vs 33.8% for acting on the top guess.
- Plain-English "Start here" guided demo (5 steps, 4 example problems) plus expert views (network map, telemetry, diagnosis, investigation, remediation, experiments).
- 181 automated tests; a 94-run end-to-end sweep with 0 errors.

Limitations: everything is simulated on one small benchmark network; not yet validated on real utility data. It only acts on confirmed faults (35% of faults end to end). It is advisory only: it cannot and does not control real valves or pumps.

## GitHub link
https://github.com/abdulrahman826/aquascope

## Tech stack
Python, FastAPI, EPyT-Flow / EPANET, scikit-learn, NumPy, pandas, Next.js 16, React, TypeScript, Tailwind CSS, Recharts

## Hardware
(leave blank)

## Dependencies
EPANET Net1 benchmark network (bundled, open). No external APIs or cloud services.
