import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The store the bubble reads, as one mutable object a test can rewrite between
 * renders. Through `vi.hoisted` because the mock factory is lifted above
 * ordinary module code.
 */
const { pendingVoiceBatches, paneNotifications, storeState } = vi.hoisted(() => ({
  pendingVoiceBatches: new Map<
    string,
    { batch_id: string; files: string[]; reserved?: boolean }
  >(),
  /** What the bell would be counting — the update card reads the same store. */
  paneNotifications: [] as Array<Record<string, unknown>>,
  storeState: {} as Record<string, unknown>,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector(storeState),
}));

vi.mock("@/lib/voiceApi", () => ({
  requestVoiceCall: vi.fn(async () => ({ armed: true })),
  requestVoiceHangup: vi.fn(async () => ({ stopped: true })),
  fetchTtsVolume: vi.fn(async () => ({ volume: 0.8 })),
  setTtsVolume: vi.fn(async () => undefined),
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
  fetchPaneNotifications: vi.fn(async () => ({
    enabled: true,
    unread: paneNotifications.filter((n) => !n.read).length,
    notifications: paneNotifications,
  })),
}));

vi.mock("@/lib/sound", () => ({ playDropConfirm: vi.fn() }));

import { VoiceBubble } from "./VoiceBubble";
import { resetDismissedNotices } from "./VoiceBubbleNotice";
import * as api from "@/lib/voiceApi";
import * as ideApi from "@/lib/agenticIdeApi";

const pushToast = vi.fn();
const onClose = vi.fn();

/** One entry as the bell's endpoint returns it. */
function notification(overrides: Record<string, unknown> = {}) {
  return {
    id: "n1",
    kind: "completed",
    workspace_id: "ws-7",
    workspace: "Jarvis",
    pane_key: "ws-7:T3",
    pane: "T3",
    agent: "claude",
    display_name: "Claude Code",
    title: "T3 went quiet.",
    detail: "Analyse the marketing options",
    created_at: 1_700_000_000,
    read: false,
    ...overrides,
  };
}

/**
 * jsdom has no PointerEvent constructor, so `fireEvent.pointerDown` falls back
 * to a bare Event that silently drops `button` and the coordinates — and the
 * drag handler correctly ignores a button-less press. A MouseEvent dispatched
 * under the pointer event's NAME carries them; React listens by name, and
 * `pointerId` is consistently undefined on both sides of the identity check.
 */
function firePointer(element: Element | Window, type: string, init: MouseEventInit) {
  fireEvent(element, new MouseEvent(type, { bubbles: true, cancelable: true, ...init }));
}

function renderBubble(props: Partial<Parameters<typeof VoiceBubble>[0]> = {}) {
  return render(
    <VoiceBubble open onClose={onClose} promptTarget="Mika" {...props} />,
  );
}

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  window.localStorage.clear();
  pendingVoiceBatches.clear();
  paneNotifications.length = 0;
  // The waved-away ids live in a module set on purpose (they survive closing
  // the bubble), so a test has to clear them the same way a reload would.
  resetDismissedNotices();
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

