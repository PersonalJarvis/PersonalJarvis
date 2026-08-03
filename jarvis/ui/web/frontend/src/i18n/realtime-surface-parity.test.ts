/**
 * Five-layer parity for the values the realtime voice surface renders.
 *
 * Each block below pins ONE vocabulary across every layer it crosses, because
 * a value that exists in one layer and not another is the BUG-008 class: the
 * UI renders nothing, renders a raw key, or — worst — silently drops the
 * update and freezes on its previous state.
 *
 *   1. supervisor voice states  Python enum -> TS union -> style maps -> i18n
 *   2. provider state chips     UI mapping -> i18n (the card's own vocabulary)
 *   3. transport issues         client union -> i18n
 *
 * Block 1 reads the Python enum from disk on purpose: a hand-copied member
 * list here would be the very drift it is supposed to catch.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { PROVIDER_STATE_CHIPS } from "@/components/providers/ProviderTierSection";
import { REALTIME_TRANSPORT_ISSUES, realtimeTransportIssueKey } from "@/lib/realtimeTransportIssue";
import { VOICE_STATES } from "@/store/events";
import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";

type Loc = Record<string, unknown>;

const LOCALES = [
  ["en", en],
  ["de", de],
  ["es", es],
] as const;

function valueAt(locale: Loc, path: string): unknown {
  let current: unknown = locale;
  for (const part of path.split(".")) {
    if (current === null || typeof current !== "object") return undefined;
    current = (current as Loc)[part];
  }
  return current;
}

function expectTranslated(path: string): void {
  for (const [name, locale] of LOCALES) {
    const value = valueAt(locale as Loc, path);
    expect(typeof value, `${name}: ${path}`).toBe("string");
    expect((value as string).trim().length, `${name}: ${path}`).toBeGreaterThan(0);
  }
}

const HERE = dirname(fileURLToPath(import.meta.url));
// src/i18n -> src -> frontend -> web -> ui -> jarvis
const SUPERVISOR_PY = resolve(HERE, "../../../../../state/supervisor.py");

describe("voice state parity (supervisor enum <-> UI)", () => {
  const source = readFileSync(SUPERVISOR_PY, "utf8");
  const body = source.split("class SupervisorState")[1] ?? "";
  const pythonStates = [...body.matchAll(/^\s{4}[A-Z_]+ = "([A-Z_]+)"$/gm)].map(
    (match) => match[1].toLowerCase(),
  );

  it("finds the Python enum (guards the parser itself)", () => {
    expect(pythonStates.length).toBeGreaterThan(3);
    expect(pythonStates).toContain("idle");
  });

  it("the TS union covers every supervisor state", () => {
    // A state the union does not know is dropped by `isVoiceState` without a
    // word, and the only live indicator the desktop has stays on its last
    // value — which is indistinguishable from a hung call.
    const missing = pythonStates.filter(
      (state) => !(VOICE_STATES as readonly string[]).includes(state),
    );
    expect(missing).toEqual([]);
  });

  it("every voice state has a label in en/de/es", () => {
    for (const state of VOICE_STATES) expectTranslated(`voice_state.${state}`);
  });

  it("the surface-only states are translated too", () => {
    // Not supervisor states: the connecting phase of a realtime handshake and
    // the indicator's accessible name.
    expectTranslated("voice_state.connecting");
    expectTranslated("voice_state.indicator_label");
  });
});

describe("provider state chip parity", () => {
  it("defines a non-trivial chip vocabulary (guards the walker)", () => {
    expect(Object.keys(PROVIDER_STATE_CHIPS).length).toBeGreaterThan(4);
  });

  it("every chip has a label in en/de/es", () => {
    for (const chip of Object.values(PROVIDER_STATE_CHIPS)) {
      expectTranslated(chip.key);
    }
  });
});

describe("realtime transport issue parity", () => {
  it("every client-side transport blocker has a label in en/de/es", () => {
    for (const issue of REALTIME_TRANSPORT_ISSUES) {
      expectTranslated(realtimeTransportIssueKey(issue));
    }
  });
});

describe("realtime status copy", () => {
  // Strings the call surface renders for states the backend really emits.
  // Missing in one locale means an English sentence in a German UI.
  const KEYS = [
    "apikeys_view.runtime_connecting",
    "apikeys_view.trademark_notice",
    "apikeys_view.experimental_note",
    "apikeys_view.experimental_consent",
    "apikeys_view.switch_note_next_start",
    "apikeys_view.switch_done_realtime",
    "apikeys_view.activate_tooltip_active",
    "apikeys_view.activate_tooltip_activate",
    "apikeys_view.activate_tooltip_blocked",
    "apikeys_view.auth_mode_api_key",
    "apikeys_view.auth_mode_codex",
    "apikeys_view.auth_mode_antigravity",
    "apikeys_view.auth_mode_none",
    "sidebar.realtime_provider_fallback",
    "sidebar.realtime_provider_fallback_short",
    "sidebar.realtime_provider_warning",
    "sidebar.realtime_provider_unknown",
    "sidebar.realtime_webrtc_degraded",
    "use_web_socket.realtime_provider_issue",
  ] as const;

  for (const key of KEYS) {
    it(`${key} exists in en/de/es`, () => expectTranslated(key));
  }
});
