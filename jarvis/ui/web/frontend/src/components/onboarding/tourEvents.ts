/**
 * Window event that asks the onboarding gate to replay the app tour. Its own
 * module so a caller (Settings) does not import the gate itself — tests mock
 * the gate wholesale.
 */
export const TOUR_START_EVENT = "jarvis:tour-start";
