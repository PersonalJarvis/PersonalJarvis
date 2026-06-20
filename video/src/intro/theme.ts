import { loadFont as loadInter } from "@remotion/google-fonts/Inter";

// Brand palette mirrors the desktop app (jarvis/ui/web/frontend/src/index.css):
// matte-black background + signal-yellow accent.
export const COLORS = {
  bg: "#0A0A0A",
  bgElevated: "#141414",
  bgCard: "#181818",
  primary: "#FFD60A",
  primaryDeep: "#E6BE00",
  text: "#FAFAFA",
  textMuted: "#9A9A9A",
  textFaint: "#6B6B6B",
  border: "rgba(255,255,255,0.10)",
  borderStrong: "rgba(255,255,255,0.18)",
  good: "#4ADE80",
  primaryGlow: "rgba(255,214,10,0.35)",
} as const;

const inter = loadInter("normal", {
  weights: ["500", "600", "700", "800"],
  subsets: ["latin"],
  ignoreTooManyRequestsWarning: true,
});
export const FONT = inter.fontFamily;

export const VIDEO = {
  width: 1280,
  height: 720,
  fps: 30,
} as const;

// Scene durations in frames (30 fps). Sum drives the composition length.
export const SCENES = {
  brand: 6 * VIDEO.fps, // 0 — short + dynamic; a long static intro felt boring
  wakeWord: 15 * VIDEO.fps, // 1 — emphasised: user picks their own wake word
  voiceChat: 12 * VIDEO.fps, // 2
  computerUse: 18 * VIDEO.fps, // 3 — Chrome live demo
  subAgents: 15 * VIDEO.fps, // 4 — one agent, a hard deep-dive job
  moreFeatures: 16 * VIDEO.fps, // 5 — plugins + integrations
  outro: 7 * VIDEO.fps, // 6
} as const;

// Consecutive scenes overlap by this many frames for a true crossfade
// (otherwise a hard cut shows only the background for a beat between scenes).
export const OVERLAP = 16;

const _durs = Object.values(SCENES);
export const TOTAL_FRAMES =
  _durs.reduce((a, b) => a + b, 0) - (_durs.length - 1) * OVERLAP;
