/**
 * A compact voice orb made from softly evolving procedural weather.
 *
 * The visual target is a luminous, cloud-filled presence rather than a glossy
 * gradient ball. A low-resolution fractal field is color-mapped through the
 * product's ivory, gold and amber palette, then enlarged with interpolation.
 * That deliberate softness creates organic depth without visible bands,
 * outlines, rotating particles or image assets.
 *
 * Voice states alter pace, breathing, turbulence and highlight energy while
 * the color identity stays stable. Rendering pauses with the document, runs
 * at a capped 20 fps, and becomes a state-aware still for reduced motion.
 */
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import type { VoiceState } from "@/store/events";

interface Motion {
  /** Pace of the slowly evolving cloud field. */
  speed: number;
  /** Whole-sphere breathing depth, as a scale fraction. */
  breathAmp: number;
  /** Whole-sphere breathing rate in hertz. */
  breathHz: number;
  /** Strength of the domain-warped cloud shapes. */
  turbulence: number;
  /** Brightness of the cream cloud highlights. */
  energy: number;
}

const MOTIONS: Record<VoiceState, Motion> = {
  idle: { speed: 0.18, breathAmp: 0.006, breathHz: 0.16, turbulence: 0.82, energy: 0.86 },
  listening: { speed: 0.52, breathAmp: 0.02, breathHz: 0.62, turbulence: 1, energy: 1 },
  thinking: { speed: 0.36, breathAmp: 0.012, breathHz: 0.32, turbulence: 0.96, energy: 0.94 },
  speaking: { speed: 0.82, breathAmp: 0.028, breathHz: 1.05, turbulence: 1.04, energy: 1 },
  error: { speed: 0.12, breathAmp: 0.004, breathHz: 0.12, turbulence: 0.72, energy: 0.72 },
};

type Rgb = readonly [number, number, number];

const IVORY: Rgb = [255, 250, 235];
const PALE_GOLD: Rgb = [248, 226, 151];
const SIGNAL_GOLD: Rgb = [231, 196, 110];
const AMBER: Rgb = [210, 147, 24];
const DEEP_AMBER: Rgb = [126, 66, 2];
const CLOUD_LIGHT: Rgb = [255, 249, 216];
// 48² pixels at 20 fps keeps this decorative renderer near 553k value-noise
// samples per second, leaving the main thread available for terminal streaming.
const TEXTURE_SIZE = 48;
const NOISE_SIZE = 64;
const FRAME_INTERVAL_MS = 1000 / 20;

function mix(a: number, b: number, amount: number): number {
  return a + (b - a) * amount;
}

function clamp(value: number, min = 0, max = 1): number {
  return Math.min(max, Math.max(min, value));
}

function smoothstep(edge0: number, edge1: number, value: number): number {
  const position = clamp((value - edge0) / (edge1 - edge0));
  return position * position * (3 - 2 * position);
}

/** A stable tile of pseudo-random values avoids expensive trigonometry per pixel. */
const NOISE_GRID = (() => {
  const grid = new Float32Array(NOISE_SIZE * NOISE_SIZE);
  let seed = 0x51f15e;
  for (let index = 0; index < grid.length; index += 1) {
    seed = (Math.imul(seed, 1_664_525) + 1_013_904_223) >>> 0;
    grid[index] = seed / 0xffffffff;
  }
  return grid;
})();

function gridValue(x: number, y: number): number {
  return NOISE_GRID[((y & (NOISE_SIZE - 1)) * NOISE_SIZE) + (x & (NOISE_SIZE - 1))];
}

function noiseAt(x: number, y: number): number {
  const left = Math.floor(x);
  const top = Math.floor(y);
  const tx = x - left;
  const ty = y - top;
  const sx = tx * tx * (3 - 2 * tx);
  const sy = ty * ty * (3 - 2 * ty);
  const upper = mix(gridValue(left, top), gridValue(left + 1, top), sx);
  const lower = mix(gridValue(left, top + 1), gridValue(left + 1, top + 1), sx);
  return mix(upper, lower, sy);
}

function fractalNoise(x: number, y: number): number {
  let value = 0;
  let amplitude = 0.54;
  let normalizer = 0;
  for (let octave = 0; octave < 3; octave += 1) {
    value += noiseAt(x, y) * amplitude;
    normalizer += amplitude;
    x = x * 2.03 + 11.7;
    y = y * 2.01 - 7.9;
    amplitude *= 0.5;
  }
  return value / normalizer;
}

