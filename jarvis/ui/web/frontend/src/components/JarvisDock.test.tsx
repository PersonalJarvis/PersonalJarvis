import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";

const send = vi.fn();
const playDropConfirm = vi.fn();
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
vi.mock("@/lib/sound", () => ({ playDropConfirm: () => playDropConfirm() }));

import { JarvisDock, MISSION_DND_MIME } from "./JarvisDock";
import { useEventStore } from "@/store/events";
import { useMissionDrag } from "@/store/missionDrag";

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
    playDropConfirm.mockClear();
    useMissionDrag.getState().end();
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

  it("plays the soft confirmation sound on a successful drop", async () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    fireEvent.drop(zone, {
      dataTransfer: fakeDataTransfer(JSON.stringify({ slug: "s", utterance: "u" })),
    });
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(playDropConfirm).toHaveBeenCalledTimes(1);
  });

  it("does not play the sound for an empty drop", () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    fireEvent.drop(zone, { dataTransfer: fakeDataTransfer("") });
    expect(playDropConfirm).not.toHaveBeenCalled();
  });

  it("stays visually hidden in idle and reveals only while a mission drag is in flight", () => {
    const { rerender } = render(<JarvisDock />);
    // Idle: present in the DOM (so the drop handler stays wired) but invisible
    // and non-interactive — no permanent icon cluttering the corner.
    const idle = screen.getByTestId("jarvis-dock");
    expect(idle.className).toContain("opacity-0");
    expect(idle.className).toContain("pointer-events-none");

    // A mission card lifts → the dock blooms into a visible target.
    useMissionDrag.getState().begin();
    rerender(<JarvisDock />);
    const live = screen.getByTestId("jarvis-dock");
    expect(live.className).toContain("opacity-100");
    expect(live.className).not.toContain("opacity-0");
  });

  it("mounts a full-window catch layer only while a mission drag is active", () => {
    const { rerender } = render(<JarvisDock />);
    expect(screen.queryByTestId("jarvis-dock-catch")).toBeNull();
    useMissionDrag.getState().begin();
    rerender(<JarvisDock />);
    expect(screen.getByTestId("jarvis-dock-catch")).toBeTruthy();
  });

  it("injects when a card is dropped on the catch layer (toss near the dock)", async () => {
    useMissionDrag.getState().begin();
    render(<JarvisDock />);
    const catcher = screen.getByTestId("jarvis-dock-catch");
    fireEvent.drop(catcher, {
      dataTransfer: fakeDataTransfer(JSON.stringify({ slug: "s", utterance: "u" })),
    });
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0][0].action).toBe("mission.inject");
  });

  it("clears the global drag state after a drop", async () => {
    useMissionDrag.getState().begin();
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    fireEvent.drop(zone, {
      dataTransfer: fakeDataTransfer(JSON.stringify({ slug: "s", utterance: "u" })),
    });
    await waitFor(() => expect(useMissionDrag.getState().dragging).toBe(false));
  });
});
