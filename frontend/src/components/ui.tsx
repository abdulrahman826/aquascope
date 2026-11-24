"use client";
import type { ReactNode } from "react";

export function Panel({ title, right, children, className = "" }: { title?: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-line bg-panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-dim">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone = "text" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone }) {
  return (
    <div className="rounded-md border border-line bg-ink/60 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-[0.12em] text-dim">{label}</div>
      <div className={`mt-1 font-mono text-lg ${toneText[tone]}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-dim">{sub}</div>}
    </div>
  );
}

export type Tone = "ok" | "warn" | "bad" | "info" | "text" | "dim";
export const toneText: Record<Tone, string> = {
  ok: "text-ok", warn: "text-warn", bad: "text-bad", info: "text-accent", text: "text-fg", dim: "text-dim",
};
const toneChip: Record<Tone, string> = {
  ok: "border-ok/40 bg-ok/10 text-ok", warn: "border-warn/40 bg-warn/10 text-warn", bad: "border-bad/40 bg-bad/10 text-bad",
  info: "border-accent/40 bg-accent/10 text-accent", text: "border-line bg-ink text-fg", dim: "border-line bg-ink text-dim",
};

export function Chip({ tone = "dim", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${toneChip[tone]}`}>{children}</span>;
}

export function Button({ children, onClick, disabled, variant = "primary", type = "button" }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; variant?: "primary" | "ghost"; type?: "button" | "submit";
}) {
  const v = variant === "primary"
    ? "bg-accent text-ink hover:bg-accent/85 disabled:bg-line disabled:text-dim"
    : "border border-line text-fg hover:border-accent/60 hover:text-accent disabled:text-dim disabled:hover:border-line";
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${v}`}>
      {children}
    </button>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div role="alert" className="rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">{error}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-line px-4 py-8 text-center text-sm text-dim">{children}</div>;
}

export function ProbBar({ value, tone = "info" }: { value: number; tone?: Tone }) {
  const bg = { ok: "bg-ok", warn: "bg-warn", bad: "bg-bad", info: "bg-accent", text: "bg-fg", dim: "bg-dim" }[tone];
  return (
    <div className="h-1.5 w-full rounded bg-line">
      <div className={`h-1.5 rounded ${bg}`} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}

export const STATUS: Record<string, { tone: Tone; label: string }> = {
  CONFIDENT: { tone: "ok", label: "Confident diagnosis" },
  AMBIGUOUS_DIAGNOSIS: { tone: "warn", label: "Ambiguous — competing explanations" },
  INSUFFICIENT_EVIDENCE: { tone: "bad", label: "Abstained — insufficient evidence" },
};

export function Spinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-dim">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      {label}
    </span>
  );
}
