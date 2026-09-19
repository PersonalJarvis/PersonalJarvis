import { afterEach, expect, test, vi } from "vitest";

afterEach(() => { vi.unstubAllGlobals(); });

test("a cold loader fetches only the requested language and deduplicates callers", async () => {
  vi.resetModules();
  const locales = await import("./coreLocales");
  expect(locales.isCoreLocaleLoaded("de")).toBe(false);
  const first = locales.loadCoreLocale("de");
  expect(locales.loadCoreLocale("de")).toBe(first);
  await first;
  expect(locales.CORE_RESOURCES.de.nav).toBeTruthy();
  expect(locales.isCoreLocaleLoaded("en")).toBe(false);
  expect(locales.isCoreLocaleLoaded("es")).toBe(false);
});

test("bootstrap prepares the chosen language and the existing fallback", async () => {
  vi.resetModules();
  const i18n = await import("./index");
  const locales = await import("./coreLocales");
  i18n.useI18nStore.setState({ ui: "es" });
  await i18n.prepareUiTranslations();
  expect(locales.isCoreLocaleLoaded("es")).toBe(true);
  expect(locales.isCoreLocaleLoaded("en")).toBe(true);
  expect(locales.isCoreLocaleLoaded("de")).toBe(false);
});

test("rapid cold language choices keep the newest choice", async () => {
  vi.resetModules();
  const i18n = await import("./index");
  const locales = await import("./coreLocales");
  await locales.loadCoreLocale("en");
  i18n.useI18nStore.setState({ ui: "en" });
  i18n.useI18nStore.getState().setUi("de", { push: false });
  i18n.useI18nStore.getState().setUi("es", { push: false });
  await Promise.all([locales.loadCoreLocale("de"), locales.loadCoreLocale("es")]);
  expect(i18n.useI18nStore.getState().ui).toBe("es");
});
