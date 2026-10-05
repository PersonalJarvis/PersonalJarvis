/**
 * A rank's insignia as an SVG: embroidered gold on navy for enlisted ranks,
 * struck metal for officers, silver stars for generals. Drawn from the same
 * polygons as the 3D pieces (`rankArt.ts`). Private (E-1) wears no insignia,
 * so it shows an empty stitched outline. Decorative: the rank name always
 * sits next to it.
 */
import { useId, type CSSProperties } from "react";
import type { RankId } from "../levelCatalog";
import { FINISH_TONES, rankArt, svgPoints, type Finish } from "./rankArt";

const FINISHES: Finish[] = ["gold", "silver", "cloth"];

export function RankInsignia({ rank, size = 32, dim = false, reveal = false, className }: {
  rank: RankId;
  /** Height in px; the width follows the insignia's own shape. */
  size?: number;
  /** A rank not reached yet: drawn as a quiet silhouette. */
  dim?: boolean;
  /** Lay the pieces on one after another (CSS: `.rank-part` with `--i`, the piece's place in the stack). */
  reveal?: boolean;
  className?: string;
}) {
  const id = useId().replace(/:/g, "");
  const art = rankArt(rank);
  const pad = 4;
  const vbH = art.h + pad * 2;
  const vbW = art.w + pad * 2;
  const width = (size * vbW) / vbH;
  if (art.parts.length === 0) {
    return (
      <svg className={className} viewBox={`0 0 ${vbW} ${vbH}`} width={width} height={size} aria-hidden data-rank={rank}>
        <rect x={pad + 18} y={pad + 22} width={64} height={56} rx={10} fill="none" stroke="currentColor" strokeOpacity={0.45}
          strokeWidth={3} strokeDasharray="6 5" />
      </svg>
    );
  }
  return (
    <svg className={className} viewBox={`${-pad} ${-pad} ${vbW} ${vbH}`} width={width} height={size} aria-hidden data-rank={rank}
      data-dim={dim || undefined}>
      <defs>
        {FINISHES.map((finish) => {
          const tone = FINISH_TONES[finish];
          return (
            <linearGradient key={finish} id={`${id}-${finish}`} x1="0" y1="0" x2="0.35" y2="1">
              <stop offset="0" stopColor={tone.light} />
              <stop offset="0.45" stopColor={tone.mid} />
              <stop offset="1" stopColor={tone.dark} />
            </linearGradient>
          );
        })}
      </defs>
      <g opacity={dim ? 0.45 : 1} style={dim ? { filter: "grayscale(1)" } : undefined}>
        {art.parts.map((part, i) => {
          const tone = FINISH_TONES[part.finish];
          const order = reveal ? { className: "rank-part", style: { "--i": i } as CSSProperties } : {};
          if (part.detail) return <polygon key={i} points={svgPoints(part.pts)} fill={tone.dark} fillOpacity={0.55} {...order} />;
          return (
            <polygon key={i} points={svgPoints(part.pts)} fill={`url(#${id}-${part.finish})`} stroke={tone.edge}
              strokeWidth={part.finish === "cloth" ? 1.6 : 1.1} strokeLinejoin="round" {...order} />
          );
        })}
      </g>
    </svg>
  );
}
