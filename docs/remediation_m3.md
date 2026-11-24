# Remediation Intelligence Engine — M3 report (ranking, explanation, verification)

Status: **M3 complete. M4 (dataset + evaluation against baselines), M5 (API/UI), M6 (docs) NOT started.**
Numbers below are from individual simulated cases (seed 31, severity 0.6 unless stated). They illustrate behaviour;
they are **not** evaluation metrics — M4 will measure performance over held-out scenarios.

## Files
| File | Change |
|---|---|
| `aquascope/remediation/rank.py` | new — hypothesis fault, simulation-based ranking, eligibility, recommendation |
| `aquascope/remediation/explain.py` | new — plain-language summary, alternatives-not-chosen, constraint table |
| `aquascope/remediation/verify.py` | new — re-run chosen action on the hidden true network; predicted vs actual |
| `aquascope/remediation/config.py` | extended — prediction model, benefit thresholds |
| `tests/test_remediation_rank.py` | new — 22 tests |

Existing engine untouched: 49/49 original tests, regression gate PASS. Full suite 155/155.

## How ranking works
1. **Hypothesis fault** on the nominal model (mismatch 0): class + confirmed location; magnitude from the investigation
   fit, else the physics signature fit at that location, else an explicitly **ASSUMED** reference magnitude; start time
   from the detector alarm (clipped to 30–44 h) or an assumed 36 h. Leaks are simulated as abrupt at the fitted size.
2. Every simulation-supported candidate (eligible or not, plus no-action) is simulated with M1 and evaluated.
3. **Eligibility is hard:** unlocked (M2) ∧ not screened out (M2) ∧ passed every configured simulation constraint (M1).
   A large benefit never compensates for a failed constraint (tested by raising min pressure to 80 m: the leak
   isolation with 1,000+ m³ benefit becomes ineligible and "no intervention" is recommended).
4. **Benefit = removal of the fault's direct physical mechanism** vs no action:
   leak volume avoided (m³) · excess head loss removed on the throttled pipe (m, vs its Hazen-Williams law fitted on the
   no-fault run) · pump head deficit removed (m, vs the healthy pump curve fitted on the no-fault run).
   *Changed during M3:* the first version used "reduction of mean pressure deviation from the no-fault network"; it made
   pump restoration look harmful (−0.1 to −2.1 m) because tank level and pump phase carry the fault's history (M1
   finding). That metric is still reported, not ranked on.
5. Lexicographic order among eligible: benefit ↓, demand deficit ↑, minimum-pressure margin ↓. Recommend the best only
   if benefit > configured minimum (1 m³ leak / 0.5 m head loss / 0.5 m pump head); otherwise "no intervention".
6. `CONFIDENT_BUT_UNVERIFIED` → simulations shown as **preview only**, recommendation "investigate first";
   `INSUFFICIENT_EVIDENCE` → no simulation; sensor / demand / normal → "no hydraulic intervention".

## Verification on the hidden true network (operator-confirmed at the true location)
| Mismatch | Fault | Recommendation | Predicted vs actual primary benefit | Min pressure pred / actual (m) | Constraints pred / actual |
|---|---|---|---|---|---|
| 0 % | leak @121 | isolate 121 | 1174 vs 1012 m³ (+16 %) | 70.46 / 70.51 | pass / pass |
| 5 % | leak @121 | isolate 121 | 1116 vs 1014 m³ (+10 %) | 70.48 / 69.95 | pass / pass |
| 15 % | leak @121 | isolate 121 | 1066 vs 1016 m³ (+5 %) | 70.53 / 68.22 | pass / pass |
| 0 % | valve @11 | restore valve | 10.57 vs 11.21 m | 103.44 / 103.68 | pass / pass |
| 5 % | valve @11 | restore valve | 10.97 vs 11.45 m | 103.54 / 103.49 | pass / pass |
| 15 % | valve @11 | restore valve | 11.67 vs 11.70 m | 103.73 / 104.13 | pass / pass |
| 0 % | pump 9 | restore pump | **2.87 vs 47.79 m** | 113.64 / 113.62 | pass / pass |
| 5 % | pump 9 | restore pump | **2.87 vs 47.79 m** | 113.64 / 113.72 | pass / pass |
| 15 % | pump 9 | restore pump | **36.67 vs 47.79 m** | 114.05 / 114.51 | pass / pass |

After the actions: leak flow 0.00 L/s; remaining excess head loss 0.07–0.14 m; remaining pump head deficit 0.00 m.
No constraint-prediction misses in these cases.

Demo case (held-out leak @121, seed 108, 5 %): investigation-confirmed → isolate 121 (864 m³ predicted); alternatives
122 and 31 simulated but ineligible (unconfirmed locations); verification: leak stopped.
Wrong confirmation (operator says 31, truth 121): recommendation follows the confirmation (isolate 31); verification
shows the leak continuing (10.2 L/s) and `diagnosis_matched_truth = False`.

## Findings / risks for M4
* **Pump magnitude is poorly estimated** by the existing physics fit (speed 0.99 / 0.86 estimated vs 0.818 true), so the
  predicted pump benefit is far too small. The recommendation was still correct here, but a smaller estimate could fall
  under the 0.5 m minimum and wrongly yield "no intervention". M4 must quantify how often this happens.
* Leak benefit is over-predicted by 5–16 % (fitted diameter slightly too large); valve prediction within 0.6 m.
* Predicted minimum pressure deviates from actual by up to 2.3 m at 15 % mismatch; with Net1's ~70 m minimum after
  isolation this is far from the 20 m constraint, so not decisive here — M4 should track constraint-prediction misses.
* Ranking assumes the confirmed diagnosis is right; robustness to wrong confirmations is visible only via verification.
* Benefit thresholds and the 0.95 confirmation posterior are configured policy, not tuned.
