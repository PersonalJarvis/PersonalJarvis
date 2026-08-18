import { useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { cn } from "@/lib/utils";

/**
 * The deck's frame language — what makes a card read as an instrument on a
 * mission deck rather than a box on a web page.
 *
 * Three silhouettes, chosen per card so the stage is not a grid of identical
 * rectangles (the maintainer's complaint of 2026-08-17):
 *
 *  - `bracket` — no full border. Four corner brackets and faint edges between
 *    them. The lightest frame; for pictures and anything that should float.
 *  - `chamfer` — a full hairline outline with the top-right and bottom-left
 *    corners cut, and a tick ruler along the bottom. For readouts and lists.
 *  - `rail`   — a bright left rail with tick marks and a faint top rule. For
 *    scrolling streams (the reasoning trace, terminals).
 *
 * All of it is ONE SVG overlay drawn from the measured size, so the strokes
 * stay hairlines at any width, take their colour from theme tokens (gold on
 * both grounds), and never hide the artwork behind the card. Nothing here is
 * decorative for its own sake: the tick rulers give the eye a scale, the
 * corner brackets say "this is a bounded instrument", the lamp says whether
 * it is live.
 */
export type HudVariant = "bracket" | "chamfer" | "rail";
export type HudTone = "primary" | "destructive" | "muted";

export function useElementSize<T extends HTMLElement>(): [RefObject<T>, { w: number; h: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setSize((s) =>
        Math.abs(s.w - r.width) < 1 && Math.abs(s.h - r.height) < 1 ? s : { w: r.width, h: r.height },
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

/**
 * A soft halo in the theme's own ground colour under every HUD stroke — the
 * SVG twin of the stage's text-shadow readability floor. Gold hairlines on a
 * light theme over a dark picture (or the reverse) would otherwise vanish;
 * with the halo they read on any wallpaper in either appearance.
 */
export function HudHaloDefs({ id }: { id: string }) {
  return (
    <defs>
      <filter id={id} x="-10%" y="-10%" width="120%" height="120%">
        <feDropShadow
          dx="0"
          dy="0"
          stdDeviation="1.1"
          floodOpacity="0.85"
          style={{ floodColor: "hsl(var(--background))" }}
        />
      </filter>
    </defs>
  );
}

const CUT = 10; // px — chamfer size
const BRACKET = 14; // px — corner bracket arm length
const TICK_STEP = 24; // px — ruler tick spacing

function ticksAlong(length: number, step = TICK_STEP): number[] {
  const out: number[] = [];
  for (let x = step; x < length - step / 2; x += step) out.push(x);
  return out;
}

function toneStroke(tone: HudTone): string {
  if (tone === "destructive") return "hsl(var(--destructive))";
  if (tone === "muted") return "hsl(var(--muted-foreground))";
  return "hsl(var(--primary))";
}

export function HudFrameOverlay({
  variant,
  w,
  h,
  live,
  tone = "primary",
}: {
  variant: HudVariant;
  w: number;
  h: number;
  live?: boolean;
  tone?: HudTone;
}) {
  const haloId = useId();
  if (w < 4 || h < 4) return null;
  const stroke = toneStroke(tone);
  const faint = "hsl(var(--border))";
  const main = live ? 0.95 : 0.6;

  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      aria-hidden
    >
      <HudHaloDefs id={haloId} />
      <g filter={`url(#${haloId})`}>
      {variant === "bracket" && (
        <>
          {[
            `M 0.5 ${BRACKET} V 0.5 H ${BRACKET}`,
            `M ${w - BRACKET} 0.5 H ${w - 0.5} V ${BRACKET}`,
            `M ${w - 0.5} ${h - BRACKET} V ${h - 0.5} H ${w - BRACKET}`,
            `M ${BRACKET} ${h - 0.5} H 0.5 V ${h - BRACKET}`,
          ].map((d) => (
            <path key={d} d={d} fill="none" stroke={stroke} strokeWidth={1.5} opacity={main} />
          ))}
          <path d={`M ${BRACKET + 4} 0.5 H ${w - BRACKET - 4}`} stroke={faint} strokeWidth={1} opacity={0.7} />
          <path d={`M ${BRACKET + 4} ${h - 0.5} H ${w - BRACKET - 4}`} stroke={faint} strokeWidth={1} opacity={0.5} />
        </>
      )}

      {variant === "chamfer" && (
        <>
          <path
            d={`M 0.5 0.5 H ${w - CUT} L ${w - 0.5} ${CUT} V ${h - 0.5} H ${CUT} L 0.5 ${h - CUT} Z`}
            fill="none"
            stroke={stroke}
            strokeWidth={1}
            opacity={main}
          />
          <path d={`M ${w - CUT} 0.5 L ${w - 0.5} ${CUT}`} stroke={stroke} strokeWidth={2} opacity={0.9} />
          <path d={`M ${CUT} ${h - 0.5} L 0.5 ${h - CUT}`} stroke={stroke} strokeWidth={2} opacity={0.9} />
          {ticksAlong(w).map((x) => (
            <line
              key={x}
              x1={x}
              y1={h - 1}
              x2={x}
              y2={h - (x % (TICK_STEP * 4) === 0 ? 6 : 3)}
              stroke={stroke}
              strokeWidth={1}
              opacity={0.35}
            />
          ))}
        </>
      )}

      {variant === "rail" && (
        <>
          <line x1={1} y1={0} x2={1} y2={h} stroke={stroke} strokeWidth={2} opacity={main} />
          {ticksAlong(h, 18).map((y) => (
            <line
              key={y}
              x1={2}
              y1={y}
              x2={y % 72 === 0 ? 9 : 5}
              y2={y}
              stroke={stroke}
              strokeWidth={1}
              opacity={0.4}
            />
          ))}
          <path d={`M 2 0.5 H ${w - 0.5}`} stroke={faint} strokeWidth={1} opacity={0.7} />
          <path d={`M ${w - 0.5} 0.5 V ${Math.min(h, 22)}`} stroke={stroke} strokeWidth={1} opacity={0.6} />
        </>
      )}
      </g>
    </svg>
  );
}

/** A tiny square status lamp — the deck's "is this live" idiom. */
export function HudLamp({
  on,
  tone = "primary",
  className,
}: {
  on: boolean;
  tone?: HudTone;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block h-1.5 w-1.5 shrink-0",
        on
          ? tone === "destructive"
            ? "bg-destructive shadow-[0_0_6px_1px_hsl(var(--destructive)/0.6)]"
            : "bg-primary shadow-[0_0_6px_1px_hsl(var(--primary)/0.6)]"
          : "bg-muted-foreground/40",
        className,
      )}
    />
  );
}

/**
 * An arc gauge — the deck's way to show one bounded quantity at a glance.
 * `value` in 0..1; the readout in the middle is the caller's, and it must be
 * a real number: a gauge with an invented value is worse than no gauge.
 */
export function HudGauge({
  value,
  size = 64,
  label,
  readout,
  className,
}: {
  value: number;
  size?: number;
  label: string;
  readout: string;
  className?: string;
}) {
  const v = Math.max(0, Math.min(1, value));
  const r = size / 2 - 4;
  const c = size / 2;
  const start = -135;
  const sweep = 270;
  const point = (deg: number, rad: number): [number, number] => {
    const a = ((deg - 90) * Math.PI) / 180;
    return [c + rad * Math.cos(a), c + rad * Math.sin(a)];
  };
  const [sx, sy] = point(start, r);
  const [ex, ey] = point(start + sweep, r);
  const [vx, vy] = point(start + sweep * v, r);
  const ticks = Array.from({ length: 10 }, (_, i) => start + (sweep * i) / 9);
  return (
    <div className={cn("flex flex-col items-center", className)}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <path
          d={`M ${sx} ${sy} A ${r} ${r} 0 1 1 ${ex} ${ey}`}
          fill="none"
          stroke="hsl(var(--border))"
          strokeWidth={3}
        />
        {v > 0.005 && (
          <path
            d={`M ${sx} ${sy} A ${r} ${r} 0 ${sweep * v > 180 ? 1 : 0} 1 ${vx} ${vy}`}
            fill="none"
            stroke="hsl(var(--primary))"
            strokeWidth={3}
          />
        )}
        {ticks.map((deg) => {
          const [ax, ay] = point(deg, r - 6);
          const [bx, by] = point(deg, r - 9);
          return (
            <line
              key={deg}
              x1={ax}
              y1={ay}
              x2={bx}
              y2={by}
              stroke="hsl(var(--muted-foreground))"
              strokeWidth={1}
              opacity={0.6}
            />
          );
        })}
        <text
          x={c}
          y={c + 4}
          textAnchor="middle"
          className="fill-foreground font-mono"
          style={{ fontSize: size * 0.2 }}
        >
          {readout}
        </text>
      </svg>
      <span className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
        {label}
      </span>
    </div>
  );
}

/** Measures itself and draws the frame overlay behind its children. */
export function HudFrame({
  variant,
  live,
  tone,
  className,
  children,
}: {
  variant: HudVariant;
  live?: boolean;
  tone?: HudTone;
  className?: string;
  children: ReactNode;
}) {
  const [ref, size] = useElementSize<HTMLDivElement>();
  return (
    <div ref={ref} className={cn("relative", className)}>
      <HudFrameOverlay variant={variant} w={size.w} h={size.h} live={live} tone={tone} />
      {children}
    </div>
  );
}
