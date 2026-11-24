"use client";
import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AppState } from "@/app/page";
import { api, ApiError, fx, type Confirmation, type RemediationPlan, type Verification } from "@/lib/api";
import { Button, Chip, Empty, ErrorBox, Panel, Spinner, type Tone } from "./ui";

const STATUS_TONE: Record<string, Tone> = {
  CONFIRMED_BY_INVESTIGATION: "ok", OPERATOR_CONFIRMED: "ok", CONFIDENT_BUT_UNVERIFIED: "warn", INSUFFICIENT_EVIDENCE: "bad",
};
const input = "rounded-md border border-line bg-ink px-2 py-1 text-sm";
const msg = (e: unknown) => (e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
const tip = { contentStyle: { background: "#0d1526", border: "1px solid #1c2a45", fontSize: 12 }, labelFormatter: (v: unknown) => `t = ${Number(v).toFixed(1)} h` };

export default function RemediationView({ s }: { s: AppState }) {
  const [conf, setConf] = useState<Confirmation | null>(null);
  const [plan, setPlan] = useState<RemediationPlan | null>(null);
  const [ver, setVer] = useState<Verification | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [op, setOp] = useState({ event_class: "abrupt_leak", location: "", operator_id: "", note: "" });
  const cls = s.opts?.classes.find((c) => c.id === op.event_class);

  if (!s.sid) return <Empty>Load or generate a scenario first (Experiments).</Empty>;
  const sid = s.sid;
  const go = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setErr(null);
    try { await fn(); } catch (e) { setErr(msg(e)); } finally { setBusy(null); }
  };
  const confirm = (body: Record<string, string | null>) => go("Confirming diagnosis…", async () => {
    setPlan(null); setVer(null); setConf(await api.remConfirm(sid, body));
  });

  const chart = ver?.series ? ver.series.with.time_h.map((t, i) => ({
    h: t, with_p: ver.series!.with.min_pressure_m[i], without_p: ver.series!.without.min_pressure_m[i],
    with_leak: ver.series!.with.leak_flow_L_s[i], without_leak: ver.series!.without.leak_flow_L_s[i],
  })) : [];
  const rec = plan?.recommendation;

  return (
    <div className="flex flex-col gap-4">
      <div role="note" className="rounded-lg border border-warn/40 bg-warn/5 px-4 py-2 text-sm text-warn">
        SIMULATED CORRECTIVE-ACTION ANALYSIS ON EPANET Net1 — NOT AN OPERATIONAL INSTRUCTION. AquaScope has no valve or pump
        control interface. Physical valve availability in Net1 is UNKNOWN; isolations are idealised modelling assumptions.
      </div>
      <ErrorBox error={err} />
      {busy && <Spinner label={busy} />}

      <Panel title="1 · Confirm the diagnosis">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="flex flex-col gap-2 text-sm">
            <p className="text-dim">Automatic: active investigation (two competing explanations) or, for a confident diagnosis, a
              verification measurement against the strongest alternative. Confirmation requires a decisive reading that also wins
              against the six leading explanations.</p>
            <div><Button onClick={() => confirm({ mode: "auto" })} disabled={!!busy}>Confirm by measurement</Button></div>
          </div>
          <form className="flex flex-wrap items-end gap-2 text-sm" onSubmit={(e) => { e.preventDefault(); confirm({ mode: "operator", ...op, location: op.location || null }); }}>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Operator-confirmed fault</span>
              <select className={input} value={op.event_class} onChange={(e) => setOp({ ...op, event_class: e.target.value, location: "" })}>
                {s.opts?.classes.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
              </select></label>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Location</span>
              <select className={input} value={op.location} disabled={!cls?.locations.length} onChange={(e) => setOp({ ...op, location: e.target.value })}>
                <option value="">{cls?.locations.length ? "select" : "n/a"}</option>
                {cls?.locations.map((l) => <option key={l} value={l}>{l}</option>)}
              </select></label>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Operator ID</span>
              <input className={input} value={op.operator_id} onChange={(e) => setOp({ ...op, operator_id: e.target.value })} placeholder="required" /></label>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Note</span>
              <input className={input} value={op.note} onChange={(e) => setOp({ ...op, note: e.target.value })} /></label>
            <Button variant="ghost" type="submit" disabled={!!busy || !op.operator_id.trim() || (!!cls?.locations.length && !op.location)}>Record operator confirmation</Button>
          </form>
        </div>
        {conf && (
          <div className="mt-3 rounded-md border border-line p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2"><Chip tone={STATUS_TONE[conf.status]}>{conf.status}</Chip>
              {conf.event_class && <span className="font-mono">{conf.event_class}{conf.location ? ` @ ${conf.location}` : ""}</span>}
              <Chip tone={conf.hydraulic_interventions_unlocked ? "ok" : "dim"}>hydraulic actions {conf.hydraulic_interventions_unlocked ? "unlocked" : "locked"}</Chip>
              <span className="text-xs text-dim">route: {conf.route}</span></div>
            <p className="mt-1 text-dim">{conf.reason}</p>
            {conf.evidence.length > 0 && <ul className="mt-1 list-disc pl-4 text-xs text-dim">{conf.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>}
            <div className="mt-2"><Button onClick={() => go("Simulating candidate actions…", async () => { setVer(null); setPlan(await api.remPlan(sid)); })} disabled={!!busy}>2 · Generate &amp; rank actions</Button></div>
          </div>)}
      </Panel>

      {plan && rec && (
        <Panel title="2 · Ranked corrective actions (simulated on the nominal model)">
          <div className={`rounded-md border px-3 py-2 ${rec.kind === "investigate_first" || rec.kind === "none" ? "border-line" : "border-accent/50 bg-accent/5"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[10px] uppercase tracking-wider text-dim">Recommendation</span>
              <span className="font-mono text-accent">{rec.kind.replaceAll("_", " ")}{rec.target ? ` ${rec.target}` : ""}</span>
              {plan.preview_only && <Chip tone="warn">preview only</Chip>}
              {rec.verdict && <Chip tone="ok">{rec.verdict}</Chip>}
            </div>
            <p className="mt-1 text-sm text-dim">{rec.reason}</p>
          </div>
          <ul className="mt-3 list-disc space-y-0.5 pl-4 text-sm">{plan.explanation.summary.map((l, i) => <li key={i}>{l}</li>)}</ul>
          {plan.ranked.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-left text-[10px] uppercase tracking-wider text-dim"><tr><th className="py-1">#</th><th>Action</th><th>Eligible</th><th>Benefit</th><th>Pressure margin</th><th>Simulated verdict</th><th>Why not eligible</th></tr></thead>
                <tbody>{plan.ranked.map((r) => (
                  <tr key={r.id} className={`border-t border-line ${rec.id === r.id ? "text-accent" : ""}`}>
                    <td className="py-1 font-mono">{r.rank}</td><td className="font-mono">{r.id}</td>
                    <td><Chip tone={r.eligible ? "ok" : "bad"}>{r.eligible ? "yes" : "no"}</Chip></td>
                    <td className="font-mono">{r.benefit == null ? "—" : `${fx(r.benefit, 2)} ${r.benefit_metric?.endsWith("m3") ? "m³" : "m"}`}</td>
                    <td className="font-mono">{r.min_pressure_margin_m == null ? "—" : `${fx(r.min_pressure_margin_m, 1)} m`}</td>
                    <td className="max-w-[16rem]">{r.verdict ?? "—"}</td>
                    <td className="max-w-[22rem] text-dim">{r.ineligible_reasons.join("; ") || "—"}</td>
                  </tr>))}</tbody>
              </table>
            </div>)}
          {plan.candidates.some((c) => c.category === "NON_HYDRAULIC") && (
            <div className="mt-3 text-xs"><div className="mb-1 text-[10px] uppercase tracking-wider text-dim">Non-hydraulic tasks (not simulated)</div>
              <ul className="list-disc pl-4">{plan.candidates.filter((c) => c.category === "NON_HYDRAULIC").map((c) => <li key={c.id}>{c.description}</li>)}</ul></div>)}
          {plan.notes.length > 0 && <ul className="mt-2 list-disc pl-4 text-[11px] text-dim">{plan.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
          <p className="mt-2 text-[11px] text-warn">{plan.disclaimer}</p>
          <div className="mt-3"><Button onClick={() => go("Simulating on the hidden true network…", async () => setVer(await api.remVerify(sid)))} disabled={!!busy}>3 · Verify on simulated true network</Button></div>
        </Panel>)}

      {ver && (
        <Panel title="3 · Before / after on the hidden true simulated network">
          <p className="mb-2 text-xs text-warn">{ver.label}</p>
          {!ver.applicable ? <p className="text-sm">{ver.reason}</p> : (
            <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
              <div>
                {chart.length > 0 && (
                  <ResponsiveContainer width="100%" height={260}>
                    <LineChart data={chart} margin={{ left: 0, right: 8, top: 8 }}>
                      <CartesianGrid stroke="#1c2a45" strokeDasharray="2 4" />
                      <XAxis dataKey="h" type="number" domain={["dataMin", "dataMax"]} unit=" h" />
                      <YAxis yAxisId="p" unit=" m" width={56} /><YAxis yAxisId="q" orientation="right" unit=" L/s" width={56} />
                      <Tooltip {...tip} /><Legend />
                      <ReferenceLine yAxisId="p" x={ver.series!.intervention_time_h} stroke="#22d3ee" strokeDasharray="4 4" label={{ value: "action", fill: "#22d3ee", fontSize: 10, position: "insideTopLeft" }} />
                      <Line yAxisId="p" dataKey="without_p" name="min pressure — no action" stroke="#3b4f75" dot={false} isAnimationActive={false} />
                      <Line yAxisId="p" dataKey="with_p" name="min pressure — with action" stroke="#22d3ee" dot={false} isAnimationActive={false} />
                      <Line yAxisId="q" dataKey="without_leak" name="leak — no action" stroke="#7f1d1d" dot={false} isAnimationActive={false} />
                      <Line yAxisId="q" dataKey="with_leak" name="leak — with action" stroke="#f87171" dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>)}
              </div>
              <div className="text-sm">
                <dl className="grid grid-cols-2 gap-1 text-xs">
                  <dt className="text-dim">True fault</dt><dd className="font-mono">{ver.true_fault.event_class}{ver.true_fault.location ? ` @ ${ver.true_fault.location}` : ""}</dd>
                  <dt className="text-dim">Diagnosis matched truth</dt><dd className="font-mono">{String(ver.diagnosis_matched_truth)}</dd>
                  {ver.leak_stopped != null && <><dt className="text-dim">Leak stopped</dt><dd className="font-mono">{String(ver.leak_stopped)}</dd></>}
                  {ver.mechanism_benefit && <><dt className="text-dim">Fault effect removed</dt><dd className="font-mono">pred {fx(ver.mechanism_benefit.predicted_benefit, 2)} / actual {fx(ver.mechanism_benefit.actual_benefit, 2)} m</dd></>}
                  <dt className="text-dim">Constraints pred / actual</dt><dd className="font-mono">{String(ver.predicted_passed_constraints)} / {String(ver.actual_passed_constraints)}</dd>
                  <dt className="text-dim">Prediction miss</dt><dd className="font-mono">{String(ver.constraint_prediction_miss)}</dd>
                </dl>
                {Object.keys(ver.predicted_vs_actual_after).length > 0 && (
                  <table className="mt-3 w-full text-xs"><thead className="text-left text-[10px] uppercase tracking-wider text-dim"><tr><th>After window</th><th>Predicted</th><th>Actual</th></tr></thead>
                    <tbody className="font-mono">{Object.entries(ver.predicted_vs_actual_after).map(([k, v]) => <tr key={k} className="border-t border-line"><td className="py-1 font-sans">{k.replaceAll("_", " ")}</td><td>{fx(v.predicted, 2)}</td><td>{fx(v.actual, 2)}</td></tr>)}</tbody></table>)}
                <p className="mt-2 text-xs">{ver.actual_verdict}</p>
                {ver.warnings.map((w, i) => <p key={i} className="mt-1 text-xs text-warn">{w}</p>)}
              </div>
            </div>)}
        </Panel>)}
    </div>
  );
}
