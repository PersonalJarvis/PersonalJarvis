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
afterEach(() => { cleanup(); setUiLanguage.mockClear(); });

it("changes UI language and advances", () => {
  const goNext = vi.fn();
  render(<LanguageStep onb={{} as never} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  fireEvent.change(screen.getByLabelText("onboarding.language.ui_label"), { target: { value: "de" } });
  expect(setUiLanguage).toHaveBeenCalledWith("de");
  fireEvent.click(screen.getByRole("button", { name: "onboarding.nav.next" }));
  expect(goNext).toHaveBeenCalled();
});
