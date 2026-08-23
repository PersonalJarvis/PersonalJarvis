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
 *   agent, and the counter below is left non-zero, so no LATER drag can bring
 *   it down either: that pane stays armed for the rest of the session and each
 *   further drag strands one more (BUG-167).
 *
 * So the arming is gated on the payload, and the disarming does not depend on
 * any particular event arriving — see ./dragSessionEnd.
 */
import {
  useCallback,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
} from "react";
import { dragCarriesFiles } from "./paneDrop";
import { useDragSessionEnd } from "./dragSessionEnd";

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

  // The backstop, and the only thing this pane can actually rely on: a drag
  // that ends anywhere other than on this pane owes it no `dragleave`, and a
  // drag out of Explorer owes the page no `dragend` either. Watched while
  // armed, which is the only time there is anything to take down.
  useDragSessionEnd(dragging, disarm);

  const handlers: PaneFileDragHandlers = {
    onDragEnter: (e) => {
      // Claimed even for payloads this pane will not take: the default action
      // for a dropped link or text is to NAVIGATE, which would replace the
      // whole IDE — every agent in the grid with it.
      e.preventDefault();
      if (!dragCarriesFiles(e.dataTransfer)) return;
      // No reset needed here, and deliberately not attempted: `dragging` has
      // not flipped yet inside the batch that crossed into a child, so a reset
      // read off it would zero the count mid-drag. What keeps the counter from
      // outliving a drag is that `disarm` zeroes it and now always runs — a
      // count that could only ever be wrong upwards is what made BUG-167
      // permanent rather than momentary.
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
