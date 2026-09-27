import { afterEach, describe, expect, it } from "vitest";

import de from "./locales/de.json";
import es from "./locales/es.json";
import { loadUiLocale, translate, useI18nStore } from "./index";

afterEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
});

describe("on-demand interface dictionaries", () => {
  it.each([
    ["de", de.common.loading],
    ["es", es.common.loading],
  ] as const)("resolves %s before showing its translated labels", async (lang, loading) => {
    const first = loadUiLocale(lang);
    expect(loadUiLocale(lang)).toBe(first);
    await first;
    useI18nStore.getState().setUi(lang, { push: false });
    expect(translate("common.loading")).toBe(loading);
  });
});
