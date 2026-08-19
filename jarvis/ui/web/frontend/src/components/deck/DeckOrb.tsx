import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useEventStore, type VoiceState } from "@/store/events";
import { JarvisOrb } from "@/components/deck/JarvisOrb";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { HudHaloDefs } from "@/components/deck/HudFrame";
import { readVoiceInputLevel } from "@/lib/voiceInputLevel";
import { driveTarget, isOnset, orbDriveFor, smoothOrbLevel } from "@/lib/orbLevel";
import { cn } from "@/lib/utils";

/**
 * The centre of the deck: the Jarvis orb in a reticle — a dial ring, corner
 * brackets, one bright arc per running step, and four small readouts at the
 * compass points.
 *
 * The orb is the product's own artwork (`JarvisOrb`: the sphere cut out of
 * `hero-orb.png`), moved a little by the real voice state, with a soft gold
 * glow carrying it past its own edge. The maintainer asked for the picture
 * itself in the middle (2026-08-18) after the procedural cloud with a dark
 * mascot on it read as a blob. A mascot later rode in the core for a day
 * and came back out (2026-08-19): two ghosts on one stage. The reticle is
 * the deck's addition, and every part of it carries information: the arcs
 * are parallel work made visible, the sweep turns only while something
 * runs, the readouts are live values the caller sources.
 *
 * And it is ALIVE at rest (maintainer, 2026-08-19: the still reticle looked
 * boring on both stages): the inner scale turns slowly one way, a sparse
 * orbit the other, a satellite rides the bezel, the glow breathes, and while
 * the assistant is idle a faint ping leaves the orb every few seconds — the
 * listening heartbeat. Calm, not busy: slow periods, low opacities, nothing
 * that competes with a real signal (the satellite steps aside for the busy
 * sweep). Each living part is its own small layer — a root `<svg>` or a div
 * with a CSS transform/opacity animation (index.css) — so the browser turns
 * it on the compositor and the main thread never hears of it; the halo
 * filter stays on the still reticle. Reduced motion stops all of it.
 *
 * And it MOVES WITH THE VOICE, all of it (maintainer, 2026-08-19: "when you
 * speak the sun moved — that, on the next level; not just the core, all of
 * it, smooth"). One animation-frame loop computes the orb's LEVEL
 * (`lib/orbLevel.ts`: the real microphone while listening, a speech-shaped
 * envelope while the assistant speaks, a heartbeat while it thinks), smooths
 * it, and writes it to ONE CSS variable on the root (`--orb-level`); the
 * core's size and brightness, the glow, the corona's rays, the rings and the
 * level arc on the bezel all read that variable in CSS (index.css). A jump
 * in the level — a word landing — sends one ripple from the sun to the
 * bezel. One number, one smoothing, so every part moves as one thing.
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
  onPress,
  pressLabel,
  pressDisabled = false,
}: {
  steps: ThinkingStep[];
  busy: boolean;
  size?: number;
  readouts?: OrbReadouts;
  className?: string;
  /**
   * The click-shaped wake word: pressing the orb does what
   * saying the wake phrase does — starts the conversation, or ends the one
   * that runs. Without it the orb is display only.
   */
  onPress?: () => void;
  /** What the press does right now — the accessible name and the tooltip. */
  pressLabel?: string;
  pressDisabled?: boolean;
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
  const orbSize = Math.round(size * 0.78);
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

  // The level loop: one number for everything that moves with the voice,
  // written to the root as `--orb-level`. Runs only while a voice state
  // drives it; idle (and reduced motion) is a still 0 and no loop at all.
  const rootRef = useRef<HTMLDivElement>(null);
  const ripplesRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const drive = orbDriveFor(voiceState);
    if (reduced || drive === "idle") {
      root.style.setProperty("--orb-level", "0");
      return;
    }
    let raf = 0;
    let level = 0;
    const t0 = performance.now();
    let last = t0;
    let lastRipple = Number.NEGATIVE_INFINITY;
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const target = driveTarget(drive, (now - t0) / 1000, readVoiceInputLevel(now));
      const next = smoothOrbLevel(level, target, dt);
      if (isOnset(level, next, now - lastRipple)) {
        lastRipple = now;
        spawnRipple(ripplesRef.current);
      }
      level = next;
      root.style.setProperty("--orb-level", level.toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      root.style.setProperty("--orb-level", "0");
    };
  }, [voiceState, reduced]);

  return (
    <div
      ref={rootRef}
      className={cn("deck-orb-root relative shrink-0", className)}
      style={{ width: size, height: size }}
      data-testid="deck-orb"
    >
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
          <path key={d} d={d} fill="none" stroke="hsl(var(--primary))" strokeWidth={1.25} opacity={0.8} />
        ))}

        {/* bezel — the hairline the ticks hang from */}
        <circle cx={R} cy={R} r={R * 0.94} fill="none" stroke="hsl(var(--primary))" strokeWidth={1} opacity={0.5} />

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
              opacity={long ? 0.78 : 0.42}
            />
          );
        })}
        {/* crosshair ticks at the compass points, outside the dial */}
        {[0, 90, 180, 270].map((deg) => {
          const [ax, ay] = point(deg, R * 0.96);
          const [bx, by] = point(deg, R * 1.0);
          return <line key={deg} x1={ax} y1={ay} x2={bx} y2={by} stroke="hsl(var(--primary))" strokeWidth={1.5} opacity={0.9} />;
        })}

        {/* inner scale ring around the orb (the dashed scale inside it turns — see below) */}
        <circle cx={R} cy={R} r={R * 0.84} fill="none" stroke="hsl(var(--primary))" strokeWidth={1} opacity={0.5} />

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
            opacity={0.7}
          />
        )}
        </g>
      </svg>

      {/* The living parts, each its own layer (CSS, index.css): the dashed
          inner scale turning one way, a sparse orbit the other, a satellite
          on the bezel while nothing runs, and the idle ping. The rings sit
          in one wrapper that swells and brightens with the level. */}
      <div className="deck-orb-rings pointer-events-none absolute inset-0" aria-hidden>
        <OrbitLayer size={size} r={R * 0.66} className="deck-orb-orbit-a" dash="2 6" opacity={0.55} />
        <OrbitLayer size={size} r={R * 0.76} className="deck-orb-orbit-b" dash="18 54" opacity={0.32} />
        {!busy && (
          <svg
            viewBox={`0 0 ${size} ${size}`}
            className="deck-orb-satellite pointer-events-none absolute inset-0 h-full w-full"
            data-testid="deck-orb-satellite"
          >
            <path
              d={`M ${point(-16, R * 0.94)[0]} ${point(-16, R * 0.94)[1]} A ${R * 0.94} ${R * 0.94} 0 0 1 ${R} ${R - R * 0.94}`}
              fill="none"
              stroke="hsl(var(--primary))"
              strokeWidth={1.5}
              opacity={0.45}
            />
            <circle cx={R} cy={R - R * 0.94} r={2.2} fill="hsl(var(--primary))" />
          </svg>
        )}
      </div>
      {voiceState === "idle" && (
        <div
          aria-hidden
          data-testid="deck-orb-ping"
          className="deck-orb-ping pointer-events-none absolute rounded-full"
          style={{ inset: size * 0.08 }}
        />
      )}

      {/* The level arc on the bezel: grows from the top down both sides with
          the voice — a meter, reading `--orb-level` (CSS). */}
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="deck-orb-vu pointer-events-none absolute inset-0 h-full w-full"
        aria-hidden
        data-testid="deck-orb-vu"
      >
        <path
          d={`M ${R} ${R - R * 0.94} A ${R * 0.94} ${R * 0.94} 0 0 1 ${R} ${R + R * 0.94}`}
          pathLength={1}
          fill="none"
          stroke="hsl(var(--primary))"
          strokeWidth={3}
          strokeLinecap="round"
        />
        <path
          d={`M ${R} ${R - R * 0.94} A ${R * 0.94} ${R * 0.94} 0 0 0 ${R} ${R + R * 0.94}`}
          pathLength={1}
          fill="none"
          stroke="hsl(var(--primary))"
          strokeWidth={3}
          strokeLinecap="round"
        />
      </svg>

      {/* Ripples: one ring per word landing, from the sun to the bezel.
          Spawned by the level loop, gone when their animation ends. */}
      <div
        ref={ripplesRef}
        className="pointer-events-none absolute inset-0 grid place-items-center"
        aria-hidden
        data-testid="deck-orb-ripples"
      />

      <div className="absolute inset-0 grid place-items-center">
        {onPress ? (
          <button
            type="button"
            onClick={onPress}
            disabled={pressDisabled}
            aria-label={pressLabel}
            title={pressLabel}
            className={cn(
              "relative rounded-full transition-transform duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              pressDisabled
                ? "cursor-wait"
                : "cursor-pointer hover:scale-[1.04] active:scale-[0.97] motion-reduce:transform-none",
            )}
            style={{ width: orbSize, height: orbSize }}
          >
            <OrbFace voiceState={voiceState} orbSize={orbSize} />
          </button>
        ) : (
          <div className="relative" style={{ width: orbSize, height: orbSize }}>
            <OrbFace voiceState={voiceState} orbSize={orbSize} />
          </div>
        )}
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

