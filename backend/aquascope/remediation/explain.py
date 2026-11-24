"""Plain-language explanation of a ranking result (M3). Pure formatting of measured/simulated values."""
from __future__ import annotations

from . import config as R


def explain(result: dict) -> dict:
    rec = result["recommendation"]
    conf = result["confirmation"]
    lines = [f"Diagnosis status: {conf['status']} — {conf['reason']}."]
    if result.get("hypothesis_fault"):
        h = result["hypothesis_fault"]
        lines.append(f"Simulated hypothesis on the nominal model: {h['event_class']} at {h['location']} "
                     f"from {h['start_time_h']:.1f} h, parameters {h['parameters']}.")
    lines += [f"Note: {n}." for n in result.get("notes", [])]
    if result.get("preview_only"):
        lines.append("PREVIEW ONLY: interventions are locked until the diagnosis is confirmed.")
    if rec["kind"] in ("investigate_first", "none"):
        lines.append(f"Recommendation: {rec['kind'].replace('_', ' ')} — {rec['reason']}.")
    else:
        best = next(r for r in result["ranked"] if r["id"] == rec["id"])
        e = best["evaluation"]
        lines.append(f"Recommendation (simulated): {rec['kind']} {rec['target']} — {rec['reason']}.")
        a, wo = e["after"], e["after_without_intervention"]
        if best["leak_volume_avoided_m3"] is not None and best["benefit_metric"] == "leak_volume_avoided_m3":
            lines.append(f"Predicted leak flow {wo['leak_flow_mean_L_s']:.2f} -> {a['leak_flow_mean_L_s']:.2f} L/s; "
                         f"{best['leak_volume_avoided_m3']:.0f} m3 avoided over {a['window_h'][0]:.0f}-{a['window_h'][1]:.0f} h.")
        else:
            lines.append(f"Predicted {best['benefit_metric'].replace('_', ' ')}: {best['benefit']:.2f} m "
                         f"(mean pressure deviation from the no-fault network changes by "
                         f"{-best['pressure_deviation_reduction_m']:+.2f} m; tank level and pump phase carry the "
                         "fault's history, so this need not shrink within 24 h).")
        lines.append(f"Predicted minimum pressure {a['min_pressure_m']:.1f} m (lowest at junction {a['min_pressure_junction']}); "
                     f"delivered {a['delivered_fraction']:.1%} of requested demand; tank level "
                     f"{a['tank_min_level_m']:.2f}-{a['tank_max_level_m']:.2f} m.")
        lines.append(f"Verdict: {best['verdict']}.")
        lines += [f"Assumption: {l}" for l in e["labels"][1:]]
        if e["intervention"]["physical_valve_availability"]:
            lines.append(f"Physical valve availability: {e['intervention']['physical_valve_availability']}.")
    others = [r for r in result["ranked"] if r.get("id") != rec.get("id")]
    why_not = [f"{r['id']}: " + ("; ".join(r["ineligible_reasons"]) if not r["eligible"] else
                                 f"eligible but lower predicted {r['benefit_metric']} ({r['benefit']:.2f})")
               for r in others if r["kind"] != "none"]
    table = [{"check": k["name"], "value": k["value"], "limit": k["limit"], "passed": k["passed"]}
             for k in (next((r for r in result["ranked"] if r["id"] == rec.get("id")), {})
                       .get("evaluation", {}).get("constraint_checks", []))]
    return {"summary": lines, "alternatives_not_chosen": why_not, "constraint_table": table,
            "disclaimer": f"{R.SIMULATION_LABEL}. {R.NOT_EXECUTABLE}."}
