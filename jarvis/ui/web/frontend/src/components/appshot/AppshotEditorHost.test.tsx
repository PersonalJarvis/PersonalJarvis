import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppshotEditorHost } from "@/components/appshot/AppshotEditorHost";
import { handleAppshotEditRequest, useAppshotEditor } from "@/store/appshotEditor";
import { useEventStore } from "@/store/events";

/**
 * The appshot editor's lifecycle: a click on the appshot card (the
 * `AppshotEditRequested` event) opens it over whatever is on screen — it
 * never navigates — and closing it, with or without edits, leaves the app
 * where it was. The reload that used to wipe it lived in the desktop shell
 * (tests/unit/ui/test_desktop_show_window.py).
 */

class LoadedImage {
  naturalWidth = 400;
  naturalHeight = 300;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  set src(_value: string) {
    queueMicrotask(() => this.onload?.());
  }
}

class BrokenImage extends LoadedImage {
  set src(_value: string) {
    queueMicrotask(() => this.onerror?.());
  }
}

// jsdom has no PointerEvent; without one the coordinates of a fired pointer
// event are lost and every stroke looks like a stray click.
class TestPointerEvent extends MouseEvent {
  pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
  }
}

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

describe("AppshotEditorHost", () => {
  beforeEach(() => {
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis", activeSection: "chats" });
    useAppshotEditor.setState({ openId: null, revision: 0 });
    vi.stubGlobal("Image", LoadedImage);
    vi.stubGlobal("PointerEvent", TestPointerEvent);
    vi.stubGlobal("fetch", vi.fn(async () => json({ native_file_actions: false, platform: "linux" })));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("opens over the current view when the card asks, without navigating", async () => {
    render(<AppshotEditorHost />);
    expect(screen.queryByTestId("appshot-editor")).toBeNull();

    act(() => {
      expect(handleAppshotEditRequest({ appshot_id: "shot-1" }, { solo: false })).toBe("opened");
    });

    expect(await screen.findByTestId("appshot-editor")).toBeDefined();
    expect(useEventStore.getState().activeSection).toBe("chats");
    await waitFor(() => expect(screen.getByTestId("appshot-editor-canvas")).toBeDefined());
  });

  it("closes on Escape when nothing was edited", async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    await screen.findByTestId("appshot-editor-canvas");

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(screen.queryByTestId("appshot-editor")).toBeNull());
    expect(useAppshotEditor.getState().openId).toBeNull();
  });

  it("switches tools with their one-letter shortcuts", async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    await screen.findByTestId("appshot-editor-canvas");

    fireEvent.keyDown(window, { key: "r" });
    expect(screen.getByTestId("appshot-editor-tool-rect").getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(window, { key: "k" });
    expect(screen.getByTestId("appshot-editor-tool-crop").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("appshot-editor-crop-ratio")).toBeDefined();
    fireEvent.keyDown(window, { key: "p" });
    expect(screen.getByTestId("appshot-editor-redact-mode")).toBeDefined();
  });

  it("asks before throwing edits away, and undo/redo follow the drawing", async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    const canvas = await screen.findByTestId("appshot-editor-canvas");

    fireEvent.pointerDown(canvas, { button: 0, clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 120, clientY: 90, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 120, clientY: 90, pointerId: 1 });

    const undoButton = screen.getByTestId("appshot-editor-undo") as HTMLButtonElement;
    await waitFor(() => expect(undoButton.disabled).toBe(false));

    // A fresh annotation is selected: the first Escape lets go of it, the
    // next one asks before throwing the edits away.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("appshot-editor-discard")).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(await screen.findByTestId("appshot-editor-discard")).toBeDefined();
    // Escape again keeps editing.
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("appshot-editor-discard")).toBeNull());
    expect(screen.getByTestId("appshot-editor")).toBeDefined();

    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await waitFor(() => expect(undoButton.disabled).toBe(true));
    expect((screen.getByTestId("appshot-editor-redo") as HTMLButtonElement).disabled).toBe(false);
    fireEvent.keyDown(window, { key: "z", ctrlKey: true, shiftKey: true });
    await waitFor(() => expect(undoButton.disabled).toBe(false));

    fireEvent.click(screen.getByTestId("appshot-editor-close"));
    fireEvent.click(await screen.findByTestId("appshot-editor-discard-confirm"));
    await waitFor(() => expect(useAppshotEditor.getState().openId).toBeNull());
  });

  it("selects, nudges and deletes an annotation with the move tool", async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    const canvas = await screen.findByTestId("appshot-editor-canvas");

    fireEvent.keyDown(window, { key: "f" });
    fireEvent.pointerDown(canvas, { button: 0, clientX: 20, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 80, clientY: 80, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 80, clientY: 80, pointerId: 1 });

    fireEvent.keyDown(window, { key: "v" });
    fireEvent.pointerDown(canvas, { button: 0, clientX: 50, clientY: 50, pointerId: 2 });
    fireEvent.pointerUp(canvas, { clientX: 50, clientY: 50, pointerId: 2 });
    expect(await screen.findByTestId("appshot-editor-delete")).toBeDefined();

    fireEvent.keyDown(window, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(window, { key: "Delete" });
    await waitFor(() => expect(screen.queryByTestId("appshot-editor-delete")).toBeNull());
    // Drawing, nudging and removing are three undoable steps.
    for (let i = 0; i < 2; i += 1) {
      fireEvent.keyDown(window, { key: "z", ctrlKey: true });
      expect((screen.getByTestId("appshot-editor-undo") as HTMLButtonElement).disabled).toBe(false);
    }
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await waitFor(() =>
      expect((screen.getByTestId("appshot-editor-undo") as HTMLButtonElement).disabled).toBe(true),
    );
  });

  it("opens the colour menu and zooms from the bottom bar", async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    await screen.findByTestId("appshot-editor-canvas");

    fireEvent.click(screen.getByTestId("appshot-editor-style"));
    expect(screen.getByTestId("appshot-editor-style-menu")).toBeDefined();
    // Escape closes the menu first, not the editor.
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("appshot-editor-style-menu")).toBeNull());
    expect(screen.getByTestId("appshot-editor")).toBeDefined();

    fireEvent.click(screen.getByTestId("appshot-editor-zoom"));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "100%" }));
    expect(screen.getByTestId("appshot-editor-zoom").textContent).toContain("100%");

    fireEvent.keyDown(window, { key: "5" });
    expect((screen.getByTestId("appshot-editor-size") as HTMLInputElement).value).toBe("4");
  });

  it("takes any annotation by the hand: drag moves it, a grip reshapes it", async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    const canvas = await screen.findByTestId("appshot-editor-canvas");
    const undo = () => screen.getByTestId("appshot-editor-undo") as HTMLButtonElement;
    const steps = () => {
      let n = 0;
      while (!undo().disabled && n < 10) {
        fireEvent.keyDown(window, { key: "z", ctrlKey: true });
        n += 1;
      }
      return n;
    };

    // Draw an arrow with the arrow tool (the default)...
    fireEvent.pointerDown(canvas, { button: 0, clientX: 20, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 200, clientY: 120, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 200, clientY: 120, pointerId: 1 });
    // ...then, with the same tool, drag its tip grip somewhere else...
    fireEvent.pointerDown(canvas, { button: 0, clientX: 200, clientY: 120, pointerId: 2 });
    fireEvent.pointerMove(canvas, { clientX: 300, clientY: 60, pointerId: 2 });
    fireEvent.pointerUp(canvas, { clientX: 300, clientY: 60, pointerId: 2 });
    // ...and the arrow itself, from its middle.
    fireEvent.pointerDown(canvas, { button: 0, clientX: 160, clientY: 40, pointerId: 3 });
    fireEvent.pointerMove(canvas, { clientX: 180, clientY: 90, pointerId: 3 });
    fireEvent.pointerUp(canvas, { clientX: 180, clientY: 90, pointerId: 3 });

    // Drawing, reshaping and moving are three steps — never a second arrow.
    await waitFor(() => expect(undo().disabled).toBe(false));
    expect(steps()).toBe(3);
  });

  it("creates further pen strokes and counters away from selected grips and shapes", async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    const canvas = await screen.findByTestId("appshot-editor-canvas");
    const undo = () => screen.getByTestId("appshot-editor-undo") as HTMLButtonElement;
    const stroke = (id: number, from: [number, number], to: [number, number]) => {
      fireEvent.pointerDown(canvas, { button: 0, clientX: from[0], clientY: from[1], pointerId: id });
      fireEvent.pointerMove(canvas, { clientX: to[0], clientY: to[1], pointerId: id });
      fireEvent.pointerUp(canvas, { clientX: to[0], clientY: to[1], pointerId: id });
    };

    // Two freehand strokes away from the selected annotation's grips.
    fireEvent.keyDown(window, { key: "d" });
    stroke(1, [20, 20], [120, 20]);
    stroke(2, [20, 120], [120, 120]);
    // A counter is movable immediately; place the next one outside its hit area.
    fireEvent.keyDown(window, { key: "c" });
    stroke(3, [60, 60], [60, 60]);
    stroke(4, [160, 220], [160, 220]);

    // Four new marks; nothing was moved or reshaped instead.
    await waitFor(() => expect(undo().disabled).toBe(false));
    let n = 0;
    while (!undo().disabled && n < 10) {
      fireEvent.keyDown(window, { key: "z", ctrlKey: true });
      n += 1;
    }
    expect(n).toBe(4);
  });

  it("says when the appshot is gone instead of showing an empty editor", async () => {
    vi.stubGlobal("Image", BrokenImage);
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("gone-1"));

    expect(await screen.findByTestId("appshot-editor-failed")).toBeDefined();
  });

  it("crops with a frame whose grips stay adjustable until Enter", async () => {
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    const canvas = await screen.findByTestId("appshot-editor-canvas");
    const size = () => screen.getByTestId("appshot-editor-crop-size").textContent;
    const drag = (id: number, from: [number, number], to: [number, number]) => {
      fireEvent.pointerDown(canvas, { button: 0, clientX: from[0], clientY: from[1], pointerId: id });
      fireEvent.pointerMove(canvas, { clientX: to[0], clientY: to[1], pointerId: id });
      fireEvent.pointerUp(canvas, { clientX: to[0], clientY: to[1], pointerId: id });
    };

    fireEvent.keyDown(window, { key: "k" });
    expect(size()).toBe("400 × 300");
    drag(1, [50, 50], [250, 200]);
    await waitFor(() => expect(size()).toBe("200 × 150"));
    // Still cropping: the right edge's grip widens the frame.
    drag(2, [250, 125], [300, 125]);
    await waitFor(() => expect(size()).toBe("250 × 150"));

    fireEvent.keyDown(window, { key: "Enter" });
    expect(screen.getByTestId("appshot-editor-tool-move").getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByTestId("appshot-editor")).not.toBeNull();

    fireEvent.keyDown(window, { key: "k" });
    fireEvent.click(screen.getByTestId("appshot-editor-crop-reset"));
    await waitFor(() => expect(size()).toBe("400 × 300"));
  });

  it("adds an earlier appshot beside the picture", async () => {
    const item = {
      id: "abc123",
      variant: "original",
      path: "/kept/appshot.png",
      mime: "image/png",
      width: 400,
      height: 300,
      label: "",
      app_name: "Code",
      trigger: "hotkey",
      taken_at: 1,
      edited_at: 0,
      has_edit: false,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).startsWith("/api/appshot/library")
          ? json({ items: [item], max_entries: 500 })
          : json({ native_file_actions: false, platform: "linux" }),
      ),
    );
    render(<AppshotEditorHost />);
    act(() => useAppshotEditor.getState().open("shot-1"));
    await screen.findByTestId("appshot-editor-canvas");

    fireEvent.keyDown(window, { key: "i" });
    expect(screen.getByTestId("appshot-editor-place-beside").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click((await screen.findAllByTestId("appshot-editor-import-item"))[0]);

    // The picture joins at the same height and is selected to be moved or sized.
    await waitFor(() => expect(screen.queryByTestId("appshot-editor-import-panel")).toBeNull());
    expect(screen.getByTestId("appshot-editor-tool-move").getAttribute("aria-pressed")).toBe("true");
    expect((screen.getByTestId("appshot-editor-undo") as HTMLButtonElement).disabled).toBe(false);
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "k" });
    expect(screen.getByTestId("appshot-editor-crop-size").textContent).toBe("800 × 300");
  });
});

describe("handleAppshotEditRequest", () => {
  beforeEach(() => useAppshotEditor.setState({ openId: null, revision: 0 }));

  it("opens the editor on the requested appshot", () => {
    expect(handleAppshotEditRequest({ appshot_id: "a1" }, { solo: false })).toBe("opened");
    expect(useAppshotEditor.getState().openId).toBe("a1");
  });

  it("leaves a detached window alone", () => {
    expect(handleAppshotEditRequest({ appshot_id: "a1" }, { solo: true })).toBe("ignored");
    expect(useAppshotEditor.getState().openId).toBeNull();
  });

  it("reports an appshot that is no longer kept", () => {
    expect(handleAppshotEditRequest({ appshot_id: "" }, { solo: false })).toBe("gone");
    expect(handleAppshotEditRequest(null, { solo: false })).toBe("gone");
    expect(useAppshotEditor.getState().openId).toBeNull();
  });
});
