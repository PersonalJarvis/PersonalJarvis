import { useEffect, useRef } from "react";

import { useEventStore } from "@/store/events";
import { driveTarget, orbDriveFor, smoothOrbLevel } from "@/lib/orbLevel";
import { readVoiceInputLevel } from "@/lib/voiceInputLevel";
import {
  createField,
  driveFor,
  excitedModes,
  fillField,
  grainCountFor,
  grainThrow,
  plateModes,
  reflect,
  sampleField,
  THROW_THRESHOLD,
  type PlateField,
  type PlateMode,
} from "@/lib/chladni";

/**
 * The whole background is a vibrating plate with sand on it.
 *
 * The physics is in `lib/chladni.ts` — a driven rectangular Kirchhoff plate,
 * its modes picked by a drive frequency and an impact point, and grains that
 * are thrown only where the plate actually moves. This file is the wiring:
 * it owns the canvas, the clock and the two budgets that make a permanent
 * background animation affordable.
 *
 * TWO CLOCKS, deliberately. The FIELD is expensive and slow-moving, so it is
 * rebuilt about ten times a second. The GRAINS are cheap and are what the eye
 * follows, so they walk every frame at up to thirty. Rebuilding the field per
 * frame would triple the cost to animate something nobody can see change.
 *
 * It also stops dead when the window is hidden, settles to one still figure
 * under reduced motion, and scales its grain count to the window's area.
 *
 * Both themes are first class and are not inversions of each other. On the
 * dark ground the sand is hot yellow drawn ADDITIVELY, so grains piling onto
 * a node line glow. On the light ground additive blending is invisible, so
 * the sand turns dark and is drawn normally — ink on paper instead of light
 * in a room. Trails are erased with `destination-out` rather than painted
 * over with translucent black, because this canvas sits ON TOP of the user's
 * wallpaper and a black wash would dim the picture they chose.
 */
