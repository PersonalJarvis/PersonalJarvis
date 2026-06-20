import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="gigi" /> }));

type P = { goNext: () => void; skip: () => void };
const { dbl } = vi.hoisted(() => {
  const dbl = (testid: string) => (p: P) => (
    <div data-testid={testid}>
      <button onClick={p.goNext}>next</button>
      <button onClick={p.skip}>skip</button>
    </div>
  );
  return { dbl };
});
vi.mock("./steps/WelcomeStep", () => ({ WelcomeStep: dbl("step-welcome") }));
vi.mock("./steps/TermsStep", () => ({ TermsStep: dbl("step-terms") }));
vi.mock("./steps/LanguageStep", () => ({ LanguageStep: dbl("step-language") }));
vi.mock("./steps/WakeWordStep", () => ({ WakeWordStep: dbl("step-wake-word") }));
vi.mock("./steps/ApiKeysStep", () => ({ ApiKeysStep: dbl("step-api-keys") }));
vi.mock("./steps/FinishStep", () => ({ FinishStep: dbl("step-finish") }));

import { OnboardingFlow, STEP_KEYS } from "./OnboardingFlow";

afterEach(cleanup);

function makeOnb(stateOverrides: Record<string, unknown> = {}) {
  return {
    state: {
      completed: false,
      current_step: null,
      skipped_steps: [],
      terms: { accepted: false, accepted_version: null, current_version: "1.0" },
      wake_word_acknowledged: false,
      legal_references: [],
      steps: ["welcome", "terms", "finish"],
      ...stateOverrides,
    },
    loading: false,
    error: null,
    refetch: vi.fn(),
    saveStep: vi.fn(),
    acceptTerms: vi.fn(),
    acknowledgeWakeWord: vi.fn(),
    complete: vi.fn(),
  } as never;
}

it("renders the Gigi host and the first step", () => {
  render(<OnboardingFlow onb={makeOnb()} />);
  expect(screen.getByTestId("gigi")).toBeDefined();
  expect(screen.getByTestId("step-welcome")).toBeDefined();
});

it("advancing persists the next step and shows it", () => {
  const onb = makeOnb();
  render(<OnboardingFlow onb={onb} />);
  fireEvent.click(within(screen.getByTestId("step-welcome")).getByText("next"));
  expect((onb as never as { saveStep: ReturnType<typeof vi.fn> }).saveStep)
    .toHaveBeenCalledWith("terms", []);
  expect(screen.getByTestId("step-terms")).toBeDefined();
});

it("resumes from current_step", () => {
  render(<OnboardingFlow onb={makeOnb({ current_step: "terms" })} />);
  expect(screen.getByTestId("step-terms")).toBeDefined();
});

it("skip accumulates the skipped step", () => {
  const onb = makeOnb();
  render(<OnboardingFlow onb={onb} />);
  fireEvent.click(within(screen.getByTestId("step-welcome")).getByText("skip"));
  expect((onb as never as { saveStep: ReturnType<typeof vi.fn> }).saveStep)
    .toHaveBeenCalledWith("terms", ["welcome"]);
});

it("REGISTRY covers exactly the canonical backend steps", () => {
  expect(new Set(STEP_KEYS)).toEqual(
    new Set([
      "welcome", "terms", "language", "wake-word",
      "api-keys", "finish",
    ]),
  );
});
