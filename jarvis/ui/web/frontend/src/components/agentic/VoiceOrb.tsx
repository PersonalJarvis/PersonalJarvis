/**
 * The voice orb — the assistant's presence as a living sphere.
 *
 * Modeled on the familiar voice-mode orbs (a soft sphere of drifting cloud
 * textures that breathes, vibrates and swells with the conversation), but in
 * this app's own brand: signal-yellow light on the dark surface, never a
 * borrowed palette.
 *
 * ## How the life-likeness is made
 *
 * The orb is a circular clip filled with a handful of soft radial-gradient
 * "clouds", each drifting on its own slow Lissajous path. Three motions layer
 * on top, all driven by the voice state:
 *
 * * **Breathing** — the whole sphere scales on a slow sine. Idle breathes
 *   slowly and shallowly; speaking breathes fast and deep, with a second
 *   out-of-phase wobble so it reads as talking rather than pulsing.
 * * **Vibration** — a small high-frequency jitter on each cloud's radius.
 *   Barely visible while idle, distinct while listening and speaking — this is
 *   the "vibrating sphere" texture of the original.
 * * **Swirl** — the cloud constellation rotates while thinking, the classic
 *   "working on it" cue.
 *
 * State changes never snap: every parameter eases toward its target a little
 * each frame, so the orb flows from listening into speaking the way the real
 * ones do.
 *
 * Honest about cost: one 2D canvas, no filters (the gradients are soft by
 * construction), the animation pauses whenever the document is hidden, and a
 * `prefers-reduced-motion` viewer gets a still sphere.
 */
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import type { VoiceState } from "@/store/events";

/** The motion parameters one voice state asks for. */
interface Motion {
  /** How far the clouds roam, as a fraction of the radius. */
  drift: number;
  /** Cloud drift speed multiplier. */
  speed: number;
  /** Whole-sphere breathing depth (fraction of scale). */
  breathAmp: number;
  /** Breathing rate, Hz. */
  breathHz: number;
  /** High-frequency cloud jitter depth — the vibration. */
  jitter: number;
  /** Constellation rotation, radians per second. */
  swirl: number;
  /** 0..1 — how much of the bright palette shows (error dims to grey). */
  vivid: number;
}

const MOTIONS: Record<VoiceState, Motion> = {
  idle: { drift: 0.16, speed: 0.5, breathAmp: 0.015, breathHz: 0.22, jitter: 0.004, swirl: 0.05, vivid: 0.8 },
  listening: { drift: 0.3, speed: 1.1, breathAmp: 0.045, breathHz: 1.0, jitter: 0.014, swirl: 0.12, vivid: 1 },
  thinking: { drift: 0.24, speed: 0.9, breathAmp: 0.02, breathHz: 0.5, jitter: 0.006, swirl: 0.9, vivid: 0.9 },
  speaking: { drift: 0.34, speed: 1.4, breathAmp: 0.07, breathHz: 2.1, jitter: 0.02, swirl: 0.18, vivid: 1 },
  error: { drift: 0.08, speed: 0.25, breathAmp: 0.008, breathHz: 0.15, jitter: 0, swirl: 0, vivid: 0 },
};

/** Brand-gold cloud palette, light to deep — the sphere's inner weather. */
const CLOUDS = [
  "255, 247, 214", // warm white
  "255, 232, 138", // pale gold
  "255, 214, 10", //  signal yellow (the brand primary)
  "255, 179, 0", //   amber
  "245, 158, 11", //  deep amber
  "255, 241, 176", // cream highlight
];

/** Grey twins of the palette, cross-faded in for the error state. */
const GREYS = ["221, 221, 221", "187, 187, 187", "153, 153, 153", "119, 119, 119", "102, 102, 102", "204, 204, 204"];

/** Fixed per-cloud motion seeds — the same weather on every mount. */
const SEEDS = [0.9, 2.1, 3.7, 4.6, 5.9, 0.3];

function mix(a: number, b: number, k: number): number {
  return a + (b - a) * k;
}

/** "r, g, b" cross-faded between the grey and vivid palettes. */
function cloudColor(index: number, vivid: number): string {
  const c = CLOUDS[index % CLOUDS.length].split(",").map(Number);
  const g = GREYS[index % GREYS.length].split(",").map(Number);
  return c.map((v, i) => Math.round(mix(g[i], v, vivid))).join(", ");
}

