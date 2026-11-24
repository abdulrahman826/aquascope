"use client";
import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AppState } from "@/app/page";
import { api, ApiError, type Confirmation, type RemediationPlan, type Spec, type Verification } from "@/lib/api";
import NetworkMap, { type MapHighlights } from "./NetworkMap";
import { Button, ErrorBox, Panel, ProbBar, Spinner } from "./ui";

// Plain-English names for the 10 scenario classes.
export const PLAIN: Record<string, string> = {
  normal: "Nothing wrong",
  abrupt_leak: "Burst pipe (sudden leak)",
  incipient_leak: "Slowly growing leak",
  sensor_bias: "Faulty sensor (reads too high/low)",
  sensor_drift: "Faulty sensor (slowly drifting)",
  sensor_noise: "Faulty sensor (noisy)",
  sensor_stuck: "Faulty sensor (frozen)",
  pump_disturbance: "Pump losing power",
  valve_disturbance: "Partly closed valve",
  demand_anomaly: "Unusual water use",
};
const where = (cls: string, locs: string[]) => {
  if (!locs.length) return "";
  const l = locs.slice(0, 3).join(" or ");
  if (cls.startsWith("sensor")) return ` at sensor ${locs[0]}`;
  if (cls === "demand_anomaly") return ` at junction ${l}`;
  if (cls === "pump_disturbance") return "";
  return ` on pipe ${l}`;
};

// Demo examples. Seeds/locations picked from scripts/sweep_journey.py runs so each shows its intended story.
// They are illustrative examples, not a representative sample (see the Experiments tab for real metrics).
const PRESETS: { title: string; story: string; spec: Spec }[] = [
  { title: "Burst pipe", story: "A pipe suddenly starts leaking. Watch AquaScope find it, check it, and stop it.",
    spec: { event_class: "abrupt_leak", seed: 108, severity: 0.5, mismatch: 0.05, pressure_noise: 0.05, location: "121" } },
  { title: "Pump losing power", story: "The main pump slows down. AquaScope spots it and suggests restoring it.",
    spec: { event_class: "pump_disturbance", seed: 5000, severity: 0.6, mismatch: 0.0, pressure_noise: 0.05, location: null } },
  { title: "Broken sensor", story: "A sensor starts lying. AquaScope blames the sensor — not the pipes — so nobody digs up a street.",
    spec: { event_class: "sensor_bias", seed: 5002, severity: 0.6, mismatch: 0.05, pressure_noise: 0.05, location: "22" } },
  { title: "Normal day", story: "Nothing is wrong. AquaScope should stay calm and recommend no action.",
    spec: { event_class: "normal", seed: 5000, severity: 0.6, mismatch: 0.05, pressure_noise: 0.05, location: null } },
];

const ACTION: Record<string, (t: string | null) => string> = {
  isolate_pipe: (t) => `Close off pipe ${t} (shut the valves on both sides) to stop the leak`,
  restore_valve: (t) => `Fully re-open the partly closed valve on pipe ${t}`,
  restore_pump: () => "Restore the pump to full speed (repair / reset it)",
  investigate_first: () => "Don't touch the pipes yet — the problem is not confirmed",
  none: () => "No pipe or pump action needed",
};
// Backend wording -> plain wording (display only).
const plainDesc = (t: string) => t.replaceAll("Abrupt pipe leak / Incipient (growing) leak", "Leak").replace(/\s*\([^)]*M\d[^)]*\)/g, "");
const confirmedText = (cls: string | null, loc: string | null) =>
  cls?.endsWith("_leak") ? `Leak on pipe ${loc}` : cls === "pump_disturbance" ? "Pump losing power"
    : `${PLAIN[cls ?? ""] ?? cls}${loc ? ` at ${cls?.startsWith("sensor") ? "sensor" : cls === "demand_anomaly" ? "junction" : "pipe"} ${loc}` : ""}`;
