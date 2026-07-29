import { memo, useEffect, useRef, useState, type MutableRefObject } from "react";

import { cn } from "@/lib/utils";
import {
  BAR_MIN_H,
  LIVE_BAR_SPAN,
  COLUMN_MS,
  LIVE_BAR_COUNT,
  PILL_CY,
  PILL_H,
  PILL_R,
  PILL_W,
  PILL_X,
  PILL_Y,
  SWEEP_PERIOD_S,
  VIEW_H,
  VIEW_W,
  barHeight,
  barWidth,
  clamp01,
  evenlySpaced,
  ringValue,
  smoothLevel,
  sweepGain,
} from "./voiceBars";

/**
 * The live voice visualizer — the same pill the desktop overlay draws, fed by
 * real microphone levels.
 *
 * Two looks, and which one is shown is a statement of fact rather than a
 * styling choice:
 *
 * - **waveform** (``listening``) — a scrolling history of MEASURED microphone
 *   levels. The newest sample enters at the right edge and the row travels
 *   left, so the shape on screen is what the user actually said a moment ago.
 * - **sweep** (``connecting`` / ``working`` / ``speaking``) — a travelling
 *   highlight for activity that is real but NOT measured here: the socket
 *   opening, the transcription and reply running after the turn was committed,
 *   the assistant's own speech (the browser has no output level tap). It
 *   promises motion and nothing more, so the visualizer can never imply a
 *   microphone reading it does not have.
 *
 * Nothing in the animation goes through React state: the level arrives at
 * ~30 Hz from the capture worklet and is read from a ref inside one
 * ``requestAnimationFrame`` loop that writes SVG attributes directly. A
 * ``setState`` per level sample would re-render the whole control 30 times a
 * second to move seven pixels.
 */
export type WaveformPhase =
  | "idle"
  | "connecting"
  | "listening"
  | "working"
  | "speaking"
  | "error";

/** Phases whose bars come from real microphone samples. */
const MEASURED_PHASES: readonly WaveformPhase[] = ["listening"];

/** rAF hands out a huge delta after the tab was hidden; cap it so the row
 *  resumes smoothly instead of snapping through a second of history. */
const MAX_FRAME_S = 0.1;

/** Repaint budget while motion is reduced — enough to follow a voice, slow
 *  enough that the row reads as a level meter rather than an animation. */
const REDUCED_PAINT_MS = 66;

/** Per-phase colouring.
 *
 * The colour is a Tailwind class (so it follows the theme), the strength is an
 * inline opacity rather than a ``/70`` modifier: this build's Tailwind emits
 * ``fill-primary`` but NOT ``fill-primary/70``, because the theme colours are
 * plain ``hsl(var(--x))`` strings with no ``<alpha-value>`` slot — the faded
 * classes would silently resolve to nothing and the bars would vanish. */
const PHASE_TONE: Record<
  WaveformPhase,
  { bars: string; barsOpacity: number; rim: string; rimOpacity: number }
> = {
  idle: { bars: "fill-muted-foreground", barsOpacity: 0.5, rim: "stroke-border", rimOpacity: 1 },
  connecting: { bars: "fill-primary", barsOpacity: 0.6, rim: "stroke-border", rimOpacity: 1 },
  listening: { bars: "fill-primary", barsOpacity: 1, rim: "stroke-primary", rimOpacity: 0.7 },
  working: { bars: "fill-primary", barsOpacity: 0.85, rim: "stroke-primary", rimOpacity: 0.5 },
  speaking: { bars: "fill-primary", barsOpacity: 0.7, rim: "stroke-primary", rimOpacity: 0.35 },
  error: { bars: "fill-destructive", barsOpacity: 0.8, rim: "stroke-destructive", rimOpacity: 0.8 },
};

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    // jsdom and older browsers without matchMedia — treat as "motion ok".
    return false;
  }
}

