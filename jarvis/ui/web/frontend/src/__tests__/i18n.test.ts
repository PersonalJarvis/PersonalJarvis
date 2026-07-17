import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import en from "@/i18n/locales/en.json";
import de from "@/i18n/locales/de.json";
import es from "@/i18n/locales/es.json";

const LOCALES = { en, de, es } as const;
const CODES = ["en", "de", "es"] as const;
const REPLY_CODES = ["auto", "en", "de", "es"] as const;

describe("locale completeness (raw-key bug guard)", () => {
  for (const [name, loc] of Object.entries(LOCALES)) {
    const lv = (loc as any).languages_view;

    it(`${name}: every interface option has a description`, () => {
      for (const code of CODES) {
        expect(lv.options[code]?.label, `${name}.options.${code}.label`).toBeTruthy();
        expect(
          lv.options[code]?.description,
          `${name}.options.${code}.description`,
        ).toBeTruthy();
      }
    });

    it(`${name}: reply_options cover auto/en/de/es`, () => {
      for (const code of REPLY_CODES) {
        expect(lv.reply_options[code], `${name}.reply_options.${code}`).toBeTruthy();
      }
    });

    it(`${name}: options.auto.label exists (reply row label)`, () => {
      expect(lv.options.auto?.label, `${name}.options.auto.label`).toBeTruthy();
    });
  }
});

describe("wiki_provider namespace parity (all locales share the same keys)", () => {
  const keysFor = (loc: unknown) =>
    Object.keys((loc as { wiki_provider?: Record<string, string> }).wiki_provider ?? {}).sort();
  const reference = keysFor(en);

  it("en: has a non-empty wiki_provider namespace", () => {
    expect(reference.length).toBeGreaterThan(0);
  });

  for (const [name, loc] of Object.entries(LOCALES)) {
    it(`${name}: wiki_provider keys match en exactly`, () => {
      expect(keysFor(loc)).toEqual(reference);
    });

    it(`${name}: every wiki_provider value is a non-empty string`, () => {
      const ns = (loc as { wiki_provider?: Record<string, unknown> }).wiki_provider ?? {};
      for (const [key, value] of Object.entries(ns)) {
        expect(typeof value, `${name}.wiki_provider.${key}`).toBe("string");
        expect((value as string).trim().length, `${name}.wiki_provider.${key}`).toBeGreaterThan(0);
      }
    });
  }
});

describe("permissions namespace parity (all locales share the same nested keys)", () => {
  const flatten = (obj: Record<string, unknown>, prefix = ""): string[] =>
    Object.entries(obj).flatMap(([key, value]) =>
      value && typeof value === "object"
        ? flatten(value as Record<string, unknown>, `${prefix}${key}.`)
        : [`${prefix}${key}`],
    );
  const keysFor = (loc: unknown) =>
    flatten((loc as { permissions?: Record<string, unknown> }).permissions ?? {}).sort();
  const reference = keysFor(en);

  it("en: has a non-empty permissions namespace", () => {
    expect(reference.length).toBeGreaterThan(0);
  });

  for (const [name, loc] of Object.entries(LOCALES)) {
    it(`${name}: permissions keys match en exactly`, () => {
      expect(keysFor(loc)).toEqual(reference);
    });
  }
});

describe("settings_view languages group (folded-in section)", () => {
  for (const [name, loc] of Object.entries(LOCALES)) {
    it(`${name}: has a languages group title for the Settings panel`, () => {
      const sv = (loc as any).settings_view;
      expect(
        sv.languages_group_title,
        `${name}.settings_view.languages_group_title`,
      ).toBeTruthy();
    });
  }
});

describe("reply language wiring", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("defaults reply language to auto", async () => {
    const { useI18nStore } = await import("@/i18n");
    expect(useI18nStore.getState().reply).toBe("auto");
  });

  it("setReplyLanguage PUTs the choice to the backend", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ language: "en" }) }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { setReplyLanguage } = await import("@/i18n");
    setReplyLanguage("en");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/settings/reply-language");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toMatchObject({ language: "en" });
  });
});
