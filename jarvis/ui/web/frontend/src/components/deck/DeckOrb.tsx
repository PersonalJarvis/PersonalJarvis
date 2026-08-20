import { useEffect, useMemo, useRef } from "react";
import { useEventStore, type VoiceState } from "@/store/events";
import { JarvisOrb } from "@/components/deck/JarvisOrb";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { readVoiceInputLevel } from "@/lib/voiceInputLevel";
import { driveTarget, orbDriveFor, smoothOrbLevel } from "@/lib/orbLevel";
import { cn } from "@/lib/utils";

/**
 * The centre of the deck: the mascot on a stage.
 *
 * This used to be a reticle — a bezel ring with 72 dial ticks, arcs riding
 * the rim, a meter drawn along it and four readouts pinned to the corners.
 * That was built for a SPHERE, and it worked while a sphere sat in it. It
 * does not survive the mascot: Gigi is a tall figure, a circle is not its
 * shape, and the ring cut straight through its head and its feet. A hoop
 * drawn around a character means nothing (maintainer, 2026-08-20: "a circle
 * around the mascot makes no sense — it has to look good and it has to make
 * sense").
 *
 * So the ring is gone and the middle is staged instead of instrumented. Five
 * parts, each of which is either the figure or something the figure actually
 * does:
 *
 *   1. BACKLIGHT — a soft upright pool of light behind Gigi, taller than it
 *      is wide, so the dark silhouette reads against it. It breathes at rest
 *      and opens up with the voice.
 *   2. FOOTLIGHT — a flat ellipse of light under the figure. It is what puts
 *      Gigi on a floor rather than afloat in the middle of a page, and it
 *      brightens with the voice, so the figure looks lit from its own sound.
 *   3. THE FIGURE — `JarvisOrb`, free, nothing drawn over or around it.
 *   4. THE WAVE — one line under the figure whose amplitude IS `--orb-level`:
 *      the real microphone while listening, a speech-shaped envelope while
 *      the assistant speaks, a heartbeat while it thinks. Flat when there is
 *      nothing to hear. This is the meter the bezel arc used to be, in the
 *      shape a voice actually has.
 *   5. THE LINE — the four readouts the corners used to scatter, set as one
 *      readable row: engine · mood · steps · words.
 *
 * Everything still reads ONE number. An animation-frame loop computes the
 * level (`lib/orbLevel.ts`), smooths it, and writes it to `--orb-level` on
 * the root; the backlight, the footlight, the wave and the figure all read
 * that variable in CSS (index.css). Transform and opacity only, so the main
 * thread pays for one style write per frame. Reduced motion gets the still
 * picture, and the loop never starts.
 */
export interface OrbReadouts {
  nw: string;
  ne: string;
  sw: string;
  se: string;
}

/**
 * The wave's shape. Drawn HALF A VIEWBOX WIDER than it is shown so the flow
 * animation can slide it by exactly one period and start over without a seam;
 * the svg clips the overhang. Flat at rest — the level scales it.
 */
