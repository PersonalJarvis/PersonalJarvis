/**
 * A calm, atmospheric voice orb with soft internal weather.
 *
 * The reference is a luminous glass sphere rather than a flat status disc:
 * cool depth rises from cobalt into a pale sky, while broad cloud layers move
 * in gentle waves. Voice states only change the pace, lift and breathing of
 * those layers. Nothing orbits and there are no particles, so the perpetual
 * motion remains comfortable in a panel that may stay open all day.
 *
 * Rendering stays deliberately small: one 2D canvas, a capped device-pixel
 * ratio, no bitmap assets, and no work while the document is hidden. Reduced
 * motion users receive the same dimensional sphere as a still image.
 */
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import type { VoiceState } from "@/store/events";

interface Motion {
  /** Horizontal travel of the broad cloud field, as a radius fraction. */
  flow: number;
  /** Pace of the internal weather. */
  speed: number;
  /** Whole-sphere breathing depth, as a scale fraction. */
  breathAmp: number;
  /** Whole-sphere breathing rate in hertz. */
  breathHz: number;
  /** Vertical wave motion inside the sphere. */
  undulation: number;
  /** Overall light strength. */
  energy: number;
  /** Cross-fade from the quiet error palette into the blue palette. */
  vivid: number;
}

const MOTIONS: Record<VoiceState, Motion> = {
  idle: {
    flow: 0.07,
    speed: 0.22,
    breathAmp: 0.012,
    breathHz: 0.18,
    undulation: 0.035,
    energy: 0.82,
    vivid: 0.92,
  },
  listening: {
    flow: 0.16,
    speed: 0.58,
    breathAmp: 0.032,
    breathHz: 0.68,
    undulation: 0.085,
    energy: 1,
    vivid: 1,
  },
  thinking: {
    flow: 0.12,
    speed: 0.4,
    breathAmp: 0.018,
    breathHz: 0.34,
    undulation: 0.12,
    energy: 0.9,
    vivid: 0.96,
  },
  speaking: {
    flow: 0.2,
    speed: 0.9,
    breathAmp: 0.047,
    breathHz: 1.25,
    undulation: 0.15,
    energy: 1,
    vivid: 1,
  },
  error: {
    flow: 0.02,
    speed: 0.12,
    breathAmp: 0.005,
    breathHz: 0.14,
    undulation: 0.015,
    energy: 0.58,
    vivid: 0,
  },
};

type Rgb = readonly [number, number, number];

/** From high mist to deep water. */
const SKY: readonly Rgb[] = [
  [244, 252, 251],
  [211, 243, 247],
  [126, 211, 243],
  [28, 148, 238],
  [3, 99, 222],
  [5, 48, 157],
];

/** Muted steel counterpart for a voice error. */
const STEEL: readonly Rgb[] = [
  [232, 236, 239],
  [208, 216, 222],
  [158, 174, 185],
  [99, 119, 135],
  [59, 76, 91],
  [31, 42, 52],
];

interface CloudSeed {
  x: number;
  y: number;
  width: number;
  height: number;
  phase: number;
  depth: number;
}

/** Broad overlapping shapes read as weather, never as individual particles. */
const CLOUDS: readonly CloudSeed[] = [
  { x: -0.42, y: -0.56, width: 0.68, height: 0.3, phase: 0.4, depth: 0 },
  { x: 0.12, y: -0.48, width: 0.78, height: 0.35, phase: 2.2, depth: 1 },
  { x: 0.5, y: -0.34, width: 0.58, height: 0.28, phase: 4.8, depth: 0 },
  { x: -0.34, y: -0.1, width: 0.92, height: 0.38, phase: 3.6, depth: 1 },
  { x: 0.36, y: 0.02, width: 0.82, height: 0.36, phase: 5.7, depth: 2 },
  { x: -0.08, y: 0.28, width: 1.02, height: 0.42, phase: 1.4, depth: 2 },
];

function mix(a: number, b: number, amount: number): number {
  return a + (b - a) * amount;
}

function orbColor(index: number, vivid: number): Rgb {
  const sky = SKY[index % SKY.length];
  const steel = STEEL[index % STEEL.length];
  return [
    Math.round(mix(steel[0], sky[0], vivid)),
    Math.round(mix(steel[1], sky[1], vivid)),
    Math.round(mix(steel[2], sky[2], vivid)),
  ];
}

