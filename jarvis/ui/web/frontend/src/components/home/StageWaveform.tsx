import { useEffect, useRef, type MutableRefObject } from "react";

import type { WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { ATTACK_TAU_S, COLUMN_MS, RELEASE_TAU_S, clamp01 } from "@/components/overlay/voiceBars";
import { cn } from "@/lib/utils";

/**
 * The front page's waveform — the heart of the Jarvis bar.
 *
 * The overlay's `VoiceWaveform` is an SVG drawn for a small pill: a fixed
 * row of bars inside a fixed view box, and "at rest nothing animates". On
 * the front page the same drawing read as a thin dotted line lost in a big
 * card (maintainer, 2026-08-23: "sieht komisch aus"). This one is built for
 * the stage instead:
 *
 *   - it FILLS its container — capsule count follows the width, capsule
 *     height the height, on any window size and DPI;
 *   - at rest it breathes: a slow, low travelling wave, so the bar reads as
 *     alive-and-waiting rather than switched off (that is what the person
 *     sees most of the time);
 *   - while listening the capsules follow the microphone, spreading from the
 *     centre outwards (the classic voice-visualiser shape), with the
 *     overlay's own attack/release and scroll cadence;
 *   - thinking and speaking are a sweep, connecting a pulse, error a
 *     still red row.
 *
 * ## The shape (maintainer, 2026-08-28)
 *
 * A silent column is a small round DOT; a column with signal blooms into a
 * wide capsule. Two readings, one path — `capsule()` puts the radius at half
 * the short side, so the dot and the capsule are the same rounded rectangle
 * at two sizes and nothing ever cross-fades between two drawings.
 *
 * Width and height do NOT move together, and that asymmetry is the whole
 * effect. `bloom()` widens the dot on a steep knee, so the faintest signal
 * already opens the column to its full stroke, while the height keeps
 * climbing linearly far past that point. The row therefore reads as dots
 * that OPEN and then stretch, rather than bars that get taller — which is
 * what makes it look like breath rather than a chart.
 *
 * The lit core is a single vertical gradient over the WHOLE canvas, built
 * once per resize rather than per capsule per frame. Every capsule is
 * centred on the same midline, so one gradient gives all of them the same
 * light: a short one sits entirely inside the bright middle, a tall one
 * reaches the soft ends. That is the depth cue — the row looks lit from its
 * own centre line instead of flatly filled.
 *
 * Canvas rather than DOM capsules: forty to sixty shapes repainted every
 * frame are cheap on a 2D context and would be layout work as elements.
 * Colours are read from the theme tokens on the element itself, so the
 * drawing follows light/dark and the wallpaper floor without a single
 * literal — the brand carries no hue any more, so the row travels along the
 * VALUE scale (dim dot → full accent) where a coloured visualiser would
 * travel along a hue. Reduced motion keeps the information (a level meter)
 * and drops the decoration (no breathing, no sweep, no scroll).
 */
export function StageWaveform({
  levelRef,
  phase,
  className,
}: {
  /** Normalised 0..1 microphone level, written by the audio callback. */
  levelRef: MutableRefObject<number>;
  phase: WaveformPhase;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const phaseRef = useRef<WaveformPhase>(phase);
  phaseRef.current = phase;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Geometry follows the box; re-measured on every resize.
    let width = 0;
    let height = 0;
    let span = 0;
    let dpr = 1;
    let count = 0;

    // Theme colours as raw `H S% L%` triples, so an alpha can be composed
    // into every gradient stop. Re-read once a second — a theme flip is
    // rare, a frame is not.
    let tokens = readTokens(canvas);
    let tokensAt = 0;
    let fills = buildFills(ctx, height, tokens);

    let history: number[] = [];
    let head = 0;
    let level = 0;
    let carryMs = 0;
    let sweep = 0;
    let breath = 0;
    let last = performance.now();
    let raf = 0;

    const measure = () => {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.max(1, window.devicePixelRatio || 1);
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // The tallest capsule stops short of the box: the row needs air above
      // and below or a loud syllable reads as a clipped block.
      span = Math.max(2, height * TALLEST - BAR_MIN);
      fills = buildFills(ctx, height, tokens);
      const next = Math.max(6, Math.floor((width + BAR_GAP) / ROW_PITCH));
      if (next !== count) {
        count = next;
        history = new Array<number>(count).fill(0);
        head = 0;
      }
    };

    /**
     * One frame. `shaped` is the 0..1 activity of each column — the single
     * input the geometry is derived from, so every phase describes WHAT it
     * has to say and the drawing stays one place.
     */
    const paint = (shaped: number[], alphas: number[], fill: string | CanvasGradient) => {
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = fill;
      // Columns sit on a fixed pitch and every capsule is drawn around its
      // column's CENTRE, so a blooming capsule grows outwards in both
      // directions instead of pushing the row sideways.
      const rowW = (count - 1) * ROW_PITCH + BAR_W;
      const cx0 = (width - rowW) / 2 + BAR_W / 2;
      const mid = height / 2;
      for (let i = 0; i < count; i += 1) {
        const v = clamp01(shaped[i] ?? 0);
        const h = Math.min(height, BAR_MIN + span * v);
        const w = DOT_W + (BAR_W - DOT_W) * bloom(v);
        ctx.globalAlpha = alphas[i] ?? 1;
        capsule(ctx, cx0 + i * ROW_PITCH - w / 2, mid - h / 2, w, h);
      }
      ctx.globalAlpha = 1;
    };

    /** How present a column is: a quiet floor that the activity lifts. The
     *  floor itself thins towards both rims, so the row ends in whispers. */
    const presence = (t: number, v: number) => {
      const floor = DOT_ALPHA * (0.3 + 0.7 * rim(t));
      return floor + (1 - floor) * v;
    };

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;
      if (now - tokensAt > 1000) {
        const next = readTokens(canvas);
        if (next.primary !== tokens.primary || next.muted !== tokens.muted) {
          tokens = next;
          fills = buildFills(ctx, height, tokens);
        }
        tokensAt = now;
      }
      const p = phaseRef.current;
      const shaped: number[] = new Array<number>(count);
      const alphas: number[] = new Array<number>(count);

      if (p === "error") {
        shaped.fill(0);
        alphas.fill(0.9);
        paint(shaped, alphas, fills.error);
        return;
      }

      if (p === "idle") {
        // Breathing: a slow wave travelling across the row. Deliberately
        // below the bloom knee for most of its arc, so the resting bar stays
        // a row of DOTS that shimmers rather than a waveform of something
        // nobody said.
        breath += dt * (reduced ? 0 : 0.85);
        for (let i = 0; i < count; i += 1) {
          const t = i / Math.max(1, count - 1);
          const wave = reduced ? 0 : 0.5 + 0.5 * Math.sin(breath * 2 + t * 6.283 * 1.2);
          const v = IDLE_SWELL * wave * rim(t);
          shaped[i] = v;
          alphas[i] = presence(t, 0.34 * wave * rim(t));
        }
        paint(shaped, alphas, fills.muted);
        return;
      }

      if (p === "connecting") {
        breath += dt * 2.4;
        const pulse = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(breath);
        for (let i = 0; i < count; i += 1) {
          const t = i / Math.max(1, count - 1);
          const v = 0.16 * pulse * rim(t);
          shaped[i] = v;
          alphas[i] = presence(t, 0.5 * pulse * rim(t));
        }
        paint(shaped, alphas, fills.primary);
        return;
      }

      if (p === "working" || p === "speaking") {
        // A sweep: a bright band travelling the row, faster when speaking.
        const period = p === "speaking" ? 1.1 : 1.8;
        sweep = reduced ? 0.5 : (sweep + dt / period) % 1;
        for (let i = 0; i < count; i += 1) {
          const t = i / Math.max(1, count - 1);
          const d = Math.abs(((t - sweep + 1.5) % 1) - 0.5); // 0 at the band
          const gain = reduced ? 0.4 : Math.max(0, 1 - d / 0.22);
          const base = p === "speaking" ? 0.12 : 0.08;
          const v = (base + 0.62 * gain) * rim(t);
          shaped[i] = v;
          alphas[i] = presence(t, Math.min(1, 0.25 + gain) * rim(t));
        }
        paint(shaped, alphas, fills.primary);
        return;
      }

      // listening: the microphone, spreading from the centre outwards.
      const target = clamp01(levelRef.current);
      const tau = target > level ? ATTACK_TAU_S : RELEASE_TAU_S;
      level += (target - level) * (1 - Math.exp(-dt / tau));
      carryMs += dt * 1000;
      while (carryMs >= COLUMN_MS) {
        carryMs -= COLUMN_MS;
        history[head] = level;
        head = (head + 1) % count;
      }
      const half = Math.floor(count / 2);
      for (let i = 0; i < count; i += 1) {
        // Distance from the centre decides how old a sample this capsule
        // shows: the newest in the middle, older ones further out on both
        // sides.
        const t = i / Math.max(1, count - 1);
        const dist = Math.abs(i - (count - 1) / 2);
        const age = reduced ? 0 : Math.min(half, Math.round(dist));
        const idx = (head - 1 - age + count * 2) % count;
        const raw = reduced ? level : Math.max(history[idx] ?? 0, age === 0 ? level : 0);
        const v = clamp01(raw) * rim(t);
        shaped[i] = v;
        alphas[i] = presence(t, v);
      }
      paint(shaped, alphas, fills.primary);
    };

    measure();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    ro?.observe(canvas);
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
    };
  }, [levelRef]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      data-testid="stage-waveform"
      data-phase={phase}
      className={cn("block h-full w-full", className)}
    />
  );
}

