import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator + fixed language selections so the rendered text equals
// the i18n keys — assertions can then match keys exactly.
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
  useReplyLanguage: () => "auto",
  useSttLanguage: () => "auto",
  // A wide list on purpose: the recogniser accepts far more languages than the
  // three the interface is translated into, and the picker must show them.
  // Long enough to be the real thing in miniature — the alphabetical head
  // (Afrikaans, Albanian, Amharic, Armenian) that used to be the entire first
  // screen is in here, which is what the shortlist band is measured against.
  useSttLanguageOptions: () => [
    "auto", "af", "am", "ar", "bn", "cs", "da", "de", "el", "en", "es", "fa",
    "fi", "fr", "he", "hi", "hu", "id", "it", "ja", "ko", "nl", "no", "pl",
    "pt", "ro", "ru", "sq", "sv", "th", "tr", "uk", "ur", "vi", "zh",
  ],
  setUiLanguage: vi.fn(),
  setReplyLanguage: vi.fn(),
  setSttLanguage: vi.fn(),
  hydrateReplyLanguage: vi.fn(),
  hydrateSttLanguage: vi.fn(),
  hydrateUiLanguage: vi.fn(),
}));

import { LanguagesGroup } from "@/views/settings/LanguagesGroup";

afterEach(cleanup);

describe("LanguagesGroup (Languages folded into Settings)", () => {
  it("renders the group title and both language sections", () => {
    render(<LanguagesGroup />);
    expect(screen.getByText("settings_view.languages_group_title")).toBeDefined();
    expect(screen.getByText("languages_view.ui_section")).toBeDefined();
    expect(screen.getByText("languages_view.reply_section")).toBeDefined();
  });

  it("renders a row for each UI language and the reply 'auto' option", () => {
    render(<LanguagesGroup />);
    // en/de/es appear in both the UI and reply lists → at least one each.
    expect(screen.getAllByText("languages_view.options.en.label").length).toBeGreaterThan(0);
    expect(screen.getAllByText("languages_view.options.de.label").length).toBeGreaterThan(0);
    expect(screen.getAllByText("languages_view.options.es.label").length).toBeGreaterThan(0);
    // Reply language offers "automatic" as a row; recognition offers it as the
    // first entry of its dropdown (asserted separately below).
    expect(screen.getAllByText("languages_view.options.auto.label")).toHaveLength(2);
  });

  it("offers recognition languages the interface is not translated into", async () => {
    render(<LanguagesGroup />);
    fireEvent.click(screen.getByTestId("stt-language"));
    const panel = await waitFor(() => screen.getByTestId("stt-language-panel"));
    const rows = [...panel.querySelectorAll('[role="option"]')] as HTMLElement[];
    const values = rows.map((o) => o.getAttribute("data-value"));
    // The whole point of the wide list: someone who speaks Mandarin or Japanese
    // can pick it even though the app itself is only translated into three
    // languages. A picker capped at those three locked them out entirely.
    expect(values).toContain("zh");
    expect(values).toContain("ja");
    // "Automatic" stays first — it is the recommended setting, not a language.
    expect(values[0]).toBe("auto");
    // Codes are never shown raw; a speaker should not need to know "ja" is
    // Japanese to find it.
    const japanese = rows.find((o) => o.getAttribute("data-value") === "ja");
    expect(japanese?.textContent).not.toBe("ja");
  });

  it("leads with a short band of common languages before the full A-Z list", async () => {
    render(<LanguagesGroup />);
    fireEvent.click(screen.getByTestId("stt-language"));
    const panel = await waitFor(() => screen.getByTestId("stt-language-panel"));
    const values = [...panel.querySelectorAll('[role="option"]')].map((o) =>
      o.getAttribute("data-value"),
    );
    // ~100 alphabetical entries put Afrikaans, Albanian, Amharic and Armenian
    // on the first screen and everything most people speak below the fold.
    // The shortcut band keeps the requested leading order. Hindi and Arabic do
    // not appear there; they remain available only at the very end of the full
    // list.
    const firstAll = values.indexOf("af");
    expect(firstAll).toBeGreaterThan(0);
    expect(values.slice(1, 5)).toEqual(["en", "de", "es", "zh"]);
    const commonBand = values.slice(1, firstAll);
    expect(commonBand).not.toContain("hi");
    expect(commonBand).not.toContain("ar");
    expect(values.slice(-2)).toEqual(["hi", "ar"]);
    for (const code of ["en", "de", "es", "zh", "fr"]) {
      expect(values.indexOf(code)).toBeLessThan(firstAll);
      expect(values.lastIndexOf(code)).toBeGreaterThan(firstAll);
    }
    // "Automatic" still opens the list, ahead of the shortlist.
    expect(values[0]).toBe("auto");
  });

  it("collapses the bands into one flat result list while searching", async () => {
    render(<LanguagesGroup />);
    fireEvent.click(screen.getByTestId("stt-language"));
    const panel = await waitFor(() => screen.getByTestId("stt-language-panel"));
    fireEvent.change(screen.getByTestId("stt-language-search"), {
      target: { value: "german" },
    });

    // German is in the shortlist AND in the A-Z list, which is the point of a
    // shortlist while browsing — and reads as a duplicate-row bug once a search
    // narrows both bands to the same single hit.
    await waitFor(() =>
      expect(
        [...panel.querySelectorAll('[role="option"]')].map((o) =>
          o.getAttribute("data-value"),
        ),
      ).toEqual(["de"]),
    );
    // No band headings either — there is one band left to name.
    expect(panel.textContent).not.toContain("language_select.group_common");
    expect(panel.textContent).not.toContain("language_select.group_all");
  });

  it("does not render a standalone page header (it lives under the Settings header)", () => {
    render(<LanguagesGroup />);
    expect(screen.queryByText("languages_view.title")).toBeNull();
  });
});
