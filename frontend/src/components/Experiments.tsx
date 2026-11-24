"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AppState } from "@/app/page";
import { Button, Chip, Empty, ErrorBox, Panel, Spinner } from "./ui";
import { api, fx, pct, type EvalResults, type Spec } from "@/lib/api";

type Go = (t: "Diagnosis") => void;
const AP = ["physics", "random_forest", "hybrid"] as const;
const AL: Record<string, string> = { physics: "A · Physics", random_forest: "B · Random Forest", hybrid: "C · Hybrid" };
const input = "w-full rounded-md border border-line bg-ink px-2 py-1.5 text-sm";

export default function Experiments({ s, go }: { s: AppState; go: Go }) {
  const [spec, setSpec] = useState<Spec>({ event_class: "abrupt_leak", seed: 7, severity: 0.5, mismatch: 0.05, pressure_noise: 0.1, location: null, start_time: null });
  const [ev, setEv] = useState<EvalResults | null>(null);
  const [evErr, setEvErr] = useState<string | null>(null);
  const cls = s.opts?.classes.find((c) => c.id === spec.event_class);
  const set = (p: Partial<Spec>) => setSpec((x) => ({ ...x, ...p }));

  const poll = useCallback(async () => {
    try { setEv(await api.evalResults()); setEvErr(null); } catch (e) { setEvErr(String((e as Error).message)); }
  }, []);
  useEffect(() => { api.evalResults().then(setEv, (e) => setEvErr(String((e as Error).message))); }, []);
  useEffect(() => {
    if (!ev?.running) return;
    const t = setInterval(poll, 3000);
    return () => clearInterval(t);
  }, [ev?.running, poll]);

  const submit = async () => { await s.run(spec); go("Diagnosis"); };
  const r = ev?.results;
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
        <Panel title="Scenario generator (EPyT-Flow / EPANET)">
          <form className="grid grid-cols-2 gap-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <label className="col-span-2 flex flex-col gap-1"><span className="text-xs text-dim">Event class</span>
              <select className={input} value={spec.event_class} onChange={(e) => set({ event_class: e.target.value, location: null })}>
                {s.opts?.classes.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
              </select></label>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Location</span>
              <select className={input} value={spec.location ?? ""} disabled={!cls?.locations.length} onChange={(e) => set({ location: e.target.value || null })}>
                <option value="">{cls?.locations.length ? "random (from seed)" : "n/a"}</option>
                {cls?.locations.map((l) => <option key={l} value={l}>{l}</option>)}
              </select></label>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Event start (h)</span>
              <select className={input} value={spec.start_time ?? ""} disabled={spec.event_class === "normal"} onChange={(e) => set({ start_time: e.target.value ? Number(e.target.value) : null })}>
                <option value="">random 30–44 h</option>
                {Array.from({ length: 29 }, (_, i) => 30 * 3600 + i * 1800).map((t) => <option key={t} value={t}>{(t / 3600).toFixed(1)}</option>)}
              </select></label>
            <label className="col-span-2 flex flex-col gap-1"><span className="text-xs text-dim">Severity: <span className="font-mono text-fg">{spec.severity.toFixed(2)}</span></span>
              <input type="range" min={0} max={1} step={0.05} value={spec.severity} onChange={(e) => set({ severity: Number(e.target.value) })} disabled={spec.event_class === "normal"} className="accent-cyan-400" /></label>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Model mismatch</span>
              <select className={input} value={spec.mismatch} onChange={(e) => set({ mismatch: Number(e.target.value) })}>
                {s.opts?.mismatch_levels.map((m) => <option key={m} value={m}>{m * 100}%</option>)}
              </select></label>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Sensor noise σ (m)</span>
              <input type="number" className={input} min={0} max={2} step={0.05} value={spec.pressure_noise} onChange={(e) => set({ pressure_noise: Number(e.target.value) })} /></label>
            <label className="flex flex-col gap-1"><span className="text-xs text-dim">Random seed</span>
              <input type="number" className={input} min={0} value={spec.seed} onChange={(e) => set({ seed: Math.max(0, Math.floor(Number(e.target.value))) })} /></label>
            <div className="flex items-end"><Button type="submit" disabled={!!s.busy || !s.opts}>{s.busy ? "Working…" : "Run experiment"}</Button></div>
          </form>
          <p className="mt-3 text-[11px] text-dim">3-day simulation, 30-min reporting. Day 1 is the event-free reference. Same seed + settings ⇒ identical scenario. Mismatch perturbs pipe roughness, base demand and demand pattern (uniform ±level) in the &quot;real&quot; network; diagnosis uses the unperturbed model.</p>
        </Panel>
        <Panel title="Scenario library">
          {s.scenarios.length === 0 ? <Empty>No scenarios yet.</Empty> : (
            <div className="max-h-[330px] overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-panel text-left text-[10px] uppercase tracking-wider text-dim"><tr><th className="py-1">ID</th><th>Loc</th><th>Sev</th><th>Mismatch</th><th>Seed</th><th /></tr></thead>
                <tbody className="font-mono">
                  {s.scenarios.map((x) => (
                    <tr key={x.scenario_id} className={`border-t border-line ${x.scenario_id === s.sid ? "text-accent" : ""}`}>
                      <td className="py-1">{x.scenario_id}</td><td>{x.spec.location ?? "—"}</td><td>{x.spec.severity.toFixed(2)}</td><td>{x.spec.mismatch * 100}%</td><td>{x.spec.seed}</td>
                      <td><button className="text-accent hover:underline" onClick={async () => { await s.load(x.scenario_id); go("Diagnosis"); }}>open</button></td>
                    </tr>))}
                </tbody>
              </table>
            </div>)}
        </Panel>
      </div>

      <Panel title="Held-out evaluation" right={
        <div className="flex items-center gap-2">
          {ev?.running ? <Spinner label={`running since ${ev.started}`} /> : r && <span className="text-[11px] text-dim">generated {r.generated_at}</span>}
          <Button variant="ghost" disabled={!!ev?.running} onClick={async () => { try { await api.evalRun(); await poll(); } catch (e) { setEvErr((e as Error).message); } }}>Re-run evaluation</Button>
        </div>}>
        <ErrorBox error={evErr ?? ev?.error ?? null} />
        {!r ? <Empty>No evaluation results yet. Click “Re-run evaluation”.</Empty> : <Results r={r} />}
      </Panel>
    </div>
  );
}

