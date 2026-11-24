"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api, ApiError, type Diagnosis, type GroundTruth, type Health, type Investigation, type Measurement,
  type Network, type Options, type ScenarioSummary, type Spec, type Telemetry,
} from "@/lib/api";
import type { MapHighlights } from "@/components/NetworkMap";
import { Chip, ErrorBox, STATUS } from "@/components/ui";
import Overview from "@/components/Overview";
import Explorer from "@/components/Explorer";
import TelemetryView from "@/components/TelemetryView";
import DiagnosisView from "@/components/DiagnosisView";
import InvestigationView from "@/components/InvestigationView";
import Experiments from "@/components/Experiments";
import RemediationView from "@/components/RemediationView";
import GuidedDemo from "@/components/GuidedDemo";

const TABS = ["Start here", "Overview", "Network", "Telemetry", "Diagnosis", "Investigation", "Remediation", "Experiments"] as const;
type Tab = (typeof TABS)[number];
const msg = (e: unknown) => (e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));

export type AppState = ReturnType<typeof useAquaScope>;

function useAquaScope() {
  const [health, setHealth] = useState<Health | null>(null);
  const [net, setNet] = useState<Network | null>(null);
  const [opts, setOpts] = useState<Options | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const [tel, setTel] = useState<Telemetry | null>(null);
  const [diag, setDiag] = useState<Diagnosis | null>(null);
  const [inv, setInv] = useState<Investigation | null>(null);
  const [meas, setMeas] = useState<Measurement | null>(null);
  const [gt, setGt] = useState<GroundTruth | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  const refreshList = useCallback(async () => setScenarios((await api.scenarios()).scenarios), []);

  useEffect(() => {
    (async () => {
      try {
        const [h, n, o] = await Promise.all([api.health(), api.network(), api.options()]);
        setHealth(h); setNet(n); setOpts(o);
        await refreshList();
      } catch (e) { setBootError(msg(e)); }
    })();
  }, [refreshList]);

  const guard = useCallback(async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError(null);
    try { await fn(); } catch (e) { setError(msg(e)); } finally { setBusy(null); }
  }, []);

  const load = useCallback((id: string) => guard("Loading scenario and running diagnosis…", async () => {
    setSid(id); setInv(null); setMeas(null); setGt(null); setDiag(null); setTel(null);
    const [t, d] = await Promise.all([api.telemetry(id), api.diagnose(id)]);
    setTel(t); setDiag(d);
  }), [guard]);

  const run = useCallback((spec: Spec) => guard("Running EPANET simulation…", async () => {
    const s = await api.run(spec);
    await refreshList();
    setSid(s.scenario_id); setInv(null); setMeas(null); setGt(null);
    const [t, d] = await Promise.all([api.telemetry(s.scenario_id), api.diagnose(s.scenario_id)]);
    setTel(t); setDiag(d);
  }), [guard, refreshList]);

  const rediagnose = useCallback((approach: string) => sid && guard(`Diagnosing with ${approach}…`, async () => {
    setDiag(await api.diagnose(sid, approach)); setInv(null); setMeas(null);
  }), [guard, sid]);

  const investigate = useCallback(() => sid && guard("Simulating competing hypotheses…", async () => {
    setMeas(null); setInv(await api.investigate(sid, diag?.approach));
  }), [guard, sid, diag?.approach]);

  const measure = useCallback((node: string) => sid && guard(`Taking virtual reading at junction ${node}…`, async () => {
    setMeas(await api.measure(sid, node));
  }), [guard, sid]);

  const reveal = useCallback(() => sid && guard("Revealing ground truth…", async () => setGt(await api.groundTruth(sid))), [guard, sid]);

  return { health, net, opts, scenarios, sid, tel, diag, inv, meas, gt, busy, error, bootError, load, run, rediagnose, investigate, measure, reveal };
}

