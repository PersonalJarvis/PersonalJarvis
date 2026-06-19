import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";

function flatten(obj: Record<string, unknown>, prefix = ""): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      out.push(...flatten(v as Record<string, unknown>, key));
    } else {
      out.push(key);
    }
  }
  return out;
}

const keysFor = (loc: Record<string, unknown>): Set<string> =>
  new Set(flatten((loc.onboarding ?? {}) as Record<string, unknown>));

describe("onboarding i18n parity", () => {
  it("en defines a non-trivial onboarding key set", () => {
    expect(keysFor(en as Record<string, unknown>).size).toBeGreaterThan(20);
  });

  for (const [lang, loc] of [["de", de], ["es", es]] as const) {
    it(`${lang} has the same onboarding keys as en`, () => {
      const enKeys = keysFor(en as Record<string, unknown>);
      const langKeys = keysFor(loc as Record<string, unknown>);
      const missing = [...enKeys].filter((k) => !langKeys.has(k));
      const extra = [...langKeys].filter((k) => !enKeys.has(k));
      expect({ missing, extra }).toEqual({ missing: [], extra: [] });
    });
  }
});
