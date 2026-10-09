/**
 * The 2D drawing of a companion design (skins.ts) on an AgentSymbol: the
 * gradient the body is filled with, plus its effect. Every effect is a few
 * small SVG shapes moved by compositor-only CSS (skinLayers.css); reduced
 * motion leaves a still frame of each.
 */
import type { ReactNode } from "react";
import { angleVector, skinEffect, skinHighlight, type CompanionSkin } from "./skins";
import "./skinLayers.css";

/** Star specks inside the body, in symbol units (40 × 40): x, y, radius. */
const STARS: readonly (readonly [number, number, number])[] = [
  [12, 10, 0.7], [27, 8.5, 0.5], [31, 17, 0.8], [9, 21, 0.55], [17, 27, 0.75],
  [25, 25, 0.45], [33, 27, 0.55], [14, 33, 0.5], [22, 33.5, 0.65], [20, 13.5, 0.4], [7, 15, 0.4],
];
/** Four-point glints around the silhouette. */
const SPARKLES: readonly (readonly [number, number, number])[] = [[6.5, 7, 1], [34.5, 11, 0.8], [33, 34, 0.9]];
const GLINT = "M0 -3.2 L0.75 -0.75 L3.2 0 L0.75 0.75 L0 3.2 L-0.75 0.75 L-3.2 0 L-0.75 -0.75Z";
/** One loop of the flowing gradient, in symbol units. */
const FLOW_PERIOD = 40;

function Stops({ colors, cyclic = false }: { colors: readonly string[]; cyclic?: boolean }) {
  const ring = cyclic ? [...colors, colors[0]!] : colors;
  return <>{ring.map((color, index) => <stop key={index} offset={index / (ring.length - 1)} stopColor={color} />)}</>;
}

/** The body's gradient, referenced as `url(#id)`. */
export function SkinDefs({ id, skin }: { id: string; skin: CompanionSkin }) {
  const [x1, y1, x2, y2] = angleVector(skin.angle);
  return <defs>
    {skin.pattern === "radial"
      ? <radialGradient id={id} cx="0.38" cy="0.34" r="0.78"><Stops colors={skin.colors} /></radialGradient>
      : <linearGradient id={id} x1={x1} y1={y1} x2={x2} y2={y2}><Stops colors={skin.colors} /></linearGradient>}
  </defs>;
}

/** A soft pulsing halo of the design's brightest colour, drawn behind the body. */
export function SkinGlow({ id, skin, body }: { id: string; skin: CompanionSkin; body: (fill: string) => ReactNode }) {
  if (skinEffect(skin) !== "glow") return null;
  return <g data-skin-glow>
    <defs><filter id={`${id}-glow`} x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation={2.4} /></filter></defs>
    <g className="agent-skin-glow" filter={`url(#${id}-glow)`}>{body(skinHighlight(skin.colors))}</g>
  </g>;
}

/**
 * The effect on top of the filled body: clipped to the silhouette through a
 * mask of the same body, so stars, the shine and the flowing colours never
 * spill past the edge. Sparkles sit outside on purpose.
 */
export function SkinOverlay({ id, skin, body }: { id: string; skin: CompanionSkin; body: (fill: string) => ReactNode }) {
  const effect = skinEffect(skin);
  if (effect === "none" || effect === "glow") return null;
  if (effect === "sparkle") {
    return <g data-skin-effect="sparkle" fill={skinHighlight(skin.colors)} stroke={skin.colors[skin.colors.length - 1]} strokeWidth={0.35} strokeLinejoin="round">
      {SPARKLES.map(([x, y, k], index) => <g key={index} transform={`translate(${x} ${y}) scale(${k})`}>
        <path className="agent-skin-glint" d={GLINT} style={{ animationDelay: `${-index * 1.1}s` }} />
      </g>)}
    </g>;
  }
  const maskId = `${id}-mask`;
  return <g data-skin-effect={effect}>
    <mask id={maskId} maskUnits="userSpaceOnUse" x={-20} y={-20} width={80} height={84}>{body("#ffffff")}</mask>
    <g mask={`url(#${maskId})`}>
      {effect === "stars" && <g className="agent-skin-stars" fill="#ffffff">
        {STARS.map(([x, y, r], index) => <circle key={index} cx={x} cy={y} r={r} style={{ animationDelay: `${-index * 0.43}s` }} />)}
      </g>}
      {effect === "shimmer" && <>
        <defs><linearGradient id={`${id}-shine`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0" /><stop offset="0.5" stopColor="#ffffff" stopOpacity="0.75" /><stop offset="1" stopColor="#ffffff" stopOpacity="0" />
        </linearGradient></defs>
        <g transform="skewX(-22)"><rect className="agent-skin-shine" x={-14} y={-10} width={13} height={64} fill={`url(#${id}-shine)`} /></g>
      </>}
      {effect === "flow" && <>
        <defs><linearGradient id={`${id}-flow`} gradientUnits="userSpaceOnUse" x1={0} y1={0} x2={FLOW_PERIOD} y2={0} spreadMethod="repeat">
          <Stops colors={skin.colors} cyclic />
        </linearGradient></defs>
        {/* Along the design's own direction: CSS 90° (to the right) is the x axis. */}
        <g transform={`rotate(${skin.angle - 90} 20 20)`}>
          <rect className="agent-skin-flow" x={-60} y={-60} width={160} height={160} fill={`url(#${id}-flow)`} />
        </g>
      </>}
    </g>
  </g>;
}
