"use client";
import type { AppState } from "@/app/page";
import { Button, Chip, Empty, Panel, ProbBar, STATUS } from "./ui";
import { fx, hours } from "@/lib/api";

const APPROACH_LABEL: Record<string, string> = { physics: "A · Physics / residual", random_forest: "B · Random Forest", hybrid: "C · Hybrid stacker" };
type Go = (t: "Investigation") => void;

export default function DiagnosisView({ s, go }: { s: AppState; go: Go }) {
  const d = s.diag;
  if (!d) return <Empty>No diagnosis yet. Generate or select a scenario in Experiments.</Empty>;
  const st = STATUS[d.status];
  const classes = Object.keys(d.all_approaches.physics);
  const label = (c: string) => s.opts?.classes.find((x) => x.id === c)?.label ?? c;
  return (
    <div className="flex flex-col gap-4">
      <div className={`rounded-lg border px-4 py-3 ${st.tone === "ok" ? "border-ok/40 bg-ok/5" : st.tone === "warn" ? "border-warn/40 bg-warn/5" : "border-bad/40 bg-bad/5"}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2"><Chip tone={st.tone}>{d.status}</Chip><span className="text-sm font-medium">{st.label}</span></div>
            <p className="mt-1 text-sm text-dim">{d.reason}</p>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-dim" htmlFor="approach">Approach</label>
            <select id="approach" value={d.approach} onChange={(e) => s.rediagnose(e.target.value)} disabled={!!s.busy}
              className="rounded-md border border-line bg-ink px-2 py-1 text-sm">
              {(s.opts?.approaches ?? []).map((a) => <option key={a} value={a}>{APPROACH_LABEL[a]}{a === d.approach_selection.selected ? " (selected)" : ""}</option>)}
            </select>
            <Button onClick={() => { s.investigate(); go("Investigation"); }} disabled={!!s.busy}>Investigate →</Button>
          </div>
        </div>
        <p className="mt-2 text-[11px] text-dim">{d.probability_type} · abstention threshold {d.threshold.toFixed(2)} (chosen on validation for ≤ 5% selective risk) · latency {d.latency_ms} ms</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Panel title="Ranked hypotheses">
          <ol className="flex flex-col gap-3">
            {d.hypotheses.map((h, i) => (
              <li key={h.event_class} className={`rounded-md border border-line p-3 ${i === 0 ? "bg-ink/70" : ""}`}>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-dim">#{i + 1}</span>
                    <span className="font-medium">{h.label}</span>
                    <Chip tone="dim">{h.family}</Chip>
                    {i === 0 && <Chip tone={st.tone}>leading</Chip>}
                    {i === 1 && <Chip tone="dim">alternative</Chip>}
                  </div>
                  <span className="font-mono text-sm">{h.probability.toFixed(3)}</span>
                </div>
                <div className="mt-2"><ProbBar value={h.probability} tone={i === 0 ? st.tone : "dim"} /></div>
                {h.candidate_locations.length > 0 && <div className="mt-2 text-xs text-dim">Candidate locations (physics ranking): <span className="font-mono text-fg">{h.candidate_locations.join(" > ")}</span></div>}
                {i < 3 && (
                  <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
                    <div><div className="mb-1 text-[10px] uppercase tracking-wider text-ok">Supporting evidence</div>
                      <ul className="list-disc space-y-0.5 pl-4">{h.supporting_evidence.length ? h.supporting_evidence.map((e, k) => <li key={k}>{e}</li>) : <li className="text-dim">none</li>}</ul></div>
                    <div><div className="mb-1 text-[10px] uppercase tracking-wider text-bad">Contradicting evidence</div>
                      <ul className="list-disc space-y-0.5 pl-4">{h.contradicting_evidence.length ? h.contradicting_evidence.map((e, k) => <li key={k}>{e}</li>) : <li className="text-dim">none</li>}</ul></div>
                  </div>)}
              </li>))}
          </ol>
        </Panel>
        <div className="flex flex-col gap-4">
          <Panel title="Approach comparison (this scenario)">
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase tracking-wider text-dim"><tr><th className="py-1">Class</th><th>Physics</th><th>RF</th><th>Hybrid</th></tr></thead>
              <tbody className="font-mono">
                {classes.map((c) => {
                  const v = ["physics", "random_forest", "hybrid"].map((a) => d.all_approaches[a][c]);
                  if (Math.max(...v) < 0.02) return null;
                  return <tr key={c} className="border-t border-line"><td className="py-1 font-sans">{label(c)}</td>{v.map((x, k) => <td key={k} className={x === Math.max(...classes.map((cc) => d.all_approaches[["physics", "random_forest", "hybrid"][k]][cc])) ? "text-accent" : ""}>{x.toFixed(2)}</td>)}</tr>;
                })}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-dim">Classes below 0.02 in all approaches hidden. Selected approach: <span className="text-fg">{APPROACH_LABEL[d.approach_selection.selected]}</span>{d.approach_selection.provisional ? " (provisional)" : " — by the pre-registered selection rule (see Experiments)"}.</p>
          </Panel>
          <Panel title="Detection & indicators">
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
              <dt className="text-dim">First alarm</dt><dd className="font-mono">{d.detection.first_alarm_time == null ? "none" : `${hours(d.detection.first_alarm_time).toFixed(1)} h`}</dd>
              <dt className="text-dim">Alarm episodes</dt><dd className="font-mono">{d.detection.n_alarm_episodes}</dd>
              <dt className="text-dim">Consumption shift</dt><dd className="font-mono">{fx(d.features.cons_shift, 2)} L/s</dd>
              <dt className="text-dim">Pattern correlation</dt><dd className="font-mono">{fx(d.features.cons_pattern_corr, 2)}</dd>
              <dt className="text-dim">Isolation ratio</dt><dd className="font-mono">{fx(d.features.iso_ratio, 2)}</dd>
              <dt className="text-dim">Lead sensor</dt><dd className="font-mono">{String(d.physics.indicators.lead_sensor)}</dd>
              <dt className="text-dim">Pump duty change</dt><dd className="font-mono">{fx(100 * d.features.pump_on_frac_change, 0)} pts</dd>
              <dt className="text-dim">Reference residual</dt><dd className="font-mono">{fx(d.features.ref_residual_level, 2)} m</dd>
            </dl>
            <p className="mt-2 text-[11px] text-dim">{d.disclaimer}</p>
          </Panel>
        </div>
      </div>
    </div>
  );
}
