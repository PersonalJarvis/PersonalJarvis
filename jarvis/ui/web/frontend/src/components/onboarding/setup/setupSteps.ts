/**
 * First-run setup as data, in two acts.
 *
 * 1. The setup window: one centred dialog with three steps — the
 *    assistant's name (which is also its wake word), connecting an AI
 *    (subscriptions and an API key), and how the user talks to it.
 * 2. The walk: the user's pet walks the REAL app and explains each section
 *    in place — chat, voice, agents, the Agentic IDE, plugins and where the
 *    wake word lives. The walk is one persisted step (`tour`); its stops
 *    resume from session storage.
 */
import type { PetState } from "@/lib/petStates";
import type { SectionId } from "@/store/events";
import type { HomeSurface } from "@/lib/homeSurface";
import type { TourPlacement } from "../tour/tourSteps";

/** Must match `ONBOARDING_STEPS` in jarvis/setup/onboarding_meta.py. */
export const SETUP_STEP_IDS = ["name", "connect", "voice", "tour"] as const;

export type SetupStepId = (typeof SETUP_STEP_IDS)[number];

/** The steps shown inside the setup window, in order. */
export const WIZARD_STEP_IDS = ["name", "connect", "voice"] as const;

export type WizardStepId = (typeof WIZARD_STEP_IDS)[number];

export function isWizardStep(id: SetupStepId): id is WizardStepId {
  return (WIZARD_STEP_IDS as readonly string[]).includes(id);
}

/**
 * Where a resumed setup starts. The backend remembers the last step, so a
 * window reload lands where the user was; a fresh start (or an unknown, older
 * step id) begins with the name.
 */
export function resumeStep(saved: string | null): SetupStepId {
  const hit = SETUP_STEP_IDS.find((id) => id === saved);
  return hit ?? "name";
}

export interface WalkStop {
  /** Also the i18n key: `first_run.tour.stops.<id>`. */
  id: string;
  /** The real element the pet stands next to; omitted = said from the middle. */
  anchor?: string;
  /** Where the app goes first, so the anchor is on screen. */
  section?: SectionId;
  /** The home surface the chats section shows (the composer or the voice bar). */
  surface?: HomeSurface;
  /** Scroll the anchor to the top of its scrolling page (a Settings group). */
  scrollTo?: boolean;
  /** Only on this platform (`process.platform` names, from the backend). */
  platform?: string;
  placement: TourPlacement;
  /** How the pet looks once the line is said. */
  pet: PetState;
}

/**
 * The walk's stops. It only navigates — it never presses a control that could
 * start work or cost money (the voice bar opens a paid call, so the walk
 * points at it and leaves the click to the user). The Agentic IDE and plugins
 * are shown at their sidebar entries: opening the IDE starts its terminals.
 */
export const WALK_STOPS: readonly WalkStop[] = [
  { id: "chat", anchor: "chat-composer", section: "chats", surface: "chat", placement: "above", pet: "talking" },
  { id: "voice", anchor: "voice-bar", section: "chats", surface: "voice", placement: "below", pet: "listening" },
  { id: "agents", anchor: "nav-agents", section: "chats", placement: "right", pet: "thinking" },
  { id: "ide", anchor: "nav-agentic-ide", placement: "right", pet: "thinking" },
  { id: "plugins", anchor: "nav-plugins", placement: "right", pet: "thinking" },
  { id: "wake", anchor: "settings-wake-word", section: "settings", scrollTo: true, placement: "left", pet: "listening" },
  // There is no permissions stop: macOS asks at the moment a feature needs
  // its permission, never as a stop of its own on first run.
  { id: "done", section: "chats", surface: "chat", placement: "inside", pet: "success" },
];

/** The stops this machine walks. A failed platform probe drops platform-only stops. */
export function walkStopsFor(platform: string | null): WalkStop[] {
  return WALK_STOPS.filter((stop) => !stop.platform || stop.platform === platform);
}
