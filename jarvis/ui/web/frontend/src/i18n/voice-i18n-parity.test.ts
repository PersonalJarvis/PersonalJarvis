/**
 * Locale parity for the merged voice section.
 *
 * The whole section's copy — the tab labels, the shortcuts/language/API-keys
 * tabs, and every dictation string added alongside them — is seeded into en, de
 * and es in one pass. A key that exists in only one locale silently renders as
 * the raw key ("dictation.stats.streak") for everyone else, which no type check
 * catches. This locks the three files to the same key set and to non-empty
 * values.
 */
import { describe, expect, it } from "vitest";
import { CODEX_STATUS_KEY_BY_REASON } from "@/components/providers/ProviderTierSection";
import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";

type Loc = Record<string, unknown>;

function flatten(obj: Loc, prefix = ""): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flatten(v as Loc, key));
    } else {
      out.push(key);
    }
  }
  return out;
}

function namespaceAt(loc: Loc, path: string): Loc {
  let cur: unknown = loc;
  for (const part of path.split(".")) {
    cur = (cur as Loc | undefined)?.[part];
  }
  return (cur ?? {}) as Loc;
}

/** Namespaces the voice section owns or extends, and their minimum key count. */
const NAMESPACES: readonly (readonly [string, number])[] = [
  ["voice", 12],
  ["dictation", 30],
  ["settings_view.keybinds", 20],
];

const LOCALES = [
  ["de", de],
  ["es", es],
] as const;

describe("voice section i18n parity", () => {
  for (const [ns, minimum] of NAMESPACES) {
    it(`en defines a non-trivial "${ns}" key set`, () => {
      // Guards the walker itself: without this a typo in the namespace path
      // would compare two empty sets and pass vacuously.
      expect(flatten(namespaceAt(en as Loc, ns)).length).toBeGreaterThan(minimum);
    });

    for (const [lang, loc] of LOCALES) {
      it(`${lang} has the same "${ns}" keys as en`, () => {
        const enKeys = new Set(flatten(namespaceAt(en as Loc, ns)));
        const langKeys = new Set(flatten(namespaceAt(loc as Loc, ns)));
        const missing = [...enKeys].filter((k) => !langKeys.has(k));
        const extra = [...langKeys].filter((k) => !enKeys.has(k));
        expect({ missing, extra }).toEqual({ missing: [], extra: [] });
      });
    }
  }

  for (const [lang, loc] of [["en", en], ...LOCALES] as const) {
    it(`${lang}: every voice value is a non-empty string`, () => {
      const ns = namespaceAt(loc as Loc, "voice");
      const empty = flatten(ns).filter((key) => {
        let cur: unknown = ns;
        for (const part of key.split(".")) cur = (cur as Loc)[part];
        return typeof cur !== "string" || cur.trim() === "";
      });
      expect(empty).toEqual([]);
    });
  }
});

describe("voice section tab labels", () => {
  const NAV_KEYS = [
    "voice",
    "voice_shortcuts",
    "voice_language",
    "voice_api_keys",
    // The two pre-existing tabs are reused as labels, so they must not vanish.
    "dictation",
    "dictionary",
  ] as const;

  for (const [lang, loc] of [["en", en], ...LOCALES] as const) {
    it(`${lang}: has every nav label the voice tab bar renders`, () => {
      const nav = namespaceAt(loc as Loc, "nav");
      for (const key of NAV_KEYS) {
        expect(typeof nav[key], `nav.${key}`).toBe("string");
        expect((nav[key] as string).trim().length, `nav.${key}`).toBeGreaterThan(0);
      }
    });

    it(`${lang}: nav.voice carries the {name} brand token and no fixed name`, () => {
      // The section is named after the user's wake word. A hardcoded product
      // name here would be wrong for every install that renamed the assistant —
      // and a trademarked one would be wrong for all of them.
      const value = namespaceAt(loc as Loc, "nav").voice as string;
      expect(value).toContain("{name}");
      expect(value.toLowerCase()).not.toContain("jarvis");
    });
  }
});

describe("subscription Realtime voice copy", () => {
  const KEYS = [
    "sidebar.realtime_unavailable",
    "apikeys_view.mode_needs_credentials",
    "apikeys_view.needs_credentials",
    "apikeys_view.experimental",
    "apikeys_view.subscription_realtime_description",
    "apikeys_view.experimental_subscription_fallback",
    "apikeys_view.experimental_subscription_consent",
    "provider_billing.api",
    "provider_billing.subscription",
    "provider_billing.subscription_or_api",
    "provider_billing.local",
    "settings_view.realtime_voice.title",
    "settings_view.realtime_voice.description",
    "settings_view.realtime_voice.unavailable",
    // Derived from the card's exhaustive reason-code mapping so a ninth code
    // cannot ship with an i18n key no locale knows.
    ...Object.values(CODEX_STATUS_KEY_BY_REASON),
    "apikeys_codex.setup_detail_label",
    "apikeys_codex.install_command_copied",
    "apikeys_codex.connected_chatgpt",
    "apikeys_codex.disconnect",
    "apikeys_codex.copy_command",
    "apikeys_codex.connect_chatgpt",
    "apikeys_codex.install_codex",
    "apikeys_codex.subscription_login_required",
  ] as const;

  function valueAt(loc: Loc, path: string): unknown {
    let current: unknown = loc;
    for (const part of path.split(".")) current = (current as Loc)[part];
    return current;
  }

  for (const [lang, loc] of [["en", en], ...LOCALES] as const) {
    it(`${lang}: has complete subscription Realtime guidance`, () => {
      for (const key of KEYS) {
        const value = valueAt(loc as Loc, key);
        expect(typeof value, key).toBe("string");
        expect((value as string).trim().length, key).toBeGreaterThan(0);
      }
    });
  }
});