export function ChladniField({ className }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const voiceState = useEventStore((s) => s.voiceState);
  // The loop reads the voice through a ref so a state change retunes the
  // plate instead of tearing the whole simulation down and scattering it.
  const voiceRef = useRef(voiceState);
  voiceRef.current = voiceState;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const dpr = Math.min(2, window.devicePixelRatio || 1);

    /** The field grid. Coarse on purpose — the plate is smooth, and the
        grains read it with bilinear interpolation, so more columns would buy
        arithmetic and no detail. */
    const COLS = 168;
    const MAX_MODES = 8;

    let w = 0;
    let h = 0;
    let aspect = 1;
    let rows = 94;
    let field: PlateField = createField(COLS, rows, MAX_MODES);
    let modes: PlateMode[] = plateModes(aspect, 1, 11);
    let px = new Float32Array(0);
    let py = new Float32Array(0);
    let count = 0;

    const measure = () => {
      const rect = canvas.getBoundingClientRect();
      const nextW = Math.max(1, Math.round(rect.width));
      const nextH = Math.max(1, Math.round(rect.height));
      if (nextW === w && nextH === h) return;
      w = nextW;
      h = nextH;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // A wide plate is a wide plate: the aspect ratio goes into the
      // eigenvalues, so the modes come out with square-ish cells instead of
      // a square figure stretched across a widescreen window.
      aspect = w / h;
      rows = Math.max(24, Math.round(COLS / Math.max(0.2, aspect)));
      field = createField(COLS, rows, MAX_MODES);
      modes = plateModes(aspect, 1, 11);

      const next = grainCountFor(w, h);
      if (next !== count) {
        count = next;
        px = new Float32Array(count);
        py = new Float32Array(count);
        for (let i = 0; i < count; i++) {
          px[i] = Math.random();
          py[i] = Math.random();
        }
      }
    };
    measure();

    const isDark = () => document.documentElement.classList.contains("dark");

    let level = 0;
    let raf = 0;
    let lastGrain = 0;
    let lastFieldAt = -1e9;
    let stopped = false;
    const t0 = performance.now();

    /** Rebuild the standing wave for the drive as it is right now. */
    function retune(now: number) {
      const { f, q } = driveFor(voiceRef.current, level);
      // The stick wanders. A plate struck in one fixed spot rings the same
      // handful of modes forever; moving the impact keeps the figure turning
      // over without anyone animating the figure itself.
      const t = (now - t0) / 1000;
      const strikeX = 0.5 + 0.28 * Math.sin(t * 0.07) * Math.cos(t * 0.031);
      const strikeY = 0.5 + 0.24 * Math.cos(t * 0.053);
      fillField(field, excitedModes(modes, f, q, strikeX, strikeY, MAX_MODES));
    }

    /** Scratch for one grain's displacement — see grainThrow. */
    const out = new Float32Array(2);

    function walk(dt: number) {
      // How far a thrown grain travels. The voice is the hammer: louder means
      // the plate throws harder and the sand rearranges faster.
      const step = (0.01 + level * 0.03) * Math.min(2, dt * 60);
      const { cols, w: fw, gx: fgx, gy: fgy } = field;
      const r = rows;
      for (let i = 0; i < count; i++) {
        const x = px[i];
        const y = py[i];
        const wv = sampleField(fw, cols, r, x, y);
        // A grain the plate cannot throw does not move, so it does not need
        // the slope either. Once the figure has settled most of the sand is
        // sitting on a node line, which makes this the difference between
        // three field reads per grain and one.
        if ((wv < 0 ? -wv : wv) <= THROW_THRESHOLD) continue;
        const gx = sampleField(fgx, cols, r, x, y);
        const gy = sampleField(fgy, cols, r, x, y);
        if (grainThrow(wv, gx, gy, step, Math.random(), Math.random(), out)) {
          px[i] = reflect(x + out[0]);
          py[i] = reflect(y + out[1]);
        }
      }
    }

    // An arrow, not a `function` declaration: a hoisted declaration is
    // analysed as if it ran before the `if (!ctx) return` above it, so the
    // narrowing is lost and every use of ctx reads as possibly null.
    const paint = () => {
      const dark = isDark();
      ctx.globalCompositeOperation = "destination-out";
      ctx.fillStyle = "rgba(0, 0, 0, 0.3)";
      ctx.fillRect(0, 0, w, h);

      ctx.globalCompositeOperation = dark ? "lighter" : "source-over";
      ctx.fillStyle = dark
        ? `rgba(255, 232, 120, ${(0.3 + level * 0.34).toFixed(3)})`
        : `rgba(58, 46, 16, ${(0.26 + level * 0.26).toFixed(3)})`;
      const size = dark ? 1.25 : 1.15;
      for (let i = 0; i < count; i++) {
        ctx.fillRect(px[i] * w, py[i] * h, size, size);
      }
      ctx.globalCompositeOperation = "source-over";
    };

    /** Let the sand find the node lines before anyone looks at it. */
    const settle = (rounds: number) => {
      retune(performance.now());
      for (let i = 0; i < rounds; i++) walk(1 / 30);
    };

    if (reduced) {
      settle(220);
      paint();
      const ro = new ResizeObserver(() => {
        measure();
        settle(220);
        paint();
      });
      ro.observe(canvas);
      return () => ro.disconnect();
    }

    const tick = (now: number) => {
      if (stopped) return;
      raf = requestAnimationFrame(tick);

      const drive = orbDriveFor(voiceRef.current);
      const target =
        drive === "idle" ? 0 : driveTarget(drive, (now - t0) / 1000, readVoiceInputLevel(now));

      // A quiet plate is redrawn ten times a second, a busy one thirty. The
      // figure at rest barely moves, and nobody is watching the wallpaper
      // while nothing is being said.
      const budget = level > 0.02 || target > 0.02 ? 1000 / 30 : 1000 / 10;
      const elapsed = now - lastGrain;
      if (elapsed < budget) return;
      lastGrain = now;

      const dt = Math.min(0.2, elapsed / 1000);
      level = smoothOrbLevel(level, target, dt);

      // The slow clock: the standing wave itself.
      if (now - lastFieldAt > 100) {
        lastFieldAt = now;
        retune(now);
      }

      walk(dt);
      paint();
    };

    const start = () => {
      if (stopped || raf) return;
      lastGrain = 0;
      raf = requestAnimationFrame(tick);
    };
    const halt = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    };
    // Nothing runs behind a hidden window. This is the whole reason a
    // permanent background animation is affordable at all.
    const onVisibility = () => (document.hidden ? halt() : start());
    document.addEventListener("visibilitychange", onVisibility);

    const ro = new ResizeObserver(() => measure());
    ro.observe(canvas);

    settle(120);
    if (!document.hidden) start();

    return () => {
      stopped = true;
      halt();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={canvasRef} aria-hidden data-testid="chladni-field" className={className} />;
}
