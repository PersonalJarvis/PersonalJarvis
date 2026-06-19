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

it("re-shows for a terms version bump even when completed", async () => {
  stub({
    ...base,
    completed: true,
    terms: { accepted: true, accepted_version: "1.0", current_version: "1.1" },
  });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.getByRole("dialog")).toBeDefined());
});

it("fails open (renders nothing) on a fetch error", async () => {
  stub("error");
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull(), { timeout: 500 });
});
