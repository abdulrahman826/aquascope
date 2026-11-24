"""Run the full user journey (simulate -> diagnose -> investigate+measure -> confirm -> plan -> verify)
through the live HTTP API for every scenario class x mismatch level x several seeds.

Purpose: smoke-test that no step errors for any input, and record what each step returns so the guided
demo can use examples whose behaviour is known. Not an accuracy evaluation (see aquascope.evaluation and
aquascope.remediation.evaluate for those).

Usage (backend running on :8077):  .venv\\Scripts\\python scripts\\sweep_journey.py [seeds_per_cell]
"""
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8077"
OUT = Path(__file__).resolve().parents[1] / "data" / "sweep_journey.csv"


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def journey(spec):
    row = {**spec, "errors": ""}
    errs = []

    def ok(step, code, expect=(200,)):
        if code not in expect:
            errs.append(f"{step}:{code}")
        return code in expect

    c, s = call("/api/scenarios/run", spec)
    if not ok("run", c):
        row["errors"] = ";".join(errs) + f" {s}"
        return row
    sid = s["scenario_id"]
    row["sid"] = sid
    for step, path in [("telemetry", f"/api/telemetry/{sid}"), ("truth", f"/api/scenarios/{sid}/ground_truth")]:
        c, r = call(path)
        ok(step, c)
        if step == "truth":
            row["true_location"] = r.get("location")
    c, d = call(f"/api/diagnosis/{sid}", {})
    if ok("diagnosis", c):
        row["status"], row["top"] = d["status"], d["hypotheses"][0]["event_class"]
        row["top_p"] = round(d["hypotheses"][0]["probability"], 3)
        row["top_locs"] = " ".join(d["hypotheses"][0]["candidate_locations"][:3])
    c, inv = call(f"/api/investigation/{sid}", {})
    if ok("investigate", c):
        row["rec_node"] = inv["recommended_node"]
        if inv["candidates"]:
            node = inv["recommended_node"] or inv["candidates"][0]["node"]
            c, m = call(f"/api/investigation/{sid}/measure", {"node": node})
            if ok("measure", c):
                row["measure_resolved"] = m["resolved_to"]
                row["measure_warning"] = bool(m["warning"])
        else:  # measuring without candidates must be a clean 409, not a crash
            c, _ = call(f"/api/investigation/{sid}/measure", {"node": "12"})
            ok("measure_nocand", c, (409,))
    c, cf = call(f"/api/remediation/{sid}/confirm", {"mode": "auto"})
    if ok("confirm", c):
        row["confirm_status"], row["confirm_class"], row["confirm_loc"] = cf["status"], cf["event_class"], cf["location"]
    c, pl = call(f"/api/remediation/{sid}/plan", {})
    if ok("plan", c):
        rec = pl["recommendation"]
        row["rec_kind"], row["rec_target"] = rec["kind"], rec["target"]
    c, v = call(f"/api/remediation/{sid}/verify", {})
    if ok("verify", c):
        row["verify_applicable"] = v.get("applicable")
        row["diag_matched"] = v.get("diagnosis_matched_truth")
        row["leak_stopped"] = v.get("leak_stopped")
        row["constraint_miss"] = v.get("constraint_prediction_miss")
    row["errors"] = ";".join(errs)
    return row


# The four "Start here" demo examples (frontend/src/components/GuidedDemo.tsx) and the recommendation each must give.
DEMO_PRESETS = [
    ({"event_class": "abrupt_leak", "seed": 108, "severity": 0.5, "mismatch": 0.05, "pressure_noise": 0.05, "location": "121"}, "isolate_pipe"),
    ({"event_class": "pump_disturbance", "seed": 5000, "severity": 0.6, "mismatch": 0.0, "pressure_noise": 0.05, "location": None}, "restore_pump"),
    ({"event_class": "sensor_bias", "seed": 5002, "severity": 0.6, "mismatch": 0.05, "pressure_noise": 0.05, "location": "22"}, "none"),
    ({"event_class": "normal", "seed": 5000, "severity": 0.6, "mismatch": 0.05, "pressure_noise": 0.05, "location": None}, "none"),
]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    opts = call("/api/options")[1]
    rows, t0 = [], time.time()
    for spec, want in DEMO_PRESETS:
        r = journey(spec)
        if r.get("rec_kind") != want:
            r["errors"] = (r["errors"] + f";demo_preset_expected_{want}_got_{r.get('rec_kind')}").lstrip(";")
        rows.append(r)
        print(f"DEMO {spec['event_class']:16s}: {r.get('status')} -> {r.get('rec_kind')} {r.get('rec_target')} "
              f"matched={r.get('diag_matched')} leak_stopped={r.get('leak_stopped')} err={r['errors'] or '-'}", flush=True)
    if n == 0:
        sys.exit(1 if any(r["errors"] for r in rows) else 0)
    for cls in opts["classes"]:
        locs = cls["locations"] or [None]
        for mm in opts["mismatch_levels"]:
            for k in range(n):
                spec = {"event_class": cls["id"], "seed": 5000 + k, "severity": 0.6, "mismatch": mm,
                        "pressure_noise": 0.05, "location": locs[k % len(locs)]}
                r = journey(spec)
                rows.append(r)
                print(f"{cls['id']:18s} mm={mm:.2f} seed={spec['seed']} loc={spec['location']}: "
                      f"{r.get('status')} top={r.get('top')} confirm={r.get('confirm_status')} "
                      f"rec={r.get('rec_kind')} {r.get('rec_target')} err={r['errors'] or '-'}", flush=True)
    keys = sorted({k for r in rows for k in r})
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if r["errors"]]
    harmful = [r for r in rows if r.get("rec_kind") in ("isolate_pipe", "restore_valve", "restore_pump")
               and r.get("diag_matched") is False]
    print(f"\n{len(rows)} journeys in {time.time() - t0:.0f}s; with errors: {len(bad)}; "
          f"hydraulic action on wrong diagnosis: {len(harmful)}; written {OUT}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