const WAVE_PATH = "M 0 8 Q 12.5 0 25 8 T 50 8 T 75 8 T 100 8 T 125 8 T 150 8";

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
   * The click-shaped wake word: pressing the mascot does what saying the
   * wake phrase does — starts the conversation, or ends the one that runs.
   * Without it the stage is display only.
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

  // The figure gets the room; the wave and the line take the rest. With no
  // ring to sit inside, the figure can be nearly the whole width.
  const figureSize = Math.round(size * 0.9);
  const running = steps.slice(0, 8);

  // The level loop: one number for everything that moves with the voice,
  // written to the root as `--orb-level`. Runs only while a voice state
  // drives it; idle (and reduced motion) is a still 0 and no loop at all.
  const rootRef = useRef<HTMLDivElement>(null);
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
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const target = driveTarget(drive, (now - t0) / 1000, readVoiceInputLevel(now));
      level = smoothOrbLevel(level, target, dt);
      root.style.setProperty("--orb-level", level.toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      root.style.setProperty("--orb-level", "0");
    };
  }, [voiceState, reduced]);

  const figure = <Figure voiceState={voiceState} size={figureSize} />;

  return (
    <div
      ref={rootRef}
      className={cn("deck-orb-root relative flex shrink-0 flex-col items-center", className)}
      style={{ width: size }}
      data-testid="deck-orb"
      data-voice={voiceState}
    >
      {/* 1. The stage light — ONE element for the whole centre, and the only
          thing that touches the wallpaper here. Warm where the figure stands
          so the silhouette has something to be dark against, then falling to
          the theme's ground colour so the wave and the readouts stay legible
          over whatever picture is behind them, then to nothing. It was two
          elements for an hour — a bright pool from here and a dark pool from
          the view above it — and they fought: the pair drew a lit niche
          around Gigi like a headstone (maintainer, 2026-08-20). One gradient
          cannot fight itself. */}
      <div
        aria-hidden
        data-testid="deck-stage-light"
        className="deck-stage-light pointer-events-none absolute"
        style={{ width: Math.round(size * 1.5), height: Math.round(size * 1.5) }}
      />

      {/* 2 + 3: the figure, and the light it stands on. */}
      <div className="relative grid place-items-center" style={{ height: figureSize }}>
        {onPress ? (
          <button
            type="button"
            onClick={onPress}
            disabled={pressDisabled}
            aria-label={pressLabel}
            title={pressLabel}
            className={cn(
              "relative rounded-2xl transition-transform duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 focus-visible:ring-offset-4 focus-visible:ring-offset-transparent",
              pressDisabled
                ? "cursor-wait"
                : "cursor-pointer hover:scale-[1.03] active:scale-[0.98] motion-reduce:transform-none",
            )}
            style={{ width: figureSize, height: figureSize }}
          >
            {figure}
          </button>
        ) : (
          <div className="relative" style={{ width: figureSize, height: figureSize }}>
            {figure}
          </div>
        )}
        <div
          aria-hidden
          data-testid="deck-stage-footlight"
          className="deck-stage-footlight pointer-events-none absolute"
          style={{ width: figureSize * 0.86, height: Math.round(figureSize * 0.11) }}
        />
      </div>

      {/* 4: the wave — the voice, in the shape a voice has. */}
      <svg
        viewBox="0 0 100 16"
        preserveAspectRatio="none"
        className="deck-orb-wave pointer-events-none mt-1"
        style={{ width: Math.round(size * 0.6), height: 16 }}
        aria-hidden
        data-testid="deck-orb-wave"
        /* While work is running the line travels; otherwise it only breathes
           with the level, so a still line means nothing is happening. */
        data-busy={busy && !reduced ? "true" : undefined}
      >
        {/* Two layers on purpose: the group carries the travel, the path
            carries the amplitude, so neither transform overwrites the other. */}
        <g className="deck-orb-wave-flow">
          <path
            className="deck-orb-wave-line"
            d={WAVE_PATH}
            fill="none"
            stroke="hsl(var(--primary))"
            strokeWidth={1.4}
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        </g>
      </svg>

      {/* One mark per step running in parallel — nothing at all when none is. */}
      {running.length > 0 && (
        <div
          className="mt-1 flex items-center gap-1"
          aria-hidden
          data-testid="deck-orb-steps"
        >
          {running.map((step) => (
            <span
              key={step.id}
              className={cn(
                "deck-orb-step h-1 rounded-full",
                step.kind === "note" ? "bg-muted-foreground/60" : "bg-primary",
                step.status === "active" ? "w-4 deck-orb-step-active" : "w-2 opacity-60",
              )}
            />
          ))}
        </div>
      )}

      {/* 5: the readouts the corners used to scatter, as one row. */}
      {/* The row may run WIDER than the stage — the stage is sized for the
          figure, and clamping the row to it truncated every field into
          "VERTEX… · R… · 0 … · 0 …" (2026-08-20). Nothing clips it, so it
          simply centres and overhangs. */}
      {readouts && (
        <div
          className="mt-2 flex w-max items-center justify-center gap-x-2 whitespace-nowrap font-mono text-[9px] uppercase tracking-[0.1em] text-primary/75"
          data-testid="deck-orb-readouts"
        >
          <span data-testid="deck-orb-provider">{readouts.sw}</span>
          <Dot />
          <span>{readouts.nw}</span>
          <Dot />
          <span>{readouts.ne}</span>
          <Dot />
          <span>{readouts.se}</span>
        </div>
      )}
    </div>
  );
}

function Dot() {
  return (
    <span aria-hidden className="text-primary/35">
      ·
    </span>
  );
}

/**
 * The figure itself. Nothing is drawn over it and nothing rings it — the
 * light behind and below is what stages it.
 */
function Figure({ voiceState, size }: { voiceState: VoiceState; size: number }) {
  return <JarvisOrb size={size} voiceState={voiceState} className="absolute inset-0" />;
}
