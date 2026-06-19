import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { MicTestStep } from "./MicTestStep";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals(); // restore navigator so the stub doesn't leak into sibling tests
});

it("shows the no-mic message when getUserMedia is unavailable", async () => {
  vi.stubGlobal("navigator", { mediaDevices: undefined });
  render(<MicTestStep onb={{} as never} goNext={vi.fn()} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  await waitFor(() => expect(screen.getByText("onboarding.mic_test.no_mic")).toBeDefined());
});

it("skip advances", () => {
  vi.stubGlobal("navigator", { mediaDevices: undefined });
  const skip = vi.fn();
  render(<MicTestStep onb={{} as never} goNext={vi.fn()} goBack={vi.fn()} skip={skip} isFirst={false} isLast={false} />);
  fireEvent.click(screen.getByRole("button", { name: "onboarding.mic_test.skip" }));
  expect(skip).toHaveBeenCalled();
});