export function highlightsFor(s: AppState): MapHighlights {
  const h: MapHighlights = {};
  const d = s.diag;
  if (d) {
    const lead = d.hypotheses.slice(0, d.status === "CONFIDENT" ? 1 : 2);
    for (const hy of lead) {
      if (hy.family === "leak" || hy.event_class === "valve_disturbance") h.candidatePipes ??= hy.candidate_locations;
      else if (hy.event_class === "demand_anomaly") h.candidateNodes ??= hy.candidate_locations;
      else if (hy.family === "sensor") h.suspectSensors = [...(h.suspectSensors ?? []), hy.candidate_locations[0]];
    }
    const sp = Number(d.physics.indicators.pressure_sigma_m);
    h.sensorTone = Object.fromEntries((s.net?.sensors.pressure ?? []).map((id) => {
      const f = d.features;
      const z = Math.abs(f[`shift_${id}`]) / sp;
      const bad = f[`stuck_${id}`] > 0.3 || f[`noise_ratio_${id}`] > 0.6;
      return [id, bad || z > 4 ? "bad" : z > 2 ? "warn" : "ok"];
    }));
  }
  h.recommended = s.inv?.recommended_node ?? null;
  h.measured = s.meas?.node ?? null;
  if (s.gt) {
    if (s.gt.family === "leak" || s.gt.event_class === "valve_disturbance") h.truePipe = s.gt.location;
    else h.trueNode = s.gt.location;
  }
  return h;
}

export default function Home() {
  const s = useAquaScope();
  const [tab, setTab] = useState<Tab>("Start here");
  const hl = useMemo(() => highlightsFor(s), [s]);
  const st = s.diag ? STATUS[s.diag.status] : null;

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[1500px] flex-col px-5 pb-10">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-3">
        <div className="flex items-center gap-3">
          <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden><path d="M13 2 C13 2 4 12 4 17 a9 9 0 0 0 18 0 C22 12 13 2 13 2Z" fill="none" stroke="#22d3ee" strokeWidth="2" /><path d="M8 17 h3 l2-4 2 7 2-3 h2" fill="none" stroke="#22d3ee" strokeWidth="1.5" /></svg>
          <div>
            <h1 className="text-base font-semibold tracking-wide">AquaScope</h1>
            <p className="text-[11px] text-dim">Evidence-driven water network diagnosis &amp; active investigation</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {s.health && <Chip tone={s.health.status === "ok" ? "ok" : "warn"}>backend {s.health.status}</Chip>}
          {s.health && <Chip tone="info">{s.health.simulation_mode} · {s.health.network}</Chip>}
          {s.sid && <Chip tone="text">scenario {s.sid}</Chip>}
          {st && <Chip tone={st.tone}>{s.diag!.status.replace("_", " ")}</Chip>}
        </div>
      </header>

      <nav className="flex gap-1 overflow-x-auto border-b border-line py-2" aria-label="Views">
        {TABS.map((t, i) => (
          <span key={t} className="flex items-center gap-1">{i === 1 && <span className="ml-2 mr-1 self-center text-[10px] uppercase tracking-wider text-dim">Expert views:</span>}
          <button onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${tab === t ? "bg-accent/15 text-accent" : "text-dim hover:text-fg"}`}>
            {t}
          </button></span>
        ))}
        <div className="ml-auto flex items-center pr-1 text-xs text-dim">{s.busy && <span className="animate-pulse">{s.busy}</span>}</div>
      </nav>

      <main className="mt-4 flex flex-col gap-4">
        <ErrorBox error={s.bootError ? `Cannot reach AquaScope backend — ${s.bootError}. Start it with: uvicorn aquascope.api:app --port 8077` : null} />
        <ErrorBox error={s.error} />
        {tab === "Start here" && <GuidedDemo s={s} hl={hl} />}
        {tab === "Overview" && <Overview s={s} hl={hl} go={setTab} />}
        {tab === "Network" && <Explorer s={s} hl={hl} />}
        {tab === "Telemetry" && <TelemetryView s={s} />}
        {tab === "Diagnosis" && <DiagnosisView s={s} go={setTab} />}
        {tab === "Investigation" && <InvestigationView s={s} hl={hl} />}
        {tab === "Remediation" && <RemediationView key={s.sid ?? "none"} s={s} />}
        {tab === "Experiments" && <Experiments s={s} go={setTab} />}
      </main>
      <footer className="mt-8 border-t border-line pt-3 text-[11px] text-dim">
        Prototype · all results are from EPyT-Flow/EPANET simulations of the Net1 benchmark network with injected model mismatch — not validated on real utility data.
      </footer>
    </div>
  );
}
