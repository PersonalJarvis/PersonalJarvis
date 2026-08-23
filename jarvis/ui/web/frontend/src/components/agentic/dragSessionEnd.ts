/**
 * When a file drag is OVER — the one question every drop target in the app has
 * to answer, and the one the browser answers unreliably.
 *
 * Arming a drop zone is easy: `dragenter` says a drag arrived. Disarming it is
 * where the platform stops cooperating, because every event that is supposed
 * to mean "the drag ended" can go missing:
 *
 * * **`drop` fires only if the release happened in THIS document.** Let go over
 *   another window, another application, or the desktop and the page hears
 *   nothing at all.
 * * **`dragend` fires only on the drag's SOURCE element.** A drag out of
 *   Explorer or Finder has no source in this page, so this never arrives for
 *   exactly the drags a terminal pane cares about.
 * * **`dragleave` is not owed to a target the drag did not leave by moving.**
 *   Escape, a release outside the window, or a drag captured by another window
 *   all end the drag with the cursor still logically over the pane.
 *
 * Targets used to paper over this with one heuristic — a `dragleave` whose
 * `relatedTarget` is null while the cursor sits at the viewport edge — which
 * covers a drag dragged slowly out of the window and nothing else. Everything
 * else left the overlay standing over a live agent until the page was
 * reloaded, and because a pane's enter/leave counter was left non-zero too, no
 * later drag could ever take it back down: the pane stayed armed for the rest
 * of the session, and each further drag stranded one more pane (BUG-167).
 *
 * So the end of a drag is detected by its ABSENCE instead. While a drag is
 * anywhere over the document the browser repeats `dragover` on its own — the
 * drag-and-drop processing model runs roughly every 350 ms whether or not the
 * cursor moves. That repetition is a heartbeat: when it stops, the drag is no
 * longer over this window, whatever the reason and whichever event went
 * missing. Nothing has to be delivered for this to work; something has to stop
 * being delivered, and that cannot be swallowed.
 *
 * The fast signals are kept alongside it so an ordinary drop still disarms in
 * the same frame rather than after the heartbeat times out.
 */
import { useEffect } from "react";

/**
 * How long the heartbeat may go quiet before the drag counts as over.
 *
 * The spec's own drag loop is ~350 ms, so this allows three missed beats. Short
 * enough that a stranded overlay clears before the user reaches for the mouse
 * again, long enough that a busy main thread cannot fake the end of a drag that
 * is still in flight.
 */
const DRAG_IDLE_MS = 1_200;

/** Everything currently waiting to hear that the drag ended. */
const watchers = new Set<() => void>();

let idleTimer: ReturnType<typeof setTimeout> | null = null;
let installed = false;

/** Tell every armed target the drag is over, once. */
function endSession(): void {
  if (idleTimer !== null) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
  // Copied first: a watcher's callback removes it from the set, and mutating a
  // Set while iterating it is how a second watcher gets skipped.
  for (const watcher of Array.from(watchers)) watcher();
}

/** A drag is still here. Restart the countdown. */
function heartbeat(): void {
  if (idleTimer !== null) clearTimeout(idleTimer);
  idleTimer = setTimeout(endSession, DRAG_IDLE_MS);
}

/**
 * The drag left the WINDOW, rather than merely moving between elements inside
 * it — a null `relatedTarget` with the cursor on the viewport edge. Kept for
 * the speed: the heartbeat would catch this a second later anyway.
 */
function onDragLeave(event: DragEvent): void {
  if (event.relatedTarget !== null) return;
  if (
    event.clientX <= 0 ||
    event.clientY <= 0 ||
    event.clientX >= window.innerWidth ||
    event.clientY >= window.innerHeight
  ) {
    endSession();
  }
}

/**
 * A key or a mouse button. Neither reaches the page while a drag is running, so
 * either one arriving proves there is no drag — which is what makes a stranded
 * overlay heal itself the moment the user touches the app again, instead of
 * waiting for them to perform another drag.
 */
function onRealInput(): void {
  endSession();
}

function install(): void {
  if (installed) return;
  installed = true;
  // Capture throughout: a handler somewhere in the tree calling
  // `stopPropagation()` on a drop must not be able to keep the end of the drag
  // from reaching the targets that are armed because of it.
  window.addEventListener("dragover", heartbeat, true);
  window.addEventListener("drop", endSession, true);
  window.addEventListener("dragend", endSession, true);
  window.addEventListener("dragleave", onDragLeave, true);
  window.addEventListener("pointerdown", onRealInput, true);
  window.addEventListener("keydown", onRealInput, true);
  heartbeat();
}

function uninstall(): void {
  if (!installed) return;
  installed = false;
  window.removeEventListener("dragover", heartbeat, true);
  window.removeEventListener("drop", endSession, true);
  window.removeEventListener("dragend", endSession, true);
  window.removeEventListener("dragleave", onDragLeave, true);
  window.removeEventListener("pointerdown", onRealInput, true);
  window.removeEventListener("keydown", onRealInput, true);
  if (idleTimer !== null) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
}

/**
 * Watch for the end of the current drag while `armed`, and disarm when it comes.
 *
 * `onEnd` is read through a ref-free closure on purpose: the effect re-runs when
 * it changes, and callers pass a `useCallback`-stable function. It may be called
 * for a drag this target never saw — disarming something already disarmed is
 * free, and it is the reason a target stranded by an earlier drag comes back.
 */
export function useDragSessionEnd(armed: boolean, onEnd: () => void): void {
  useEffect(() => {
    if (!armed) return;
    watchers.add(onEnd);
    install();
    return () => {
      watchers.delete(onEnd);
      if (watchers.size === 0) uninstall();
    };
  }, [armed, onEnd]);
}

/** Test seam: forget every watcher and unwire the window. */
export function resetDragSessionForTests(): void {
  watchers.clear();
  uninstall();
}
