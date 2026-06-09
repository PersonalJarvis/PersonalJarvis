import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator + fixed language selections so the rendered text equals
// the i18n keys — assertions can then match keys exactly.
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
  useReplyLanguage: () => "auto",
  setUiLanguage: vi.fn(),
  setReplyLanguage: vi.fn(),
  hydrateReplyLanguage: vi.fn(),
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
    // The reply section is the only one offering "automatic".
    expect(screen.getByText("languages_view.options.auto.label")).toBeDefined();
  });

  it("does not render a standalone page header (it lives under the Settings header)", () => {
    render(<LanguagesGroup />);
    expect(screen.queryByText("languages_view.title")).toBeNull();
  });
});
