# AquaScope — how to demo it (5–7 minutes)

## Before the audience arrives
1. Start the backend:
   ```powershell
   cd C:\Users\rehan\aquascope\backend; .venv\Scripts\python -m uvicorn aquascope.api:app --port 8077
   ```
2. Start the frontend (second terminal):
   ```powershell
   cd C:\Users\rehan\aquascope\frontend; npm run dev
   ```
3. Open http://localhost:3000. It opens on the **Start here** tab. Click **Burst pipe** once to warm everything up
   (the first simulation takes a few seconds), then reload the page.

## The pitch (30 s)
"Water utilities lose a large share of their water to leaks, and when an alarm goes off they often don't know whether it is
a leak, a broken sensor or just unusual demand. Digging in the wrong place is expensive. AquaScope works out **what** and
**where**, admits when it is unsure, asks for **one extra measurement** to settle it, and only then suggests an action —
which it tests in simulation before recommending it."

## Demo 1 — Burst pipe (the main story, ~3 min)
On **Start here**, click **Burst pipe**, then follow the numbered steps:

| Step | Click | What to say |
|---|---|---|
| 1 | **Burst pipe** | "We simulate a leak on a real benchmark network (EPANET Net1) with realistic model errors. AquaScope does not know where it is." |
| 2 | (appears) | "It's sure something is wrong, but it says honestly that there are two possible explanations — it doesn't bluff." (It shows a leak on pipe 31, 121 or 122; whether it's a burst or a slow leak doesn't matter yet — the question is *where*.) |
| 3 | **Find the best place to measure** → **Take the reading** | "Instead of guessing, it picks the one junction (32) where a portable gauge best separates pipe 31 from pipe 121. The reading settles it: pipe 121." |
| 4 | **Get the recommended action** | "Only now, with a confirmed diagnosis, does it allow pipe actions. It simulated every option, rejected any that would cut off customers, and recommends closing off pipe 121 — about 860 m³ of water saved over the next day." |
| 5 | **Test it** | "The simulation knows the truth. The leak stops and every customer keeps enough pressure." Point at the chart: red = do nothing, blue = with action. |

## Demo 2 — Broken sensor (~1 min)
Click **Broken sensor**. Step 2 says it is confident: faulty sensor 22. Skip step 3 (optional) and click
**Get the recommended action**: "No pipe or pump action needed — recalibrate sensor 22." Say: "The pressure looked wrong,
but AquaScope blames the sensor, not the pipes. That saves a crew from digging up a street." Step 5 confirms it was right.

## Demo 3 — Normal day (~30 s)
Click **Normal day**. "No false alarm and no action. A tool that cries wolf gets ignored."

## Optional — Pump losing power
Click **Pump losing power**, skip step 3, then steps 4–5: it confirms the pump problem itself (it takes a checking reading
automatically), recommends restoring the pump, and the test shows the lost pump pressure recovered.

## If someone asks "how good is it?" (show the Experiments tab)
Quote only these measured, simulated numbers:
* Diagnosis on 600 unseen scenarios: macro-F1 **0.92**; it abstains rather than guess when evidence is weak.
* The recommended extra reading settles the question **98.5 %** of the time (vs 81.7 % for a random spot).
* Corrective actions on 600 fresh scenarios: **0 %** harmful or ineffective actions and **0** constraint violations,
  versus **33.8 %** ineffective/harmful for "just act on the top guess". The cost: it acts only when confirmed (35 % of
  faults end to end; 100 % once the fault is correctly confirmed).

Be upfront: everything is simulated on one small benchmark network; it is not validated on real utility data and cannot
operate valves or pumps.

## If something goes wrong during the demo
* Red box "Cannot reach AquaScope backend": the backend isn't running — start it (step 1 above) and reload.
* A step seems slow: each step runs real hydraulic simulations; step 4 can take ~5–10 s.
* Unexpected result on your own scenario: that's real behaviour, not scripted — the four buttons are pre-checked examples
  (re-check them any time with `.venv\Scripts\python scripts\sweep_journey.py 0` in `backend`, backend running).
* Someone asks "why didn't it act?" on a scenario: that's by design — without a decisive confirming reading it refuses to
  touch pipes or pumps (0 harmful actions in testing). An operator can confirm in the **Remediation** tab.
