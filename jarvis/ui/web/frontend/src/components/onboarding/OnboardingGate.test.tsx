import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("./setup/SetupTour", () => ({
  SetupTour: ({ preview }: { preview: boolean }) => <div data-testid="guide" data-preview={String(preview)} />,
}));
vi.mock("./tour/GuidedTour", () => ({
  GuidedTour: ({ onDone }: { onDone: () => void }) => (
    <button type="button" data-testid="tour" onClick={onDone}>
      tour
    </button>
  ),
}));

import { OnboardingGate } from "./OnboardingGate";
import { TOUR_START_EVENT } from "./tourEvents";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const base = {
  current_step: null,
  skipped_steps: [],
  terms: { accepted: false, accepted_version: null, current_version: "1.0" },
  wake_word_acknowledged: false,
  legal_references: [],
  steps: ["welcome"],
};

function stub(state: object | "error") {
  const fetchMock = vi.fn().mockImplementation(() =>
    state === "error"
      ? Promise.reject(new Error("net"))
      : Promise.resolve({ ok: true, json: () => Promise.resolve(state) }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

it("shows the guide while setup is not complete", async () => {
  stub({ ...base, completed: false, tour_completed: false });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.getByTestId("guide")).toBeDefined());
  // A first run is real: it completes and restarts at its end.
  expect(screen.getByTestId("guide").dataset.preview).toBe("false");
  expect(screen.queryByTestId("tour")).toBeNull();
});

it("keeps a fresh install's IDE free of the guide", async () => {
  stub({ ...base, completed: false, tour_completed: false });
  const { rerender } = render(<OnboardingGate activeSection="agentic-ide" />);
  await waitFor(() => expect(screen.queryByTestId("guide")).toBeNull());
  rerender(<OnboardingGate activeSection="chats" />);
  await waitFor(() => expect(screen.getByTestId("guide")).toBeDefined());
});

it("tours the app once setup is complete and the tour is not seen", async () => {
  stub({ ...base, completed: true, tour_completed: false });
  render(<OnboardingGate activeSection="chats" />);
  await waitFor(() => expect(screen.getByTestId("tour")).toBeDefined());
  expect(screen.queryByTestId("guide")).toBeNull();
});

it("records the tour and closes it when it ends", async () => {
  const fetchMock = stub({ ...base, completed: true, tour_completed: false });
  render(<OnboardingGate activeSection="chats" />);
  const tour = await screen.findByTestId("tour");
  act(() => tour.click());
  await waitFor(() => expect(screen.queryByTestId("tour")).toBeNull());
  expect(fetchMock).toHaveBeenCalledWith("/api/onboarding/tour-complete", expect.objectContaining({ method: "POST" }));
});

it("does not start the automatic tour inside the IDE", async () => {
  stub({ ...base, completed: true, tour_completed: false });
  render(<OnboardingGate activeSection="agentic-ide" />);
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByTestId("tour")).toBeNull();
});

it("renders nothing when setup and tour are done", async () => {
  stub({ ...base, completed: true, tour_completed: true });
  render(<OnboardingGate />);
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByTestId("guide")).toBeNull();
  expect(screen.queryByTestId("tour")).toBeNull();
});

it("never tours on a backend that does not report the tour", async () => {
  // An older backend: `tour_completed` is absent, not false.
  stub({ ...base, completed: true });
  render(<OnboardingGate />);
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByTestId("tour")).toBeNull();
});

it("replays the tour on request from Settings", async () => {
  stub({ ...base, completed: true, tour_completed: true });
  render(<OnboardingGate activeSection="profile" />);
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByTestId("tour")).toBeNull();
  act(() => {
    window.dispatchEvent(new CustomEvent(TOUR_START_EVENT));
  });
  await waitFor(() => expect(screen.getByTestId("tour")).toBeDefined());
});

it("fails open (renders nothing) on a fetch error", async () => {
  stub("error");
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.queryByTestId("guide")).toBeNull(), { timeout: 500 });
});

it("closes the guide when setup completes", async () => {
  stub({ ...base, completed: false, tour_completed: false });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.getByTestId("guide")).toBeDefined());
  act(() => {
    window.dispatchEvent(new CustomEvent("jarvis:onboarding-changed"));
  });
  await waitFor(() => expect(screen.queryByTestId("guide")).toBeNull());
});

it("replays the setup on a finished install without completing it", async () => {
  stub({ ...base, completed: true, tour_completed: true });
  window.history.replaceState(null, "", "/?onboarding=force");
  try {
    render(<OnboardingGate activeSection="chats" />);
    await waitFor(() => expect(screen.getByTestId("guide").dataset.preview).toBe("true"));
  } finally {
    window.history.replaceState(null, "", "/");
  }
});
