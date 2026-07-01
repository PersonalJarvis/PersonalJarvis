// Brand tokens + shared motion helpers for the Personal Jarvis Wiki tutorial.
// Palette is binding: warm charcoal base (never pure #000) + one gold accent.
// Adapted from the promo film's brand.ts so the whole product stays on-brand.
import { Easing, interpolate, spring } from "remotion";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";

// Load fonts at module top (deterministic render) — never inside a render body.
export const { fontFamily: INTER } = loadInter("normal", {
  weights: ["400", "500", "600", "700"],
  subsets: ["latin"],
});
export const { fontFamily: MONO } = loadMono("normal", {
  weights: ["400", "500"],
  subsets: ["latin"],
});

// --- Video config (binding: 16:9 / 1920x1080 / 30fps / 60s) ---
export const VIDEO = {
  width: 1920,
  height: 1080,
  fps: 30,
} as const;

// Scene frame budget — sums to exactly 1800 frames (60.0 s).
export const SCENES = {
  intro: { from: 0, dur: 240 },
  idea: { from: 240, dur: 330 },
  arch: { from: 570, dur: 360 },
  page: { from: 930, dur: 300 },
  read: { from: 1230, dur: 330 },
  outro: { from: 1560, dur: 240 },
} as const;
export const TOTAL_FRAMES = 1800;

// Safe margins (px) — keep all load-bearing content inside this frame.
export const MARGIN = { x: 160, y: 110 } as const;

// --- Palette (binding) ---
export const COLORS = {
  bg: "#0e0d0c", // warm charcoal — base stage
  bgDeep: "#080707", // near-black editorial punctuation
  panel: "#161412", // raised surface (cards)
  panelHi: "#1d1a17", // hovered / active surface
  headline: "#f3efe6",
  body: "#cfcbc3",
  faint: "#8f8a80", // captions / secondary
  gold: "#e7c46e", // THE single decorative accent
  goldBright: "#ffd77a", // active rows / emphasis
  green: "#94d3a6", // "added / verified"
  red: "#ff6a58", // "invalidate / rejected"
  blue: "#8bb4d6", // connectors / state
  hairline: "rgba(255,255,255,0.08)",
  hairlineGold: "rgba(231,196,110,0.35)",
} as const;

// --- Type scale (few sizes across the whole film) ---
export const TYPE = {
  hero: 96,
  h1: 68,
  h2: 46,
  body: 30,
  small: 24,
  eyebrow: 18,
  mono: 22,
} as const;

// Standard clamped interpolate.
export const lerp = (
  frame: number,
  range: [number, number],
  out: [number, number],
  easing?: (n: number) => number,
) =>
  interpolate(frame, range, out, {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing,
  });

// --- Signature easing curves ---
export const EASE = {
  outExpo: Easing.bezier(0.16, 1, 0.3, 1),
  outQuint: Easing.bezier(0.22, 1, 0.36, 1),
  outBack: Easing.bezier(0.34, 1.56, 0.64, 1),
  inOutCubic: Easing.bezier(0.65, 0, 0.35, 1),
  inCubic: Easing.in(Easing.cubic),
  outCubic: Easing.out(Easing.cubic),
} as const;

// --- Spring vocabulary (one physical signature across the film) ---
export const SPRING = {
  soft: { damping: 200, mass: 0.5, stiffness: 100 },
  pop: { damping: 12, mass: 0.6, stiffness: 200 },
  snap: { damping: 26, mass: 0.7, stiffness: 240 },
} as const;

export const springAt = (
  frame: number,
  fps: number,
  delay: number,
  config: { damping: number; mass: number; stiffness: number } = SPRING.soft,
) => spring({ fps, frame: frame - delay, config });

// Tiny continuous idle so nothing ever freezes (anti-slop). Long period, small amp.
export const breathe = (frame: number, fps: number, freq = 0.7, amp = 0.01) =>
  1 + Math.sin((frame / fps) * freq) * amp;
export const bob = (frame: number, fps: number, freq = 0.5, amp = 5) =>
  Math.sin((frame / fps) * freq) * amp;

// Standard in/out for a text block over its local scene frame.
export const enterExit = (
  frame: number,
  dur: number,
  inDur = 16,
  outDur = 12,
): React.CSSProperties => {
  const o = Math.min(
    lerp(frame, [0, inDur], [0, 1], EASE.outExpo),
    lerp(frame, [dur - outDur, dur], [1, 0], EASE.inCubic),
  );
  const y = lerp(frame, [0, inDur], [14, 0], EASE.outExpo);
  return { opacity: o, transform: `translateY(${y}px)` };
};
