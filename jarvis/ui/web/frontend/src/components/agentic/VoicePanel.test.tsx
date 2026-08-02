import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The store the panel reads, as one mutable object a test can rewrite between
 * renders. Through `vi.hoisted` because the mock factory is lifted above
 * ordinary module code.
 */
const { pendingVoiceBatches, storeState } = vi.hoisted(() => ({
  pendingVoiceBatches: new Map<
    string,
    { batch_id: string; files: string[]; reserved?: boolean }
  >(),
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

vi.mock("@/lib/agenticIdeApi", () => ({
  attachToTerminal: vi.fn(async (name: string, payload: { files?: File[] }) => {
    const files = payload.files?.map((file) => file.name) ?? [];
    pendingVoiceBatches.set("batch-1", { batch_id: "batch-1", files });
    return {
      terminal: name,
      references: ["@.jarvis/drops/layout.png"],
      files,
      copied: 1,
      submitted: false,
      delivered: false,
      staged_for_voice: 1,
      voice_batch_id: "batch-1",
      analysis: [],
    };
  }),
  fetchVoiceAttachments: vi.fn(async (terminal: string) => ({
    terminal,
    batches: Array.from(pendingVoiceBatches.values()).map((batch) => ({
      ...batch,
      reserved: batch.reserved ?? false,
    })),
  })),
  fetchAllVoiceAttachments: vi.fn(async () => ({
    batches: Array.from(pendingVoiceBatches.values()).map((batch) => ({
      ...batch,
      terminal: "Mika",
      reserved: batch.reserved ?? false,
    })),
  })),
  removeVoiceAttachment: vi.fn(async (_terminal: string, batchId: string) => {
    pendingVoiceBatches.delete(batchId);
  }),
}));

vi.mock("@/lib/sound", () => ({ playDropConfirm: vi.fn() }));

import { VoicePanel } from "./VoicePanel";
import * as api from "@/lib/voiceApi";
import * as ideApi from "@/lib/agenticIdeApi";

const pushToast = vi.fn();

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  window.localStorage.clear();
  pendingVoiceBatches.clear();
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
    expect(orb.getAttribute("style")).toContain("width: 176px");
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

  it("stages a file dropped on the orb for the selected pane's next prompt", async () => {
    render(<VoicePanel promptTarget="Mika" />);
    const orb = screen.getByTestId("voice-orb-button");
    const file = new File(["pixels"], "layout.png", { type: "image/png" });
    const dataTransfer = {
      files: [file],
      types: ["Files"],
      getData: () => "",
      dropEffect: "none",
    } as unknown as DataTransfer;

    fireEvent.dragEnter(orb, { dataTransfer });
    expect(screen.getByTestId("voice-orb-stage").getAttribute("data-drop-state")).toBe(
      "over",
    );
    fireEvent.drop(orb, { dataTransfer });

    await waitFor(() => expect(ideApi.attachToTerminal).toHaveBeenCalledTimes(1));
    expect(ideApi.attachToTerminal).toHaveBeenCalledWith(
      "Mika",
      expect.objectContaining({
        files: [file],
        analyze: true,
        deliver: false,
        stageForVoice: true,
      }),
    );
    expect(
      (await screen.findByTestId("voice-orb-drop-context")).textContent,
    ).toContain("layout.png");
  });

  it("keeps a receipt bound to its original pane until the backend consumes it", async () => {
    const { rerender } = render(<VoicePanel promptTarget="Mika" />);
    const file = new File(["pixels"], "layout.png", { type: "image/png" });
    const dataTransfer = {
      files: [file],
      types: ["Files"],
      getData: () => "",
      dropEffect: "none",
    } as unknown as DataTransfer;
    fireEvent.drop(screen.getByTestId("voice-orb-button"), { dataTransfer });
    const receipt = await screen.findByTestId("voice-orb-drop-context");
    expect(receipt.textContent).toContain("Mika");

    storeState.voiceState = "thinking";
    rerender(<VoicePanel promptTarget="Bruno" />);
    expect(screen.getByTestId("voice-orb-drop-context").textContent).toContain("Mika");
    pendingVoiceBatches.clear();
    storeState.voiceState = "speaking";
    rerender(<VoicePanel promptTarget="Bruno" />);
    await waitFor(() =>
      expect(screen.queryByTestId("voice-orb-drop-context")).toBeNull(),
    );
  });

  it("hydrates pending context after a panel remount and locks reserved context", async () => {
    pendingVoiceBatches.set("batch-old", {
      batch_id: "batch-old",
      files: ["existing.png"],
      reserved: true,
    });
    render(<VoicePanel promptTarget="Mika" />);

    const receipt = await screen.findByTestId("voice-orb-drop-context");
    expect(receipt.textContent).toContain("existing.png");
    expect(receipt.textContent).toContain("Adding");
    expect(
      (screen.getByLabelText("Remove this pending context") as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("discovers a native Jarvis Bar drop after the panel is already mounted", async () => {
    render(<VoicePanel promptTarget="Mika" />);
    await waitFor(() => expect(ideApi.fetchAllVoiceAttachments).toHaveBeenCalled());
    expect(screen.queryByTestId("voice-orb-drop-context")).toBeNull();

    pendingVoiceBatches.set("batch-native", {
      batch_id: "batch-native",
      files: ["bar-drop.png"],
    });
    fireEvent.focus(window);

    const receipt = await screen.findByTestId("voice-orb-drop-context");
    expect(receipt.textContent).toContain("bar-drop.png");
    expect(receipt.textContent).toContain("Mika");
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