/**
 * The orb — the part of the centre a press lands on.
 *
 * Back to front: a soft gold glow wider than the sphere (`.deck-orb-glow`,
 * keyed on the voice state), then the artwork itself (`JarvisOrb`).
 */
function OrbFace({ voiceState, orbSize }: { voiceState: VoiceState; orbSize: number }) {
  const glow = Math.round(orbSize * 1.35);
  return (
    <>
      <div
        aria-hidden
        className="deck-orb-glow pointer-events-none absolute left-1/2 top-1/2 rounded-full"
        data-voice={voiceState}
        style={{ width: glow, height: glow }}
      />
      <JarvisOrb size={orbSize} voiceState={voiceState} className="absolute inset-0" />
    </>
  );
}

/** The most ripples alive at once — a burst is a few waves, not a strobe. */
const MAX_RIPPLES = 4;

/** One ripple ring, leaving the sun for the bezel; removes itself when done. */
function spawnRipple(host: HTMLDivElement | null): void {
  if (!host || host.childElementCount >= MAX_RIPPLES) return;
  const el = document.createElement("div");
  el.className = "deck-orb-ripple rounded-full";
  el.addEventListener("animationend", () => el.remove(), { once: true });
  host.appendChild(el);
}

/**
 * One turning ring of the reticle: a dashed circle in its own root `<svg>`,
 * so its CSS rotation is a compositor transform (a transform on a group
 * INSIDE an svg repaints the whole svg every frame).
 */
function OrbitLayer({
  size,
  r,
  className,
  dash,
  opacity,
}: {
  size: number;
  r: number;
  className: string;
  dash: string;
  opacity: number;
}) {
  const c = size / 2;
  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      className={cn("pointer-events-none absolute inset-0 h-full w-full", className)}
      aria-hidden
    >
      <circle
        cx={c}
        cy={c}
        r={r}
        fill="none"
        stroke="hsl(var(--primary))"
        strokeWidth={1}
        strokeDasharray={dash}
        opacity={opacity}
      />
    </svg>
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
