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
 *   - it FILLS its container — bar count follows the width, bar height the
 *     height, on any window size and DPI;
 *   - at rest it breathes: a slow, low travelling wave, so the bar reads as
 *     alive-and-waiting rather than switched off (that is what the person
 *     sees most of the time);
 *   - while listening the bars follow the microphone, spreading from the
 *     centre outwards (the classic voice-visualiser shape), with the
 *     overlay's own attack/release and scroll cadence;
 *   - thinking and speaking are a sweep, connecting a pulse, error a
 *     still red row.
 *
 * Canvas rather than DOM bars: sixty to ninety bars repainted every frame
 * are cheap on a 2D context and would be layout work as elements. Colours
 * are read from the theme tokens on the element itself, so the drawing
 * follows light/dark and the wallpaper floor without a single literal.
 * Reduced motion keeps the information (a level meter) and drops the
 * decoration (no breathing, no sweep, no scroll).
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
    let dpr = 1;
    let count = 0;
    const measure = () => {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.max(1, window.devicePixelRatio || 1);
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const next = Math.max(8, Math.floor((width + BAR_GAP) / (BAR_W + BAR_GAP)));
      if (next !== count) {
        count = next;
        history = new Array<number>(count).fill(0);
        head = 0;
      }
    };

    // Theme colours, read from the tokens on the canvas itself. Re-read
    // once a second — a theme flip is rare, a frame is not.
    let colors = readColors(canvas);
    let colorsAt = 0;

    let history: number[] = [];
    let head = 0;
    let level = 0;
    let carryMs = 0;
    let sweep = 0;
    let breath = 0;
    let last = performance.now();
    let raf = 0;

    const paint = (heights: number[], alphas: number[], fill: string) => {
      ctx.clearRect(0, 0, width, height);
      const pitch = BAR_W + BAR_GAP;
      const rowW = count * pitch - BAR_GAP;
      const x0 = (width - rowW) / 2;
      const mid = height / 2;
      for (let i = 0; i < count; i += 1) {
        const h = Math.max(BAR_MIN, Math.min(height, heights[i] ?? BAR_MIN));
        ctx.globalAlpha = alphas[i] ?? 1;
        ctx.fillStyle = fill;
        roundedBar(ctx, x0 + i * pitch, mid - h / 2, BAR_W, h);
      }
      ctx.globalAlpha = 1;
    };

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;
      if (now - colorsAt > 1000) {
        colors = readColors(canvas);
        colorsAt = now;
      }
      const p = phaseRef.current;
      const maxH = height;
      const heights: number[] = new Array<number>(count);
      const alphas: number[] = new Array<number>(count);

      if (p === "error") {
        heights.fill(BAR_MIN + 2);
        alphas.fill(0.9);
        paint(heights, alphas, colors.error);
        return;
      }

      if (p === "idle") {
        // Breathing: a slow wave travelling across the row, low and quiet.
        breath += dt * (reduced ? 0 : 0.9);
        for (let i = 0; i < count; i += 1) {
          const t = i / Math.max(1, count - 1);
          const wave = reduced ? 0 : 0.5 + 0.5 * Math.sin(breath * 2 + t * 6.283 * 1.5);
          const env = 0.55 + 0.45 * Math.sin(t * Math.PI); // softer at the edges
          heights[i] = BAR_MIN + maxH * 0.12 * wave * env;
          alphas[i] = 0.35 + 0.3 * wave;
        }
        paint(heights, alphas, colors.muted);
        return;
      }

      if (p === "connecting") {
        breath += dt * 2.4;
        const pulse = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(breath);
        heights.fill(BAR_MIN + maxH * 0.1 * pulse);
        alphas.fill(0.45 + 0.4 * pulse);
        paint(heights, alphas, colors.primary);
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
          const base = p === "speaking" ? 0.22 : 0.14;
          heights[i] = BAR_MIN + maxH * (base + 0.55 * gain);
          alphas[i] = 0.4 + 0.6 * gain;
        }
        paint(heights, alphas, colors.primary);
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
        // Distance from the centre decides how old a sample this bar shows:
        // the newest in the middle, older ones further out on both sides.
        const dist = Math.abs(i - (count - 1) / 2);
        const age = reduced ? 0 : Math.min(half, Math.round(dist));
        const idx = (head - 1 - age + count * 2) % count;
        const v = reduced ? level : Math.max(history[idx] ?? 0, age === 0 ? level : 0);
        heights[i] = BAR_MIN + maxH * (0.06 + 0.9 * v);
        alphas[i] = 0.55 + 0.45 * v;
      }
      paint(heights, alphas, colors.primary);
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

const BAR_W = 3;
const BAR_GAP = 3;
const BAR_MIN = 3;

function roundedBar(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
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

/**
 * The theme's channels as canvas colours. The tokens are `H S% L%` triples
 * (index.css); `hsl(H S% L%)` is what the CSS side builds from them too.
 */
function readColors(el: HTMLElement): { primary: string; muted: string; error: string } {
  const style = getComputedStyle(el);
  const token = (name: string, fallback: string) => {
    const raw = style.getPropertyValue(name).trim();
    return raw ? `hsl(${raw})` : fallback;
  };
  return {
    primary: token("--primary", "hsl(50 100% 52%)"),
    muted: token("--muted-foreground", "hsl(47 5% 59%)"),
    error: token("--destructive", "hsl(0 84% 60%)"),
  };
}
