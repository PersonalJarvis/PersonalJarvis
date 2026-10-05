/**
 * Flat bot silhouettes and eye placement adapted from Nous Research's
 * hermes-agent, apps/desktop/src/plugins/hermes-bots/avatar.tsx.
 * Source revision: 4a2bd7406ee3e09bd30a42cf9a7b970aac6edf9e.
 * Copyright (c) 2025 Nous Research. MIT license; see THIRD_PARTY_NOTICES.txt.
 *
 * Jarvis adaptation: stable ID-based appearance and state-driven CSS motion,
 * without a SDK, JavaScript frame clock, model download or WebGL in the roster.
 */

import { useId, useMemo, type ReactNode } from "react";
import { defaultCompanion, companionEyeColors, type SymbolShape } from "./companion/appearance";
import {
  ACCESSORY_CATALOG, FACE_SLOTS, partDepth, resolveFill, smoothPath, symbolViewBox, wornAccessories,
  type AccessoryChoice, type AccessoryItem, type AccessoryPart,
} from "./companion/accessories";
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

/** grow > 0 widens the silhouette by that many units on every side (a hood rim). */
function SymbolBody({ shape, color, grow = 0 }: { shape: SymbolShape; color: string; grow?: number }) {
  const edge = (base: number) => base + grow * 2 > 0 ? { stroke: color, strokeWidth: base + grow * 2, strokeLinejoin: "round" as const } : {};
  switch (shape) {
    case "squircle":
    case "pill":
      return <path d={ROUNDED_OUTLINES[shape]} fill={color} {...edge(0)} />;
    case "triangle":
      return <path d="M20 5.5 L36 33.5 L4 33.5 Z" fill={color} {...edge(5)} />;
    case "hexagon":
      return <path d="M20 3.5 L34.5 11.75 L34.5 28.25 L20 36.5 L5.5 28.25 L5.5 11.75 Z" fill={color} {...edge(3)} />;
    case "cloud":
      return <path d="M11 32 a7.5 7.5 0 0 1 -1 -14.9 A9.5 9.5 0 0 1 29 12.5 A7 7 0 0 1 30 32 Z" fill={color} {...edge(0)} />;
    case "drop":
      return <path d="M20 3 C20 3 6 20 6 27 a14 13.5 0 0 0 28 0 C34 20 20 3 20 3 Z" fill={color} {...edge(0)} />;
    default:
      return <circle cx={20} cy={20} fill={color} r={16.2} {...edge(0)} />;
  }
}

/** A monochrome thinking interlude, using the agent's own identity colour. */
export function SymbolThinkingDots({ color }: { color: string }) {
  return <g className="agent-symbol-thoughts" fill={color}>
    <circle cx={10} cy={22} r={3} /><circle cx={20} cy={22} r={4} /><circle cx={30} cy={22} r={3} />
  </g>;
}

/** The front projection of one accessory part in symbol units. */
function AccessoryPartShape({ part, color }: { part: AccessoryPart; color: string }): ReactNode {
  const fill = resolveFill(part.fill, color);
  const spin = "c" in part && part.rot ? `rotate(${part.rot} ${part.c[0]} ${part.c[1]})` : undefined;
  switch (part.t) {
    case "sphere":
      return <ellipse cx={part.c[0]} cy={part.c[1]} rx={part.r[0]} ry={part.r[1]} fill={fill} transform={spin} />;
    case "dome": {
      const [cx, cy] = part.c; const [rx, ry] = part.r;
      return <path d={`M${cx - rx} ${cy} A${rx} ${ry} 0 0 1 ${cx + rx} ${cy}Z`} fill={fill} transform={spin} />;
    }
    case "box": {
      const [w, h] = part.s;
      return <rect x={part.c[0] - w / 2} y={part.c[1] - h / 2} width={w} height={h} rx={Math.min(part.round, w / 2, h / 2)} fill={fill} transform={spin} />;
    }
    case "cyl": {
      const [cx, cy] = part.c; const [rb, rt] = part.r; const h = part.h / 2;
      return <path d={`M${cx - rb} ${cy + h} L${cx + rb} ${cy + h} L${cx + rt} ${cy - h} L${cx - rt} ${cy - h}Z`} fill={fill} transform={spin} />;
    }
    case "torus":
      return <ellipse cx={part.c[0]} cy={part.c[1]} rx={part.R} ry={Math.max(part.R * Math.abs(Math.cos(part.tilt * Math.PI / 180)), 0.15)} fill="none" stroke={fill} strokeWidth={part.r * 2} transform={spin} />;
    case "poly":
      return part.smooth ? <path d={smoothPath(part.pts, true)} fill={fill} /> : <polygon points={part.pts.map(p => p.join(",")).join(" ")} fill={fill} />;
    case "tube": {
      const flat = part.pts.map(p => [p[0], p[1]] as [number, number]);
      const d = part.smooth ? smoothPath(flat, false) : `M${flat.map(p => p.join(" ")).join(" L")}`;
      return <path d={d} fill="none" stroke={fill} strokeWidth={part.w} strokeLinecap="round" strokeLinejoin="round" />;
    }
    case "region":
      return <polygon points={part.pts.map(p => p.join(",")).join(" ")} fill={fill} />;
    case "rim":
      return null; // Drawn behind the body by AgentSymbol itself.
  }
}

