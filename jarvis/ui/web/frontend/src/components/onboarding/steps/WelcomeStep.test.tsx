import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("../IntroSequence", () => ({ IntroSequence: () => <div data-testid="intro" /> }));
import { WelcomeStep } from "./WelcomeStep";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const base = {
  goBack: vi.fn(),
  skip: vi.fn(),
  isFirst: true,
  isLast: false,
  summaries: {},
};

it("renders the intro and the capability register; continue stays disabled until accepted", () => {
  render(
    <WelcomeStep
      {...base}
      onb={{ acceptTerms: vi.fn() } as never}
      goNext={vi.fn()}
      setSummary={vi.fn()}
      setGap={vi.fn()}
      gaps={{}}
    />,
  );
  expect(screen.getByTestId("intro")).toBeDefined();
  expect(screen.getByText("onboarding.welcome.cap_commands")).toBeDefined();
  expect(screen.getByText("onboarding.welcome.cap_mistakes")).toBeDefined();
  const cta = screen.getByTestId("onboarding-primary") as HTMLButtonElement;
  expect(cta.disabled).toBe(true);
});

it("AWAITS the terms record before advancing and reports the summary", async () => {
  const goNext = vi.fn();
  const setSummary = vi.fn();
  let resolveAccept: () => void = () => undefined;
  const acceptTerms = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveAccept = resolve;
      }),
  );
  render(
    <WelcomeStep
      {...base}
      onb={{ acceptTerms } as never}
      goNext={goNext}
      setSummary={setSummary}
      setGap={vi.fn()}
      gaps={{}}
    />,
  );
  fireEvent.click(screen.getByTestId("onboarding-accept"));
  expect(setSummary).toHaveBeenLastCalledWith("onboarding.welcome.summary_accepted");
  fireEvent.click(screen.getByTestId("onboarding-primary"));
  expect(acceptTerms).toHaveBeenCalled();
  // Not yet: the POST is still in flight.
  expect(goNext).not.toHaveBeenCalled();
  resolveAccept();
  await waitFor(() => expect(goNext).toHaveBeenCalled());
});

it("a failed acceptance stays on the step and says so", async () => {
  const goNext = vi.fn();
  render(
    <WelcomeStep
      {...base}
      onb={{ acceptTerms: vi.fn().mockRejectedValue(new Error("HTTP 503")) } as never}
      goNext={goNext}
      setSummary={vi.fn()}
      setGap={vi.fn()}
      gaps={{}}
    />,
  );
  fireEvent.click(screen.getByTestId("onboarding-accept"));
  fireEvent.click(screen.getByTestId("onboarding-primary"));
  await waitFor(() =>
    expect(screen.getByText("onboarding.welcome.accept_failed")).toBeDefined(),
  );
  expect(goNext).not.toHaveBeenCalled();
});

it("decline posts decline-terms and renders the goodbye state", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      calls.push(url);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }),
  );
  render(
    <WelcomeStep
      {...base}
      onb={{ acceptTerms: vi.fn() } as never}
      goNext={vi.fn()}
      setSummary={vi.fn()}
      setGap={vi.fn()}
      gaps={{}}
    />,
  );
  fireEvent.click(screen.getByTestId("onboarding-decline"));
  expect(screen.getByTestId("onboarding-declined")).toBeDefined();
  await waitFor(() => expect(calls).toContain("/api/onboarding/decline-terms"));
});
