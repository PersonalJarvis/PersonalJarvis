/**
 * The realtime tab of the API Keys page renders every `apikeys_realtime.*`
 * string in every locale. A key present in one locale and missing in
 * another renders as a raw key there, so the sets must match exactly.
 */
import { describe, expect, it } from "vitest";

import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";
import pt from "./locales/pt.json";

type Block = Record<string, string>;

const LOCALES = { en, de, es, pt } as unknown as Record<string, { apikeys_realtime?: Block }>;

describe("apikeys_realtime i18n parity", () => {
  const reference = Object.keys(LOCALES.en.apikeys_realtime ?? {}).sort();

  it("exists in English", () => {
    expect(reference.length).toBeGreaterThan(0);
  });

  for (const [lang, locale] of Object.entries(LOCALES)) {
    it(`${lang} carries every key, each with text`, () => {
      const block = locale.apikeys_realtime ?? {};
      expect(Object.keys(block).sort()).toEqual(reference);
      for (const value of Object.values(block)) expect(value.trim()).not.toBe("");
    });
  }
});
