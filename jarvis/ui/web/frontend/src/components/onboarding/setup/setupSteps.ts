/**
 * First-run setup as data: the steps the guide walks through INSIDE the real
 * app. There is no separate setup screen — each step opens the app's own
 * place for the job (the API Keys page and its Agents tab, the wake-word
 * group in Settings) and
 * points at it, so what the user learns on day one is where things live.
 */
import type { SectionId } from "@/store/events";
import type { TourPlacement } from "../tour/tourSteps";

/**
 * Must match `ONBOARDING_STEPS` in jarvis/setup/onboarding_meta.py.
 *
 * There is no permissions step: macOS asks at the moment a feature needs its
 * permission (the wake-word switch on the voice step is one such moment, and
 * says so inside the spotlight hole), never as a stop of its own on first run.
 */
export const SETUP_STEP_IDS = ["welcome", "keys", "subscriptions", "voice", "ready"] as const;

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
  keys: { id: "keys", section: "apikeys", anchor: "apikeys-page", placement: "left", width: 320 },
  subscriptions: {
    id: "subscriptions",
    section: "apikeys",
    apiKeysTab: "subagents",
    anchor: "apikeys-page",
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
  ready: { id: "ready", section: "chats", placement: "inside", width: 400 },
};

/** The steps every machine walks, in order (the same list on every OS). */
export function stepsFor(): SetupStepId[] {
  return [...SETUP_STEP_IDS];
}

/**
 * A step id an older build stored that no longer exists, and where it lands.
 * `permissions` used to sit right before `voice` (macOS only); a person who was
 * on it resumes at the voice step, on every OS.
 */
const LEGACY_STEP_IDS: Readonly<Record<string, SetupStepId>> = { permissions: "voice" };

/**
 * Where a resumed setup starts. The backend remembers the last step, so a
 * window reload lands where the user was — never past the consent.
 */
export function resumeStep(
  steps: readonly SetupStepId[],
  saved: string | null,
  termsAccepted: boolean,
): SetupStepId {
  if (!termsAccepted) return "welcome";
  const wanted = saved === null ? null : (LEGACY_STEP_IDS[saved] ?? saved);
  const hit = steps.find((id) => id === wanted);
  return hit && hit !== "welcome" ? hit : (steps[1] ?? "welcome");
}
