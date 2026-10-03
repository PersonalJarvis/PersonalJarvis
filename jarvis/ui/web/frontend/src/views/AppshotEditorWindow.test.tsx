import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";
import {
  AppshotEditorWindow,
  appshotIdFromUrl,
  isAppshotEditorWindow,
  type EditorWindowDeps,
} from "@/views/AppshotEditorWindow";

/**
 * The editor's own desktop window: opened by a click on the corner card, it
 * shows only the editor, and when it closes (Done or Close) the appshot goes
 * back into the screen corner BEFORE the window closes itself.
 */

class LoadedImage {
  naturalWidth = 400;
  naturalHeight = 300;
  onload: (() => void) | null = null;
  set src(_value: string) {
    queueMicrotask(() => this.onload?.());
  }
}

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

describe("the appshot editor window", () => {
  beforeEach(() => {
    useEventStore.setState({ events: [], toasts: [], assistantName: "George" });
    vi.stubGlobal("Image", LoadedImage);
    vi.stubGlobal("fetch", vi.fn(async () => json({ native_file_actions: false, platform: "linux" })));
    window.history.replaceState(null, "", "/?view=appshot-editor&solo=1&appshot=a1b2c3d4");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("knows it is the editor window and which appshot it edits", () => {
    expect(isAppshotEditorWindow("?view=appshot-editor&solo=1&appshot=a1b2c3d4")).toBe(true);
    expect(isAppshotEditorWindow("?view=chats")).toBe(false);
    expect(appshotIdFromUrl("?appshot=a1b2c3d4")).toBe("a1b2c3d4");
    expect(appshotIdFromUrl("?appshot=../../etc")).toBe("");
  });

  it("sends the appshot back to the corner, then closes itself", async () => {
    const order: string[] = [];
    const deps: EditorWindowDeps = {
      returnCard: async () => {
        order.push("card");
      },
      closeWindow: async () => {
        order.push("close");
      },
    };
    render(<AppshotEditorWindow deps={deps} />);
    await screen.findByTestId("appshot-editor-canvas");
    expect(screen.getByTestId("appshot-editor").getAttribute("data-variant")).toBe("window");

    fireEvent.click(screen.getByTestId("appshot-editor-close"));

    await waitFor(() => expect(order).toEqual(["card", "close"]));
  });

  it("after Save the edited picture becomes the appshot and flies back to the corner", async () => {
    const proxy: ProxyHandler<object> = {
      get: (_t, key) =>
        key === "measureText" ? () => ({ width: 10 }) : new Proxy(() => undefined, { ...proxy, apply: () => new Proxy({}, proxy) }),
      set: () => true,
    };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => new Proxy({}, proxy) as never);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((done: BlobCallback) =>
      done(new Blob(["png"], { type: "image/png" })),
    );
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${url}`);
        return json({ native_file_actions: false, platform: "linux" });
      }),
    );
    const flights: unknown[] = [];
    const ids: string[] = [];
    const closeWindow = vi.fn(async () => undefined);
    render(
      <AppshotEditorWindow
        deps={{
          returnCard: async (id, flyFrom) => {
            ids.push(id);
            flights.push(flyFrom);
          },
          closeWindow,
        }}
      />,
    );
    await screen.findByTestId("appshot-editor-canvas");

    fireEvent.click(screen.getByTestId("appshot-editor-save"));

    await waitFor(() => expect(closeWindow).toHaveBeenCalledTimes(1));
    expect(calls).toContain("PUT /api/appshot/latest/image?id=a1b2c3d4");
    expect(flights).toHaveLength(1);
    expect(ids).toEqual(["a1b2c3d4"]);
    const from = flights[0] as number[];
    expect(Array.isArray(from) && from.length === 4).toBe(true);
    vi.restoreAllMocks();
  });

  it("waits hidden without an appshot and opens one in place when pointed at it", async () => {
    window.history.replaceState(null, "", "/?view=appshot-editor&solo=1");
    const closeWindow = vi.fn(async () => undefined);
    render(<AppshotEditorWindow deps={{ returnCard: async () => undefined, closeWindow }} />);
    expect(screen.queryByTestId("appshot-editor")).toBeNull();

    await waitFor(() => expect(typeof window.__jarvisOpenAppshot).toBe("function"));
    expect(window.__jarvisOpenAppshot!("../../etc")).toBe(false);
    act(() => {
      expect(window.__jarvisOpenAppshot!("a1b2c3d4")).toBe(true);
    });
    await screen.findByTestId("appshot-editor-canvas");

    fireEvent.click(screen.getByTestId("appshot-editor-close"));
    await waitFor(() => expect(closeWindow).toHaveBeenCalledTimes(1));
    // Hidden again with nothing in it, ready for the next appshot.
    await waitFor(() => expect(screen.queryByTestId("appshot-editor")).toBeNull());
  });

  it("still closes when the card cannot come back", async () => {
    const closeWindow = vi.fn(async () => undefined);
    render(<AppshotEditorWindow deps={{ returnCard: () => Promise.reject(new Error("no overlay")), closeWindow }} />);
    await screen.findByTestId("appshot-editor-canvas");

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(closeWindow).toHaveBeenCalledTimes(1));
  });
});
