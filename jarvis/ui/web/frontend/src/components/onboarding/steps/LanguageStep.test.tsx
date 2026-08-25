import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
const { setUiLanguage } = vi.hoisted(() => ({ setUiLanguage: vi.fn() }));
vi.mock("@/i18n", () => ({
  useT: () => (k: string) => k,
  useUiLanguage: () => "en",
  useReplyLanguage: () => "auto",
  setUiLanguage,
  setReplyLanguage: vi.fn(),
}));
import { LanguageStep } from "./LanguageStep";
afterEach(() => {
  cleanup();
  setUiLanguage.mockClear();
});

it("changes UI language, reports the summary, and advances", async () => {
  const goNext = vi.fn();
  const setSummary = vi.fn();
  render(
    <LanguageStep
      onb={{} as never}
      goNext={goNext}
      goBack={vi.fn()}
      skip={vi.fn()}
      isFirst={false}
      isLast={false}
      setSummary={setSummary}
      setGap={vi.fn()}
      gaps={{}}
      summaries={{}}
    />,
  );
  expect(setSummary).toHaveBeenCalledWith("onboarding.language.summary");
  fireEvent.click(screen.getByLabelText("onboarding.language.ui_label"));
  fireEvent.click(await screen.findByText("Deutsch"));
  expect(setUiLanguage).toHaveBeenCalledWith("de");
  fireEvent.click(screen.getByTestId("onboarding-primary"));
  expect(goNext).toHaveBeenCalled();
});
