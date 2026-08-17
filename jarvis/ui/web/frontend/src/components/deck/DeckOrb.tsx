import { useEffect, useMemo, useRef, useState } from "react";
import { useEventStore } from "@/store/events";
import { VoiceOrb } from "@/components/agentic/VoiceOrb";
import { MascotGigi } from "@/components/MascotGigi";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { cn } from "@/lib/utils";

/**
 * The centre of the deck: the voice orb with the mascot inside it, ringed by
 * one arc per running step.
 *
 * The orb is the app's own (`agentic/VoiceOrb`), driven by the real voice
 * state — it breathes, listens and speaks the way it does everywhere else in
 * the product, so the deck's centre is recognisably the assistant and not a
 * new mascot. The mascot sits on top of it; the arcs are the deck's addition:
 * every active reasoning step (a tool, a worker, a look at the screen) is one
 * bright arc, so parallel work is visible as parallel arcs.
 *
 * Motion only while something runs. An idle orb still breathes (that is the
 * VoiceOrb's own idle), but the sweep and the arcs stop.
 */
export function DeckOrb({
  steps,
  busy,
  size = 260,
  className,
}: {
  steps: ThinkingStep[];
  busy: boolean;
  size?: number;
  className?: string;
}) {
  const voiceState = useEventStore((s) => s.voiceState);
  const reduced = useMemo(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    [],
  );

  // A slow sweep that only turns while something runs.
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
  const orbSize = Math.round(size * 0.62);
  const point = (deg: number, r: number): [number, number] => {
    const rad = ((deg - 90) * Math.PI) / 180;
    return [R + r * Math.cos(rad), R + r * Math.sin(rad)];
  };

  const arcs = steps.slice(0, 8).map((step, i, all) => {
    const span = 320 / Math.max(1, all.length);
    const a0 = -160 + i * span + 3;
    const a1 = a0 + span - 6;
    const [x0, y0] = point(a0, R * 0.86);
    const [x1, y1] = point(a1, R * 0.86);
    return {
      id: step.id,
      d: `M ${x0} ${y0} A ${R * 0.86} ${R * 0.86} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1} ${y1}`,
      note: step.kind === "note",
    };
  });

  const [sx, sy] = point(sweep, R * 0.95);
  const [ex, ey] = point(sweep + 36, R * 0.95);

  return (
    <div className={cn("relative shrink-0", className)} style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="absolute inset-0 h-full w-full" aria-hidden>
        {/* Scale ring: 60 ticks, brighter every 15° — a dial, not a decoration:
            it is what the arcs and the sweep are read against. */}
        {Array.from({ length: 60 }, (_, i) => {
          const deg = i * 6;
          const [ax, ay] = point(deg, R * 0.72);
          const [bx, by] = point(deg, R * 0.76);
          return (
            <line
              key={deg}
              x1={ax}
              y1={ay}
              x2={bx}
              y2={by}
              stroke="hsl(var(--primary))"
              strokeWidth={1}
              opacity={i % 5 === 0 ? 0.45 : 0.14}
            />
          );
        })}
        <circle
          cx={R}
          cy={R}
          r={R * 0.86}
          fill="none"
          stroke="hsl(var(--border))"
          strokeWidth={1}
          opacity={0.6}
        />
        {arcs.map((a) => (
          <path
            key={a.id}
            d={a.d}
            fill="none"
            stroke={a.note ? "hsl(var(--muted-foreground))" : "hsl(var(--primary))"}
            strokeWidth={3}
            strokeLinecap="round"
            opacity={0.9}
          />
        ))}
        {busy && !reduced && (
          <path
            d={`M ${sx} ${sy} A ${R * 0.95} ${R * 0.95} 0 0 1 ${ex} ${ey}`}
            fill="none"
            stroke="hsl(var(--primary))"
            strokeWidth={1.5}
            strokeLinecap="round"
            opacity={0.55}
          />
        )}
      </svg>

      <div className="absolute inset-0 grid place-items-center">
        <div className="relative" style={{ width: orbSize, height: orbSize }}>
          <VoiceOrb state={voiceState} size={orbSize} className="absolute inset-0" />
          <div className="absolute inset-0 grid place-items-center">
            <MascotGigi size={Math.round(orbSize * 0.42)} reactToVoice enableComments={false} />
          </div>
        </div>
      </div>
    </div>
  );
}
