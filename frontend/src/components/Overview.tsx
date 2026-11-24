"use client";
import type { AppState } from "@/app/page";
import NetworkMap, { type MapHighlights } from "./NetworkMap";
import { Button, Chip, Empty, Panel, STATUS, Stat, type Tone } from "./ui";
import { fx, hours } from "@/lib/api";

type Go = (t: "Overview" | "Network" | "Telemetry" | "Diagnosis" | "Investigation" | "Experiments") => void;

export default function Overview({ s, hl, go }: { s: AppState; hl: MapHighlights; go: Go }) {
  const d = s.diag, t = s.tel;
  const last = t ? t.telemetry.time.length - 1 : 0;
  const pumpQ = t ? t.telemetry.flow["9"][last] : null;
  const tankQ = t ? t.telemetry.flow["110"][last] : null;
  const st = d ? STATUS[d.status] : null;
  const lead = d?.hypotheses[0];
  const alerts: { tone: Tone; text: string }[] = [];
  if (d?.detection.alarm) alerts.push({ tone: "warn", text: `Anomaly detector alarm at t = ${hours(d.detection.first_alarm_time!).toFixed(1)} h (${d.detection.n_alarm_episodes} episode${d.detection.n_alarm_episodes === 1 ? "" : "s"})` });
  if (d && d.status !== "CONFIDENT") alerts.push({ tone: st!.tone, text: d.reason });
  if (d && d.status === "CONFIDENT" && lead && lead.event_class !== "normal") alerts.push({ tone: "bad", text: `${lead.label} (p = ${lead.probability.toFixed(2)})${lead.candidate_locations.length ? ` — candidates: ${lead.candidate_locations.join(", ")}` : ""}` });
  for (const [id, tone] of Object.entries(hl.sensorTone ?? {})) if (tone !== "ok") alerts.push({ tone: tone as Tone, text: `Pressure sensor ${id}: abnormal residual or health indicator` });

  return (
    <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Backend" value={s.health?.status ?? "—"} tone={s.health?.status === "ok" ? "ok" : "warn"} sub={s.health ? `model: ${s.health.selected_approach}` : "connecting"} />
          <Stat label="Diagnostic status" value={d ? d.status.split("_")[0] : "—"} tone={st?.tone ?? "dim"} sub={st?.label ?? "no scenario loaded"} />
          <Stat label="Pump P9 (last)" value={pumpQ == null ? "—" : `${fx(pumpQ, 1)} L/s`} tone={pumpQ && pumpQ > 1 ? "info" : "dim"} sub={pumpQ == null ? "" : pumpQ > 1 ? "running" : "off"} />
          <Stat label="Tank T2 (last)" value={tankQ == null ? "—" : `${fx(Math.abs(tankQ), 1)} L/s`} sub={tankQ == null ? "" : tankQ < 0 ? "filling" : "draining"} />
        </div>
        <Panel title="Network status — Net1" right={<Button variant="ghost" onClick={() => go("Network")}>Open explorer</Button>}>
          {s.net ? <NetworkMap net={s.net} h={hl} /> : <Empty>Loading topology…</Empty>}
        </Panel>
      </div>
      <div className="flex flex-col gap-4">
        <Panel title="Active alerts">
          {!d ? <Empty>No scenario loaded. <button className="text-accent underline" onClick={() => go("Experiments")}>Inject an event</button> to begin.</Empty>
            : alerts.length === 0 ? <p className="text-sm text-ok">No alerts. Residuals within expected range.</p>
            : <ul className="flex flex-col gap-2">{alerts.map((a, i) => <li key={i} className="flex gap-2 text-sm"><Chip tone={a.tone}>{a.tone === "bad" ? "alarm" : a.tone === "warn" ? "warn" : "info"}</Chip><span>{a.text}</span></li>)}</ul>}
        </Panel>
        <Panel title="Sensor health">
          {!d ? <Empty>Load a scenario to evaluate sensors.</Empty> : (
            <table className="w-full text-sm">
              <thead className="text-left text-[10px] uppercase tracking-wider text-dim"><tr><th className="py-1">Sensor</th><th>Day-3 shift</th><th>Roughness</th><th>Frozen</th><th>State</th></tr></thead>
              <tbody className="font-mono">
                {s.net?.sensors.pressure.map((id) => (
                  <tr key={id} className="border-t border-line">
                    <td className="py-1.5">P-{id}</td>
                    <td>{fx(d.features[`shift_${id}`], 2)} m</td>
                    <td>{fx(d.features[`noise_ratio_${id}`], 2)}</td>
                    <td>{(100 * d.features[`stuck_${id}`]).toFixed(0)}%</td>
                    <td><Chip tone={hl.sensorTone?.[id] ?? "dim"}>{hl.sensorTone?.[id] ?? "—"}</Chip></td>
                  </tr>))}
              </tbody>
            </table>)}
          {d && <p className="mt-2 text-[11px] text-dim">Shift is relative to the model + day-1 reference profile; σ ≈ {fx(Number(d.physics.indicators.pressure_sigma_m), 2)} m at this mismatch level.</p>}
        </Panel>
        <Panel title="Recent scenarios" right={<Button variant="ghost" onClick={() => go("Experiments")}>New</Button>}>
          {s.scenarios.length === 0 ? <Empty>None yet.</Empty> : (
            <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto">
              {s.scenarios.slice(0, 10).map((x) => (
                <li key={x.scenario_id}>
                  <button onClick={() => s.load(x.scenario_id)} className={`flex w-full justify-between rounded px-2 py-1 text-left text-xs hover:bg-line/50 ${x.scenario_id === s.sid ? "bg-accent/10 text-accent" : ""}`}>
                    <span className="font-mono">{x.scenario_id}</span><span className="text-dim">mismatch {x.spec.mismatch * 100}% · seed {x.spec.seed}</span>
                  </button>
                </li>))}
            </ul>)}
        </Panel>
      </div>
    </div>
  );
}
