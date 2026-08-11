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

const keys = (locale: Locale, section: string) =>
  flatten((locale[section] ?? {}) as Locale).sort();

describe("workspace launcher i18n parity", () => {
  for (const [language, locale] of Object.entries({ de, es })) {
    it(`${language} has the same workspace launcher keys as en`, () => {
      expect(keys(locale as Locale, "workspace_launcher")).toEqual(
        keys(en as Locale, "workspace_launcher"),
      );
    });

    // Its own section rather than part of the launcher's: the dialog is reached
    // from the launcher's agent step but is about the CLI registry, not about
    // this workspace. Covered here because it is the same screen's vocabulary
    // and would otherwise be the one part of it with no parity check at all.
    it(`${language} has the same custom-CLI keys as en`, () => {
      expect(keys(locale as Locale, "custom_cli")).toEqual(
        keys(en as Locale, "custom_cli"),
      );
    });
  }
});