function rgba(color: Rgb, alpha: number): string {
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`;
}

function paintCloud(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  color: Rgb,
  alpha: number,
): void {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(width, height);
  const cloud = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
  cloud.addColorStop(0, rgba(color, alpha));
  cloud.addColorStop(0.42, rgba(color, alpha * 0.72));
  cloud.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = cloud;
  ctx.beginPath();
  ctx.arc(0, 0, 1, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

export function VoiceOrb({
  state,
  size = 208,
  className,
}: {
  state: VoiceState;
  size?: number;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const redrawStillRef = useRef<(() => void) | null>(null);
  const stateRef = useRef<VoiceState>(state);
  useEffect(() => {
    stateRef.current = state;
    redrawStillRef.current?.();
  }, [state]);

  const visible = useDocumentVisible();
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false,
  );

  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!query) return;
    const update = (event: MediaQueryListEvent) => setReducedMotion(event.matches);
    setReducedMotion(query.matches);
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    const half = (size * dpr) / 2;

    const live: Motion = { ...MOTIONS[stateRef.current] };
    let last = performance.now();

    const drawFrame = (now: number) => {
      const time = now / 1000;
      const dt = Math.min(0.1, Math.max(0, (now - last) / 1000));
      last = now;

      const target = MOTIONS[stateRef.current] ?? MOTIONS.idle;
      const ease = 1 - Math.exp(-dt * 3.4);
      for (const key of Object.keys(live) as (keyof Motion)[]) {
        live[key] = mix(live[key], target[key], ease);
      }

      // Pulse inward from the canvas edge so an energetic state never clips.
      const breath =
        1 -
        live.breathAmp * 0.7 +
        live.breathAmp * 0.55 * Math.sin(2 * Math.PI * live.breathHz * time) +
        live.breathAmp * 0.15 * Math.sin(2 * Math.PI * live.breathHz * 2.1 * time + 1.7);
      const radius = (half - 2.5 * dpr) * breath;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.save();
      ctx.beginPath();
      ctx.arc(half, half, radius, 0, Math.PI * 2);
      ctx.clip();

      // The vertical atmosphere provides depth before any moving layer is added.
      const body = ctx.createLinearGradient(half, half - radius, half, half + radius);
      body.addColorStop(0, rgba(orbColor(0, live.vivid), 1));
      body.addColorStop(0.28, rgba(orbColor(1, live.vivid), 1));
      body.addColorStop(0.58, rgba(orbColor(3, live.vivid), 1));
      body.addColorStop(0.82, rgba(orbColor(4, live.vivid), 1));
      body.addColorStop(1, rgba(orbColor(5, live.vivid), 1));
      ctx.fillStyle = body;
      ctx.fillRect(half - radius, half - radius, radius * 2, radius * 2);

      // Slow layers move laterally and rise/fall as waves. Their large scale and
      // overlap avoid the busy, orbiting-particle look of the previous orb.
      CLOUDS.forEach((cloud) => {
        const phase = time * live.speed + cloud.phase;
        const x =
          half +
          radius * cloud.x +
          radius * live.flow * Math.sin(phase * (0.72 + cloud.depth * 0.08));
        const y =
          half +
          radius * cloud.y +
          radius * live.undulation * Math.sin(phase * 0.86 + cloud.phase * 0.6);
        const swell = 1 + 0.08 * Math.sin(phase * 0.54 + cloud.phase);
        const color = orbColor(Math.min(2, cloud.depth), live.vivid);
        paintCloud(
          ctx,
          x,
          y,
          radius * cloud.width * swell,
          radius * cloud.height * swell,
          color,
          (0.3 - cloud.depth * 0.035) * live.energy,
        );
      });

      // A translucent upwelling keeps the lower blue from becoming a flat band.
      paintCloud(
        ctx,
        half + radius * live.flow * 0.6 * Math.sin(time * live.speed * 0.55 + 1.2),
        half + radius * 0.48,
        radius * 0.9,
        radius * 0.55,
        orbColor(3, live.vivid),
        0.34 * live.energy,
      );

      // Edge shade and a restrained top reflection finish the glass volume.
      const vignette = ctx.createRadialGradient(
        half - radius * 0.16,
        half - radius * 0.2,
        radius * 0.2,
        half,
        half,
        radius,
      );
      vignette.addColorStop(0.54, "rgba(3, 16, 38, 0)");
      vignette.addColorStop(0.86, "rgba(3, 16, 38, 0.05)");
      vignette.addColorStop(1, "rgba(3, 16, 38, 0.34)");
      ctx.fillStyle = vignette;
      ctx.fillRect(half - radius, half - radius, radius * 2, radius * 2);

      const reflection = ctx.createRadialGradient(
        half - radius * 0.38,
        half - radius * 0.5,
        0,
        half - radius * 0.3,
        half - radius * 0.42,
        radius * 0.72,
      );
      reflection.addColorStop(0, `rgba(255, 255, 255, ${0.25 * live.energy})`);
      reflection.addColorStop(0.42, `rgba(255, 255, 255, ${0.08 * live.energy})`);
      reflection.addColorStop(1, "rgba(255, 255, 255, 0)");
      ctx.fillStyle = reflection;
      ctx.fillRect(half - radius, half - radius, radius * 2, radius * 2);

      ctx.restore();

      // A hairline rim keeps the silhouette crisp against the dark workspace.
      ctx.save();
      ctx.beginPath();
      ctx.arc(half, half, radius - 0.5 * dpr, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(216, 244, 251, ${0.18 * live.energy})`;
      ctx.lineWidth = dpr;
      ctx.stroke();
      ctx.restore();
    };

    if (reducedMotion) {
      const drawStill = () => {
        Object.assign(live, MOTIONS[stateRef.current] ?? MOTIONS.idle);
        const now = performance.now();
        last = now;
        drawFrame(now);
      };
      redrawStillRef.current = drawStill;
      drawStill();
      return () => {
        if (redrawStillRef.current === drawStill) redrawStillRef.current = null;
      };
    }

    redrawStillRef.current = null;
    let raf = requestAnimationFrame(function loop(now: number) {
      drawFrame(now);
      raf = requestAnimationFrame(loop);
    });
    return () => cancelAnimationFrame(raf);
  }, [visible, reducedMotion, size]);

  return (
    <canvas
      ref={canvasRef}
      data-testid="voice-orb-canvas"
      data-state={state}
      aria-hidden="true"
      className={cn("block rounded-full bg-transparent", className)}
      style={{ width: size, height: size }}
    />
  );
}
