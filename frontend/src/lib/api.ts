export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8077";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit, timeoutMs = 30000): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(API_BASE + path, {
      ...init,
      signal: ctl.signal,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    });
    const body = await r.json().catch(() => null);
    if (!r.ok) {
      throw new ApiError(r.status, body?.error?.code ?? "http_error", body?.error?.message ?? `HTTP ${r.status}`);
    }
    return body as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if ((e as Error).name === "AbortError") throw new ApiError(0, "timeout", `Request to ${path} timed out`);
    throw new ApiError(0, "network", `Backend unreachable at ${API_BASE} (${(e as Error).message})`);
  } finally {
    clearTimeout(timer);
  }
}

const post = <T,>(p: string, body: unknown = {}, t?: number) => req<T>(p, { method: "POST", body: JSON.stringify(body) }, t);

// ---------- types (mirror backend responses)
export type Health = { status: string; simulation_mode: string; network: string; models_loaded: boolean; selected_approach: string; evaluation_available: boolean };
export type NetNode = { id: string; type: "junction" | "reservoir" | "tank"; x: number; y: number; elevation_m: number; pressure_sensor: boolean };
export type NetLink = { id: string; type: "pipe" | "pump"; from: string; to: string; length_m: number; diameter_mm: number | null; flow_sensor: boolean };
export type Network = { network_id: string; nodes: NetNode[]; links: NetLink[]; sensors: { pressure: string[]; flow: string[] } };
export type ClassOption = { id: string; label: string; family: string; locations: string[] };
export type Options = { classes: ClassOption[]; mismatch_levels: number[]; approaches: string[]; event_start_range_s: [number, number]; report_step_s: number };
export type Spec = { event_class: string; seed: number; severity: number; mismatch: number; pressure_noise: number; location?: string | null; start_time?: number | null };
export type ScenarioSummary = { scenario_id: string; spec: Spec; network_id: string; simulation_mode: string; created?: string; metadata: { runtime_s: number } };
export type Telemetry = {
  scenario_id: string;
  telemetry: { time: number[]; pressure: Record<string, number[]>; flow: Record<string, number[]> };
  nominal_model: { pressure: Record<string, number[]>; flow: Record<string, number[]> };
  reference_period_s: [number, number];
};
export type Hypothesis = { event_class: string; label: string; family: string; probability: number; candidate_locations: string[]; supporting_evidence: string[]; contradicting_evidence: string[] };
export type LocFit = { location: string; scale: number; rss: number; loglik: number };
export type Diagnosis = {
  scenario_id: string; approach: string; status: "CONFIDENT" | "AMBIGUOUS_DIAGNOSIS" | "INSUFFICIENT_EVIDENCE";
  reason: string; threshold: number; probability_type: string; hypotheses: Hypothesis[];
  approach_selection: { selected: string; checks?: Record<string, boolean>; provisional?: boolean };
  all_approaches: Record<string, Record<string, number>>;
  physics: { localization: Record<string, LocFit[]>; indicators: Record<string, number | string> };
  features: Record<string, number>;
  detection: { alarm: boolean; first_alarm_time: number | null; n_alarm_episodes: number; score: number[]; threshold: number };
  residuals: { time: number[]; pressure: Record<string, number[]>; consumption: number[]; expected_pressure: Record<string, number[]> };
  latency_ms: number; disclaimer: string;
};
export type Candidate = { node: string; monitored: boolean; predicted_h1_m: number; predicted_h2_m: number; difference_m: number; uncertainty_m: number; separation: number; p_correct_single_reading: number };
export type Investigation = {
  scenario_id: string; method: string; recommended_node: string | null; explanation: string; limitations: string[];
  hypotheses: { rank: number; description: string; probability: number; event_class: string; location: string | null; fitted_parameters: Record<string, number> }[];
  candidates: Candidate[]; min_separation: number; measurement_window: string; diagnosis_status: string; approach: string;
};
export type Measurement = {
  node: string; reading_m: number; predicted_h1_m: number; predicted_h2_m: number;
  posterior: { description: string; probability: number }[]; resolved_to: string;
  z_scores: [number, number]; consistent_with_a_hypothesis: boolean; warning: string | null; note: string;
};
export type GroundTruth = { event_class: string; family: string; location: string | null; start_time: number | null; severity: number; physical_parameters: Record<string, number> };
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type EvalResults = { running: boolean; error: string | null; started: string | null; results: any };

