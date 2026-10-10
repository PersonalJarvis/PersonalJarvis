import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";
import pt from "./locales/pt.json";

type Locale = Record<string, unknown>;

function flatten(value: Locale, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, nested]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return nested && typeof nested === "object"
      ? flatten(nested as Locale, path)
      : [path];
  });
}

const keys = (locale: Locale) =>
  flatten((locale.code_editor ?? {}) as Locale).sort();

describe("code editor i18n parity", () => {
  it("en defines the code editor section", () => {
    expect(keys(en as Locale).length).toBeGreaterThan(0);
  });

  for (const [language, locale] of Object.entries({ de, es, pt })) {
    it(`${language} has the same code editor keys as en`, () => {
      expect(keys(locale as Locale)).toEqual(keys(en as Locale));
    });
  }
});