/** Track the OS "reduce motion" setting, including a live change. */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);
  useEffect(() => {
    const query = (() => {
      try {
        return window.matchMedia("(prefers-reduced-motion: reduce)");
      } catch {
        // jsdom and older browsers without matchMedia — nothing to track.
        return null;
      }
    })();
    if (typeof query?.addEventListener !== "function") return;
    const onChange = () => setReduced(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

function VoiceWaveformImpl({
  levelRef,
  phase,
  count = LIVE_BAR_COUNT,
  className,
}: {
  /** Normalized 0..1 microphone level, written by the audio callback. */
  levelRef: MutableRefObject<number>;
  phase: WaveformPhase;
  count?: number;
  className?: string;
}) {
  // One ref on the group rather than one per bar: an inline ref callback is a
  // new function on every render, so React would detach and re-attach all
  // eighteen bars whenever the parent re-renders (a status word changing, an
  // interim transcript arriving) and the animation loop would be writing to
  // nulls for a frame.
  const row = useRef<SVGGElement>(null);
  const reduced = useReducedMotion();
  // Committed waveform history, kept across phase changes so the shape the
  // user just spoke is still on screen while the transcription runs.
  const history = useRef<number[]>([]);
  const head = useRef(0);
  const level = useRef(0);

  if (history.current.length !== count) {
    history.current = new Array<number>(count).fill(0);
    head.current = 0;
  }

  useEffect(() => {
    const group = row.current;
    if (!group) return;
    const rects = Array.from(group.children) as SVGRectElement[];
    const measured = MEASURED_PHASES.includes(phase);
    const sweepPeriod = SWEEP_PERIOD_S[phase] ?? 0;

    const paint = (heights: number[], opacities?: number[]) => {
      for (let i = 0; i < rects.length; i += 1) {
        const h = heights[i];
        if (h === undefined) continue;
        rects[i].setAttribute("y", String(PILL_CY - h / 2));
        rects[i].setAttribute("height", String(h));
        rects[i].setAttribute("opacity", String(opacities?.[i] ?? 1));
      }
    };

    // At rest nothing animates: "when nothing is happening, nothing is in the
    // bar" — the same rule the desktop overlay follows.
    if (phase === "idle" || phase === "error") {
      level.current = 0;
      paint(new Array<number>(count).fill(BAR_MIN_H));
      return;
    }

    let raf = 0;
    let last = performance.now();
    let painted = last;
    let carryMs = 0;
    let sweepPhase = 0;

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min((now - last) / 1000, MAX_FRAME_S);
      last = now;

      if (measured) {
        level.current = smoothLevel(level.current, clamp01(levelRef.current), dt);
      }

      if (reduced) {
        // Reduced motion keeps the INFORMATION (a level meter that follows the
        // voice) and drops the decoration: no scrolling and no sweep.
        if (now - painted < REDUCED_PAINT_MS) return;
        painted = now;
        paint(new Array<number>(count).fill(barHeight(measured ? level.current : 0.2)));
        return;
      }

      if (!measured) {
        sweepPhase = sweepPeriod > 0 ? (sweepPhase + dt / sweepPeriod) % 1 : 0;
        const heights: number[] = [];
        const opacities: number[] = [];
        for (let i = 0; i < count; i += 1) {
          const gain = sweepGain(i, count, sweepPhase);
          heights.push(barHeight(0.12 + 0.58 * gain));
          opacities.push(0.35 + 0.65 * gain);
        }
        paint(heights, opacities);
        return;
      }

      // Advance the scroll on a WALL-CLOCK cadence, not once per frame, so the
      // row travels at the same speed on a 60 Hz and a 144 Hz display.
      carryMs += dt * 1000;
      while (carryMs >= COLUMN_MS) {
        carryMs -= COLUMN_MS;
        history.current[head.current] = level.current;
        head.current = (head.current + 1) % count;
      }

      const heights: number[] = [];
      for (let i = 0; i < count; i += 1) {
        heights.push(barHeight(ringValue(history.current, head.current, i)));
      }
      // The newest column tracks the live level between advances, so the onset
      // of a word shows up on the frame its sample arrives instead of waiting
      // out the rest of the column.
      const newest = barHeight(level.current);
      if (newest > (heights[count - 1] ?? 0)) heights[count - 1] = newest;
      paint(heights);
    };

    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [count, levelRef, phase, reduced]);

  const tone = PHASE_TONE[phase];
  const w = barWidth(count, LIVE_BAR_SPAN);

  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      className={cn("h-14 w-full", className)}
      data-testid="voice-waveform"
      data-phase={phase}
      aria-hidden="true"
    >
      <rect
        x={PILL_X}
        y={PILL_Y}
        width={PILL_W}
        height={PILL_H}
        rx={PILL_R}
        strokeWidth={1.6}
        className={cn("fill-card", tone.rim)}
        style={{
          strokeOpacity: tone.rimOpacity,
          transition: "stroke 300ms ease, stroke-opacity 300ms ease",
        }}
      />
      <g
        ref={row}
        className={tone.bars}
        style={{
          opacity: tone.barsOpacity,
          transition: "opacity 300ms ease, fill 300ms ease",
        }}
      >
        {evenlySpaced(VIEW_W / 2, LIVE_BAR_SPAN, count).map((x) => (
          <rect
            key={x}
            x={x - w / 2}
            y={PILL_CY - BAR_MIN_H / 2}
            width={w}
            height={BAR_MIN_H}
            rx={w / 2}
          />
        ))}
      </g>
    </svg>
  );
}

export const VoiceWaveform = memo(VoiceWaveformImpl);
