import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

import { ApiKeysStep } from "./ApiKeysStep";

afterEach(cleanup);

function renderStep(overrides: Record<string, unknown> = {}) {
  const props = {
    onb: {} as never,
    goNext: vi.fn(),
    goBack: vi.fn(),
    skip: vi.fn(),
    isFirst: false,
    isLast: false,
    ...overrides,
  };
  render(<ApiKeysStep {...props} />);
  return props;
}

it("shows the real API Keys view with the navigation and voice-mode switch marked", () => {
  renderStep();

  const screenshot = screen.getByRole("img", {
    name: "onboarding.api_keys.screenshot_alt",
  });
  expect(screenshot.getAttribute("src")).toBe(
    "/onboarding/api-keys-realtime-guide.png",
  );
  expect(screen.getByTestId("api-keys-marker")).toBeDefined();
  expect(screen.getByTestId("voice-mode-marker")).toBeDefined();
  expect(screen.getByText("onboarding.api_keys.voice_mode_title")).toBeDefined();
  expect(screen.getByText("onboarding.api_keys.realtime_label")).toBeDefined();
  expect(screen.getByText("onboarding.api_keys.pipeline_label")).toBeDefined();
});

it("keeps credential entry out of the onboarding modal", () => {
  renderStep();

  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
});

it("offers one clear action to continue onboarding", () => {
  const props = renderStep();
  const buttons = screen.getAllByRole("button");
  expect(buttons).toHaveLength(1);
  fireEvent.click(buttons[0]);
  expect(props.goNext).toHaveBeenCalledTimes(1);
  expect(props.skip).not.toHaveBeenCalled();
});
