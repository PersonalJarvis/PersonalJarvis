/**
 * The first-run guide as data: which steps ("beats") exist, in which order,
 * how wide the card is on each, and how the mascot greets each one.
 *
 * The guide is ONE card that changes shape between beats instead of a stack
 * of screens. Everything a beat needs to know about its neighbours lives here,
 * so the card itself stays a dumb frame and the order can be tested without
 * rendering anything.
 */

/** Must match `ONBOARDING_STEPS` in jarvis/setup/onboarding_meta.py. */
export const BEAT_IDS = ["welcome", "brain", "agents", "permissions", "voice", "ready"] as const;

export type BeatId = (typeof BEAT_IDS)[number];

/**
 * The beats this machine shows. macOS is the only platform that asks for
 * permissions one capability at a time; Windows and Linux never see that beat.
 * An unknown platform (the probe failed) keeps it out too — the Settings panel
 * stays the recovery path, and a guide that shows seven rows of "unknown" is
 * worse than one that skips them.
 */
export function beatsFor(platform: string | null): BeatId[] {
  return BEAT_IDS.filter((beat) => beat !== "permissions" || platform === "darwin");
}

export function nextBeat(beats: readonly BeatId[], current: BeatId): BeatId | null {
  const i = beats.indexOf(current);
  return i >= 0 && i < beats.length - 1 ? beats[i + 1] : null;
}

export function previousBeat(beats: readonly BeatId[], current: BeatId): BeatId | null {
  const i = beats.indexOf(current);
  return i > 0 ? beats[i - 1] : null;
}

/**
 * Where a resumed guide starts. The backend remembers the last beat reached,
 * so a window reload (a frontend build reloads open windows by itself) lands
 * where the user was instead of back at the consent. Consent must exist
 * before anything after it, so without accepted terms it is always welcome.
 */
export function resumeBeat(
  beats: readonly BeatId[],
  saved: string | null,
  termsAccepted: boolean,
): BeatId {
  if (!termsAccepted) return "welcome";
  const hit = beats.find((b) => b === saved);
  return hit ?? (beats[1] ?? "welcome");
}

/**
 * The card's maximum width per beat, in px. The width is the one thing that
 * visibly changes between beats; title and mascot glide to their new places.
 * Provider lists and agent cards need room, a consent does not.
 */
export const CARD_WIDTH: Record<BeatId, number> = {
  welcome: 520,
  brain: 640,
  agents: 640,
  permissions: 560,
  voice: 560,
  ready: 520,
};

/** The one-shot mascot move each beat opens with. */
export const MASCOT_CUE: Record<BeatId, "wave" | "look-left" | "look-right" | "jump" | "spin"> = {
  welcome: "wave",
  brain: "look-right",
  agents: "look-left",
  permissions: "look-right",
  voice: "look-left",
  ready: "jump",
};