function Results({ r }: { r: any }) {
  const sel = r.selection;
  const classes: string[] = r.dataset.classes;
  const cm: number[][] = r.approaches[sel.selected].overall.confusion_matrix;
  const rc = AP.map((a) => r.approaches[a].overall.risk_coverage);
  const rcData = rc[0].map((_: any, i: number) => ({ coverage: rc[0][i].coverage, physics: rc[0][i].risk, random_forest: rc[1][i].risk, hybrid: rc[2][i].risk }));
  const loc = r.localization.by_mismatch;
  const det = r.detection, inv = r.investigation;
  return (
    <div className="flex flex-col gap-5 text-sm">
      <p className="text-xs text-dim">{r.disclaimer} Test split: {r.dataset.test} scenarios (train {r.dataset.train}, validation {r.dataset.val}); leak/valve/demand test locations are never seen in training.</p>
      <div>
        <h3 className="mb-2 text-[11px] uppercase tracking-wider text-dim">Approach comparison</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase tracking-wider text-dim">
              <tr><th className="py-1">Approach</th><th>n</th><th>Accuracy</th><th>Macro-P</th><th>Macro-R</th><th>Macro-F1</th><th>F1 @0%</th><th>F1 @5%</th><th>F1 @15%</th><th>Brier</th><th>ECE</th><th>Coverage @thr</th><th>Selective risk</th></tr>
            </thead>
            <tbody className="font-mono">
              {AP.map((a) => { const o = r.approaches[a].overall, b = r.approaches[a].by_mismatch; return (
                <tr key={a} className={`border-t border-line ${a === sel.selected ? "text-accent" : ""}`}>
                  <td className="py-1 font-sans">{AL[a]} {a === sel.selected && <Chip tone="info">selected</Chip>}</td><td>{o.n}</td><td>{pct(o.accuracy)}</td><td>{fx(o.macro_precision)}</td><td>{fx(o.macro_recall)}</td><td>{fx(o.macro_f1)}</td>
                  <td>{fx(b["0.0"].macro_f1)}</td><td>{fx(b["0.05"].macro_f1)}</td><td>{fx(b["0.15"].macro_f1)}</td><td>{fx(o.brier)}</td><td>{fx(o.ece)}</td><td>{pct(o.coverage_at_threshold)}</td><td>{o.selective_risk == null ? "N/A (abstains on all)" : pct(o.selective_risk)}</td>
                </tr>); })}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-[11px] text-dim">Per mismatch level n = {Object.values(r.dataset.test_by_mismatch).join(" / ")}. Probabilities temperature-scaled on validation.</p>
      </div>
      <div className="rounded-md border border-line p-3 text-xs">
        <div className="mb-1 font-medium">Architecture selection: <span className="text-accent">{AL[sel.selected]}</span></div>
        <div className="text-dim">{sel.rule}</div>
        <div className="mt-2 flex flex-wrap gap-2">{Object.entries(sel.checks).map(([k, v]) => <Chip key={k} tone={v ? "ok" : "bad"}>{k}: {String(v)}</Chip>)}</div>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-dim">Confusion matrix — {AL[sel.selected]} (rows = truth)</h3>
          <div className="overflow-x-auto">
            <table className="font-mono text-[10px]">
              <thead><tr><th />{classes.map((c) => <th key={c} className="px-1 text-dim" title={c}>{c.split("_").map((w) => w[0]).join("").toUpperCase()}</th>)}</tr></thead>
              <tbody>{cm.map((row, i) => { const tot = row.reduce((a, b) => a + b, 0); return (
                <tr key={i}><td className="pr-2 text-right text-dim">{classes[i]}</td>{row.map((v, j) => <td key={j} className="h-6 w-8 text-center" style={{ background: `rgba(${i === j ? "34,211,238" : "248,113,113"},${tot ? (v / tot) * 0.8 : 0})` }}>{v || ""}</td>)}</tr>); })}</tbody>
            </table>
          </div>
        </div>
        <div>
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-dim">Risk–coverage (lower is better)</h3>
          <ResponsiveContainer width="100%" height={210}>
            <LineChart data={rcData} margin={{ left: 0, right: 8, top: 4 }}>
              <CartesianGrid stroke="#1c2a45" strokeDasharray="2 4" />
              <XAxis dataKey="coverage" type="number" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <YAxis tickFormatter={(v) => `${Math.round(v * 100)}%`} width={44} />
              <Tooltip contentStyle={{ background: "#0d1526", border: "1px solid #1c2a45", fontSize: 12 }} formatter={(v) => pct(Number(v))} labelFormatter={(v) => `coverage ${pct(Number(v))}`} />
              <Legend />
              <Line dataKey="physics" name="Physics" stroke="#a78bfa" dot={false} isAnimationActive={false} />
              <Line dataKey="random_forest" name="Random Forest" stroke="#22d3ee" dot={false} isAnimationActive={false} />
              <Line dataKey="hybrid" name="Hybrid" stroke="#fbbf24" dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <div className="rounded-md border border-line p-3 text-xs">
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-dim">Leak localisation (physics)</h3>
          <table className="w-full font-mono"><thead className="text-left text-dim"><tr><th>Mismatch</th><th>n</th><th>Top-1</th><th>Top-3</th><th>Mean dist</th></tr></thead>
            <tbody>{["0.0", "0.05", "0.15", "all"].map((k) => loc[k] && <tr key={k} className="border-t border-line"><td>{k === "all" ? "all" : `${Number(k) * 100}%`}</td><td>{loc[k].n}</td><td>{pct(loc[k].top1)}</td><td>{pct(loc[k].top3)}</td><td>{fx(loc[k].mean_distance_m, 0)} m</td></tr>)}</tbody></table>
          <p className="mt-1 text-dim">Random baseline: top-1 {pct(r.localization.random_baseline_top1)}, top-3 {pct(r.localization.random_baseline_top3)}.</p>
        </div>
        <div className="rounded-md border border-line p-3 text-xs">
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-dim">Detection (online)</h3>
          <dl className="grid grid-cols-2 gap-1 font-mono">
            <dt className="font-sans text-dim">False alarms / day</dt><dd>{fx(det.false_alarms_per_day, 2)} (n={det.n_normal})</dd>
            <dt className="font-sans text-dim">Detection rate</dt><dd>{pct(det.event_detection_rate)} (n={det.n_events})</dd>
            <dt className="font-sans text-dim">Median delay</dt><dd>{fx(det.median_detection_delay_h, 1)} h</dd>
            <dt className="font-sans text-dim">P90 delay</dt><dd>{fx(det.p90_detection_delay_h, 1)} h</dd>
            <dt className="font-sans text-dim">Latency p95</dt><dd>{fx(r.latency.p95_ms, 0)} ms (n={r.latency.n})</dd>
          </dl>
        </div>
        <div className="rounded-md border border-line p-3 text-xs">
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-dim">Active investigation vs random</h3>
          <dl className="grid grid-cols-2 gap-1 font-mono">
            <dt className="font-sans text-dim">Cases (n)</dt><dd>{inv.n_with_recommendation_and_truth_in_pair}</dd>
            <dt className="font-sans text-dim">Before reading</dt><dd>{pct(inv.accuracy_before_measurement)}</dd>
            <dt className="font-sans text-dim">Recommended node</dt><dd className="text-ok">{pct(inv.accuracy_recommended_node)}</dd>
            <dt className="font-sans text-dim">Random node</dt><dd>{pct(inv.accuracy_random_eligible_node)}</dd>
            <dt className="font-sans text-dim">No reliable loc.</dt><dd>{inv.n_truth_in_pair_but_no_reliable_location}</dd>
          </dl>
          <p className="mt-1 text-dim">{inv.definition}</p>
        </div>
      </div>
    </div>
  );
}
