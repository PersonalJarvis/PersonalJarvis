import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";

const send = vi.fn();
vi.mock("@/hooks/useWebSocket", () => ({ getWSClient: () => ({ send }) }));
vi.mock("@/hooks/useOverlayStyle", () => ({
  useOverlayStyle: () => ({
    config: { style: "whisper_bar", options: [] },
    loading: false,
    error: null,
    refetch: vi.fn(),
    saveStyle: vi.fn(),
  }),
}));

import { JarvisDock, MISSION_DND_MIME } from "./JarvisDock";
import { useEventStore } from "@/store/events";

function fakeDataTransfer(json: string) {
  return {
    getData: (mime: string) => (mime === MISSION_DND_MIME ? json : ""),
    setData: vi.fn(),
    types: json ? [MISSION_DND_MIME] : [],
    dropEffect: "none",
    effectAllowed: "all",
  } as unknown as DataTransfer;
}

describe("JarvisDock", () => {
  beforeEach(() => {
    send.mockClear();
    // Inject a deterministic thread resolver (mirrors ChatInput's usage).
    useEventStore.setState({
      ensureActiveThread: async () => "thread-9",
    } as never);
  });
  afterEach(() => cleanup());

  it("sends a mission.inject command on a valid drop", async () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    fireEvent.drop(zone, {
      dataTransfer: fakeDataTransfer(
        JSON.stringify({ slug: "s", utterance: "u", status: "success", summary: "y" }),
      ),
    });
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const arg = send.mock.calls[0][0];
    expect(arg.type).toBe("command");
    expect(arg.action).toBe("mission.inject");
    expect(arg.payload.utterance).toBe("u");
    expect(arg.payload.thread_id).toBe("thread-9");
  });

  it("ignores a drop with no mission payload", () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    fireEvent.drop(zone, { dataTransfer: fakeDataTransfer("") });
    expect(send).not.toHaveBeenCalled();
  });
});
