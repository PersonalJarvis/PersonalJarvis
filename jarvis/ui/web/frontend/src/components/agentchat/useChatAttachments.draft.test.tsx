import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { discardHeldFiles, forgetHeldFiles, moveHeldFiles, restoreHeldFiles, useChatAttachments } from "@/components/agentchat/useChatAttachments";

/**
 * Files held for an unsent message survive the composer unmounting, the way
 * its typed text does. Switching app sections unmounts the composer; the
 * reported bug was the text coming back without its pictures.
 */

const SHOT = {
  name: "shot.png",
  reference: '".jarvis/drops/shot.png"',
  kind: "image" as const,
  detail: "",
  described_by: "vision" as const,
  note: "",
};

const TARGET = { sessionId: null, cwd: "C:\\work", provider: "claude-api", surface: "agent" as const };
const owner = {};

function png() {
  return new File([new Uint8Array([1])], "shot.png", { type: "image/png" });
}

describe("held files across unmounts", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ attachments: [SHOT], cwd: "C:\\work" }),
    }) as Response));
    URL.createObjectURL = vi.fn(() => "blob:shot");
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    cleanup();
    forgetHeldFiles(owner);
    vi.unstubAllGlobals();
  });

  it("gives a draft its files and pictures back after a remount", async () => {
    const first = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "draft:a" }));
    await act(async () => first.result.current.attachFiles([png()]));
    await waitFor(() => expect(first.result.current.attachments).toEqual([SHOT]));
    first.unmount();

    // The picture is still alive: a kept draft frees it on send, not on unmount.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    const second = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "draft:a" }));
    expect(second.result.current.attachments).toEqual([SHOT]);
    expect(second.result.current.previews).toEqual({ "shot.png": "blob:shot" });
  });

  it("keeps another draft's files out", async () => {
    const first = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "draft:a" }));
    await act(async () => first.result.current.attachFiles([png()]));
    await waitFor(() => expect(first.result.current.attachments).toHaveLength(1));

    const other = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "session-2" }));
    expect(other.result.current.attachments).toEqual([]);
    const stranger = renderHook(() => useChatAttachments(TARGET, () => {}, { owner: {}, key: "draft:a" }));
    expect(stranger.result.current.attachments).toEqual([]);
  });

  it("frees the pictures once the files are cleared for sending", async () => {
    const { result } = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "draft:a" }));
    await act(async () => result.current.attachFiles([png()]));
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));

    act(() => result.current.clear());
    expect(result.current.attachments).toEqual([]);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:shot");
  });

  it("lets files without a draft go with the composer", async () => {
    const first = renderHook(() => useChatAttachments(TARGET, () => {}));
    await act(async () => first.result.current.attachFiles([png()]));
    await waitFor(() => expect(first.result.current.attachments).toHaveLength(1));
    first.unmount();

    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:shot");
    const second = renderHook(() => useChatAttachments(TARGET, () => {}));
    expect(second.result.current.attachments).toEqual([]);
  });

  it("moves files to an assigned session and restores a failed submission there", async () => {
    const blank = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "" }));
    await act(async () => blank.result.current.attachFiles([png()]));
    await waitFor(() => expect(blank.result.current.attachments).toHaveLength(1));
    act(() => moveHeldFiles(owner, "", "created"));
    const assigned = renderHook(() => useChatAttachments({ ...TARGET, sessionId: "created" }, () => {}, { owner, key: "created" }));
    expect(blank.result.current.attachments).toEqual([]);
    expect(assigned.result.current.attachments).toEqual([SHOT]);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    let sent!: ReturnType<typeof assigned.result.current.take>;
    act(() => { sent = assigned.result.current.take(); });
    expect(assigned.result.current.attachments).toEqual([]);
    act(() => restoreHeldFiles(owner, "created", sent));
    expect(assigned.result.current.attachments).toEqual([SHOT]);
    expect(assigned.result.current.previews).toEqual({ "shot.png": "blob:shot" });
  });

  it("routes an upload completing after session creation to the assigned draft", async () => {
    let uploaded!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { uploaded = resolve; })));
    const blank = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "" }));
    act(() => blank.result.current.attachFiles([png()]));
    act(() => moveHeldFiles(owner, "", "created"));
    const assigned = renderHook(() => useChatAttachments({ ...TARGET, sessionId: "created" }, () => {}, { owner, key: "created" }));
    await act(async () => uploaded(new Response(JSON.stringify({ attachments: [SHOT], cwd: "C:\\work" }))));
    await waitFor(() => expect(assigned.result.current.attachments).toEqual([SHOT]));
    expect(blank.result.current.attachments).toEqual([]);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    const freshBlank = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "" }));
    expect(freshBlank.result.current.attachments).toEqual([]);
  });

  it("keeps an abandoned upload out of an explicitly new blank chat", async () => {
    let uploaded!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { uploaded = resolve; })));
    const blank = renderHook(() => useChatAttachments(TARGET, () => {}, { owner, key: "" }));
    act(() => blank.result.current.attachFiles([png()]));
    act(() => discardHeldFiles(owner, ""));
    await act(async () => uploaded(new Response(JSON.stringify({ attachments: [SHOT], cwd: "" }))));
    expect(blank.result.current.attachments).toEqual([]);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:shot");
    // The same mounted composer can still attach to the newly requested chat.
    act(() => blank.result.current.attachFiles([png()]));
    await act(async () => uploaded(new Response(JSON.stringify({ attachments: [SHOT], cwd: "" }))));
    await waitFor(() => expect(blank.result.current.attachments).toEqual([SHOT]));
  });
});
