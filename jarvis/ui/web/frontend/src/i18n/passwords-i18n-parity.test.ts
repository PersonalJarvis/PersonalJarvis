/**
 * Locale parity for the Passwords section.
 *
 * The section's copy — the trust panel, the editor disclosure, the owner
 * grouping and the status explanations — is seeded into en, de and es in one
 * pass. A key that exists in only one locale silently renders as the raw key
 * ("passwords.trust.ai_body") for everyone else, which no type check catches.
 * This locks the three files to the same key set and to non-empty values.
 *
 * The last block pins the section's honesty: the sentences that tell the user
 * the assistant gains access to what they type must exist, must speak of the
 * assistant via the {name} token (contract §4 — the brand follows the wake
 * word), and must never hardcode a product name.
 */
import { describe, expect, it } from "vitest";
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

function valueAt(loc: Loc, path: string): unknown {
  let cur: unknown = loc;
  for (const part of path.split(".")) cur = (cur as Loc)[part];
  return cur;
}

const LOCALES = [
  ["de", de],
  ["es", es],
] as const;

describe("passwords section i18n parity", () => {
  it('en defines a non-trivial "passwords" key set', () => {
    // Guards the walker itself: without this a typo in the namespace path
    // would compare two empty sets and pass vacuously.
    expect(flatten(namespaceAt(en as Loc, "passwords")).length).toBeGreaterThan(40);
  });

  for (const [lang, loc] of LOCALES) {
    it(`${lang} has the same "passwords" keys as en`, () => {
      const enKeys = new Set(flatten(namespaceAt(en as Loc, "passwords")));
      const langKeys = new Set(flatten(namespaceAt(loc as Loc, "passwords")));
      const missing = [...enKeys].filter((k) => !langKeys.has(k));
      const extra = [...langKeys].filter((k) => !enKeys.has(k));
      expect({ missing, extra }).toEqual({ missing: [], extra: [] });
    });
  }

  for (const [lang, loc] of [["en", en], ...LOCALES] as const) {
    it(`${lang}: every passwords value is a non-empty string`, () => {
      const ns = namespaceAt(loc as Loc, "passwords");
      const empty = flatten(ns).filter((key) => {
        const value = valueAt(ns, key);
        return typeof value !== "string" || value.trim() === "";
      });
      expect(empty).toEqual([]);
    });
  }
});

describe("passwords section trust copy", () => {
  // The sentences that disclose the assistant's access to what the user types.
  // Their existence IS the feature: without them the section silently gains
  // access to credentials, which is exactly what it must never do.
  const DISCLOSURE_KEYS = [
    "passwords.trust.ai_body",
    "passwords.editor_disclosure",
  ] as const;

  for (const [lang, loc] of [["en", en], ...LOCALES] as const) {
    for (const key of DISCLOSURE_KEYS) {
      it(`${lang}: ${key} names the assistant via {name}, never a fixed brand`, () => {
        const value = valueAt(loc as Loc, key);
        expect(typeof value, key).toBe("string");
        expect(value as string).toContain("{name}");
        expect((value as string).toLowerCase()).not.toContain("jarvis");
      });
    }
  }
});