/** Column pitch, and the two widths a column lives between: a resting DOT
 *  and the full capsule stroke it blooms into. */
const DOT_W = 4;
const BAR_W = 13;
const BAR_GAP = 2;
const ROW_PITCH = BAR_W + BAR_GAP;

/** Height of a silent column — a hair taller than the dot is wide, so the
 *  rest state is a round dot rather than a dash. */
const BAR_MIN = 5;

/** Fraction of the box the loudest capsule may take. */
const TALLEST = 0.8;

/** How present a resting dot is at the centre of the row. Visible as a row,
 *  quiet enough that the measured part of the drawing is the part that
 *  reads. */
const DOT_ALPHA = 0.3;

/** How much of each rim tapers back into plain dots, as a row fraction.
 *  The silhouette we are after: activity in the middle, dots at the edges. */
const RIM = 0.12;

/** Activity at which a column has fully opened to the capsule stroke. Low
 *  on purpose — the width is a "there is signal here" cue, the height is the
 *  measurement. */
const BLOOM_KNEE = 0.14;

/** Idle swell, kept under the bloom knee so resting stays dotty. */
const IDLE_SWELL = 0.05;

/** Smooth 0..1 rim envelope — 0 at both edges, 1 from `RIM` inwards. */
function rim(t: number): number {
  const d = Math.min(t, 1 - t) / RIM;
  return d >= 1 ? 1 : d * d * (3 - 2 * d);
}

