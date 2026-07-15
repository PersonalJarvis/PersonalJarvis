import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMissionsStore } from "./store";
import { useMissionWebSocket } from "./useMissionWebSocket";

const socket = vi.hoisted(() => ({
  sendJsonMessage: vi.fn(),
  urlFactory: null as null | (() => Promise<string>),
  options: null as null | { onOpen?: () => void },
}));

vi.mock("react-use-websocket", () => ({
  default: vi.fn((urlFactory, options) => {
    socket.urlFactory = urlFactory;
    socket.options = options;
    return {
      sendJsonMessage: socket.sendJsonMessage,
      lastJsonMessage: null,
      readyState: 1,
    };
  }),
  ReadyState: {
    UNINSTANTIATED: -1,
    CONNECTING: 0,
    OPEN: 1,
    CLOSING: 2,
    CLOSED: 3,
  },
}));

describe("useMissionWebSocket", () => {
  beforeEach(() => {
    socket.sendJsonMessage.mockReset();
    socket.urlFactory = null;
    socket.options = null;
    useMissionsStore.getState().reset();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("keeps the token out of the URL and sends it only in hello", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ token: "mission-secret" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    useMissionsStore.setState({ lastSeq: 42 });

    renderHook(() => useMissionWebSocket());
    let url = "";
    await act(async () => {
      url = await socket.urlFactory!();
    });

    expect(url).toBe("ws://localhost:3000/api/missions/ws");
    expect(url).not.toContain("mission-secret");
    act(() => socket.options?.onOpen?.());
    expect(socket.sendJsonMessage).toHaveBeenCalledWith({
      type: "hello",
      last_seq: 42,
      token: "mission-secret",
    });
  });
});
