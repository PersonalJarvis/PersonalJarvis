import { useEffect, useId, useMemo, useRef } from "react";
import { useEventStore, type VoiceState } from "@/store/events";
import { JarvisOrb } from "@/components/deck/JarvisOrb";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { HudHaloDefs } from "@/components/deck/HudFrame";
import { readVoiceInputLevel } from "@/lib/voiceInputLevel";
import { driveTarget, isOnset, orbDriveFor, smoothOrbLevel } from "@/lib/orbLevel";
import { cn } from "@/lib/utils";

/**
 * The centre of the deck: the mascot on a STAGE — a light from above, the
 * pool that light throws on the ground, the figure's own shadow, one bright
 * arc per running step, and a single line of readouts along the bottom.
 *
 * The middle is Gigi itself (`JarvisOrb`), moved by the real voice state,
 * with a soft gold glow throwing the dark silhouette forward. It was a PNG
 * of a glowing sphere until 2026-08-20; the maintainer had asked repeatedly
 * for that picture to go and for the mascot to carry the product, and the
 * picture had earned it — its baked-in white halo read as a grey box on the
 * dark deck, and this reticle sliced that box off at the ring.
 *
 * The bezel and the level arc that rode it went on 2026-08-21 (maintainer:
 * the ring made Gigi small, measured nothing, and three of the four corner
 * readouts said "0" or "Ready" — the centre exhibiting its own emptiness).
 * What is left carries information: the arcs are parallel work made visible,
 * the sweep turns only while something runs, and the readouts are live values
 * the caller sources, on one line, each one dropped while it would read as a
 * zero. The voice is not a meter any more — it is the light on the stage. What went with the PNG was the scenery that measured
 * nothing — 72 dial ticks, corner brackets, two counter-turning orbits, a
 * satellite riding the bezel and an idle ping (maintainer, 2026-08-20:
 * instruments that show nothing are a stage set). The deck does not need
 * them to look alive: the living thing in the middle blinks, its pupils
 * drift and it waves, so the instruments can hold still until they have
 * something to say. Reduced motion stops what is left.
 *
 * And it MOVES WITH THE VOICE, all of it (maintainer, 2026-08-19: "when you
 * speak the sun moved — that, on the next level; not just the core, all of
 * it, smooth"). One animation-frame loop computes the orb's LEVEL
 * (`lib/orbLevel.ts`: the real microphone while listening, a speech-shaped
 * envelope while the assistant speaks, a heartbeat while it thinks), smooths
 * it, and writes it to ONE CSS variable on the root (`--orb-level`); the
 * core's size and brightness, the glow, and the stage's beam, pool and
 * shadow all read that variable in CSS (index.css). A jump
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

  const R = size / 2;
  // The sphere sat at 0.78 of the reticle and left a ring of nothing around
  // it. Gigi carries a lot of air inside its own 256 viewBox — the figure is
  // roughly two thirds of it — so at 0.78 it read as a small toy in a big
  // dial. At 0.96 the figure fills the bezel the way the sphere used to.
  const orbSize = Math.round(size * 0.96);
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

  // The sweep is drawn ONCE, at rest, and turned by CSS (`.deck-orb-sweep`,
  // index.css). It used to be an animation-frame loop that put its angle in
  // React state: sixty renders a second of this whole component, each one
  // rewriting the arc's `d` INSIDE the halo filter, which forced the filter
  // to raster again — on the CPU, at every frame, while the deck was busy.
  // That is exactly when a person is waiting for an answer and watching the
  // centre, and on a four-core laptop it is what made the deck crawl
  // (maintainer, 2026-08-21: "1 FPS"). The geometry never changed; only the
  // angle did, and an angle is a transform. So it is one now, on its own
  // unfiltered group with the same halo as a cheap `drop-shadow`, the way
  // `.deck-ring-orbit` already turns the standby's ring: compositor work,
  // no render, no repaint, no filter pass.
  const [sx, sy] = point(0, R * 0.94);
  const [ex, ey] = point(36, R * 0.94);
  const haloId = useId();

  // The corners used to carry four readouts, and two of them were counters
  // that said "0" whenever nothing had happened yet — the centre measuring
  // its own emptiness. They are ONE line now, and a value joins it only once
  // it has something to say: the caller passes an empty string for a value
  // that would be a zero, and an empty string never reaches the line. The
  // engine keeps its place at the front, because which voice is answering is
  // true whether or not anything has run yet.
  const extras = readouts
    ? [
        { id: "mood", text: readouts.nw },
        { id: "steps", text: readouts.ne },
        { id: "words", text: readouts.se },
      ].filter((r) => r.text.trim().length > 0)
    : [];

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
      {/* The stage. A light from above, a pool of it on the ground and the
          figure's own shadow — the three things that put Gigi SOMEWHERE
          instead of nowhere. All three read the same `--orb-level` the rest
          of the centre reads, so the voice arrives as light rather than as a
          number: louder is brighter and wider. This replaced the bezel and
          its level arc, which drew a ring around the figure and measured the
          voice on it (maintainer, 2026-08-21: the reticle made Gigi small and
          nothing on it carried its weight). */}
      <div
        aria-hidden
        className="deck-stage-beam pointer-events-none absolute"
        data-testid="deck-orb-vu"
      />
      <div aria-hidden className="deck-stage-pool pointer-events-none absolute" />
      <div aria-hidden className="deck-stage-shadow pointer-events-none absolute" />

      <svg viewBox={`0 0 ${size} ${size}`} className="absolute inset-0 h-full w-full" aria-hidden>
        <HudHaloDefs id={haloId} />
        <g filter={`url(#${haloId})`}>
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
        </g>
        {/* The sweep turns on its OWN group, outside the halo filter: a
            filtered group has to be rastered again every time anything
            inside it moves, and this one moves every frame. It carries the
            same halo as a `drop-shadow` in CSS, which is rastered once and
            then only turned. */}
        {busy && !reduced && (
          <g className="deck-orb-sweep">
            <path
              d={`M ${sx} ${sy} A ${R * 0.94} ${R * 0.94} 0 0 1 ${ex} ${ey}`}
              fill="none"
              stroke="hsl(var(--primary))"
              strokeWidth={2}
              strokeLinecap="butt"
              opacity={0.7}
            />
          </g>
        )}
      </svg>

      {/* Ripples: one ring per word landing, leaving the figure for the edge.
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
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-baseline justify-center gap-x-2 px-2 text-center font-mono text-[9px] uppercase tracking-[0.16em] text-primary/80"
          data-testid="deck-orb-readouts"
        >
          <span data-testid="deck-orb-provider">{readouts.sw}</span>
          {extras.map((r) => (
            <span key={r.id}>
              <span className="mr-2 text-primary/30">·</span>
              {r.text}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The orb — the part of the centre a press lands on.
 *
 * Back to front: a soft gold glow wider than the sphere (`.deck-orb-glow`,
 * keyed on the voice state), then the mascot itself (`JarvisOrb`).
 */
function OrbFace({ voiceState, orbSize }: { voiceState: VoiceState; orbSize: number }) {
  // The light behind the figure, a little wider than it so the silhouette
  // stands IN it rather than on it. It followed a smaller orb at 1.35; with
  // the figure nearly filling the bezel that would spill across the cards.
  const glow = Math.round(orbSize * 1.05);
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