export function VoiceOrb({
  state,
  size = 176,
  className,
}: {
  state: VoiceState;
  size?: number;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Read by the draw loop without restarting it — a state flip mid-frame just
  // gives the easing a new target.
  const stateRef = useRef<VoiceState>(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // Nobody watches a hidden window; the sphere holds its breath until they do.
  const visible = useDocumentVisible();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return; // jsdom / a WebView without canvas — the orb stays a disc

    const dpr = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    const half = (size * dpr) / 2;

    // The live parameters, eased toward the current state's targets each frame.
    const live: Motion = { ...MOTIONS[stateRef.current] };
    let angle = 0;
    let last = performance.now();

    const drawFrame = (now: number) => {
      const t = now / 1000;
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;

      const target = MOTIONS[stateRef.current] ?? MOTIONS.idle;
      for (const key of Object.keys(live) as (keyof Motion)[]) {
        live[key] = mix(live[key], target[key], 0.055);
      }
      angle += live.swirl * dt;

      // Breathing, with the second wobble that makes "speaking" read as speech.
      const breath =
        1 +
        live.breathAmp * Math.sin(2 * Math.PI * live.breathHz * t) +
        0.35 * live.breathAmp * Math.sin(2 * Math.PI * live.breathHz * 2.7 * t + 1.3);
      const radius = (half - 2 * dpr) * breath;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.save();
      ctx.beginPath();
      ctx.arc(half, half, radius, 0, Math.PI * 2);
      ctx.clip();

      // The sphere's body: a dark-amber ground the clouds glow out of.
      const body = ctx.createRadialGradient(
        half - radius * 0.3,
        half - radius * 0.35,
        radius * 0.1,
        half,
        half,
        radius,
      );
      body.addColorStop(0, `rgba(${cloudColor(1, live.vivid)}, 0.9)`);
      body.addColorStop(0.55, `rgba(${cloudColor(3, live.vivid)}, 0.85)`);
      body.addColorStop(1, `rgba(${cloudColor(4, live.vivid)}, 0.95)`);
      ctx.fillStyle = body;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // The inner weather: soft clouds on slow individual orbits.
      ctx.globalCompositeOperation = "lighter";
      SEEDS.forEach((seed, i) => {
        const wobble = live.jitter * Math.sin(2 * Math.PI * 13 * t + seed * 7);
        const reach = radius * live.drift;
        const cx =
          half +
          reach * Math.sin(t * live.speed * (0.7 + seed * 0.13) + seed + angle) +
          reach * 0.4 * Math.sin(t * live.speed * 1.7 + seed * 2);
        const cy =
          half +
          reach * Math.cos(t * live.speed * (0.5 + seed * 0.17) + seed * 3 + angle) +
          reach * 0.4 * Math.cos(t * live.speed * 1.3 + seed);
        const r =
          radius * (0.42 + 0.22 * Math.sin(t * live.speed * 0.9 + seed * 5) + wobble);
        if (r <= 0) return;
        const cloud = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        cloud.addColorStop(0, `rgba(${cloudColor(i, live.vivid)}, 0.5)`);
        cloud.addColorStop(1, `rgba(${cloudColor(i, live.vivid)}, 0)`);
        ctx.fillStyle = cloud;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
      });
      ctx.globalCompositeOperation = "source-over";

      // A soft top-light, so it reads as a sphere rather than a disc.
      const sheen = ctx.createRadialGradient(
        half - radius * 0.35,
        half - radius * 0.45,
        0,
        half - radius * 0.35,
        half - radius * 0.45,
        radius * 1.1,
      );
      sheen.addColorStop(0, `rgba(255, 255, 255, ${0.28 * Math.max(0.4, live.vivid)})`);
      sheen.addColorStop(0.5, "rgba(255, 255, 255, 0)");
      ctx.fillStyle = sheen;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.restore();
    };

    // A viewer who asked for reduced motion gets the sphere, standing still.
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
      drawFrame(performance.now());
      return;
    }

    let raf = requestAnimationFrame(function loop(now: number) {
      drawFrame(now);
      raf = requestAnimationFrame(loop);
    });
    return () => cancelAnimationFrame(raf);
  }, [visible, size]);

  return (
    <canvas
      ref={canvasRef}
      data-testid="voice-orb-canvas"
      aria-hidden="true"
      className={cn("rounded-full", className)}
      style={{ width: size, height: size }}
    />
  );
}
