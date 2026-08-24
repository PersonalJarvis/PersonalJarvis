import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePromptAttachments } from "@/components/agentic/PromptAttachments";
import * as api from "@/lib/agenticIdeApi";
import { NATIVE_DROP_EVENT } from "@/lib/nativeDrop";

/**
 * Holding a file for an agent's next instruction.
 *
 * The case worth a test of its own is the desktop shell's: a file dragged out
 * of Explorer reaches the page as BYTES with no path, and the host reports the
 * real location a moment later. Attaching the bytes anyway would hand the agent
 * a copy in `.jarvis/drops` — it would answer about a file the user cannot see
 * and cannot have edited, which looks exactly like the agent ignoring them.
 */

vi.mock("@/lib/agenticIdeApi", () => ({ attachToTerminal: vi.fn() }));

const analysed = (name: string) => ({
  name,
  reference: `@${name}`,
  kind: "image" as const,
  detail: "a screenshot",
  described_by: "vision" as const,
  note: "",
});

function reply(names: string[]) {
  return {
    terminal: "T1",
    references: [],
    files: [],
    copied: 0,
    submitted: false,
    delivered: false,
    analysis: names.map(analysed),
  };
}

/** A drag out of Explorer as the desktop shell delivers it: bytes, no path. */
function bytesOnly(file: File): DataTransfer {
  return {
    types: ["Files"],
    files: [file],
    items: [],
    getData: () => "",
    dropEffect: "none",
  } as unknown as DataTransfer;
}

/** Pretend to be the pywebview shell, which answers a drop it has resolved. */
function shellAnswers(detail: { paths: string[]; names: string[] } | null) {
  (window as unknown as { pywebview?: unknown }).pywebview = {};
  if (detail === null) return;
  queueMicrotask(() =>
    window.dispatchEvent(new CustomEvent(NATIVE_DROP_EVENT, { detail })),
  );
}

describe("usePromptAttachments", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.attachToTerminal).mockResolvedValue(reply(["shot.png"]));
  });

  afterEach(() => {
    delete (window as unknown as { pywebview?: unknown }).pywebview;
  });

  it("attaches a browser drop as bytes — outside the shell there is no path", async () => {
    const problem = vi.fn();
    const { result } = renderHook(() => usePromptAttachments("T1", problem));

    act(() => {
      result.current.dragHandlers.onDrop({
        preventDefault: () => {},
        dataTransfer: bytesOnly(new File(["x"], "shot.png", { type: "image/png" })),
      } as never);
    });

    await waitFor(() => expect(api.attachToTerminal).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.attachToTerminal).mock.calls[0];
    expect(payload.files?.map((f) => f.name)).toEqual(["shot.png"]);
    expect(payload.paths ?? []).toEqual([]);
    // Held for the message being written, and read so a text-only CLI can
    // still be told what is in the picture.
    expect(payload).toMatchObject({ analyze: true, deliver: false });
    expect(problem).not.toHaveBeenCalled();
  });

  it("prefers the path the desktop shell reports over uploading the same bytes", async () => {
    shellAnswers({ paths: ["C:\\work\\app\\src\\main.ts"], names: ["main.ts"] });
    const { result } = renderHook(() => usePromptAttachments("T1", vi.fn()));

    act(() => {
      result.current.dragHandlers.onDrop({
        preventDefault: () => {},
        dataTransfer: bytesOnly(new File(["x"], "main.ts", { type: "text/plain" })),
      } as never);
    });

    await waitFor(() => expect(api.attachToTerminal).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.attachToTerminal).mock.calls[0];
    expect(payload.paths).toEqual(["C:\\work\\app\\src\\main.ts"]);
    // The byte copy of the very same file is dropped, or the agent would be
    // handed the file twice — once where it lives and once as a copy.
    expect(payload.files ?? []).toEqual([]);
  });

  it("still attaches the bytes when the shell resolves nothing", async () => {
    shellAnswers(null); // in the shell, but no answer for this drop
    const { result } = renderHook(() => usePromptAttachments("T1", vi.fn()));

    act(() => {
      result.current.dragHandlers.onDrop({
        preventDefault: () => {},
        dataTransfer: bytesOnly(new File(["x"], "shot.png", { type: "image/png" })),
      } as never);
    });

    await waitFor(() => expect(api.attachToTerminal).toHaveBeenCalled(), { timeout: 4000 });
    const [, payload] = vi.mocked(api.attachToTerminal).mock.calls[0];
    expect(payload.files?.map((f) => f.name)).toEqual(["shot.png"]);
  });

  it("renames a pasted screenshot and leaves pasted text alone", async () => {
    const { result } = renderHook(() => usePromptAttachments("T1", vi.fn()));

    act(() => {
      result.current.onPaste({
        preventDefault: () => {},
        clipboardData: { items: [], types: ["text/plain"], files: [] },
      } as never);
    });
    expect(api.attachToTerminal).not.toHaveBeenCalled();

    const image = new File(["png"], "image.png", { type: "image/png" });
    act(() => {
      result.current.onPaste({
        preventDefault: () => {},
        clipboardData: { items: [{ kind: "file", getAsFile: () => image }] },
      } as never);
    });

    await waitFor(() => expect(api.attachToTerminal).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.attachToTerminal).mock.calls[0];
    // Every clipboard calls its image `image.png`; two screenshots must not
    // arrive under one name.
    expect(payload.files?.[0].name).not.toBe("image.png");
    expect(payload.files?.[0].name).toContain("t1-paste-");
  });

  it("holds the same file once, however often it is dropped", async () => {
    const { result } = renderHook(() => usePromptAttachments("T1", vi.fn()));

    for (let i = 0; i < 2; i += 1) {
      act(() => {
        result.current.attachFiles([new File(["x"], "shot.png", { type: "image/png" })]);
      });
      await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    }
    expect(api.attachToTerminal).toHaveBeenCalledTimes(2);
    // Two attempts, one chip: a repeated reference has the agent read the same
    // file twice for nothing.
    expect(result.current.attachments.map((a) => a.name)).toEqual(["shot.png"]);
  });

  it("tells a failure apart from a drop that carried nothing", async () => {
    const problem = vi.fn();
    const { result } = renderHook(() => usePromptAttachments("T1", problem));

    vi.mocked(api.attachToTerminal).mockResolvedValueOnce(reply([]));
    act(() => result.current.attachFiles([new File(["x"], "empty.bin")]));
    await waitFor(() => expect(problem).toHaveBeenCalled());
    expect(problem.mock.calls[0][1]).toBe("warning");

    vi.mocked(api.attachToTerminal).mockRejectedValueOnce(new Error("pane is gone"));
    act(() => result.current.attachFiles([new File(["x"], "again.bin")]));
    await waitFor(() => expect(problem).toHaveBeenCalledTimes(2));
    expect(problem.mock.calls[1]).toEqual(["pane is gone", "error"]);
  });

  it("refuses a drop with no session picked instead of swallowing it", async () => {
    const problem = vi.fn();
    const { result } = renderHook(() => usePromptAttachments("", problem));

    act(() => result.current.attachFiles([new File(["x"], "shot.png")]));

    await waitFor(() => expect(problem).toHaveBeenCalled());
    expect(problem.mock.calls[0][1]).toBe("warning");
    expect(api.attachToTerminal).not.toHaveBeenCalled();
  });
});