/** Dot → capsule width, on a steep knee (see the component's note). */
function bloom(v: number): number {
  const d = v / BLOOM_KNEE;
  return d >= 1 ? 1 : d * d * (3 - 2 * d);
}

/** A capsule: a rectangle whose radius is half its SHORT side, so the same
 *  call draws a tall stroke, a circle and a squat dot. */
function capsule(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  const r = Math.min(w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
  ctx.lineTo(x + w, y + h - r);
  ctx.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
  ctx.lineTo(x + r, y + h);
  ctx.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
  ctx.lineTo(x, y + r);
  ctx.arc(x + r, y + r, r, Math.PI, (3 * Math.PI) / 2);
  ctx.closePath();
  ctx.fill();
}

type Tokens = { primary: string; muted: string; error: string };
type Fills = {
  primary: string | CanvasGradient;
  muted: string | CanvasGradient;
  error: string | CanvasGradient;
};

/**
 * One vertical gradient per channel, spanning the whole canvas.
 *
 * All capsules share the midline, so a single gradient lights the row from
 * its own centre: full accent where the line runs, half-strength at the top
 * and bottom edges. A short capsule therefore sits entirely in the bright
 * band and a tall one fades towards its caps — the same depth cue the
 * reference gets from a violet-to-blue ramp, spelled in value because the
 * brand no longer carries a hue.
 */
function buildFills(ctx: CanvasRenderingContext2D, height: number, tokens: Tokens): Fills {
  const make = (triple: string): string | CanvasGradient => {
    if (height <= 1) return `hsl(${triple})`;
    const g = ctx.createLinearGradient(0, 0, 0, height);
    g.addColorStop(0, `hsl(${triple} / ${CAP_ALPHA})`);
    g.addColorStop(0.34, `hsl(${triple} / ${SHOULDER_ALPHA})`);
    g.addColorStop(0.5, `hsl(${triple} / 1)`);
    g.addColorStop(0.66, `hsl(${triple} / ${SHOULDER_ALPHA})`);
    g.addColorStop(1, `hsl(${triple} / ${CAP_ALPHA})`);
    return g;
  };
  return { primary: make(tokens.primary), muted: make(tokens.muted), error: make(tokens.error) };
}

/** Strength of the lit core at the caps and at the shoulders. */
const CAP_ALPHA = 0.45;
const SHOULDER_ALPHA = 0.82;

/**
 * The theme's channels as raw `H S% L%` triples (index.css); `hsl(H S% L% /
 * a)` is what the CSS side builds from them too, and keeping the triple
 * rather than a finished colour is what lets every gradient stop carry its
 * own alpha.
 */
function readTokens(el: HTMLElement): Tokens {
  const style = getComputedStyle(el);
  const token = (name: string, fallback: string) => {
    const raw = style.getPropertyValue(name).trim();
    return raw || fallback;
  };
  return {
    primary: token("--primary", "0 0% 100%"),
    muted: token("--muted-foreground", "47 5% 59%"),
    error: token("--destructive", "0 84% 60%"),
  };
}
