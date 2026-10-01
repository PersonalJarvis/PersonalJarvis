/**
 * First-run setup as data: the steps the guide walks through INSIDE the real
 * app. There is no separate setup screen — each step opens the app's own
 * place for the job (the API Keys page and its Agents tab, the wake-word
 * group in Settings) and
 * points at it, so what the user learns on day one is where things live.
 */
import type { SectionId } from "@/store/events";
import type { TourPlacement } from "../tour/tourSteps";

/** Must match `ONBOARDING_STEPS` in jarvis/setup/onboarding_meta.py. */
// Permissions precede voice so the macOS microphone grant exists before the
// wake-word group's own microphone test.
export const SETUP_STEP_IDS = ["welcome", "how", "keys", "subscriptions", "permissions", "voice", "ready"] as const;

export type SetupStepId = (typeof SETUP_STEP_IDS)[number];

export interface SetupStep {
  id: SetupStepId;
  /** Where the app goes before the step points; omitted = stay where it is. */
  section?: SectionId;
  /** The `data-tour` element the step points at; omitted = a centred card. */
  anchor?: string;
  /** The API Keys tab to show; omitted = the page's default tab. */
  apiKeysTab?: string;
  /** Scroll the anchor to the top of its scrolling page first (a Settings group). */
  scrollTo?: boolean;
  placement: TourPlacement;
  /** Card width in px — the consent and the review need more room. */
  width: number;
}

export const SETUP_STEPS: Record<SetupStepId, SetupStep> = {
  welcome: { id: "welcome", placement: "inside", width: 420 },
  // The pet's walk through the real app (HowWalk) places itself beat by beat.
  how: { id: "how", placement: "inside", width: 500 },
  keys: { id: "keys", section: "apikeys", anchor: "apikeys-page", placement: "left", width: 320 },
  subscriptions: {
    id: "subscriptions",
    section: "apikeys",
    apiKeysTab: "subagents",
    // The subscription rows themselves (Connect buttons), scrolled into view —
    // the tab opens on model settings further up.
    anchor: "apikeys-subscriptions",
    scrollTo: true,
    placement: "left",
    width: 340,
  },
  voice: {
    id: "voice",
    section: "settings",
    anchor: "settings-wake-word",
    scrollTo: true,
    placement: "left",
    width: 320,
  },
  permissions: {
    id: "permissions",
    section: "settings",
    anchor: "settings-permissions",
    scrollTo: true,
    placement: "left",
    width: 320,
  },
  ready: { id: "ready", section: "chats", placement: "inside", width: 400 },
};

/**
 * The steps this machine walks. Only macOS asks for permissions one ability
 * at a time; Windows, Linux and a failed platform probe leave that step out
 * (Settings stays the way to grant them later).
 */
export function stepsFor(platform: string | null): SetupStepId[] {
  return SETUP_STEP_IDS.filter((id) => id !== "permissions" || platform === "darwin");
}

/**
 * Where a resumed setup starts. The backend remembers the last step, so a
 * window reload lands where the user was; a fresh start (or an unknown, old
 * step id) begins at the welcome.
 */
export function resumeStep(steps: readonly SetupStepId[], saved: string | null): SetupStepId {
  const hit = steps.find((id) => id === saved);
  return hit ?? "welcome";
}
