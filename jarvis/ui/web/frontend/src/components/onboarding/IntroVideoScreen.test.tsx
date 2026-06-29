import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));

import { IntroVideoScreen } from "./IntroVideoScreen";

afterEach(cleanup);

it("shows a click-to-play facade and only mounts the heavy iframe on demand", () => {
  render(<IntroVideoScreen onContinue={() => {}} />);
  // Facade first: the heavy YouTube iframe is NOT mounted until the user plays,
  // so the step renders instantly with no loading flash.
  expect(screen.queryByTitle("onboarding.tutorial.title")).toBeNull();

  fireEvent.click(screen.getByLabelText("onboarding.tutorial.play"));

  const frame = screen.getByTitle("onboarding.tutorial.title") as HTMLIFrameElement;
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.src).toContain("youtube-nocookie.com/embed/FXz1HclXL1g");
  expect(frame.src).toContain("autoplay=1");
});

it("the primary button advances the flow", () => {
  const onContinue = vi.fn();
  render(<IntroVideoScreen onContinue={onContinue} />);
  fireEvent.click(screen.getByText("onboarding.tutorial.continue"));
  expect(onContinue).toHaveBeenCalledOnce();
});

it("the skip link also advances the flow", () => {
  const onContinue = vi.fn();
  render(<IntroVideoScreen onContinue={onContinue} />);
  fireEvent.click(screen.getByText("onboarding.tutorial.skip"));
  expect(onContinue).toHaveBeenCalledOnce();
});
