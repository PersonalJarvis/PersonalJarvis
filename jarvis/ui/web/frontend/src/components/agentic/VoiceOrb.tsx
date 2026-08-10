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
import { readVoiceInputLevel } from "@/lib/voiceInputLevel";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import type { VoiceState } from "@/store/events";

interface Motion {
  /** Horizontal travel through the procedural weather field. */
  flowX: number;
  /** Vertical travel through the procedural weather field. */
  flowY: number;
  /** Whole-sphere breathing depth, as a scale fraction. */
  breathAmp: number;
  /** Whole-sphere breathing rate in hertz. */
  breathHz: number;
  /** Strength of the domain-warped cloud shapes. */
  turbulence: number;
  /** Brightness of the cream cloud highlights. */
  energy: number;
  /** Strength of the assistant-speech envelope. */
  voiceImpact: number;
  /**
   * How far the cloud field may drag the COLOUR ramp out of its vertical
   * order. At rest it only softens the horizon; while thinking it is what
   * makes ivory, gold and amber visibly churn through each other instead of
   * sitting in bands. Kept in step with the desktop twin (`ui/orb/voice_orb.py`).
   */
  colorChurn: number;
}

const MOTIONS: Record<VoiceState, Motion> = {
  connecting: {
    // Waiting on a transport, not on the user: busier than idle so the wait is
    // visible, calmer than thinking so it does not claim work is happening.
    // Nothing is being heard yet, hence no voice impact.
    flowX: 0.42,
    flowY: -0.12,
    breathAmp: 0.012,
    breathHz: 0.5,
    turbulence: 1.2,
    energy: 0.94,
    voiceImpact: 0,
    colorChurn: 0.7,
  },
  idle: {
    flowX: 0.025,
    flowY: 0.008,
    breathAmp: 0.004,
    breathHz: 0.14,
    turbulence: 0.8,
    energy: 0.86,
    voiceImpact: 0,
    colorChurn: 0.28,
  },
  listening: {
    flowX: 0.11,
    flowY: 0.035,
    breathAmp: 0.016,
    breathHz: 0.72,
    turbulence: 1.02,
    energy: 0.98,
    voiceImpact: 0,
    colorChurn: 0.34,
  },
  thinking: {
    // The one state with nothing to hear, so it has to be the most visibly
    // busy: the field races and the palette churns through itself.
    flowX: 0.95,
    flowY: -0.44,
    breathAmp: 0.01,
    breathHz: 0.34,
    turbulence: 1.85,
    energy: 1.08,
    voiceImpact: 0,
    colorChurn: 1.45,
  },
  speaking: {
    flowX: 0.34,
    flowY: 0.085,
    breathAmp: 0.014,
    breathHz: 0.82,
    turbulence: 1.06,
    energy: 1,
    voiceImpact: 0.11,
    colorChurn: 0.45,
  },
  paused: {
    // Stiller than idle: the session is held, not gone — the weather keeps
    // drifting, just with nothing to say and no breath worth noticing.
    flowX: 0.015,
    flowY: 0.004,
    breathAmp: 0.003,
    breathHz: 0.1,
    turbulence: 0.75,
    energy: 0.78,
    voiceImpact: 0,
    colorChurn: 0.28,
  },
  error: {
    flowX: 0.012,
    flowY: 0,
    breathAmp: 0.003,
    breathHz: 0.12,
    turbulence: 0.7,
    energy: 0.72,
    voiceImpact: 0,
    colorChurn: 0.28,
  },
};

/**
 * Resting size as a fraction of the canvas. Below 1 ON PURPOSE: the swell has
 * to have somewhere to go. At the old value the sphere already filled its box
 * at rest, every beat hit the `Math.min(1, …)` ceiling, and the result was a
 * pulse nobody could see.
 */
// Kept identical to the spawned desktop renderer (`ui/orb/voice_orb.py`).
const BASE_SCALE = 0.75;

/** How much of the remaining headroom a full-volume moment claims. */
const LEVEL_SWELL = 0.1;

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

/** Irregular syllable-like movement without pretending it is measured audio. */
function speechEnvelope(elapsed: number): number {
  const cadence = 0.5 + 0.5 * Math.sin(elapsed * Math.PI * 5.4 + Math.sin(elapsed * 1.7) * 0.8);
  const articulation = 0.5 + 0.5 * Math.sin(elapsed * Math.PI * 9.2 + 1.3);
  return smoothstep(0.38, 0.86, cadence * 0.74 + articulation * 0.26);
}