describe("voice bubble", () => {
  it("shows the orb, ready, with the brand name in its tooltip", () => {
    renderBubble();
    expect(screen.getByTestId("voice-bubble")).toBeTruthy();
    const orb = screen.getByTestId("voice-orb-canvas");
    expect(orb.getAttribute("data-state")).toBe("idle");
    expect(orb.getAttribute("style")).toContain("width: 132px");
    expect(screen.getByTestId("voice-bubble-status").textContent).toBe("Ready");
    // The brand rule: the assistant's own name, never a hardcoded one.
    expect(screen.getByTestId("voice-orb-button").getAttribute("title")).toContain(
      "Ben",
    );
  });

  it("renders nothing while closed or off screen", () => {
    renderBubble({ open: false });
    expect(screen.queryByTestId("voice-bubble")).toBeNull();
    cleanup();
    renderBubble({ onScreen: false });
    expect(screen.queryByTestId("voice-bubble")).toBeNull();
  });

  it("a click on the orb while idle starts the conversation", async () => {
    renderBubble();
    fireEvent.click(screen.getByTestId("voice-orb-button"));
    await waitFor(() => expect(api.requestVoiceCall).toHaveBeenCalled());
    expect(api.requestVoiceHangup).not.toHaveBeenCalled();
  });

  it("the mic button is the same toggle said explicitly", async () => {
    renderBubble();
    fireEvent.click(screen.getByTestId("voice-bubble-mic"));
    await waitFor(() => expect(api.requestVoiceCall).toHaveBeenCalled());
    cleanup();
    vi.clearAllMocks();
    storeState.voiceState = "listening";
    renderBubble();
    fireEvent.click(screen.getByTestId("voice-bubble-mic"));
    await waitFor(() => expect(api.requestVoiceHangup).toHaveBeenCalled());
  });

  it("dragging moves the bubble, remembers the spot, and swallows the click", async () => {
    renderBubble();
    const bubble = screen.getByTestId("voice-bubble");
    const before = { left: bubble.style.left, top: bubble.style.top };

    firePointer(bubble, "pointerdown", { button: 0, clientX: 300, clientY: 300 });
    firePointer(bubble, "pointermove", { clientX: 240, clientY: 380 });
    firePointer(bubble, "pointerup", { clientX: 240, clientY: 380 });

    expect(bubble.style.left).not.toBe(before.left);
    expect(bubble.style.top).not.toBe(before.top);
    expect(
      window.localStorage.getItem("jarvis.agenticIde.voiceBubblePos"),
    ).toBeTruthy();

    // The click that ends a drag must not also toggle the conversation…
    fireEvent.click(screen.getByTestId("voice-orb-button"));
    expect(api.requestVoiceCall).not.toHaveBeenCalled();
    // …and the suppression is one-shot: the next real click goes through.
    fireEvent.click(screen.getByTestId("voice-orb-button"));
    await waitFor(() => expect(api.requestVoiceCall).toHaveBeenCalledTimes(1));
  });

  it("a press that never moved is a click, not a drag", async () => {
    renderBubble();
    const orb = screen.getByTestId("voice-orb-button");
    firePointer(orb, "pointerdown", { button: 0, clientX: 100, clientY: 100 });
    firePointer(orb, "pointerup", { clientX: 101, clientY: 100 });
    fireEvent.click(orb);
    await waitFor(() => expect(api.requestVoiceCall).toHaveBeenCalled());
  });

  it("the speaker mutes session-only and restores the previous volume", async () => {
    renderBubble();
    const speaker = screen.getByTestId("voice-bubble-speaker");
    await waitFor(() => expect(api.fetchTtsVolume).toHaveBeenCalled());

    fireEvent.click(speaker);
    await waitFor(() => expect(api.setTtsVolume).toHaveBeenCalledWith(0, false));
    await waitFor(() => expect(speaker.getAttribute("aria-pressed")).toBe("true"));

    fireEvent.click(speaker);
    // persist=false both ways: a bubble mute must never survive into
    // jarvis.toml as the boot default.
    await waitFor(() => expect(api.setTtsVolume).toHaveBeenCalledWith(0.8, false));
    await waitFor(() => expect(speaker.getAttribute("aria-pressed")).toBe("false"));
  });

  it("X hangs up a running conversation and puts the bubble away", async () => {
    storeState.voiceState = "speaking";
    renderBubble();
    fireEvent.click(screen.getByTestId("voice-bubble-close"));
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(api.requestVoiceHangup).toHaveBeenCalled());
  });

  it("X while idle only closes — there is nothing to hang up", () => {
    renderBubble();
    fireEvent.click(screen.getByTestId("voice-bubble-close"));
    expect(onClose).toHaveBeenCalled();
    expect(api.requestVoiceHangup).not.toHaveBeenCalled();
  });

  it("stages a file dropped on the orb for the selected pane's next prompt", async () => {
    renderBubble();
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
    const { rerender } = renderBubble();
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
    rerender(<VoiceBubble open onClose={onClose} promptTarget="Bruno" />);
    expect(screen.getByTestId("voice-orb-drop-context").textContent).toContain("Mika");
    pendingVoiceBatches.clear();
    storeState.voiceState = "speaking";
    rerender(<VoiceBubble open onClose={onClose} promptTarget="Bruno" />);
    await waitFor(() =>
      expect(screen.queryByTestId("voice-orb-drop-context")).toBeNull(),
    );
  });

  it("hydrates pending context after a remount and locks reserved context", async () => {
    pendingVoiceBatches.set("batch-old", {
      batch_id: "batch-old",
      files: ["existing.png"],
      reserved: true,
    });
    renderBubble();

    const receipt = await screen.findByTestId("voice-orb-drop-context");
    expect(receipt.textContent).toContain("existing.png");
    expect(receipt.textContent).toContain("Adding");
    expect(
      (screen.getByLabelText("Remove this pending context") as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("discovers a native Jarvis Bar drop after the bubble is already open", async () => {
    renderBubble();
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
    renderBubble();
    expect(screen.getByTestId("voice-bubble-status").textContent).toBe("Speaking");
    fireEvent.click(screen.getByTestId("voice-orb-button"));
    await waitFor(() => expect(api.requestVoiceHangup).toHaveBeenCalled());
    expect(api.requestVoiceCall).not.toHaveBeenCalled();
  });

  it("says so when the pipeline refuses to arm", async () => {
    vi.mocked(api.requestVoiceCall).mockResolvedValue({ armed: false });
    renderBubble();
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
    renderBubble();
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
    renderBubble();
    expect(screen.getByTestId("voice-bubble-transcript").textContent).toContain(
      "open the tests folder",
    );
    cleanup();
    // The same sentence after the conversation ended must NOT linger.
    storeState.voiceState = "idle";
    renderBubble();
    expect(screen.queryByTestId("voice-bubble-transcript")).toBeNull();
  });

  it("shows the streaming caret only while the sentence is provisional", () => {
    storeState.voiceState = "listening";
    storeState.transcription = "open the tests";
    storeState.transcriptionFinal = false;
    renderBubble();
    expect(
      screen
        .getByTestId("voice-bubble-transcript")
        .querySelector(".agentic-voice-transcript-caret"),
    ).toBeTruthy();
    cleanup();
    // The recognizer committed — the "still writing" caret would be a lie.
    storeState.transcriptionFinal = true;
    renderBubble();
    expect(
      screen
        .getByTestId("voice-bubble-transcript")
        .querySelector(".agentic-voice-transcript-caret"),
    ).toBeNull();
  });

  /*
   * The bubble has no panel of its own. What that has to mean, mechanically, is
   * that the space it spans keeps belonging to the terminals underneath — the
   * wrapper takes no pointer events and every real control turns them back on.
   * Without this the bubble would be an invisible click shield over the wall.
   */
  it("takes no clicks except on its own controls", () => {
    renderBubble();
    const bubble = screen.getByTestId("voice-bubble");
    expect(bubble.className).toContain("pointer-events-none");
    // No card: nothing paints a background across the whole column.
    expect(bubble.className).not.toContain("bg-background/95");

    for (const id of [
      "voice-bubble-attach",
      "voice-bubble-mic",
      "voice-bubble-close",
      "voice-bubble-speaker",
    ]) {
      expect(screen.getByTestId(id).className).toContain("pointer-events-auto");
    }
    // The row itself is only the gaps between the discs, and those belong to
    // whatever pane is behind them.
    const row = screen.getByTestId("voice-bubble-mic").parentElement!;
    expect(row.className).toContain("pointer-events-none");
  });

  it("keeps the status line off the screen while nothing is happening", () => {
    renderBubble();
    // Still spoken to a screen reader, just not painted over a terminal.
    const idle = screen.getByTestId("voice-bubble-status").parentElement!;
    expect(idle.className).toContain("sr-only");
    cleanup();

    storeState.voiceState = "speaking";
    renderBubble();
    const busy = screen.getByTestId("voice-bubble-status").parentElement!;
    expect(busy.className).not.toContain("sr-only");
    expect(busy.className).toContain("pointer-events-auto");
  });

  it("reports the newest unread pane update and takes you to that pane", async () => {
    paneNotifications.push(notification());
    const onJumpToPane = vi.fn();
    renderBubble({ onJumpToPane });

    const card = await screen.findByTestId("voice-bubble-notice");
    expect(card.getAttribute("data-kind")).toBe("completed");
    // What the pane was asked to do, not the backend's "went quiet" sentence.
    expect(card.textContent).toContain("Analyse the marketing options");
    expect(card.textContent).toContain("T3");

    fireEvent.click(screen.getByTestId("voice-bubble-notice-jump"));
    expect(onJumpToPane).toHaveBeenCalledWith("ws-7", "T3");
  });

  it("says nothing about updates the bell has already been shown", async () => {
    paneNotifications.push(notification({ read: true }));
    renderBubble();
    await waitFor(() => expect(ideApi.fetchPaneNotifications).toHaveBeenCalled());
    expect(screen.queryByTestId("voice-bubble-notice")).toBeNull();
  });

  it("hiding an update card leaves the entry in the bell", async () => {
    paneNotifications.push(notification());
    renderBubble();

    await screen.findByTestId("voice-bubble-notice");
    fireEvent.click(screen.getByTestId("voice-bubble-notice-dismiss"));
    await waitFor(() =>
      expect(screen.queryByTestId("voice-bubble-notice")).toBeNull(),
    );
    // The peek hid it. Nothing was discarded and nothing was marked read, so
    // the bell still counts it — that is the whole contract of the X here.
    expect(paneNotifications[0].read).toBe(false);
  });

  it("is a plain report, not a link, when nobody can move the grid", async () => {
    paneNotifications.push(notification());
    renderBubble();
    await screen.findByTestId("voice-bubble-notice");
    expect(screen.queryByTestId("voice-bubble-notice-jump")).toBeNull();
  });
});
