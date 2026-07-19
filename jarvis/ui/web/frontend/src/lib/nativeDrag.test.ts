import { beforeEach, describe, expect, it, vi } from "vitest";

import { canNativeDrag, startNativeFileDrag } from "./nativeDrag";

interface MessageBridge {
  postMessage: (message: unknown) => void;
}

type NativeHostWindow = Window & {
  chrome?: { webview?: MessageBridge };
  webkit?: { messageHandlers?: Record<string, MessageBridge | undefined> };
};

const host = window as NativeHostWindow;

describe("native file drag bridge", () => {
  beforeEach(() => {
    delete host.chrome;
    delete host.webkit;
  });

  it("stays disabled in a plain browser", () => {
    expect(canNativeDrag()).toBe(false);
    expect(startNativeFileDrag("/tmp/session.txt")).toBe(false);
  });

  it("posts a real-file request through WebView2 on Windows", () => {
    const postMessage = vi.fn();
    host.chrome = { webview: { postMessage } };

    expect(canNativeDrag()).toBe(true);
    expect(startNativeFileDrag("C:\\Users\\Nova\\Downloads\\session.txt")).toBe(
      true,
    );
    expect(postMessage).toHaveBeenCalledWith([
      "jarvis-file-drag",
      { files: ["C:\\Users\\Nova\\Downloads\\session.txt"] },
    ]);
  });

  it("posts the same request through WKWebView on macOS", () => {
    const postMessage = vi.fn();
    host.webkit = {
      messageHandlers: { jarvisFileDrag: { postMessage } },
    };

    expect(canNativeDrag()).toBe(true);
    expect(
      startNativeFileDrag("/Users/nova/Downloads/voice-session.txt"),
    ).toBe(true);
    expect(postMessage).toHaveBeenCalledWith([
      "jarvis-file-drag",
      { files: ["/Users/nova/Downloads/voice-session.txt"] },
    ]);
  });

  it("does not treat an unrelated WebKit host as the desktop bridge", () => {
    host.webkit = { messageHandlers: {} };

    expect(canNativeDrag()).toBe(false);
    expect(startNativeFileDrag("/tmp/session.txt")).toBe(false);
  });

  it("rejects an empty file path without posting", () => {
    const postMessage = vi.fn();
    host.webkit = {
      messageHandlers: { jarvisFileDrag: { postMessage } },
    };

    expect(startNativeFileDrag("")).toBe(false);
    expect(postMessage).not.toHaveBeenCalled();
  });
});
