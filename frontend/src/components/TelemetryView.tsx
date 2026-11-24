"use client";
import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AppState } from "@/app/page";
import { Empty, Panel } from "./ui";
import { hours } from "@/lib/api";

const COLORS = ["#22d3ee", "#a78bfa", "#fbbf24", "#34d399"];
const tip = { contentStyle: { background: "#0d1526", border: "1px solid #1c2a45", fontSize: 12 }, labelFormatter: (v: unknown) => `t = ${Number(v).toFixed(1)} h` };

function Markers({ s }: { s: AppState }) {
  const a = s.diag?.detection.first_alarm_time;
  return (
    <>
      <ReferenceArea x1={0} x2={24} fill="#3b82f6" fillOpacity={0.05} />
      <ReferenceLine x={24} stroke="#3b82f6" strokeDasharray="4 4" label={{ value: "end of reference day", fill: "#7f8fab", fontSize: 10, position: "insideTopLeft" }} />
      {a != null && <ReferenceLine x={hours(a)} stroke="#fbbf24" strokeDasharray="3 3" label={{ value: "first alarm", fill: "#fbbf24", fontSize: 10, position: "insideTopRight" }} />}
      {s.gt?.start_time != null && <ReferenceLine x={hours(s.gt.start_time)} stroke="#e879f9" label={{ value: "true start", fill: "#e879f9", fontSize: 10, position: "insideBottomRight" }} />}
    </>
  );
}

export default function TelemetryView({ s }: { s: AppState }) {
  const sensors = useMemo(() => s.net?.sensors.pressure ?? [], [s.net]);
  const [sel, setSel] = useState<string>("22");
  const t = s.tel, d = s.diag;
  const pressure = useMemo(() => t ? t.telemetry.time.map((tt, i) => ({
    h: hours(tt), observed: t.telemetry.pressure[sel]?.[i], model: t.nominal_model.pressure[sel]?.[i],
    expected: d?.residuals.expected_pressure[sel]?.[i],
  })) : [], [t, d, sel]);
  const resid = useMemo(() => d ? d.residuals.time.map((tt, i) => ({
    h: hours(tt), ...Object.fromEntries(sensors.map((x) => [x, d.residuals.pressure[x][i]])), consumption: d.residuals.consumption[i],
    score: d.detection.score[i],
  })) : [], [d, sensors]);
  const flow = useMemo(() => t ? t.telemetry.time.map((tt, i) => ({
    h: hours(tt), pump: t.telemetry.flow["9"][i], pump_model: t.nominal_model.flow["9"][i],
    tank: t.telemetry.flow["110"][i], tank_model: t.nominal_model.flow["110"][i],
  })) : [], [t]);
  if (!t) return <Empty>No telemetry. Generate or select a scenario in Experiments.</Empty>;
  const ax = { dataKey: "h", type: "number" as const, domain: [0, 72], ticks: [0, 12, 24, 36, 48, 60, 72], unit: " h" };
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel title={`Pressure — sensor ${sel}`} right={
        <div className="flex gap-1" role="group" aria-label="Select sensor">
          {sensors.map((x) => <button key={x} onClick={() => setSel(x)} className={`rounded px-2 py-0.5 font-mono text-xs ${sel === x ? "bg-accent/20 text-accent" : "text-dim hover:text-fg"}`}>P-{x}</button>)}
        </div>}>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={pressure} margin={{ left: 0, right: 8, top: 8 }}>
            <CartesianGrid stroke="#1c2a45" strokeDasharray="2 4" />
            <XAxis {...ax} /><YAxis unit=" m" domain={["auto", "auto"]} width={60} />
            <Tooltip {...tip} /><Legend />
            <Markers s={s} />
            <Line dataKey="model" name="nominal model" stroke="#3b4f75" dot={false} strokeWidth={1} isAnimationActive={false} />
            <Line dataKey="expected" name="expected (model + reference profile)" stroke="#a78bfa" dot={false} strokeWidth={1.2} strokeDasharray="4 3" isAnimationActive={false} />
            <Line dataKey="observed" name="observed" stroke="#22d3ee" dot={false} strokeWidth={1.6} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </Panel>
      <Panel title="Pressure residuals (observed − expected), all sensors">
        {!d ? <Empty>Diagnosis required for residuals.</Empty> : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={resid} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke="#1c2a45" strokeDasharray="2 4" />
              <XAxis {...ax} /><YAxis unit=" m" width={60} />
              <Tooltip {...tip} /><Legend />
              <Markers s={s} />
              {sensors.map((x, i) => <Line key={x} dataKey={x} name={`P-${x}`} stroke={COLORS[i]} dot={false} strokeWidth={sel === x ? 2 : 1} isAnimationActive={false} />)}
            </LineChart>
          </ResponsiveContainer>)}
      </Panel>
      <Panel title="Flow — pump P9 and tank pipe 110 (observed vs model)">
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={flow} margin={{ left: 0, right: 8, top: 8 }}>
            <CartesianGrid stroke="#1c2a45" strokeDasharray="2 4" />
            <XAxis {...ax} /><YAxis unit=" L/s" width={64} />
            <Tooltip {...tip} /><Legend />
            <Markers s={s} />
            <Line dataKey="pump_model" name="pump model" stroke="#3b4f75" dot={false} isAnimationActive={false} />
            <Line dataKey="pump" name="pump observed" stroke="#22d3ee" dot={false} isAnimationActive={false} />
            <Line dataKey="tank_model" name="tank model" stroke="#4b3f6b" dot={false} isAnimationActive={false} />
            <Line dataKey="tank" name="tank observed" stroke="#a78bfa" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </Panel>
      <Panel title="Consumption residual & detector score">
        {!d ? <Empty>Diagnosis required.</Empty> : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={resid} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke="#1c2a45" strokeDasharray="2 4" />
              <XAxis {...ax} /><YAxis yAxisId="c" unit=" L/s" width={64} /><YAxis yAxisId="z" orientation="right" width={40} />
              <Tooltip {...tip} /><Legend />
              <Markers s={s} />
              <ReferenceLine yAxisId="z" y={d.detection.threshold} stroke="#fbbf24" strokeDasharray="2 2" />
              <Line yAxisId="c" dataKey="consumption" name="consumption residual (L/s)" stroke="#34d399" dot={false} isAnimationActive={false} />
              <Line yAxisId="z" dataKey="score" name="detector score" stroke="#fbbf24" dot={false} strokeWidth={1} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>)}
        <p className="mt-2 text-[11px] text-dim">Day 1 (shaded) is the reference period used to absorb stationary model mismatch; it is not monitored.</p>
      </Panel>
    </div>
  );
}
