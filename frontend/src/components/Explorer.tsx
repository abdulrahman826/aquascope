"use client";
import { useState } from "react";
import type { AppState } from "@/app/page";
import NetworkMap, { type MapHighlights } from "./NetworkMap";
import { Chip, Empty, Panel } from "./ui";
import { fx } from "@/lib/api";

export default function Explorer({ s, hl }: { s: AppState; hl: MapHighlights }) {
  const [sel, setSel] = useState<string | null>(null);
  if (!s.net) return <Empty>Loading topology…</Empty>;
  const node = s.net.nodes.find((n) => n.id === sel);
  const links = s.net.links.filter((l) => sel && (l.from === sel || l.to === sel));
  const loc = s.diag?.physics.localization;
  const t = s.tel;
  const last = t ? t.telemetry.time.length - 1 : 0;
  return (
    <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
      <Panel title="Network explorer — click a node">
        <NetworkMap net={s.net} h={hl} onNode={setSel} selected={sel} />
      </Panel>
      <div className="flex flex-col gap-4">
        <Panel title="Selection">
          {!node ? <Empty>Select a junction, tank or reservoir.</Empty> : (
            <div className="flex flex-col gap-2 text-sm">
              <div className="flex items-center gap-2"><span className="font-mono text-lg">{node.id}</span><Chip tone="info">{node.type}</Chip>{node.pressure_sensor && <Chip tone="ok">pressure sensor</Chip>}</div>
              <div className="text-dim">Elevation {fx(node.elevation_m, 1)} m</div>
              {node.pressure_sensor && t && <div>Last reading: <span className="font-mono">{fx(t.telemetry.pressure[node.id][last], 2)} m</span> (model {fx(t.nominal_model.pressure[node.id][last], 2)} m)</div>}
              {!node.pressure_sensor && node.type === "junction" && <div className="text-dim">Unmonitored — eligible location for a portable pressure gauge.</div>}
              <div className="mt-1 text-[10px] uppercase tracking-wider text-dim">Connected links</div>
              <ul className="font-mono text-xs">{links.map((l) => <li key={l.id}>{l.type} {l.id}: {l.from}→{l.to} · {fx(l.length_m, 0)} m{l.diameter_mm ? ` · Ø${l.diameter_mm} mm` : ""}{l.flow_sensor ? " · flow sensor" : ""}</li>)}</ul>
            </div>)}
        </Panel>
        <Panel title="Physics localisation (signature fit)">
          {!loc ? <Empty>Run a diagnosis to rank candidate locations.</Empty> : (
            <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
              {([["leak", "Leak — pipe"], ["valve_disturbance", "Throttled — pipe"], ["demand_anomaly", "Demand — node"], ["sensor", "Sensor fault"]] as const).map(([k, title]) => (
                <div key={k}>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-dim">{title}</div>
                  <ol className="font-mono">{loc[k]?.slice(0, 3).map((f, i) => <li key={f.location} className="flex justify-between"><span>#{i + 1} {f.location}</span><span className="text-dim">misfit {fx(f.rss, 1)}</span></li>)}</ol>
                </div>))}
            </div>)}
          <p className="mt-3 text-[11px] text-dim">Lower misfit = observed residual signature better explained by that location on the nominal EPANET model.</p>
        </Panel>
      </div>
    </div>
  );
}
