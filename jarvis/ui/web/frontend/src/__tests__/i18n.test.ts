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
