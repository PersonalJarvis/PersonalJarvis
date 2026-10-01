/**
 * The small, dependency-free contract between the first-run guide and the
 * rest of the app. Its own module so callers (Settings, the Settings hub) do
 * not import the gate itself — tests mock the gate wholesale.
 */

/** Window event that asks the onboarding gate to replay the app tour. */
export const TOUR_START_EVENT = "jarvis:tour-start";

/**
 * Window event that asks the onboarding gate to replay setup (API keys, agent
 * subscriptions, wake word) as a preview, followed by the app tour.
 */
export const SETUP_REPLAY_EVENT = "jarvis:setup-replay";

/** Window event that (re)starts the first-steps guide from its first quest. */
export const FIRST_STEPS_START_EVENT = "jarvis:first-steps-start";

/** Marks the guide's dim and card, so dialogs can tell its clicks apart. */
export const TOUR_LAYER_ATTR = "data-tour-layer";

/**
 * True when an outside-interaction event of a dialog came from the guide's
 * layer. A modal the guide points into must not close on a click on the
 * guide's own card or dim.
 */
export function isTourEvent(event: {
  target?: EventTarget | null;
  detail?: { originalEvent?: Event };
}): boolean {
  const node = event.detail?.originalEvent?.target ?? event.target;
  return node instanceof Element && node.closest(`[${TOUR_LAYER_ATTR}]`) !== null;
}