/** One worn item at its slot anchor: either its body patches or its free parts. */
function AccessoryItemShapes({ item, shape, color, regions }: { item: AccessoryItem; shape: SymbolShape; color: string; regions: boolean }) {
  const parts = item.parts.filter(part => part.t !== "rim" && part.only !== "3d" && (part.t === "region") === regions);
  if (!parts.length) return null;
  const [anchorX, y, anchorK] = ACCESSORY_CATALOG.shapes[shape].anchors[item.slot];
  const k = anchorK * (item.scale ?? 1);
  const x = anchorX + (ACCESSORY_CATALOG.slotFlatShift[item.slot] ?? 0);
  // Paint back to front; body patches keep their authored layering.
  const ordered = regions ? parts : [...parts].sort((a, b) => partDepth(a) - partDepth(b));
  return <g data-accessory={item.id} transform={`translate(${x} ${y}) scale(${k})`}>
    {ordered.map((part, index) => part.opacity === undefined
      ? <AccessoryPartShape key={index} part={part} color={color} />
      : <g key={index} opacity={part.opacity}><AccessoryPartShape part={part} color={color} /></g>)}
  </g>;
}

/** Plain ink eyes share one resting gaze; only a working agent moves. */
export function AgentSymbol({ shape, color, size, eyes = "lines", thinking = false, accessories }: { shape: SymbolShape; color: string; size: number; eyes?: "dots" | "lines"; thinking?: boolean; accessories?: AccessoryChoice }) {
  const eyeY = shape === "cloud" || shape === "triangle" ? 23 : shape === "drop" ? 25 : 17.2;
  const ink = companionEyeColors(color);
  const maskId = `agent-body-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const worn = useMemo(() => wornAccessories(accessories), [accessories]);
  const viewBox = useMemo(() => symbolViewBox(shape, worn), [shape, worn]);
  const draw = (items: AccessoryItem[], regions = false) => items.map(item => <AccessoryItemShapes key={item.id} item={item} shape={shape} color={color} regions={regions} />);
  const faceTransform = `translate(2 -0.6) rotate(-14 20 ${eyeY})`;
  const onFace = worn.filter(item => FACE_SLOTS.has(item.slot));
  return (
    <svg aria-hidden focusable="false" data-agent-symbol={shape} data-thinking={thinking ? "true" : undefined} width={size} height={size} style={{ width: size, height: size, flexShrink: 0 }} viewBox={viewBox} className="society-agent-symbol block">
      <g className="agent-symbol-character">
        {draw(worn.filter(item => item.slot === "back"))}
        {worn.flatMap(item => item.parts.map((part, index) => {
          if (part.t !== "rim" || part.only === "3d") return null;
          // A hood frames the head only: clip the grown silhouette at maxY below the neckline.
          const [, anchorY, anchorK] = ACCESSORY_CATALOG.shapes[shape].anchors[item.slot];
          const clipId = `${maskId}-rim-${index}`;
          const bottom = part.maxY === undefined ? 90 : anchorY + part.maxY * anchorK;
          return <g key={`${item.id}-rim-${index}`} data-accessory-rim={item.id}>
            <clipPath id={clipId}><rect x={-30} y={-30} width={100} height={bottom + 30} /></clipPath>
            <g clipPath={`url(#${clipId})`}><SymbolBody shape={shape} color={resolveFill(part.fill, color)} grow={part.w} /></g>
          </g>;
        }))}
        <g data-agent-body><SymbolBody shape={shape} color={color} /></g>
        {worn.some(item => item.regions) && <>
          <mask id={maskId} maskUnits="userSpaceOnUse" x={-20} y={-20} width={80} height={84}><SymbolBody shape={shape} color="#ffffff" /></mask>
          <g mask={`url(#${maskId})`}>{draw(worn, true)}</g>
        </>}
        {draw(worn.filter(item => item.slot === "outfit" || item.slot === "neck"))}
        <g className="agent-symbol-gaze">
          <g data-agent-eyes fill={ink.eye} transform={faceTransform}>
            <g className="agent-symbol-lids">
              <ellipse cx={15.4} cy={eyeY} rx={eyes === "lines" ? 1.45 : 2} ry={eyes === "lines" ? 3.1 : 2.3} />
              <ellipse cx={24.6} cy={eyeY} rx={eyes === "lines" ? 1.45 : 2} ry={eyes === "lines" ? 3.1 : 2.3} />
            </g>
          </g>
          {onFace.length > 0 && <g transform={faceTransform}>{draw(onFace)}</g>}
        </g>
        {draw(worn.filter(item => item.slot === "head" || item.slot === "held"))}
      </g>
      {thinking && <SymbolThinkingDots color={color} />}
    </svg>
  );
}
