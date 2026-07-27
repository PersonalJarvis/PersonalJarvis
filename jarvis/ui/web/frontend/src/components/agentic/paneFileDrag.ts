/**
 * When a terminal pane offers itself as a file target — and, the harder half,
 * when it stops.
 *
 * Arming a drop zone looks like three lines of `dragenter`/`dragleave` until it
 * meets a real desktop, where both halves lie:
 *
 * * **`dragenter` fires for drags that carry nothing droppable.** Selected text
 *   lifted by a stray mouse-drag is still a drag, and a pane that arms on it
 *   tells a user holding nothing to "drop the file here" (BUG-110).
 * * **`dragleave` is not guaranteed to arrive.** Release the drag over another
 *   element, drop it outside the window, or cancel with Escape and the pane
 *   that armed never hears the end of it — the overlay then sits over a live
 *   agent until the next drag happens to balance the books.
 *
 * So the arming is gated on the payload, and the disarming has a window-level
 * backstop that does not depend on a matching leave ever being delivered.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
} from "react";
import { dragCarriesFiles } from "./paneDrop";

export interface PaneFileDragHandlers {
  onDragEnter: (e: ReactDragEvent) => void;
  onDragOver: (e: ReactDragEvent) => void;
  onDragLeave: (e: ReactDragEvent) => void;
  onDrop: (e: ReactDragEvent) => void;
}

export interface PaneFileDrag {
  /** True while a file drag is over this pane — drives the drop overlay. */
  dragging: boolean;
  /** Spread onto the pane's root element. */
  handlers: PaneFileDragHandlers;
}

/**
 * Track a file drag over one pane.
 *
 * `onFiles` runs on a drop that actually carried files; it is handed the live
 * `DataTransfer` and must read it SYNCHRONOUSLY (see ./paneDrop).
 */
export function usePaneFileDrag(
  onFiles: (dt: DataTransfer) => void,
): PaneFileDrag {
  const [dragging, setDragging] = useState(false);
  // Counted, not a boolean: a drag moving across the pane fires enter/leave for
  // every child element it crosses, and a boolean flickers.
  const depth = useRef(0);

  const disarm = useCallback(() => {
    depth.current = 0;
    setDragging(false);
  }, []);

  // The backstop. A drag that ends anywhere other than on this pane owes it no
  // `dragleave`, so the end of the drag is watched globally instead — and only
  // while armed, which is the only time there is anything to take down.
  useEffect(() => {
    if (!dragging) return;
    const onWindowDragLeave = (e: DragEvent) => {
      // `relatedTarget === null` plus a cursor at the viewport edge means the
      // drag left the WINDOW, not merely moved between elements inside it.
      if (
        e.relatedTarget === null &&
        (e.clientX <= 0 ||
          e.clientY <= 0 ||
          e.clientX >= window.innerWidth ||
          e.clientY >= window.innerHeight)
      ) {
        disarm();
      }
    };
    window.addEventListener("drop", disarm);
    window.addEventListener("dragend", disarm);
    window.addEventListener("dragleave", onWindowDragLeave);
    return () => {
      window.removeEventListener("drop", disarm);
      window.removeEventListener("dragend", disarm);
      window.removeEventListener("dragleave", onWindowDragLeave);
    };
  }, [dragging, disarm]);

  const handlers: PaneFileDragHandlers = {
    onDragEnter: (e) => {
      // Claimed even for payloads this pane will not take: the default action
      // for a dropped link or text is to NAVIGATE, which would replace the
      // whole IDE — every agent in the grid with it.
      e.preventDefault();
      if (!dragCarriesFiles(e.dataTransfer)) return;
      depth.current += 1;
      setDragging(true);
    },
    onDragOver: (e) => {
      e.preventDefault();
      // An honest cursor: "copy" only where a drop will actually do something.
      e.dataTransfer.dropEffect = dragCarriesFiles(e.dataTransfer)
        ? "copy"
        : "none";
    },
    onDragLeave: () => {
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    onDrop: (e) => {
      e.preventDefault();
      disarm();
      if (!dragCarriesFiles(e.dataTransfer)) return;
      onFiles(e.dataTransfer);
    },
  };

  return { dragging, handlers };
}
