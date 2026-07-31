import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DragEvent as ReactDragEvent } from "react";
import { usePaneFileDrag } from "./paneFileDrag";

/** A drag event as React hands it to a handler. */
function dragEvent(types: string[]) {
  const dataTransfer = { types, dropEffect: "" } as unknown as DataTransfer;
  return {
    dataTransfer,
    preventDefault: vi.fn(),
  } as unknown as ReactDragEvent & {
    dataTransfer: DataTransfer;
    preventDefault: ReturnType<typeof vi.fn>;
  };
}

describe("arming a terminal pane for a file drop", () => {
  it("arms for a file drag", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));

    act(() => result.current.handlers.onDragEnter(dragEvent(["Files"])));

    expect(result.current.dragging).toBe(true);
  });

  it("stays quiet for dragged text — the user is holding no file", () => {
    // BUG-110: the pane offered itself to a stray text selection drag.
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));

    act(() => result.current.handlers.onDragEnter(dragEvent(["text/plain"])));

    expect(result.current.dragging).toBe(false);
  });

  it("claims even a text drag so a dropped link cannot navigate the IDE away", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));
    const e = dragEvent(["text/plain"]);

    act(() => result.current.handlers.onDragEnter(e));

    expect(e.preventDefault).toHaveBeenCalled();
  });

  it("shows a copy cursor only where a drop would do something", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));
    const withFile = dragEvent(["Files"]);
    const withText = dragEvent(["text/plain"]);

    act(() => {
      result.current.handlers.onDragOver(withFile);
      result.current.handlers.onDragOver(withText);
    });

    expect(withFile.dataTransfer.dropEffect).toBe("copy");
    expect(withText.dataTransfer.dropEffect).toBe("none");
  });

  it("stays armed while the drag crosses child elements", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));

    act(() => {
      result.current.handlers.onDragEnter(dragEvent(["Files"]));
      result.current.handlers.onDragEnter(dragEvent(["Files"])); // onto a child
      result.current.handlers.onDragLeave(dragEvent(["Files"])); // off the parent
    });

    expect(result.current.dragging).toBe(true);

    act(() => result.current.handlers.onDragLeave(dragEvent(["Files"])));

    expect(result.current.dragging).toBe(false);
  });

  it("hands a real drop to the pane", () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => usePaneFileDrag(onFiles));
    const e = dragEvent(["Files"]);

    act(() => {
      result.current.handlers.onDragEnter(dragEvent(["Files"]));
      result.current.handlers.onDrop(e);
    });

    expect(onFiles).toHaveBeenCalledWith(e.dataTransfer);
    expect(result.current.dragging).toBe(false);
  });

  it("does not treat a dropped text selection as an attach", () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => usePaneFileDrag(onFiles));

    act(() => result.current.handlers.onDrop(dragEvent(["text/plain"])));

    expect(onFiles).not.toHaveBeenCalled();
  });

  it("disarms when the drag ends somewhere else in the window", () => {
    // No `dragleave` is owed to this pane when the drop lands elsewhere, so
    // without the window backstop the overlay would sit over a live agent.
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));
    act(() => result.current.handlers.onDragEnter(dragEvent(["Files"])));

    act(() => {
      window.dispatchEvent(new Event("drop"));
    });

    expect(result.current.dragging).toBe(false);
  });

  it("disarms when the drag is cancelled", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));
    act(() => result.current.handlers.onDragEnter(dragEvent(["Files"])));

    act(() => {
      window.dispatchEvent(new Event("dragend"));
    });

    expect(result.current.dragging).toBe(false);
  });

  it("disarms when the drag leaves the window entirely", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));
    act(() => result.current.handlers.onDragEnter(dragEvent(["Files"])));

    act(() => {
      // relatedTarget null + a cursor at the viewport edge = gone from the window.
      const leave = new MouseEvent("dragleave", {
        clientX: 0,
        clientY: 12,
        relatedTarget: null,
      });
      window.dispatchEvent(leave);
    });

    expect(result.current.dragging).toBe(false);
  });

  it("keeps the overlay up while the drag merely moves inside the window", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));
    act(() => result.current.handlers.onDragEnter(dragEvent(["Files"])));

    act(() => {
      const leave = new MouseEvent("dragleave", {
        clientX: 200,
        clientY: 200,
        relatedTarget: document.body,
      });
      window.dispatchEvent(leave);
    });

    expect(result.current.dragging).toBe(true);
  });
});
