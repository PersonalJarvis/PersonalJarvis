/**
 * Sigil — the generative mark at the heart of the profile.
 *
 * `ledger.ts` describes the profile as a kept ledger and promises "a
 * generative sigil whose geometry is derived from which fields are inked".
 * This is that sigil.
 *
 * The geometry is not decoration with a random seed: every one of the
 * vocabulary fields owns exactly one spoke, laid out in `CLUSTER_ORDER` so
 * each cluster occupies a contiguous arc. A field that carries a value draws
 * a full stroke with a terminal dot; a blank one draws a short, faint tick.
 * The five outer arcs carry their cluster's fill ratio as opacity. Read the
 * mark and you have read the ledger — the counters below it only confirm it.
 *
 * Colour comes from `currentColor` throughout, so the mark inherits the
 * surrounding text colour and is correct in light and dark mode without a
 * single hardcoded value.
 */
import { useMemo } from "react";

import {
  CLUSTER_FIELD_KEYS,
  CLUSTER_ORDER,
  clusterFilledCount,
  isEmptyValue,
  type ClusterId,
} from "@/views/profile/ledger";

// Drawing space. The avatar sits in the middle, so every radius below is
// measured from (C, C) outwards and the inner disc is left empty.
const VIEW = 220;
const C = VIEW / 2;

const R_SPOKE_INNER = 76;
const R_SPOKE_OUTER = 94;
const R_TICK_INNER = 85;
const R_TICK_OUTER = 93;
const R_DOT = 99;
const R_ARC = 106;

/** Polar → cartesian, with 0° at twelve o'clock. */
function point(angleDeg: number, radius: number): [number, number] {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return [C + radius * Math.cos(rad), C + radius * Math.sin(rad)];
}

interface Spoke {
  field: string;
  cluster: ClusterId;
  inked: boolean;
  angle: number;
}

export function Sigil({
  meta,
  label,
  highlight,
  className,
}: {
  meta: Record<string, unknown>;
  /** Accessible description — the filled/total sentence the rail also shows. */
  label: string;
  /**
   * Field key currently pointed at in the ledger. Its spoke lights up, which
   * turns the mark into a live legend: hover a row and the ring shows you
   * where that fact sits in the whole.
   */
  highlight?: string | null;
  className?: string;
}) {
  const { spokes, arcs } = useMemo(() => {
    // One spoke per vocabulary field, in cluster order, so a cluster reads as
    // one continuous run around the ring instead of scattered ticks.
    const flat: { field: string; cluster: ClusterId; inked: boolean }[] = [];
    for (const cid of CLUSTER_ORDER) {
      const raw = meta[cid];
      const data = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
      for (const field of CLUSTER_FIELD_KEYS[cid]) {
        flat.push({ field, cluster: cid, inked: !isEmptyValue(data[field]) });
      }
    }

    const step = 360 / Math.max(flat.length, 1);
    const built: Spoke[] = flat.map((s, i) => ({ ...s, angle: i * step + step / 2 }));

    // One arc per cluster, spanning exactly the slots its fields occupy. A
    // small gap on each side keeps neighbouring clusters legible as separate
    // runs rather than one unbroken circle.
    const GAP = step * 0.22;
    let cursor = 0;
    const clusterArcs = CLUSTER_ORDER.map((cid) => {
      const count = CLUSTER_FIELD_KEYS[cid].length;
      const start = cursor * step + GAP;
      const end = (cursor + count) * step - GAP;
      cursor += count;
      const filled = clusterFilledCount(meta, cid);
      const ratio = count > 0 ? filled / count : 0;
      const [x1, y1] = point(start, R_ARC);
      const [x2, y2] = point(end, R_ARC);
      const large = end - start > 180 ? 1 : 0;
      return {
        cid,
        ratio,
        d: `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${R_ARC} ${R_ARC} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`,
      };
    });

    return { spokes: built, arcs: clusterArcs };
  }, [meta]);

  return (
    <svg
      viewBox={`0 0 ${VIEW} ${VIEW}`}
      className={className}
      role="img"
      aria-label={label}
    >
      {/* A hairline inside the spokes closes the composition, so the mark still
          reads as one object on a profile where almost nothing is written. */}
      <circle
        cx={C}
        cy={C}
        r={R_SPOKE_INNER - 5}
        fill="none"
        stroke="currentColor"
        strokeWidth={1}
        opacity={0.07}
      />

      {/* Cluster arcs — opacity carries how much of that cluster is written. */}
      {arcs.map((a) => (
        <path
          key={a.cid}
          d={a.d}
          fill="none"
          stroke="currentColor"
          strokeWidth={a.ratio > 0 ? 1.7 : 1}
          strokeLinecap="round"
          opacity={0.12 + a.ratio * 0.5}
          className="transition-all duration-700"
        />
      ))}

      {spokes.map((s) => {
        const lit = highlight === s.field;
        const [ix, iy] = point(s.angle, (s.inked ? R_SPOKE_INNER : R_TICK_INNER) - (lit ? 6 : 0));
        const [ox, oy] = point(s.angle, (s.inked ? R_SPOKE_OUTER : R_TICK_OUTER) + (lit ? 4 : 0));
        const [dx, dy] = point(s.angle, R_DOT);
        return (
          <g key={s.field}>
            <line
              x1={ix}
              y1={iy}
              x2={ox}
              y2={oy}
              stroke="currentColor"
              strokeWidth={lit ? 2.6 : s.inked ? 1.8 : 1.1}
              strokeLinecap="round"
              opacity={lit ? 1 : s.inked ? 0.95 : 0.22}
              className="transition-all duration-300"
            />
            {(s.inked || lit) && (
              <circle
                cx={dx}
                cy={dy}
                r={lit ? 3.2 : 2.4}
                fill="currentColor"
                opacity={lit ? 1 : 0.9}
                className="transition-all duration-300"
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}
