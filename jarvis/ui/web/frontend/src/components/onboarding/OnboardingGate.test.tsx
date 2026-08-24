import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("./OnboardingFlow", () => ({ OnboardingFlow: () => <div data-testid="flow" /> }));

import { OnboardingGate } from "./OnboardingGate";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const base = {
  current_step: null,
  skipped_steps: [],
  terms: { accepted: false, accepted_version: null, current_version: "1.0" },
  wake_word_acknowledged: false,
  legal_references: [],
  steps: ["welcome"],
};

function stub(state: object | "error") {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      state === "error"
        ? Promise.reject(new Error("net"))
        : Promise.resolve({ ok: true, json: () => Promise.resolve(state) }),
    ),
  );
}

it("shows the overlay when not completed", async () => {
  stub({ ...base, completed: false });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.getByRole("dialog")).toBeDefined());
});

it("renders nothing when completed", async () => {
  stub({ ...base, completed: true });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("fails open (renders nothing) on a fetch error", async () => {
  stub("error");
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull(), { timeout: 500 });
});

it("stays hidden when completed, even with an outdated accepted terms version", async () => {
  // The update contract: a version bump (app or terms) must never re-open the
  // gate — `completed` is the only signal it reads.
  stub({
    ...base,
    completed: true,
    terms: { accepted: true, accepted_version: "0.1", current_version: "9.9" },
  });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("renders the step flow directly — no separate risk or video screens", async () => {
  stub({ ...base, completed: false });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.getByRole("dialog")).toBeDefined());
  // The flow is a lazy chunk; nothing else stands between the gate and it.
  expect(await screen.findByTestId("flow")).toBeDefined();
  expect(document.querySelector("iframe")).toBeNull();
});

it("closes when the flow reports completion", async () => {
  stub({ ...base, completed: false });
  render(<OnboardingGate />);
  await screen.findByTestId("flow");
  window.dispatchEvent(new CustomEvent("jarvis:onboarding-changed"));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