function paintWeather(image: ImageData, weatherPhase: number, motion: Motion): void {
  const phase = weatherPhase * 0.1;
  const data = image.data;

  for (let y = 0; y < TEXTURE_SIZE; y += 1) {
    const ny = (y / (TEXTURE_SIZE - 1)) * 2 - 1;
    for (let x = 0; x < TEXTURE_SIZE; x += 1) {
      const nx = (x / (TEXTURE_SIZE - 1)) * 2 - 1;
      const warpX = fractalNoise(nx * 1.3 + phase + 8.2, ny * 1.22 - phase * 0.7 + 3.4);
      const warpY = fractalNoise(nx * 1.18 - phase * 0.55 + 19.7, ny * 1.35 + phase + 12.1);
      const cloudField = fractalNoise(
        nx * 1.85 + warpX * motion.turbulence * 1.35 + phase,
        ny * 1.72 + warpY * motion.turbulence * 1.2 - phase * 0.8,
      );
      const detail = fractalNoise(nx * 3.15 - phase * 0.45 + 31.2, ny * 2.9 + phase * 0.35);
      const weather = cloudField * 0.76 + detail * 0.24;

      // Warping the vertical color position removes the synthetic horizon band.
      const vertical = clamp((ny + 1) * 0.5 + (weather - 0.5) * 0.28);
      let start: Rgb;
      let end: Rgb;
      let paletteMix: number;
      if (vertical < 0.22) {
        start = IVORY;
        end = PALE_GOLD;
        paletteMix = vertical / 0.22;
      } else if (vertical < 0.5) {
        start = PALE_GOLD;
        end = SIGNAL_GOLD;
        paletteMix = (vertical - 0.22) / 0.28;
      } else if (vertical < 0.76) {
        start = SIGNAL_GOLD;
        end = AMBER;
        paletteMix = (vertical - 0.5) / 0.26;
      } else {
        start = AMBER;
        end = DEEP_AMBER;
        paletteMix = (vertical - 0.76) / 0.24;
      }
      let red = mix(start[0], end[0], paletteMix);
      let green = mix(start[1], end[1], paletteMix);
      let blue = mix(start[2], end[2], paletteMix);

      const shadow =
        smoothstep(0.48, 0.66, 1 - weather) * smoothstep(0.28, 0.95, vertical) * 0.18;
      red = mix(red, DEEP_AMBER[0], shadow);
      green = mix(green, DEEP_AMBER[1], shadow);
      blue = mix(blue, DEEP_AMBER[2], shadow);

      // Large cream masses form the soft, irregular clouds visible in the target.
      const cloud = smoothstep(0.46, 0.64, weather) * (1 - vertical * 0.32);
      const cloudMix = cloud * 0.78 * motion.energy;
      red = mix(red, CLOUD_LIGHT[0], cloudMix);
      green = mix(green, CLOUD_LIGHT[1], cloudMix);
      blue = mix(blue, CLOUD_LIGHT[2], cloudMix);

      // A second, quieter field breaks up any remaining uniform areas.
      const shimmer = smoothstep(0.58, 0.76, warpX * 0.55 + detail * 0.45);
      const shimmerMix = shimmer * 0.24 * motion.energy;
      red = mix(red, PALE_GOLD[0], shimmerMix);
      green = mix(green, PALE_GOLD[1], shimmerMix);
      blue = mix(blue, PALE_GOLD[2], shimmerMix);

      // Restrained spherical shading, with no dark outline or glossy rim.
      const radius = Math.sqrt(nx * nx + ny * ny);
      const edgeShade = smoothstep(0.7, 1, radius) * 0.1;
      const volumeLight = 1.02 + (1 - Math.min(1, radius)) * 0.05 - edgeShade;
      const offset = (y * TEXTURE_SIZE + x) * 4;
      // Deterministic sub-LSB dither prevents broad 8-bit color contours.
      const dither = ((((x * 73 + y * 151) & 255) / 255) - 0.5) * 0.8;
      data[offset] = clamp(Math.round(red * volumeLight + dither), 0, 255);
      data[offset + 1] = clamp(Math.round(green * volumeLight + dither), 0, 255);
      data[offset + 2] = clamp(Math.round(blue * volumeLight + dither), 0, 255);
      data[offset + 3] = 255;
    }
  }
}

export function VoiceOrb({
  state,
  size = 160,
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

    const texture = document.createElement("canvas");
    texture.width = TEXTURE_SIZE;
    texture.height = TEXTURE_SIZE;
    const textureCtx = texture.getContext("2d");
    if (!textureCtx) return;
    const weather = textureCtx.createImageData(TEXTURE_SIZE, TEXTURE_SIZE);

    const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    const half = (size * dpr) / 2;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";

    const live: Motion = { ...MOTIONS[stateRef.current] };
    let last = performance.now();
    let weatherPhase = 0;
    let breathPhase = 0;

    const drawFrame = (now: number) => {
      const dt = Math.min(0.1, Math.max(0, (now - last) / 1000));
      last = now;

      const target = MOTIONS[stateRef.current] ?? MOTIONS.idle;
      const ease = 1 - Math.exp(-dt * 3.2);
      for (const key of Object.keys(live) as (keyof Motion)[]) {
        live[key] = mix(live[key], target[key], ease);
      }
      weatherPhase += dt * live.speed;
      breathPhase += dt * live.breathHz * Math.PI * 2;

      const breath =
        1 -
        live.breathAmp * 0.65 +
        live.breathAmp * 0.52 * Math.sin(breathPhase) +
        live.breathAmp * 0.13 * Math.sin(breathPhase * 2.05 + 1.4);
      const radius = (half - 0.75 * dpr) * breath;

      paintWeather(weather, weatherPhase, live);
      textureCtx.putImageData(weather, 0, 0);

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.save();
      ctx.beginPath();
      ctx.arc(half, half, radius, 0, Math.PI * 2);
      ctx.clip();
      ctx.drawImage(texture, half - radius, half - radius, radius * 2, radius * 2);
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
    let lastPaint = performance.now();
    drawFrame(lastPaint);
    let raf = requestAnimationFrame(function loop(now: number) {
      if (now - lastPaint >= FRAME_INTERVAL_MS) {
        drawFrame(now);
        lastPaint = now;
      }
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
