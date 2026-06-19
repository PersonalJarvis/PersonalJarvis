import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("../IntroClip", () => ({ IntroClip: () => <div data-testid="clip" /> }));
import { WelcomeStep } from "./WelcomeStep";
afterEach(cleanup);

it("renders the clip and advances on the CTA", () => {
  const goNext = vi.fn();
  render(
    <WelcomeStep onb={{} as never} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst isLast={false} />,
  );
  expect(screen.getByTestId("clip")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "onboarding.welcome.cta" }));
  expect(goNext).toHaveBeenCalled();
});

it("skip-setup calls skip", () => {
  const skip = vi.fn();
  render(
    <WelcomeStep onb={{} as never} goNext={vi.fn()} goBack={vi.fn()} skip={skip} isFirst isLast={false} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "onboarding.welcome.skip_setup" }));
  expect(skip).toHaveBeenCalled();
});
