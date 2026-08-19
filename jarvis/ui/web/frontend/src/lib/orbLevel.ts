import type { VoiceState } from "@/store/events";

/**
 * The one number the orb lives by — its LEVEL, 0..1 — and where it comes
 * from in each voice state. The deck orb (DeckOrb.tsx) runs a single
 * animation-frame loop that computes this and writes it to one CSS custom
 * property (`--orb-level`) on the orb's root; everything that reacts — the
 * core's size and brightness, the glow, the corona's rays, the rings, the
 * level arc on the bezel — reads that variable in CSS. One source, one
 * smoothing, one look; nothing fights.
 *
 * What drives it (maintainer, 2026-08-19: "when you speak the sun moved —
 * that, on the next level — and not just the core, all of it, smooth"):
 *
 *   listening   the REAL microphone level (`lib/voiceInputLevel`), the same
 *               samples the header's waveform draws — loud is big and bright,
 *               a consonant is a flick, silence is still
 *   speaking    the assistant's speech — there is no output-level tap on
 *               any platform (the audio plays in the backend or the browser
 *               without a meter), so this is a speech-shaped envelope keyed
 *               to the truthful speaking state: syllables, not a sine
 *   thinking    a steady heartbeat
 *   connecting  a small, quick flicker while the line comes up
 *   else        0 — the idle breathing and the listening ping are CSS of
 *               their own (index.css) and do not go through the level
 *
 * Smoothing is asymmetric — a quick attack so a consonant lands at once, a
 * softer release so syllables do not make the orb nervous — the same
 * figures the classic surface's orb uses. An ONSET (a jump up) spawns one
 * ripple, rate-limited, so a burst of speech reads as waves leaving the sun.
 *
 * Pure: no DOM, no timers, so orbLevel.test.ts can pin the curves.
 */
export type OrbDrive = "idle" | "mic" | "speech" | "pulse" | "flicker";

export function orbDriveFor(state: VoiceState): OrbDrive {
  switch (state) {
    case "listening":
      return "mic";
    case "speaking":
      return "speech";
    case "thinking":
      return "pulse";
    case "connecting":
      return "flicker";
    default:
      return "idle";
  }
}

/** Attack and release rates, per second (the exponential ease constants). */
export const ATTACK = 24;
export const RELEASE = 9;

/** One smoothing step towards `target` over `dt` seconds. */
export function smoothOrbLevel(prev: number, target: number, dt: number): number {
  const t = clamp01(target);
  const rate = t > prev ? ATTACK : RELEASE;
  const ease = 1 - Math.exp(-Math.max(0, dt) * rate);
  return prev + (t - prev) * ease;
}

/**
 * A speech-shaped envelope at `t` seconds since speaking began: three slow
 * modulations at speech-like rates (syllables ~4/s, words ~1.5/s, phrases
 * ~0.4/s) on a floor, clipped to 0..1, with a short swell at the onset so
 * the first word lands. Deterministic, so two orbs speak the same.
 */
export function speechEnvelope(t: number): number {
  if (t < 0) return 0;
  const onset = Math.min(1, t / 0.18);
  const syllables = 0.5 + 0.5 * Math.sin(t * 2 * Math.PI * 4.1);
  const words = 0.5 + 0.5 * Math.sin(t * 2 * Math.PI * 1.4 + 0.9);
  const phrase = 0.6 + 0.4 * Math.sin(t * 2 * Math.PI * 0.37 + 2.1);
  const v = (0.18 + 0.62 * syllables * (0.55 + 0.45 * words)) * phrase;
  return clamp01(v * onset);
}

/** The thinking heartbeat: a double beat, 0.9 s apart. */
export function pulseEnvelope(t: number): number {
  if (t < 0) return 0;
  const beat = (t % 0.9) / 0.9;
  const a = Math.exp(-Math.pow((beat - 0.08) / 0.06, 2));
  const b = 0.55 * Math.exp(-Math.pow((beat - 0.3) / 0.07, 2));
  return clamp01(0.06 + 0.38 * (a + b));
}

/** The connecting flicker: small and quick. */
export function flickerEnvelope(t: number): number {
  if (t < 0) return 0;
  return clamp01(0.1 + 0.1 * (0.5 + 0.5 * Math.sin(t * 2 * Math.PI * 2.6)));
}

export function driveTarget(drive: OrbDrive, t: number, mic: number): number {
  switch (drive) {
    case "mic":
      return clamp01(mic);
    case "speech":
      return speechEnvelope(t);
    case "pulse":
      return pulseEnvelope(t);
    case "flicker":
      return flickerEnvelope(t);
    default:
      return 0;
  }
}

/** How much the level must jump in one step to count as an onset. */
export const ONSET_JUMP = 0.1;
/** The least time between two ripples — a burst is one wave, not a strobe. */
export const RIPPLE_MIN_GAP_MS = 260;
/** Only a level this high rings at all — breath and room noise do not. */
export const ONSET_FLOOR = 0.22;

export function isOnset(prev: number, next: number, sinceLastRippleMs: number): boolean {
  return next >= ONSET_FLOOR && next - prev >= ONSET_JUMP && sinceLastRippleMs >= RIPPLE_MIN_GAP_MS;
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return v < 0 ? 0 : v > 1 ? 1 : v;
}
