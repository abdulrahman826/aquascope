"use client";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AppState } from "@/app/page";
import NetworkMap, { type MapHighlights } from "./NetworkMap";
import { Button, Chip, Empty, Panel, ProbBar, Spinner } from "./ui";
import { fx, hours, type Investigation } from "@/lib/api";

export default function InvestigationView({ s, hl }: { s: AppState; hl: MapHighlights }) {
  const inv = s.inv, m = s.meas;
  const [pick, setPick] = useState<{ of: Investigation | null; node: string }>({ of: null, node: "" });
  const node = pick.of === inv ? pick.node : (inv?.recommended_node ?? inv?.candidates[0]?.node ?? "");
  const setNode = (n: string) => setPick({ of: inv, node: n });
  if (!s.diag) return <Empty>Run a diagnosis first (Experiments → generate a scenario).</Empty>;
  if (!inv) return (
    <Panel title="Active investigation">
      <div className="flex flex-col items-start gap-3">
        <p className="text-sm text-dim">Simulate the leading competing explanations on the EPANET model and find the junction where one extra pressure reading best separates them.</p>
        {s.busy ? <Spinner label={s.busy} /> : <Button onClick={s.investigate}>Compute measurement recommendation</Button>}
      </div>
    </Panel>);
  const chart = inv.candidates.map((c) => ({ node: c.node, separation: c.separation, rec: c.node === inv.recommended_node }));
  return (
    <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
      <div className="flex flex-col gap-4">
        <Panel title="Recommendation" right={<Chip tone="dim">{inv.method}</Chip>}>
          {inv.recommended_node
            ? <div className="flex items-start gap-3"><div className="rounded-md border border-accent/50 bg-accent/10 px-3 py-2 text-center"><div className="text-[10px] uppercase tracking-wider text-accent">measure at</div><div className="font-mono text-2xl text-accent">J-{inv.recommended_node}</div></div><p className="text-sm">{inv.explanation}</p></div>
            : <div className="flex items-start gap-3"><Chip tone="warn">no reliable location</Chip><p className="text-sm">{inv.explanation}</p></div>}
          {inv.hypotheses.length > 0 && (
            <div className="mt-4 grid gap-2 md:grid-cols-2">
              {inv.hypotheses.map((h) => (
                <div key={h.rank} className="rounded-md border border-line p-2.5 text-xs">
                  <div className="flex justify-between"><span className="text-dim">H{h.rank}</span><span className="font-mono">{h.probability.toFixed(3)}</span></div>
                  <div className="mt-1 text-sm">{h.description}</div>
                  <div className="mt-1.5"><ProbBar value={h.probability} tone={h.rank === 1 ? "info" : "dim"} /></div>
                  {Object.keys(h.fitted_parameters).length > 0 && <div className="mt-1 font-mono text-dim">fitted: {Object.entries(h.fitted_parameters).map(([k, v]) => `${k}=${fx(v, 3)}`).join(", ")}</div>}
                </div>))}
            </div>)}
          <ul className="mt-3 list-disc pl-4 text-[11px] text-dim">{inv.limitations?.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </Panel>
        {inv.candidates.length > 0 && (
          <Panel title="Ranked candidate junctions">
            <ResponsiveContainer width="100%" height={170}>
              <BarChart data={chart} margin={{ left: 0, right: 8, top: 8 }}>
                <CartesianGrid stroke="#1c2a45" strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="node" tickFormatter={(v) => `J-${v}`} /><YAxis width={40} />
                <Tooltip contentStyle={{ background: "#0d1526", border: "1px solid #1c2a45", fontSize: 12 }} formatter={(v) => [Number(v).toFixed(2), "separation (σ)"]} />
                <ReferenceLine y={inv.min_separation} stroke="#fbbf24" strokeDasharray="3 3" label={{ value: `reliability threshold ${inv.min_separation}σ`, fill: "#fbbf24", fontSize: 10, position: "insideTopRight" }} />
                <Bar dataKey="separation" isAnimationActive={false}>{chart.map((c) => <Cell key={c.node} fill={c.rec ? "#22d3ee" : "#2f4469"} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
            <table className="mt-2 w-full text-xs">
              <thead className="text-left text-[10px] uppercase tracking-wider text-dim"><tr><th className="py-1">Junction</th><th>Under H1</th><th>Under H2</th><th>Δ</th><th>± uncert.</th><th>Separation (σ)</th><th>P(correct)</th></tr></thead>
              <tbody className="font-mono">
                {inv.candidates.map((c) => (
                  <tr key={c.node} className={`border-t border-line ${c.node === inv.recommended_node ? "text-accent" : ""}`}>
                    <td className="py-1">J-{c.node}{c.monitored ? <span className="ml-1 text-dim">(sensor)</span> : null}</td>
                    <td>{fx(c.predicted_h1_m, 2)} m</td><td>{fx(c.predicted_h2_m, 2)} m</td><td>{fx(c.difference_m, 2)}</td>
                    <td>{fx(c.uncertainty_m, 2)}</td><td>{fx(c.separation, 2)}</td><td>{(100 * c.p_correct_single_reading).toFixed(0)}%</td>
                  </tr>))}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-dim">Window: {inv.measurement_window}. &quot;(sensor)&quot; = co-located verification gauge beside an existing sensor.</p>
          </Panel>)}
      </div>
      <div className="flex flex-col gap-4">
        <Panel title="Map">{s.net && <NetworkMap net={s.net} h={hl} onNode={(id) => inv.candidates.some((c) => c.node === id) && setNode(id)} selected={node} />}</Panel>
        <Panel title="Simulate the field measurement">
          {inv.candidates.length === 0 ? <Empty>No competing hypotheses to test.</Empty> : (
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-2">
                <label htmlFor="mnode" className="text-xs text-dim">Junction</label>
                <select id="mnode" value={node} onChange={(e) => setNode(e.target.value)} className="rounded-md border border-line bg-ink px-2 py-1 font-mono text-sm">
                  {inv.candidates.map((c) => <option key={c.node} value={c.node}>J-{c.node}{c.node === inv.recommended_node ? " (recommended)" : ""}</option>)}
                </select>
                <Button onClick={() => node && s.measure(node)} disabled={!node || !!s.busy}>Take reading</Button>
              </div>
              {m && (
                <div className="rounded-md border border-line p-3 text-sm">
                  <div>Reading at <span className="font-mono">J-{m.node}</span>: <span className="font-mono text-accent">{fx(m.reading_m, 2)} m</span> <span className="text-dim">(H1 predicted {fx(m.predicted_h1_m, 2)}, H2 {fx(m.predicted_h2_m, 2)}; z = {m.z_scores?.map((z) => z.toFixed(1)).join(" / ") ?? "n/a"})</span></div>
                  <div className="mt-2 flex flex-col gap-1.5">{m.posterior.map((p) => <div key={p.description}><div className="flex justify-between text-xs"><span>{p.description}</span><span className="font-mono">{p.probability.toFixed(3)}</span></div><ProbBar value={p.probability} tone={p.description === m.resolved_to ? "ok" : "dim"} /></div>)}</div>
                  {m.warning ? <div className="mt-2 rounded border border-warn/40 bg-warn/10 px-2 py-1 text-xs text-warn">{m.warning}</div>
                    : <div className="mt-2 text-xs text-ok">Resolved to: {m.resolved_to}</div>}
                  <p className="mt-2 text-[11px] text-dim">{m.note}</p>
                </div>)}
            </div>)}
        </Panel>
        <Panel title="Ground truth (simulation only)">
          {s.gt ? (
            <dl className="grid grid-cols-2 gap-1 text-xs">
              <dt className="text-dim">Event</dt><dd className="font-mono">{s.gt.event_class}</dd>
              <dt className="text-dim">Location</dt><dd className="font-mono">{s.gt.location ?? "—"}</dd>
              <dt className="text-dim">Start</dt><dd className="font-mono">{s.gt.start_time == null ? "—" : `${hours(s.gt.start_time).toFixed(1)} h`}</dd>
              <dt className="text-dim">Severity</dt><dd className="font-mono">{s.gt.severity.toFixed(2)} {Object.entries(s.gt.physical_parameters).map(([k, v]) => `${k}=${fx(v, 3)}`).join(" ")}</dd>
            </dl>
          ) : <div className="flex items-center justify-between gap-2"><p className="text-xs text-dim">Hidden from the diagnosis. Reveal only to check the result.</p><Button variant="ghost" onClick={s.reveal} disabled={!!s.busy}>Reveal</Button></div>}
        </Panel>
      </div>
    </div>
  );
}
