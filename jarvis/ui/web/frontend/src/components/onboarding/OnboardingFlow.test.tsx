import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="gigi" /> }));

type P = { goNext: () => void; skip: () => void; setSummary: (s: string | null) => void };
const { dbl } = vi.hoisted(() => {
  const dbl = (testid: string) => (p: P) => (
    <div data-testid={testid}>
      <button onClick={p.goNext}>next</button>
      <button onClick={p.skip}>skip</button>
      <button onClick={() => p.setSummary(`sum:${testid}`)}>summarize</button>
    </div>
  );
  return { dbl };
});
vi.mock("./steps/WelcomeStep", () => ({ WelcomeStep: dbl("step-welcome") }));
vi.mock("./steps/LanguageStep", () => ({ LanguageStep: dbl("step-language") }));
vi.mock("./steps/PermissionsStep", () => ({ PermissionsStep: dbl("step-permissions") }));
vi.mock("./steps/WakeWordStep", () => ({ WakeWordStep: dbl("step-wake-word") }));
vi.mock("./steps/ApiKeysStep", () => ({ ApiKeysStep: dbl("step-api-keys") }));
vi.mock("./steps/FinishStep", () => ({ FinishStep: dbl("step-finish") }));

import { OnboardingFlow, STEP_KEYS, visibleSteps } from "./OnboardingFlow";

function stubPlatform(platform: string | "error") {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      platform === "error"
        ? Promise.reject(new Error("net"))
        : Promise.resolve({ ok: true, json: () => Promise.resolve({ platform }) }),
    ),
  );
}

beforeEach(() => stubPlatform("darwin"));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function makeOnb(stateOverrides: Record<string, unknown> = {}) {
  return {
    state: {
      completed: false,
      current_step: null,
      skipped_steps: [],
      terms: { accepted: false, accepted_version: null, current_version: "1.0" },
      wake_word_acknowledged: false,
      legal_references: [],
      steps: ["welcome", "language", "finish"],
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

it("renders the Gigi host, the register and the first step", () => {
  render(<OnboardingFlow onb={makeOnb()} />);
  expect(screen.getByTestId("gigi")).toBeDefined();
  expect(screen.getByTestId("step-welcome")).toBeDefined();
  expect(screen.getByTestId("onboarding-step-welcome").getAttribute("aria-current")).toBe("step");
  // Steps not yet visited cannot be jumped to from the register.
  expect((screen.getByTestId("onboarding-step-finish") as HTMLButtonElement).disabled).toBe(true);
});

it("advancing persists the next step, shows it, and lets the register go back to a visited step", () => {
  const onb = makeOnb();
  render(<OnboardingFlow onb={onb} />);
  fireEvent.click(within(screen.getByTestId("step-welcome")).getByText("next"));
  expect((onb as never as { saveStep: ReturnType<typeof vi.fn> }).saveStep)
    .toHaveBeenCalledWith("language", []);
  expect(screen.getByTestId("step-language")).toBeDefined();
  fireEvent.click(screen.getByTestId("onboarding-step-welcome"));
  expect(screen.getByTestId("step-welcome")).toBeDefined();
});

it("a step's summary shows under its register entry", () => {
  render(<OnboardingFlow onb={makeOnb()} />);
  fireEvent.click(within(screen.getByTestId("step-welcome")).getByText("summarize"));
  expect(screen.getByTestId("onboarding-step-welcome").textContent).toContain("sum:step-welcome");
});

it("always starts at the first step, ignoring a saved current_step", () => {
  render(<OnboardingFlow onb={makeOnb({ current_step: "finish" })} />);
  expect(screen.getByTestId("step-welcome")).toBeDefined();
});

it("skip accumulates the skipped step", () => {
  const onb = makeOnb();
  render(<OnboardingFlow onb={onb} />);
  fireEvent.click(within(screen.getByTestId("step-welcome")).getByText("skip"));
  expect((onb as never as { saveStep: ReturnType<typeof vi.fn> }).saveStep)
    .toHaveBeenCalledWith("language", ["welcome"]);
});

it("hides the macOS permissions step on Windows and Linux, keeps it on macOS and when unknown", async () => {
  const all = ["welcome", "permissions", "finish"];
  expect(visibleSteps(all, "win32")).toEqual(["welcome", "finish"]);
  expect(visibleSteps(all, "linux")).toEqual(["welcome", "finish"]);
  expect(visibleSteps(all, "darwin")).toEqual(all);
  expect(visibleSteps(all, null)).toEqual(all);

  stubPlatform("win32");
  render(<OnboardingFlow onb={makeOnb({ steps: all })} />);
  await waitFor(() => expect(screen.queryByTestId("onboarding-step-permissions")).toBeNull());
  expect(screen.getByTestId("onboarding-step-finish")).toBeDefined();
});

it("REGISTRY covers exactly the canonical backend steps", () => {
  expect(new Set(STEP_KEYS)).toEqual(
    new Set(["welcome", "language", "permissions", "wake-word", "api-keys", "finish"]),
  );
});
