import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("./setup/SetupTour", () => ({
  SetupTour: ({
    preview,
    startAt,
    onFinished,
    onSkipAll,
  }: {
    preview: boolean;
    startAt?: string;
    onFinished: () => void;
    onSkipAll?: () => void;
  }) => (
    <>
      <button type="button" data-testid="guide" data-preview={String(preview)} data-start={startAt ?? ""} onClick={onFinished}>
        guide
      </button>
      <button type="button" data-testid="guide-skip-all" onClick={onSkipAll}>
        skip
      </button>
    </>
  ),
}));
vi.mock("./tour/GuidedTour", () => ({
  GuidedTour: ({ onDone }: { onDone: () => void }) => (
    <button type="button" data-testid="tour" onClick={onDone}>
      tour
    </button>
  ),
}));

vi.mock("./firstSteps/FirstStepsGuide", () => ({
  FirstStepsGuide: ({ onClose }: { onClose: () => void }) => (
    <button type="button" data-testid="first-steps" onClick={onClose}>
      first steps
    </button>
  ),
}));

import { OnboardingGate } from "./OnboardingGate";
import { FIRST_STEPS_START_EVENT, SETUP_REPLAY_EVENT, TOUR_START_EVENT } from "./tourEvents";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
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
  // A first run is real: it completes and restarts once its tour ends.
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

it("replays setup from the explainer on request from Settings", async () => {
  stub({ ...base, completed: true, tour_completed: true });
  render(<OnboardingGate activeSection="profile" />);
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByTestId("guide")).toBeNull();
  act(() => {
    window.dispatchEvent(new CustomEvent(SETUP_REPLAY_EVENT));
  });
  await waitFor(() => expect(screen.getByTestId("guide").dataset.preview).toBe("true"));
  expect(screen.getByTestId("guide").dataset.start).toBe("how");
});

it("goes from setup straight into the tour and completes when the tour ends", async () => {
  const fetchMock = stub({ ...base, completed: false, tour_completed: false });
  render(<OnboardingGate activeSection="chats" />);
  const guide = await screen.findByTestId("guide");
  act(() => guide.click());
  const tour = await screen.findByTestId("tour");
  const urls = () => fetchMock.mock.calls.map((c) => String(c[0]));
  // Nothing completes (and nothing restarts) while the tour is still showing.
  expect(urls()).not.toContain("/api/onboarding/complete");
  act(() => tour.click());
  await waitFor(() => expect(urls()).toContain("/api/onboarding/complete"));
  expect(urls().indexOf("/api/onboarding/tour-complete")).toBeLessThan(urls().indexOf("/api/onboarding/complete"));
});

it("never completes onboarding after a replayed setup", async () => {
  const fetchMock = stub({ ...base, completed: true, tour_completed: true });
  render(<OnboardingGate activeSection="chats" />);
  await new Promise((r) => setTimeout(r, 20));
  act(() => {
    window.dispatchEvent(new CustomEvent(SETUP_REPLAY_EVENT));
  });
  const guide = await screen.findByTestId("guide");
  act(() => guide.click());
  const tour = await screen.findByTestId("tour");
  act(() => tour.click());
  await waitFor(() => expect(screen.queryByTestId("tour")).toBeNull());
  await new Promise((r) => setTimeout(r, 20));
  expect(fetchMock.mock.calls.map((c) => String(c[0]))).not.toContain("/api/onboarding/complete");
});

it("hands the first tour over to the first-steps guide, and keeps it after a reload", async () => {
  stub({ ...base, completed: true, tour_completed: false });
  render(<OnboardingGate activeSection="chats" />);
  const tour = await screen.findByTestId("tour");
  act(() => tour.click());
  await waitFor(() => expect(screen.getByTestId("first-steps")).toBeDefined());
  cleanup();
  // The completion restart reloads the window: the guide comes back from storage.
  stub({ ...base, completed: true, tour_completed: true });
  render(<OnboardingGate activeSection="chats" />);
  await waitFor(() => expect(screen.getByTestId("first-steps")).toBeDefined());
});

it("does not bring the first-steps guide back once it was closed", async () => {
  stub({ ...base, completed: true, tour_completed: false });
  render(<OnboardingGate activeSection="chats" />);
  const tour = await screen.findByTestId("tour");
  act(() => tour.click());
  const guide = await screen.findByTestId("first-steps");
  act(() => guide.click());
  await waitFor(() => expect(screen.queryByTestId("first-steps")).toBeNull());
  // A later tour replay must not restart a guide the user already met.
  act(() => {
    window.dispatchEvent(new CustomEvent(TOUR_START_EVENT));
  });
  const again = await screen.findByTestId("tour");
  act(() => again.click());
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByTestId("first-steps")).toBeNull();
});

it("starts the first-steps guide on request from Settings", async () => {
  stub({ ...base, completed: true, tour_completed: true });
  render(<OnboardingGate activeSection="profile" />);
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByTestId("first-steps")).toBeNull();
  act(() => {
    window.dispatchEvent(new CustomEvent(FIRST_STEPS_START_EVENT));
  });
  await waitFor(() => expect(screen.getByTestId("first-steps")).toBeDefined());
});

it("skips the whole first run: tour recorded, onboarding completed, no guide left", async () => {
  const fetchMock = stub({ ...base, completed: false, tour_completed: false });
  render(<OnboardingGate activeSection="chats" />);
  const skip = await screen.findByTestId("guide-skip-all");
  act(() => skip.click());
  const urls = () => fetchMock.mock.calls.map((c) => String(c[0]));
  await waitFor(() => expect(urls()).toContain("/api/onboarding/complete"));
  expect(urls()).toContain("/api/onboarding/tour-complete");
  expect(screen.queryByTestId("guide")).toBeNull();
  expect(screen.queryByTestId("tour")).toBeNull();
});

it("starts the first-steps guide fresh at the end of a real first run, even if an old one was closed", async () => {
  window.localStorage.setItem(
    "jarvis.firstSteps.v1",
    JSON.stringify({ status: "dismissed", done: [], skipped: [], current: "wake", collapsed: false }),
  );
  stub({ ...base, completed: false, tour_completed: false });
  render(<OnboardingGate activeSection="chats" />);
  const guide = await screen.findByTestId("guide");
  act(() => guide.click());
  const tour = await screen.findByTestId("tour");
  act(() => tour.click());
  await waitFor(() => expect(screen.getByTestId("first-steps")).toBeDefined());
});
