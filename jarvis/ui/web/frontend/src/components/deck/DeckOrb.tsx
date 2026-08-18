import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useEventStore } from "@/store/events";
import { VoiceOrb } from "@/components/agentic/VoiceOrb";
import { MascotGigi } from "@/components/MascotGigi";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { HudHaloDefs } from "@/components/deck/HudFrame";
import { cn } from "@/lib/utils";

/**
 * The centre of the deck: the voice orb with the mascot inside it, set in a
 * reticle — a dial ring, corner brackets, one bright arc per running step,
 * and four small readouts at the compass points.
 *
 * The orb is the app's own (`agentic/VoiceOrb`), driven by the real voice
 * state — it breathes, listens and speaks the way it does everywhere else in
 * the product, so the deck's centre is recognisably the assistant. The
 * reticle is the deck's addition, and every part of it carries information:
 * the arcs are parallel work made visible, the sweep turns only while
 * something runs, the readouts are live values the caller sources.
 */
export interface OrbReadouts {
  nw: string;
  ne: string;
  sw: string;
  se: string;
}

export function DeckOrb({
  steps,
  busy,
  size = 240,
  readouts,
  className,
}: {
  steps: ThinkingStep[];
  busy: boolean;
  size?: number;
  readouts?: OrbReadouts;
  className?: string;
}) {
  const voiceState = useEventStore((s) => s.voiceState);
  const reduced = useMemo(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    [],
  );

  const [sweep, setSweep] = useState(0);
  const raf = useRef<number | null>(null);
  useEffect(() => {
    if (!busy || reduced) return;
    let start: number | null = null;
    const tick = (ts: number) => {
      if (start === null) start = ts;
      setSweep(((ts - start) / 30) % 360);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    };
  }, [busy, reduced]);

  const R = size / 2;
  const orbSize = Math.round(size * 0.58);
  const point = (deg: number, r: number): [number, number] => {
    const rad = ((deg - 90) * Math.PI) / 180;
    return [R + r * Math.cos(rad), R + r * Math.sin(rad)];
  };

  const arcs = steps.slice(0, 8).map((step, i, all) => {
    const span = 320 / Math.max(1, all.length);
    const a0 = -160 + i * span + 3;
    const a1 = a0 + span - 6;
    const [x0, y0] = point(a0, R * 0.84);
    const [x1, y1] = point(a1, R * 0.84);
    return {
      id: step.id,
      d: `M ${x0} ${y0} A ${R * 0.84} ${R * 0.84} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1} ${y1}`,
      note: step.kind === "note",
    };
  });

  const [sx, sy] = point(sweep, R * 0.94);
  const [ex, ey] = point(sweep + 36, R * 0.94);
  const B = 16; // bracket arm
  const haloId = useId();

  return (
    <div className={cn("relative shrink-0", className)} style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="absolute inset-0 h-full w-full" aria-hidden>
        <HudHaloDefs id={haloId} />
        <g filter={`url(#${haloId})`}>
        {/* corner brackets — the reticle's bounds */}
        {[
          `M 0.5 ${B} V 0.5 H ${B}`,
          `M ${size - B} 0.5 H ${size - 0.5} V ${B}`,
          `M ${size - 0.5} ${size - B} V ${size - 0.5} H ${size - B}`,
          `M ${B} ${size - 0.5} H 0.5 V ${size - B}`,
        ].map((d) => (
          <path key={d} d={d} fill="none" stroke="hsl(var(--primary))" strokeWidth={1.5} opacity={0.6} />
        ))}

        {/* outer dial: 72 ticks, long every 15° */}
        {Array.from({ length: 72 }, (_, i) => {
          const deg = i * 5;
          const long = i % 3 === 0;
          const [ax, ay] = point(deg, R * 0.94);
          const [bx, by] = point(deg, R * (long ? 0.885 : 0.91));
          return (
            <line
              key={deg}
              x1={ax}
              y1={ay}
              x2={bx}
              y2={by}
              stroke="hsl(var(--primary))"
              strokeWidth={1}
              opacity={long ? 0.5 : 0.18}
            />
          );
        })}
        {/* crosshair ticks at the compass points, outside the dial */}
        {[0, 90, 180, 270].map((deg) => {
          const [ax, ay] = point(deg, R * 0.96);
          const [bx, by] = point(deg, R * 1.0);
          return <line key={deg} x1={ax} y1={ay} x2={bx} y2={by} stroke="hsl(var(--primary))" strokeWidth={1.5} opacity={0.7} />;
        })}

        {/* inner scale ring around the orb */}
        <circle cx={R} cy={R} r={R * 0.84} fill="none" stroke="hsl(var(--border))" strokeWidth={1} opacity={0.7} />
        <circle
          cx={R}
          cy={R}
          r={R * 0.66}
          fill="none"
          stroke="hsl(var(--primary))"
          strokeWidth={1}
          strokeDasharray="2 6"
          opacity={0.35}
        />

        {arcs.map((a) => (
          <path
            key={a.id}
            d={a.d}
            fill="none"
            stroke={a.note ? "hsl(var(--muted-foreground))" : "hsl(var(--primary))"}
            strokeWidth={3}
            strokeLinecap="butt"
            opacity={0.95}
          />
        ))}
        {busy && !reduced && (
          <path
            d={`M ${sx} ${sy} A ${R * 0.94} ${R * 0.94} 0 0 1 ${ex} ${ey}`}
            fill="none"
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            strokeLinecap="butt"
            opacity={0.6}
          />
        )}
        </g>
      </svg>

      <div className="absolute inset-0 grid place-items-center">
        <div className="relative" style={{ width: orbSize, height: orbSize }}>
          <VoiceOrb state={voiceState} size={orbSize} className="absolute inset-0" />
          <div className="absolute inset-0 grid place-items-center">
            <MascotGigi size={Math.round(orbSize * 0.46)} reactToVoice enableComments={false} />
          </div>
        </div>
      </div>

      {readouts && (
        <>
          <Readout className="left-1 top-1 text-left" text={readouts.nw} />
          <Readout className="right-1 top-1 text-right" text={readouts.ne} />
          <Readout className="bottom-1 left-1 text-left" text={readouts.sw} />
          <Readout className="bottom-1 right-1 text-right" text={readouts.se} />
        </>
      )}
    </div>
  );
}

function Readout({ text, className }: { text: string; className?: string }) {
  return (
    <span
      className={cn(
        "pointer-events-none absolute max-w-[42%] truncate px-1.5 font-mono text-[9px] uppercase tracking-[0.16em] text-primary/90",
        className,
      )}
    >
      {text}
    </span>
  );
}
