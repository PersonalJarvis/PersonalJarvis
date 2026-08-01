import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The store the panel reads, as one mutable object a test can rewrite between
 * renders. Through `vi.hoisted` because the mock factory is lifted above
 * ordinary module code.
 */
const { storeState } = vi.hoisted(() => ({
  storeState: {} as Record<string, unknown>,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector(storeState),
}));

vi.mock("@/lib/voiceApi", () => ({
  requestVoiceCall: vi.fn(async () => ({ armed: true })),
  requestVoiceHangup: vi.fn(async () => ({ stopped: true })),
}));

import { VoicePanel } from "./VoicePanel";
import * as api from "@/lib/voiceApi";

const pushToast = vi.fn();

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  window.localStorage.clear();
  for (const key of Object.keys(storeState)) delete storeState[key];
  Object.assign(storeState, {
    voiceState: "idle",
    transcription: "",
    assistantName: "Ben",
    pushToast,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("voice panel", () => {
  it("shows the orb, ready, with the wake-word hint", () => {
    render(<VoicePanel />);
    expect(screen.getByTestId("voice-panel")).toBeTruthy();
    const orb = screen.getByTestId("voice-orb-canvas");
    expect(orb.getAttribute("data-state")).toBe("idle");
    expect(orb.getAttribute("style")).toContain("width: 208px");
    expect(screen.getByTestId("voice-panel-status").textContent).toBe("Ready");
    // The brand rule: the assistant's own name, never a hardcoded one.
    expect(screen.getByTestId("voice-orb-button").getAttribute("title")).toContain(
      "Ben",
    );
  });

  it("a click while idle starts the conversation", async () => {
    render(<VoicePanel />);
    fireEvent.click(screen.getByTestId("voice-orb-button"));
    await waitFor(() => expect(api.requestVoiceCall).toHaveBeenCalled());
    expect(api.requestVoiceHangup).not.toHaveBeenCalled();
  });

  it("a click during a conversation hangs up", async () => {
    storeState.voiceState = "speaking";
    render(<VoicePanel />);
    expect(screen.getByTestId("voice-panel-status").textContent).toBe("Speaking");
    fireEvent.click(screen.getByTestId("voice-orb-button"));
    await waitFor(() => expect(api.requestVoiceHangup).toHaveBeenCalled());
    expect(api.requestVoiceCall).not.toHaveBeenCalled();
  });

  it("says so when the pipeline refuses to arm", async () => {
    vi.mocked(api.requestVoiceCall).mockResolvedValue({ armed: false });
    render(<VoicePanel />);
    fireEvent.click(screen.getByTestId("voice-orb-button"));
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        "warning",
        expect.stringContaining("Could not start"),
      ),
    );
  });

  it("reports an unreachable voice backend instead of a dead click", async () => {
    vi.mocked(api.requestVoiceCall).mockRejectedValue(
      new Error("Voice is not running on this computer."),
    );
    render(<VoicePanel />);
    fireEvent.click(screen.getByTestId("voice-orb-button"));
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("not running"),
      ),
    );
  });

  it("shows the live transcript only while a conversation runs", () => {
    storeState.voiceState = "listening";
    storeState.transcription = "open the tests folder";
    render(<VoicePanel />);
    expect(screen.getByTestId("voice-panel-transcript").textContent).toContain(
      "open the tests folder",
    );
    cleanup();
    // The same sentence after the conversation ended must NOT linger.
    storeState.voiceState = "idle";
    render(<VoicePanel />);
    expect(screen.queryByTestId("voice-panel-transcript")).toBeNull();
  });

  it("collapses to a strip and remembers that choice", () => {
    render(<VoicePanel />);
    fireEvent.click(screen.getByTestId("voice-panel-close"));
    expect(screen.getByTestId("voice-panel-collapsed")).toBeTruthy();
    expect(screen.queryByTestId("voice-panel")).toBeNull();
    cleanup();
    // A fresh mount (another workspace, a restart) keeps the collapsed strip.
    render(<VoicePanel />);
    expect(screen.getByTestId("voice-panel-collapsed")).toBeTruthy();
    fireEvent.click(screen.getByTestId("voice-panel-open"));
    expect(screen.getByTestId("voice-panel")).toBeTruthy();
  });
});
