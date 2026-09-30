import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import type { useOnboarding } from "@/hooks/useOnboarding";
import type { BeatProps } from "./WelcomeFlow";

// Every beat after the consent is a stub with the two ways on, so these tests
// cover the card's own job: order, progress, skipping and resuming.
function stubBeat(id: string) {
  return function Stub({ next, skip, report }: BeatProps) {
    return (
      <div data-testid={`beat-${id}`}>
        <button type="button" onClick={() => { report({ summary: `${id} done`, gap: null }); next(); }}>
          next-{id}
        </button>
        <button type="button" onClick={skip}>skip-{id}</button>
      </div>
    );
  };
}
vi.mock("./beats/BrainBeat", () => ({ BrainBeat: stubBeat("brain") }));
vi.mock("./beats/AgentsBeat", () => ({ AgentsBeat: stubBeat("agents") }));
vi.mock("./beats/PermissionsBeat", () => ({ PermissionsBeat: stubBeat("permissions") }));
vi.mock("./beats/VoiceBeat", () => ({ VoiceBeat: stubBeat("voice") }));

import { WelcomeFlow } from "./WelcomeFlow";

beforeAll(async () => {
  await loadLocaleChunk("onboarding");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

type Onb = ReturnType<typeof useOnboarding>;

function fakeOnb(over: Partial<NonNullable<Onb["state"]>> = {}): Onb {
  return {
    state: {
      completed: false,
      current_step: null,
      skipped_steps: [],
      terms: { accepted: false, accepted_version: null, current_version: "1.0" },
      wake_word_acknowledged: false,
      tour_completed: false,
      legal_references: [],
      steps: [],
      ...over,
    },
    loading: false,
    error: null,
    refetch: vi.fn(async () => undefined),
    saveStep: vi.fn(async () => undefined),
    acceptTerms: vi.fn(async () => undefined),
    acknowledgeWakeWord: vi.fn(async () => undefined),
    complete: vi.fn(async () => undefined),
    completeTour: vi.fn(async () => undefined),
  };
}

function stubFetch(platform = "win32") {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url === "/api/permissions/status") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ platform }) });
      }
      if (url === "/api/settings/autostart") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ enabled: false, supported: true }) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }),
  );
}

it("holds the way on until the consent is ticked", async () => {
  stubFetch();
  const onb = fakeOnb();
  render(<WelcomeFlow onb={onb} />);
  const start = screen.getByTestId("onboarding-primary") as HTMLButtonElement;
  expect(start.disabled).toBe(true);
  fireEvent.click(screen.getByTestId("onboarding-accept"));
  expect(start.disabled).toBe(false);
  await act(async () => {
    fireEvent.click(start);
  });
  expect(onb.acceptTerms).toHaveBeenCalled();
  await waitFor(() => expect(screen.getByTestId("beat-brain")).toBeDefined());
  expect(onb.saveStep).toHaveBeenCalledWith("brain", []);
});

it("stays on the consent when saving it fails", async () => {
  stubFetch();
  const onb = fakeOnb();
  onb.acceptTerms = vi.fn(async () => {
    throw new Error("down");
  });
  render(<WelcomeFlow onb={onb} />);
  fireEvent.click(screen.getByTestId("onboarding-accept"));
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  expect(screen.queryByTestId("beat-brain")).toBeNull();
  expect(screen.getByRole("status").textContent).toMatch(/could not be saved/i);
});

it("says goodbye on decline", async () => {
  stubFetch();
  render(<WelcomeFlow onb={fakeOnb()} />);
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-decline"));
  });
  expect(screen.getByTestId("onboarding-declined")).toBeDefined();
});

it("walks every beat, records skips and ends on the review", async () => {
  stubFetch("win32");
  const onb = fakeOnb({ terms: { accepted: true, accepted_version: "1.0", current_version: "1.0" } });
  render(<WelcomeFlow onb={onb} />);
  // Consent already given: the guide resumes after it.
  await screen.findByTestId("beat-brain");
  fireEvent.click(screen.getByText("next-brain"));
  await screen.findByTestId("beat-agents");
  fireEvent.click(screen.getByText("skip-agents"));
  await screen.findByTestId("beat-voice");
  expect(onb.saveStep).toHaveBeenLastCalledWith("voice", ["agents"]);
  fireEvent.click(screen.getByText("next-voice"));
  await screen.findByTestId("onboarding-review");
  expect(screen.getByTestId("onboarding-review").textContent).toContain("brain done");
  expect(screen.getByTestId("onboarding-review").textContent).toContain("voice done");
  // Windows: no permissions beat anywhere.
  expect(screen.queryByTestId("beat-permissions")).toBeNull();
});

it("goes back, but never behind the consent", async () => {
  stubFetch();
  const onb = fakeOnb({ terms: { accepted: true, accepted_version: "1.0", current_version: "1.0" }, current_step: "agents" });
  render(<WelcomeFlow onb={onb} />);
  await screen.findByTestId("beat-agents");
  fireEvent.click(screen.getByTestId("onboarding-back"));
  await screen.findByTestId("beat-brain");
  expect(screen.queryByTestId("onboarding-back")).toBeNull();
});

it("adds the permissions beat on macOS", async () => {
  stubFetch("darwin");
  const onb = fakeOnb({ terms: { accepted: true, accepted_version: "1.0", current_version: "1.0" }, current_step: "agents" });
  render(<WelcomeFlow onb={onb} />);
  await screen.findByTestId("beat-agents");
  await waitFor(() => expect(screen.getByText(/of 6/)).toBeDefined());
  fireEvent.click(screen.getByText("next-agents"));
  await screen.findByTestId("beat-permissions");
});

it("starts the assistant from the review", async () => {
  stubFetch();
  const onb = fakeOnb({ terms: { accepted: true, accepted_version: "1.0", current_version: "1.0" }, current_step: "ready" });
  render(<WelcomeFlow onb={onb} />);
  const start = await screen.findByTestId("onboarding-start");
  await act(async () => {
    fireEvent.click(start);
  });
  expect(onb.complete).toHaveBeenCalled();
});

it("keeps the start live and explains a failed completion", async () => {
  stubFetch();
  const onb = fakeOnb({ terms: { accepted: true, accepted_version: "1.0", current_version: "1.0" }, current_step: "ready" });
  onb.complete = vi.fn(async () => {
    throw new Error("down");
  });
  render(<WelcomeFlow onb={onb} />);
  const start = (await screen.findByTestId("onboarding-start")) as HTMLButtonElement;
  await act(async () => {
    fireEvent.click(start);
  });
  expect(screen.getByText(/could not be finished/i)).toBeDefined();
  expect(start.disabled).toBe(false);
});