export type Confirmation = {
  scenario_id: string; status: "CONFIDENT_BUT_UNVERIFIED" | "CONFIRMED_BY_INVESTIGATION" | "OPERATOR_CONFIRMED" | "INSUFFICIENT_EVIDENCE";
  event_class: string | null; location: string | null; probability: number | null; source: string; reason: string; route: string;
  evidence: string[]; hydraulic_interventions_unlocked: boolean; diagnosis_status: string; label: string;
};
export type ConstraintCheck = { name: string; value: unknown; limit: unknown; passed: boolean };
export type RankedAction = {
  id: string; kind: string; target: string | null; role?: string; eligible: boolean; ineligible_reasons: string[]; rank: number;
  benefit_metric?: string; benefit?: number; min_pressure_margin_m?: number; demand_deficit_m3?: number; verdict?: string;
  constraint_checks?: ConstraintCheck[]; warnings?: string[]; physical_valve_availability?: Record<string, string> | null;
};
export type CandidateAction = { id: string; kind: string; target: string | null; category: string; role: string; description: string;
  unlocked: boolean; lock_reasons: string[]; screened_out: boolean; screen_reasons: string[]; execution_status: string };
export type RemediationPlan = {
  confirmation: Confirmation; candidates: CandidateAction[]; ranked: RankedAction[];
  recommendation: { kind: string; target: string | null; reason: string; verdict?: string; id?: string };
  hypothesis_fault: { event_class: string; location: string; start_time_h: number; parameters: Record<string, number> } | null;
  notes: string[]; preview_only: boolean; disclaimer: string; execution_status: string;
  explanation: { summary: string[]; alternatives_not_chosen: string[]; constraint_table: ConstraintCheck[]; disclaimer: string };
};
export type Series = { time_h: number[]; min_pressure_m: number[]; mean_pressure_m: number[]; leak_flow_L_s: number[]; tank_volume_m3: number[] };
export type Verification = {
  label: string; applicable: boolean; reason?: string; action?: { kind: string; target: string | null };
  true_fault: { event_class: string; location: string | null }; diagnosis_matched_truth: boolean | null;
  predicted_vs_actual_after: Record<string, { predicted: number; actual: number; error: number }>;
  predicted_passed_constraints: boolean | null; actual_passed_constraints: boolean; constraint_prediction_miss: boolean;
  leak_stopped: boolean | null; mechanism_benefit: { metric: string; predicted_benefit: number; actual_benefit: number; remaining_after_m: number } | null;
  actual_verdict: string; warnings: string[];
  series: { with: Series; without: Series; intervention_time_h: number } | null;
};

export const api = {
  health: () => req<Health>("/api/health", undefined, 8000),
  network: () => req<Network>("/api/network"),
  options: () => req<Options>("/api/options"),
  scenarios: () => req<{ scenarios: ScenarioSummary[] }>("/api/scenarios"),
  run: (spec: Spec) => post<ScenarioSummary>("/api/scenarios/run", spec, 60000),
  telemetry: (id: string) => req<Telemetry>(`/api/telemetry/${id}`),
  diagnose: (id: string, approach?: string) => post<Diagnosis>(`/api/diagnosis/${id}`, approach ? { approach } : {}),
  investigate: (id: string, approach?: string) => post<Investigation>(`/api/investigation/${id}`, approach ? { approach } : {}, 60000),
  measure: (id: string, node: string) => post<Measurement>(`/api/investigation/${id}/measure`, { node }),
  groundTruth: (id: string) => req<GroundTruth>(`/api/scenarios/${id}/ground_truth`),
  evalResults: () => req<EvalResults>("/api/evaluation/results"),
  evalRun: () => post<{ status: string }>("/api/evaluation/run"),
  remConfirm: (id: string, body: Record<string, string | null>) => post<Confirmation>(`/api/remediation/${id}/confirm`, body, 90000),
  remPlan: (id: string) => post<RemediationPlan>(`/api/remediation/${id}/plan`, {}, 90000),
  remVerify: (id: string) => post<Verification>(`/api/remediation/${id}/verify`, {}, 90000),
};

export const hours = (s: number) => s / 3600;
export const pct = (x: number | null | undefined, d = 1) => (x == null ? "N/A" : `${(100 * x).toFixed(d)}%`);
export const fx = (x: number | null | undefined, d = 3) => (x == null ? "N/A" : x.toFixed(d));
