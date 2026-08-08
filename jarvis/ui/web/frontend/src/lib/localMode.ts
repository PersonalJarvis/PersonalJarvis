/**
 * Local Mode: show only the providers that run on the user's own hardware.
 *
 * A meaningful share of downloaders never enter a cloud credential — the local
 * cards are the whole point of that path, and `provider_spec.py` guarantees
 * every tier has one. For those installs the hosted cards are pure noise: a
 * wall of key forms the user will never fill in, buried among the two or three
 * cards that matter. Local Mode hides them.
 *
 * Three properties this switch deliberately has:
 *
 * 1. **Capability-driven, never name-driven.** "Local" is the backend's own
 *    `billing === "local"`, derived from `auth_mode == "none"` in
 *    `provider_billing()`. Adding a keyless provider makes it appear here with
 *    no change to this file (AP-21).
 * 2. **Presentation only.** Nothing here switches a provider, writes config, or
 *    gates a runtime path. Flipping it off restores the full catalog and
 *    changes nothing about what the app is actually running.
 * 3. **The active provider is never hidden.** Filtering away the card that is
 *    powering a tier right now would answer "what am I running on?" with
 *    silence. A hosted card that is active stays visible, and the notice above
 *    the list says why.
 *
 * "Local" here means *your own hardware*, which includes an Ollama server on
 * another box in the house — the honest line for the badge is "runs on your own
 * machines", not "on-device". A view preference of this machine, so it lives in
 * localStorage next to `jarvis.wiki.graphDimension` and `jarvis.ui.sound`
 * rather than in `jarvis.toml`.
 *
 * Pure apart from the small store at the bottom, so the filter rules are
 * unit-testable without a renderer.
 */
import { create } from "zustand";
import type { Billing } from "@/hooks/useProviders";

/** Where the preference is stored, per machine. */
export const LOCAL_MODE_KEY = "jarvis.providers.localMode";

/** Off by default: the full catalog is what a first-time visitor expects. */
export const DEFAULT_LOCAL_MODE = false;

/** Narrow an arbitrary stored string; anything else is treated as unset. */
export function parseLocalMode(raw: string | null): boolean | null {
  return raw === "1" ? true : raw === "0" ? false : null;
}

/** The stored preference, or the default when storage is unavailable/empty. */
export function readLocalMode(): boolean {
  try {
    return parseLocalMode(window.localStorage.getItem(LOCAL_MODE_KEY)) ?? DEFAULT_LOCAL_MODE;
  } catch {
    // Private mode / storage disabled — a view preference is never worth
    // taking the provider console down for.
    return DEFAULT_LOCAL_MODE;
  }
}

/** Persist the preference; a storage failure only costs the memory of it. */
export function writeLocalMode(value: boolean): void {
  try {
    window.localStorage.setItem(LOCAL_MODE_KEY, value ? "1" : "0");
  } catch {
    // Same reasoning as readLocalMode: never fatal.
  }
}

/**
 * Whether this provider runs on hardware the user controls.
 *
 * The backend already answers this: a card with no credential slot bills as
 * `"local"`. Reading that field keeps the definition in ONE place — the moment
 * a new keyless card ships, it shows up in Local Mode without a code change
 * here, and a hosted card can never sneak in by being named "local-something".
 *
 * Deliberately typed on the field rather than on `ProviderDescriptor`: the
 * subagent tier has its OWN row shape (`/api/jarvis-agent/status` returns a
 * different payload) and carries the very same backend `billing` value. One
 * definition serves both, so Local Mode cannot mean two different things on
 * two tabs — which is exactly how the subagents tab came to ignore it.
 */
export function runsOnOwnHardware(provider: { billing: Billing }): boolean {
  return provider.billing === "local";
}

export interface LocalModeFilter<T> {
  /** The cards to render, in the order they came in. */
  visible: T[];
  /** How many hosted cards Local Mode took off the screen. */
  hiddenCount: number;
  /** True when a hosted card was kept because it is the ACTIVE one. */
  keptActiveHosted: boolean;
}

/**
 * Apply Local Mode to one list of provider cards.
 *
 * Disabled, this is the identity function — the exact same array comes back, so
 * the off state cannot differ from the pre-feature behaviour in any way.
 *
 * `isActive` defaults to the `active` field the provider-tier payload uses; the
 * subagent rows name theirs differently and pass their own reader.
 */
export function filterForLocalMode<T extends { billing: Billing; active?: boolean }>(
  items: T[],
  enabled: boolean,
  isActive: (item: T) => boolean = (item) => item.active === true,
): LocalModeFilter<T> {
  if (!enabled) {
    return { visible: items, hiddenCount: 0, keptActiveHosted: false };
  }
  const visible = items.filter((p) => runsOnOwnHardware(p) || isActive(p));
  return {
    visible,
    hiddenCount: items.length - visible.length,
    keptActiveHosted: visible.some((p) => isActive(p) && !runsOnOwnHardware(p)),
  };
}

interface LocalModeStore {
  enabled: boolean;
  setEnabled: (value: boolean) => void;
}

const useLocalModeStore = create<LocalModeStore>((set) => ({
  enabled: readLocalMode(),
  setEnabled: (value) => {
    writeLocalMode(value);
    set({ enabled: value });
  },
}));

/**
 * Set the preference from outside React — the same path the switch takes, so
 * every mounted provider list reacts. Used by onboarding's local path (picking
 * the local brain there turns the mode on) and by tests.
 */
export function setLocalMode(value: boolean): void {
  useLocalModeStore.getState().setEnabled(value);
}

/** Read the preference outside React (tests, imperative callers). */
export function getLocalMode(): boolean {
  return useLocalModeStore.getState().enabled;
}

export interface LocalModeHandle {
  localMode: boolean;
  setLocalMode: (value: boolean) => void;
}

/** The switch state, shared by every provider list on screen. */
export function useLocalMode(): LocalModeHandle {
  const localMode = useLocalModeStore((s) => s.enabled);
  const setEnabled = useLocalModeStore((s) => s.setEnabled);
  return { localMode, setLocalMode: setEnabled };
}
