import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppshotEditorHost } from "@/components/appshot/AppshotEditorHost";
import { useAppshotEditor } from "@/store/appshotEditor";
import { useEventStore } from "@/store/events";

/**
 * The "Drag me" handle: pressing it writes the finished picture once
 * (POST /api/appshot/drag-file) and hands that path to the native drag bridge
 * while the button is still held. Its own file, because the desktop
 * capability is cached per module and the other editor tests run as a browser.
 */

class LoadedImage {
  naturalWidth = 400;
  naturalHeight = 300;
  onload: (() => void) | null = null;
  set src(_value: string) {
    queueMicrotask(() => this.onload?.());
  }
}

class TestPointerEvent extends MouseEvent {
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
  }
}

/** A 2D context that accepts every call, so the export can run in jsdom. */
function fakeContext(): unknown {
  const handler: ProxyHandler<object> = {
    get: (_target, key) => {
      if (key === "measureText") return () => ({ width: 10 });
      return new Proxy(() => undefined, { ...handler, apply: () => new Proxy({}, handler) });
    },
    set: () => true,
  };
  return new Proxy({}, handler);
}

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

describe("the editor's drag handle", () => {
  const posted: unknown[] = [];

  beforeEach(() => {
    posted.length = 0;
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis" });
    useAppshotEditor.setState({ openId: null, revision: 0 });
    vi.stubGlobal("Image", LoadedImage);
    vi.stubGlobal("PointerEvent", TestPointerEvent);
    vi.stubGlobal("chrome", { webview: { postMessage: (message: unknown) => posted.push(message) } });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => fakeContext() as never);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((done: BlobCallback) =>
      done(new Blob(["png"], { type: "image/png" })),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/downloads/capabilities") return json({ native_file_actions: true, platform: "win32" });
        if (url === "/api/appshot/drag-file") return json({ path: "C:/tmp/jarvis-appshots/appshot-1.png" });
        return json({}, 404);
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("hands a written file to the native drag while the handle is held", { timeout: 10_000 }, async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    // The capability check waits out the boot stagger (4 s) first.
    const handle = await screen.findByTestId("appshot-editor-drag", undefined, { timeout: 6000 });

    fireEvent.pointerDown(handle, { button: 0, pointerId: 3 });

    await waitFor(() =>
      expect(posted).toEqual([["jarvis-file-drag", { files: ["C:/tmp/jarvis-appshots/appshot-1.png"] }]]),
    );
  });

  it("does not start a drag the user already let go of", { timeout: 10_000 }, async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    // The capability check waits out the boot stagger (4 s) first.
    const handle = await screen.findByTestId("appshot-editor-drag", undefined, { timeout: 6000 });

    fireEvent.pointerDown(handle, { button: 0, pointerId: 3 });
    fireEvent.pointerUp(window, { pointerId: 3 });

    await waitFor(() => expect(useEventStore.getState().toasts.length).toBe(1));
    expect(posted).toEqual([]);
  });
});
