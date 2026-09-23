/**
 * Flat bot silhouettes and eye placement adapted from Nous Research's
 * hermes-agent, apps/desktop/src/plugins/hermes-bots/avatar.tsx.
 * Source revision: 4a2bd7406ee3e09bd30a42cf9a7b970aac6edf9e.
 * Copyright (c) 2025 Nous Research. MIT license; see THIRD_PARTY_NOTICES.txt.
 *
 * Jarvis adaptation: stable ID-based appearance and state-driven CSS motion,
 * without a SDK, JavaScript frame clock, model download or WebGL in the roster.
 */

import { defaultCompanion, companionEyeColors, type SymbolShape } from "./companion/appearance";
import "./agentSymbol.css";
export type { SymbolShape } from "./companion/appearance";

export function symbolAppearance(identity: string): { shape: SymbolShape; color: string } {
  const { shape, color } = defaultCompanion(identity);
  return { shape, color };
}

/** The upstream superellipse sampler keeps the square soft without a bevel. */
function roundedOutline(shape: "squircle" | "pill"): string {
  const points: string[] = [];
  for (let i = 0; i < 52; i++) {
    const angle = (i / 52) * Math.PI * 2 - Math.PI / 2;
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    const power = shape === "pill" ? 8 : 5;
    const divisor = Math.pow(Math.abs(c) ** power + Math.abs(s / (shape === "pill" ? 0.72 : 1)) ** power, 1 / power) || 1;
    const radius = (shape === "pill" ? 16 : 16.2) / divisor;
    points.push(`${i === 0 ? "M" : "L"}${(20 + radius * c).toFixed(2)} ${(20 + radius * s).toFixed(2)}`);
  }
  return points.join(" ") + "Z";
}

const ROUNDED_OUTLINES = { squircle: roundedOutline("squircle"), pill: roundedOutline("pill") };

function SymbolBody({ shape, color }: { shape: SymbolShape; color: string }) {
  switch (shape) {
    case "squircle":
    case "pill":
      return <path d={ROUNDED_OUTLINES[shape]} fill={color} />;
    case "triangle":
      return <path d="M20 5.5 L36 33.5 L4 33.5 Z" fill={color} stroke={color} strokeWidth={5} strokeLinejoin="round" />;
    case "hexagon":
      return <path d="M20 3.5 L34.5 11.75 L34.5 28.25 L20 36.5 L5.5 28.25 L5.5 11.75 Z" fill={color} stroke={color} strokeWidth={3} strokeLinejoin="round" />;
    case "cloud":
      return <path d="M11 32 a7.5 7.5 0 0 1 -1 -14.9 A9.5 9.5 0 0 1 29 12.5 A7 7 0 0 1 30 32 Z" fill={color} />;
    case "drop":
      return <path d="M20 3 C20 3 6 20 6 27 a14 13.5 0 0 0 28 0 C34 20 20 3 20 3 Z" fill={color} />;
    default:
      return <circle cx={20} cy={20} fill={color} r={16.2} />;
  }
}

/** Plain ink eyes share one resting gaze; only a working agent moves. */
export function AgentSymbol({ shape, color, size, eyes = "lines", thinking = false }: { shape: SymbolShape; color: string; size: number; eyes?: "dots" | "lines"; thinking?: boolean }) {
  const eyeY = shape === "cloud" || shape === "triangle" ? 23 : shape === "drop" ? 25 : 17.2;
  const ink = companionEyeColors(color);
  return (
    <svg aria-hidden focusable="false" data-agent-symbol={shape} data-thinking={thinking ? "true" : undefined} width={size} height={size} style={{ width: size, height: size, flexShrink: 0 }} viewBox="0 0 40 44" className="society-agent-symbol block">
      <g className="agent-symbol-character">
        <g data-agent-body><SymbolBody shape={shape} color={color} /></g>
        <g className="agent-symbol-gaze">
          <g data-agent-eyes fill={ink.eye} transform={`translate(2 -0.6) rotate(-14 20 ${eyeY})`}>
            <g className="agent-symbol-lids">
              <ellipse cx={15.4} cy={eyeY} rx={eyes === "lines" ? 1.45 : 2} ry={eyes === "lines" ? 3.1 : 2.3} />
              <ellipse cx={24.6} cy={eyeY} rx={eyes === "lines" ? 1.45 : 2} ry={eyes === "lines" ? 3.1 : 2.3} />
            </g>
          </g>
        </g>
      </g>
    </svg>
  );
}
