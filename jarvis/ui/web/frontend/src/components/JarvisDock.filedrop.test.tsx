import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";

const send = vi.fn();
const playDropConfirm = vi.fn();
vi.mock("@/hooks/useWebSocket", () => ({ getWSClient: () => ({ send }) }));
vi.mock("@/hooks/useOverlayStyle", () => ({
  useOverlayStyle: () => ({
    config: { style: "jarvis_bar", options: [] },
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

/** A minimal DataTransfer stand-in for native (OS) drags. `files` carries real
 *  File parts; `data` backs getData for text/URL drags. `types` mirrors what a
 *  browser exposes so the dock's detection logic sees "Files" / "text/*". */
function nativeDataTransfer(opts: {
  files?: File[];
  data?: Record<string, string>;
}): DataTransfer {
  const files = opts.files ?? [];
  const data = opts.data ?? {};
  const types: string[] = [];
  if (files.length) types.push("Files");
  types.push(...Object.keys(data));
  return {
    files: files as unknown as FileList,
    items: [] as unknown as DataTransferItemList,
    getData: (mime: string) => data[mime] ?? "",
    setData: vi.fn(),
    types,
    dropEffect: "none",
    effectAllowed: "all",
  } as unknown as DataTransfer;
}

describe("JarvisDock — native OS file/text drop", () => {
  beforeEach(() => {
    send.mockClear();
    playDropConfirm.mockClear();
    useMissionDrag.getState().end();
    useEventStore.setState({
      ensureActiveThread: async () => "thread-9",
    } as never);
    global.fetch = vi.fn(async () => ({ ok: true, status: 200 })) as never;
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("POSTs a dropped file to /api/chat/drop with files + thread_id", async () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    const file = new File(["hello"], "note.txt", { type: "text/plain" });
    fireEvent.drop(zone, {
      dataTransfer: nativeDataTransfer({ files: [file] }),
    });
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    const [url, init] = (global.fetch as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe("/api/chat/drop");
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("files")).toBe(file);
    expect(body.get("thread_id")).toBe("thread-9");
    expect(body.get("surface")).toBe("dock");
    // The WS mission path must NOT fire for a native drop.
    expect(send).not.toHaveBeenCalled();
  });

  it("POSTs dragged text/URL with a text field and no files", async () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    fireEvent.drop(zone, {
      dataTransfer: nativeDataTransfer({
        data: {
          "text/uri-list": "https://example.com/article",
          "text/plain": "https://example.com/article",
        },
      }),
    });
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    const [url, init] = (global.fetch as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe("/api/chat/drop");
    const body = init.body as FormData;
    expect(body.get("text")).toBe("https://example.com/article");
    expect(body.get("files")).toBeNull();
    expect(body.get("thread_id")).toBe("thread-9");
    expect(send).not.toHaveBeenCalled();
  });

  it("plays the confirmation feedback on a successful native drop", async () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    const file = new File(["x"], "a.png", { type: "image/png" });
    fireEvent.drop(zone, {
      dataTransfer: nativeDataTransfer({ files: [file] }),
    });
    await waitFor(() => expect(playDropConfirm).toHaveBeenCalledTimes(1));
  });

  it("keeps the mission path: a mission-MIME drop sends mission.inject, not fetch", async () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    const dt = {
      getData: (mime: string) =>
        mime === MISSION_DND_MIME
          ? JSON.stringify({ slug: "s", utterance: "u" })
          : "",
      setData: vi.fn(),
      files: [] as unknown as FileList,
      types: [MISSION_DND_MIME],
      dropEffect: "none",
      effectAllowed: "all",
    } as unknown as DataTransfer;
    fireEvent.drop(zone, { dataTransfer: dt });
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0][0].action).toBe("mission.inject");
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("ignores an empty/garbage native drop (no files, no text)", () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    fireEvent.drop(zone, { dataTransfer: nativeDataTransfer({}) });
    expect(global.fetch).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });

  it("blooms (becomes visible) during a native file drag, not just a mission drag", () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    expect(zone.className).toContain("opacity-0");
    fireEvent.dragEnter(zone, {
      dataTransfer: nativeDataTransfer({
        files: [new File(["x"], "a.txt")],
      }),
    });
    const live = screen.getByTestId("jarvis-dock");
    expect(live.className).toContain("opacity-100");
    expect(live.className).not.toContain("opacity-0");
  });

  it("accepts a native drop on the full-window catch layer (toss near Jarvis)", async () => {
    render(<JarvisDock />);
    const zone = screen.getByTestId("jarvis-dock");
    // Arm a native drag so the catch layer mounts.
    fireEvent.dragEnter(zone, {
      dataTransfer: nativeDataTransfer({ files: [new File(["x"], "a.txt")] }),
    });
    const catcher = screen.getByTestId("jarvis-dock-catch");
    const file = new File(["payload"], "drop.txt", { type: "text/plain" });
    fireEvent.drop(catcher, {
      dataTransfer: nativeDataTransfer({ files: [file] }),
    });
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    expect(
      (global.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0],
    ).toBe("/api/chat/drop");
  });
});