/** One soft outward beat when assistant speech begins. */
function speakingOnset(age: number): number {
  if (age < 0 || age >= 0.24) return 0;
  return Math.sin((age / 0.24) * Math.PI) * Math.exp(-age * 2.4);
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

function paintWeather(
  image: ImageData,
  phaseX: number,
  phaseY: number,
  motion: Motion,
  impact: number,
): void {
  const data = image.data;
  // Slow drift keeps the colour mixing in motion, so a thinking orb never
  // settles into a still picture.
  const churn = motion.colorChurn;
  const drift = Math.sin(phaseX * 2.4 + phaseY * 1.7) * churn * 0.2;

  for (let y = 0; y < TEXTURE_SIZE; y += 1) {
    const ny = (y / (TEXTURE_SIZE - 1)) * 2 - 1;
    for (let x = 0; x < TEXTURE_SIZE; x += 1) {
      const nx = (x / (TEXTURE_SIZE - 1)) * 2 - 1;
      // Speech briefly compresses the material while the silhouette expands.
      const sampleX = nx * (1 + impact * 0.055);
      const sampleY = ny * (1 - impact * 0.035);
      const warpX = fractalNoise(sampleX * 1.3 + phaseX + 8.2, sampleY * 1.22 + phaseY + 3.4);
      const warpY = fractalNoise(
        sampleX * 1.18 - phaseX * 0.55 + 19.7,
        sampleY * 1.35 + phaseY * 0.8 + 12.1,
      );
      const cloudField = fractalNoise(
        sampleX * 1.85 + warpX * motion.turbulence * 1.35 + phaseX,
        sampleY * 1.72 + warpY * motion.turbulence * 1.2 + phaseY,
      );
      const detail = fractalNoise(
        sampleX * 3.15 - phaseX * 0.45 + 31.2,
        sampleY * 2.9 + phaseY * 0.35,
      );
      const weather = cloudField * 0.76 + detail * 0.24;

      // Where a pixel sits on the ivory→amber ramp. Warping it by the cloud
      // field removes the synthetic horizon band at rest; opening the warp up
      // makes the palette visibly MIX rather than sit in stripes.
      const vertical = clamp((ny + 1) * 0.5 + (weather - 0.5) * churn + drift);
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
    let phaseX = 0;
    let phaseY = 0;
    let breathPhase = 0;
    let activityElapsed = 0;
    let activeState = stateRef.current;
    let speakingAge = Number.POSITIVE_INFINITY;
    let liveImpact = 0;
    let liveInput = 0;

    const drawFrame = (now: number) => {
      const dt = Math.min(0.1, Math.max(0, (now - last) / 1000));
      last = now;

      const target = MOTIONS[stateRef.current] ?? MOTIONS.idle;
      if (stateRef.current !== activeState) {
        activeState = stateRef.current;
        speakingAge = activeState === "speaking" ? 0 : Number.POSITIVE_INFINITY;
      }
      const ease = 1 - Math.exp(-dt * 3.2);
      for (const key of Object.keys(live) as (keyof Motion)[]) {
        live[key] = mix(live[key], target[key], ease);
      }
      phaseX += dt * live.flowX;
      phaseY += dt * live.flowY;
      breathPhase += dt * live.breathHz * Math.PI * 2;
      activityElapsed += dt;
      if (Number.isFinite(speakingAge)) speakingAge += dt;

      // The panel receives lifecycle states on every platform, but no reliable
      // normalized TTS output level. This choreography reacts to the truthful
      // speaking state without masquerading as a measured audio waveform.
      const speech = activeState === "speaking" ? speechEnvelope(activityElapsed) : 0;
      const onset = activeState === "speaking" ? speakingOnset(speakingAge) : 0;
      const impactTarget = live.voiceImpact * speech + 0.032 * onset;
      const impactEase = 1 - Math.exp(-dt * (impactTarget > liveImpact ? 22 : 8));
      liveImpact = mix(liveImpact, impactTarget, impactEase);

      // Native capture and browser realtime both write the real normalized
      // microphone level into a shared ref. A quick attack makes consonants
      // feel immediate; the softer release prevents a nervous flicker between
      // syllables. Only listening reacts, so stale samples cannot move the orb
      // while the assistant is thinking or speaking.
      const inputTarget = activeState === "listening" ? readVoiceInputLevel(now) : 0;
      const inputEase = 1 - Math.exp(-dt * (inputTarget > liveInput ? 24 : 9));
      liveInput = mix(liveInput, inputTarget, inputEase);
      const visualImpact = liveImpact + liveInput * LEVEL_SWELL;

      const breath =
        BASE_SCALE -
        live.breathAmp * 0.65 +
        live.breathAmp * 0.52 * Math.sin(breathPhase) +
        live.breathAmp * 0.13 * Math.sin(breathPhase * 2.05 + 1.4) +
        visualImpact;
      const radius = (half - 0.75 * dpr) * Math.min(1, breath);

      paintWeather(weather, phaseX, phaseY, live, visualImpact);
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
