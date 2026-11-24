"use client";
import type { Network } from "@/lib/api";

export type MapHighlights = {
  candidatePipes?: string[];      // ranked; first = strongest
  candidateNodes?: string[];      // ranked (demand anomaly)
  suspectSensors?: string[];
  sensorTone?: Record<string, "ok" | "warn" | "bad">;
  recommended?: string | null;
  measured?: string | null;
  truePipe?: string | null;
  trueNode?: string | null;
};

const X = (x: number) => 50 + (x - 10) * 9.5;
const Y = (y: number) => 34 + (90 - y) * 4.4;
const TONE = { ok: "#34d399", warn: "#fbbf24", bad: "#f87171" };
const RANK = ["#f87171", "#fb923c", "#fbbf24"];

export default function NetworkMap({ net, h = {}, onNode, selected }: {
  net: Network; h?: MapHighlights; onNode?: (id: string) => void; selected?: string | null;
}) {
  const pos = Object.fromEntries(net.nodes.map((n) => [n.id, [X(n.x), Y(n.y)] as const]));
  const pipeRank = (id: string) => h.candidatePipes?.indexOf(id) ?? -1;
  return (
    <svg viewBox="0 0 660 440" className="h-auto w-full" role="img" aria-label="Net1 network topology">
      <defs>
        <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
          <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#12203a" strokeWidth="1" />
        </pattern>
      </defs>
      <rect width="660" height="440" fill="url(#grid)" />
      {net.links.map((l) => {
        const [x1, y1] = pos[l.from]; const [x2, y2] = pos[l.to];
        const r = pipeRank(l.id);
        const isTrue = h.truePipe === l.id;
        const stroke = r >= 0 ? RANK[Math.min(r, 2)] : l.type === "pump" ? "#22d3ee" : "#2f4469";
        const w = l.type === "pump" ? 2 : Math.max(2, (l.diameter_mm ?? 150) / 80);
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
        return (
          <g key={l.id}>
            {isTrue && <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#e879f9" strokeWidth={w + 8} strokeOpacity={0.35} />}
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={stroke} strokeWidth={r >= 0 ? w + 2 : w}
              strokeDasharray={l.type === "pump" ? "6 4" : undefined} strokeLinecap="round" />
            <text x={mx} y={my - 6} textAnchor="middle" className="fill-dim font-mono" fontSize="10">
              {l.type === "pump" ? `P${l.id}` : l.id}{r >= 0 ? ` #${r + 1}` : ""}
            </text>
            {l.flow_sensor && <rect x={mx - 4} y={my + 3} width="8" height="8" fill="#0d1526" stroke="#3b82f6" strokeWidth="1.5" />}
          </g>
        );
      })}
      {net.nodes.map((n) => {
        const [x, y] = pos[n.id];
        const tone = h.sensorTone?.[n.id];
        const suspect = h.suspectSensors?.includes(n.id);
        const nodeRank = h.candidateNodes?.indexOf(n.id) ?? -1;
        const isSel = selected === n.id;
        const common = { onClick: onNode ? () => onNode(n.id) : undefined, style: { cursor: onNode ? "pointer" : "default" } };
        return (
          <g key={n.id} {...common}>
            {h.recommended === n.id && <circle cx={x} cy={y} r="9" fill="none" stroke="#22d3ee" strokeWidth="2" className="target-ring" />}
            {h.recommended === n.id && <circle cx={x} cy={y} r="14" fill="none" stroke="#22d3ee" strokeWidth="1.5" strokeDasharray="3 3" />}
            {h.measured === n.id && <circle cx={x} cy={y} r="17" fill="none" stroke="#34d399" strokeWidth="1.5" />}
            {h.trueNode === n.id && <circle cx={x} cy={y} r="15" fill="#e879f9" fillOpacity={0.25} />}
            {n.type === "reservoir" && <rect x={x - 11} y={y - 11} width="22" height="22" rx="3" fill="#0d1526" stroke="#3b82f6" strokeWidth="2" />}
            {n.type === "tank" && <rect x={x - 14} y={y - 9} width="28" height="18" rx="2" fill="#0d1526" stroke="#3b82f6" strokeWidth="2" />}
            {n.type === "junction" && (
              <circle cx={x} cy={y} r={n.pressure_sensor ? 8 : 5.5}
                fill={nodeRank >= 0 ? RANK[Math.min(nodeRank, 2)] : n.pressure_sensor ? "#0d1526" : "#2f4469"}
                stroke={suspect ? "#f87171" : tone ? TONE[tone] : n.pressure_sensor ? "#22d3ee" : "#2f4469"}
                strokeWidth={suspect ? 3 : n.pressure_sensor ? 2.5 : 1} />
            )}
            {isSel && <circle cx={x} cy={y} r="12" fill="none" stroke="#dbe5f5" strokeWidth="1" />}
            <text x={x + 12} y={y + 16} className="fill-fg font-mono" fontSize="11">
              {n.type === "reservoir" ? "R9 source" : n.type === "tank" ? "T2 tank" : n.id}
            </text>
          </g>
        );
      })}
      <g transform="translate(14,428)" className="font-mono" fontSize="10">
        <circle cx="5" cy="0" r="6" fill="#0d1526" stroke="#22d3ee" strokeWidth="2" /><text x="16" y="4" className="fill-dim">pressure sensor</text>
        <rect x="112" y="-4" width="8" height="8" fill="#0d1526" stroke="#3b82f6" strokeWidth="1.5" /><text x="125" y="4" className="fill-dim">flow sensor</text>
        <line x1="200" y1="0" x2="222" y2="0" stroke="#f87171" strokeWidth="4" /><text x="227" y="4" className="fill-dim">candidate #1–3</text>
        <circle cx="330" cy="0" r="6" fill="none" stroke="#22d3ee" strokeDasharray="3 3" /><text x="341" y="4" className="fill-dim">recommended gauge</text>
        <circle cx="462" cy="0" r="6" fill="#e879f9" fillOpacity="0.35" /><text x="473" y="4" className="fill-dim">ground truth (revealed)</text>
      </g>
    </svg>
  );
}
