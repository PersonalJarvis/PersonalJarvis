import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";

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
  flatten((locale.workspace_launcher ?? {}) as Locale).sort();

describe("workspace launcher i18n parity", () => {
  for (const [language, locale] of Object.entries({ de, es })) {
    it(`${language} has the same workspace launcher keys as en`, () => {
      expect(keys(locale as Locale)).toEqual(keys(en as Locale));
    });
  }
});
