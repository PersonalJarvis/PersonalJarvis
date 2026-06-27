import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));

import { IntroVideoScreen } from "./IntroVideoScreen";

afterEach(cleanup);

it("embeds the YouTube tutorial via the privacy-enhanced domain", () => {
  render(<IntroVideoScreen onContinue={() => {}} />);
  const frame = screen.getByTitle("onboarding.tutorial.title") as HTMLIFrameElement;
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.src).toContain("youtube-nocookie.com/embed/FXz1HclXL1g");
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