function whyText(plan: RemediationPlan): string {
  const rec = plan.recommendation;
  const cls = plan.confirmation.event_class ?? "";
  if (rec.kind === "none" && cls.startsWith("sensor")) return "The pipes and pump are fine — the fault is in the sensor itself, so the fix is to the sensor, not the network.";
  if (rec.kind === "none" && cls === "demand_anomaly") return "Unusual water use is not a network fault; check with the customer rather than closing pipes.";
  if (rec.kind === "none" && cls === "normal") return "Nothing needs fixing.";
  const row = plan.ranked.find((x) => x.id === rec.id);
  if (!row || row.benefit == null) return plainDesc(rec.reason);
  const b = row.benefit.toFixed(row.benefit_metric?.endsWith("m3") ? 0 : 1);
  const gain = row.benefit_metric === "leak_volume_avoided_m3" ? `stops about ${b} m³ of water being lost over the next day`
    : row.benefit_metric === "excess_headloss_removed_m" ? `removes the ${b} m pressure loss caused by the valve`
    : `recovers the ${b} m of pump pressure that was lost`;
  return `Of all the options AquaScope simulated, this one ${gain}, and every customer still keeps enough water pressure.`;
}
const msg = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function Step({ n, title, done, children }: { n: number; title: string; done?: boolean; children: React.ReactNode }) {
  return (
    <Panel>
      <div className="flex gap-3">
        <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${done ? "bg-ok text-ink" : "bg-accent/20 text-accent"}`}>{done ? "✓" : n}</div>
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <h2 className="text-base font-semibold">{title}</h2>
          {children}
        </div>
      </div>
    </Panel>
  );
}

export default function GuidedDemo({ s, hl }: { s: AppState; hl: MapHighlights }) {
  // remediation results belong to one scenario; anything from an older scenario is ignored
  const [rem, setRem] = useState<{ sid: string; conf?: Confirmation; plan?: RemediationPlan; ver?: Verification }>({ sid: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const r = rem.sid === s.sid ? rem : { sid: s.sid ?? "" };
  const d = s.diag, inv = s.inv, m = s.meas;
  const working = !!busy || !!s.busy;

  const go = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setErr(null);
    try { await fn(); } catch (e) { setErr(msg(e)); } finally { setBusy(null); }
  };
  const getAction = () => s.sid && go("Working out what to do (simulating every option)…", async () => {
    const sid = s.sid!;
    const conf = await api.remConfirm(sid, { mode: "auto" });
    const plan = await api.remPlan(sid);
    setRem({ sid, conf, plan });
  });
  const test = () => s.sid && go("Trying the action on the simulated real network…", async () => {
    const sid = s.sid!;
    const ver = await api.remVerify(sid);
    setRem({ ...r, sid, ver });
    s.reveal();
  });

  const top = d?.hypotheses.slice(0, 3) ?? [];
  const lead = top[0];
  const verdict = !d ? null
    : d.status === "CONFIDENT"
      ? lead.event_class === "normal" ? { tone: "text-ok", text: "Everything looks normal." }
        : { tone: "text-bad", text: `AquaScope is confident: ${PLAIN[lead.event_class]}${where(lead.event_class, lead.candidate_locations)}.` }
      : d.status === "AMBIGUOUS_DIAGNOSIS"
        ? { tone: "text-warn", text: "Something is wrong, but there are two possible explanations. One extra reading will settle it." }
        : { tone: "text-warn", text: "Not enough evidence to say what is wrong — AquaScope refuses to guess." };

  const rec = r.plan?.recommendation;
  const tasks = r.plan?.candidates.filter((c) => c.category === "NON_HYDRAULIC") ?? [];
  const ver = r.ver;
  const chart = ver?.series ? ver.series.with.time_h.map((t, i) => ({
    h: t, without: ver.series!.without.leak_flow_L_s[i], with: ver.series!.with.leak_flow_L_s[i],
    pwithout: ver.series!.without.min_pressure_m[i], pwith: ver.series!.with.min_pressure_m[i],
  })) : [];
  const leakChart = (ver?.series?.without.leak_flow_L_s.some((x) => x > 0.01)) ?? false;

  return (
    <div className="grid gap-4 lg:grid-cols-[1.25fr_1fr]">
      <div className="flex flex-col gap-4">
        <div className="rounded-lg border border-line bg-panel px-4 py-3 text-sm">
          <p><b>What is this?</b> A small town water network is watched by 4 pressure sensors and 2 flow meters. When something goes
            wrong, AquaScope works out <b>what</b> and <b>where</b>, asks for <b>one extra reading</b> if unsure, and suggests <b>what to do</b>.</p>
          <p className="mt-1 text-xs text-dim">Everything here is a computer simulation (EPANET), not a real network. Follow steps 1 → 5.</p>
        </div>
        <ErrorBox error={err ?? s.error} />
        {(busy || s.busy) && <Spinner label={busy ?? s.busy!} />}

        <Step n={1} title="Pick a problem to simulate" done={!!d}>
          <div className="grid gap-2 sm:grid-cols-2">
            {PRESETS.map((p) => (
              <button key={p.title} disabled={working} onClick={() => s.run(p.spec)}
                className="rounded-md border border-line p-3 text-left transition-colors hover:border-accent/60 disabled:opacity-50">
                <div className="font-semibold text-accent">{p.title}</div>
                <div className="mt-1 text-xs text-dim">{p.story}</div>
              </button>))}
          </div>
          <p className="text-xs text-dim">Want something else? The <b>Experiments</b> tab lets you choose any problem, location and setting.</p>
        </Step>

        {d && (
          <Step n={2} title="What AquaScope thinks is happening" done>
            <p className={`text-base font-medium ${verdict!.tone}`}>{verdict!.text}</p>
            <div className="flex flex-col gap-2">
              {top.map((h) => (
                <div key={h.event_class}>
                  <div className="flex justify-between text-sm"><span>{PLAIN[h.event_class] ?? h.label}{where(h.event_class, h.candidate_locations)}</span><span className="font-mono">{Math.round(100 * h.probability)}%</span></div>
                  <ProbBar value={h.probability} tone={h === lead ? "info" : "dim"} />
                </div>))}
            </div>
            <p className="text-xs text-dim">Percentages are how likely AquaScope thinks each explanation is.</p>
          </Step>)}

        {d && (
          <Step n={3} title={d.status === "CONFIDENT" ? "Double-check with one extra reading (optional)" : "Double-check with one extra reading"} done={!!m}>
            {!inv ? (
              <>
                <p className="text-sm text-dim">Instead of guessing, AquaScope picks the single spot where a portable pressure gauge would best tell the explanations apart.</p>
                <div><Button onClick={s.investigate} disabled={working}>Find the best place to measure</Button></div>
              </>
            ) : !inv.recommended_node || inv.candidates.length === 0 ? (
              <p className="text-sm">{inv.candidates.length === 0 ? "No competing explanations to check — an extra reading would not change anything." : "No single spot would reliably tell the explanations apart, so AquaScope does not ask for one."} <span className="text-dim">{inv.explanation}</span></p>
            ) : (
              <>
                <p className="text-sm">Send someone to <b className="text-accent">junction {inv.recommended_node}</b> (blue ring on the map) with a pressure gauge.
                  <span className="text-dim"> It is comparing: {inv.hypotheses.map((h) => plainDesc(h.description)).join("  vs  ")}.</span></p>
                {!m ? <div><Button onClick={() => s.measure(inv.recommended_node!)} disabled={working}>Take the reading (simulated)</Button></div> : (
                  <div className="rounded-md border border-line p-3 text-sm">
                    Reading: <b className="font-mono">{m.reading_m.toFixed(1)} m</b> of pressure.{" "}
                    {m.warning ? <span className="text-warn">It doesn&apos;t match either explanation well — AquaScope flags this instead of forcing an answer.</span>
                      : <span className="text-ok">This points to: <b>{plainDesc(m.resolved_to)}</b>.</span>}
                  </div>)}
              </>)}
          </Step>)}

        {d && (
          <Step n={4} title="What should be done?" done={!!rec}>
            {!rec ? (
              <>
                <p className="text-sm text-dim">AquaScope only allows pipe/pump actions once the problem is <b>confirmed</b> by a measurement. If needed it takes that confirming reading itself. It then simulates every possible action and rejects any that would cut off customers or drop pressure too low.</p>
                <div><Button onClick={getAction} disabled={working}>Get the recommended action</Button></div>
              </>
            ) : (
              <>
                <div className={`rounded-md border px-3 py-2 ${["isolate_pipe", "restore_valve", "restore_pump"].includes(rec.kind) ? "border-accent/50 bg-accent/5" : "border-line"}`}>
                  <div className="text-base font-medium text-accent">{(ACTION[rec.kind] ?? (() => rec.kind))(rec.target)}</div>
                  <p className="mt-1 text-sm text-dim">{r.conf?.hydraulic_interventions_unlocked
                    ? `Confirmed: ${confirmedText(r.conf.event_class, r.conf.location)}. ${whyText(r.plan!)}`
                    : rec.kind === "none" ? whyText(r.plan!)
                    : rec.kind === "investigate_first" ? "AquaScope could not confirm the problem with a decisive reading, so it refuses to touch pipes or pumps — a wrong action could cut off customers. An operator who has checked on site can confirm it in the Remediation tab."
                    : plainDesc(rec.reason)}</p>
                </div>
                {tasks.length > 0 && (
                  <div className="text-sm"><div className="text-xs text-dim">Also suggested (field tasks):</div>
                    <ul className="list-disc pl-5">{tasks.map((t) => <li key={t.id}>{plainDesc(t.description)}</li>)}</ul></div>)}
                <p className="text-[11px] text-warn">Simulation result only — passed the configured simulation checks, NOT validated for real-world execution. AquaScope cannot operate valves or pumps.</p>
              </>)}
          </Step>)}

        {rec && (
          <Step n={5} title="Did it work? (check against the hidden truth)" done={!!ver}>
            {!ver ? (
              <>
                <p className="text-sm text-dim">The simulation knows what really happened. Apply the recommendation to that hidden &quot;real&quot; network and compare.</p>
                <div><Button onClick={test} disabled={working}>Test it</Button></div>
              </>
            ) : (
              <>
                <div className="text-sm">
                  <p>What really happened: <b>{PLAIN[ver.true_fault.event_class] ?? ver.true_fault.event_class}{ver.true_fault.location && ver.true_fault.event_class !== "pump_disturbance" ? ` (${ver.true_fault.event_class.startsWith("sensor") ? "sensor" : ver.true_fault.event_class === "demand_anomaly" ? "junction" : "pipe"} ${ver.true_fault.location})` : ""}</b>.</p>
                  {ver.diagnosis_matched_truth == null && r.conf?.event_class && r.conf.event_class !== "normal" && (() => {
                    const ok = r.conf!.event_class === ver.true_fault.event_class && (!r.conf!.location || r.conf!.location === ver.true_fault.location);
                    return <p className={ok ? "text-ok" : "text-bad"}>{ok ? "✓ AquaScope's diagnosis was correct." : "✗ AquaScope's diagnosis did not match."}</p>;
                  })()}
                  {ver.diagnosis_matched_truth != null && <p className={ver.diagnosis_matched_truth ? "text-ok" : "text-bad"}>{ver.diagnosis_matched_truth ? "✓ AquaScope's confirmed diagnosis was correct." : "✗ AquaScope's confirmed diagnosis was wrong."}</p>}
                  {ver.leak_stopped != null && <p className={ver.leak_stopped ? "text-ok" : "text-bad"}>{ver.leak_stopped ? "✓ The leak was stopped." : "✗ The leak was not stopped."}</p>}
                  {ver.mechanism_benefit && !ver.leak_stopped && <p className="text-ok">✓ The fault&apos;s effect was removed (simulated).</p>}
                  {ver.applicable && <p className={ver.actual_passed_constraints ? "text-ok" : "text-bad"}>{ver.actual_passed_constraints ? "✓ All customers kept enough water pressure." : "✗ Some customers lost pressure."}</p>}
                  {!ver.applicable && <p className="text-dim">{ver.reason}</p>}
                </div>
                {chart.length > 0 && rec.kind !== "none" && (
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={chart} margin={{ left: 0, right: 8, top: 8 }}>
                      <CartesianGrid stroke="#1c2a45" strokeDasharray="2 4" />
                      <XAxis dataKey="h" type="number" domain={["dataMin", "dataMax"]} unit=" h" />
                      <YAxis width={56} unit={leakChart ? " L/s" : " m"} />
                      <Tooltip contentStyle={{ background: "#0d1526", border: "1px solid #1c2a45", fontSize: 12 }} labelFormatter={(v) => `hour ${Number(v).toFixed(1)}`} />
                      <Legend />
                      <ReferenceLine x={ver.series!.intervention_time_h} stroke="#22d3ee" strokeDasharray="4 4" label={{ value: "action", fill: "#22d3ee", fontSize: 10, position: "insideTopLeft" }} />
                      <Line dataKey={leakChart ? "without" : "pwithout"} name={leakChart ? "water lost — do nothing" : "lowest pressure — do nothing"} stroke="#f87171" dot={false} isAnimationActive={false} />
                      <Line dataKey={leakChart ? "with" : "pwith"} name={leakChart ? "water lost — with action" : "lowest pressure — with action"} stroke="#22d3ee" dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>)}
                <p className="text-xs text-dim">Try another problem in step 1. Expert detail for every step is in the other tabs.</p>
              </>)}
          </Step>)}
      </div>

      <div className="flex flex-col gap-4 lg:sticky lg:top-4 lg:self-start">
        <Panel title="The water network">
          {s.net && <NetworkMap net={s.net} h={hl} />}
          <p className="mt-2 text-xs text-dim">Water comes from the reservoir, is pushed by the pump into pipes and a storage tank, and reaches homes at the junctions (circles). Highlighted pipes/junctions are AquaScope&apos;s suspects; the blue ring is where to measure.</p>
        </Panel>
      </div>
    </div>
  );
}
