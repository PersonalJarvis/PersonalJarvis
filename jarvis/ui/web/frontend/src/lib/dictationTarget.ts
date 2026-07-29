/**
 * Where a finished dictation goes when Jarvis itself is the window in front.
 *
 * ## Why this file has to exist
 *
 * Dictation into a FOREIGN application works by putting the transcript on the
 * clipboard and sending a paste chord. Into Jarvis's own window that route is
 * deliberately not taken: `jarvis/dictation/insert.py::resolve_target` sees its
 * own process in the foreground and switches the target to the in-app channel
 * instead of typing into itself.
 *
 * That channel then had exactly one consumer — the chat composer, which only
 * exists while the Chats section is on screen. So a dictation into an IDE
 * terminal pane, a settings field or a setup step was transcribed, polished,
 * published… and delivered to a component that was not mounted. The words
 * reached nothing but the dictation history, with no error anywhere (measured
 * 2026-07-29: 18 of 139 stored dictations carry outcome `chat` and no insertion
 * method).
 *
 * This module is the missing consumer. It delivers inside the page rather than
 * through synthetic keystrokes, which is what makes it work identically on
 * Windows, macOS and Linux: no clipboard, no paste chord, nothing the operating
 * system can block, and no race against the clipboard being restored.
 *
 * ## How the target is chosen
 *
 * The element that has focus when the transcript arrives, and — as a fallback —
 * the last editable one that had it. The fallback is load-bearing rather than
 * defensive: a dictation started from a button (the composer's microphone, a
 * toolbar) moves focus onto that button, and "insert where the caret was" is
 * what every dictation tool does and what people expect.
 *
 * A remembered element that has since left the DOM is skipped, so switching
 * sections cannot deliver into a field that is no longer on screen.
 *
 * Terminal panes are not text: they paint onto a canvas and read their keyboard
 * through a hidden textarea, so writing into that textarea would do nothing.
 * They register a bridge (`editActions.ts::attachTerminalBridge`) and are
 * pasted through xterm, which brackets the sequence the way a coding agent's
 * TUI expects. That registry already existed for the right-click menu; this
 * module reuses it rather than growing a second one that could drift from it.
 */

import { captureEditSnapshot, pasteInto } from "./editActions";

/**
 * What became of one transcript.
 *
 * `none` is the honest answer, not a swallowed failure: nothing on screen could
 * take the text. The caller says so — see `useWebSocket`.
 */
export type DictationDelivery = "field" | "terminal" | "none";

/** The last editable element that had focus, for the button case above. */
let lastEditable: HTMLElement | null = null;

/**
 * The element itself when text can be written at it, else `null`.
 *
 * Deliberately re-derived through `captureEditSnapshot` rather than trusting a
 * tag check: it is the same classification the right-click menu uses, so a
 * surface that can be pasted into with the mouse can be dictated into as well,
 * for good and for ill, without two lists of what counts as a text field.
 */
function editableTarget(element: Element | null): HTMLElement | null {
  if (!(element instanceof HTMLElement)) return null;
  // A remembered field whose view was unmounted is not a target any more.
  if (!element.isConnected) return null;
  const snapshot = captureEditSnapshot(element);
  if (snapshot.kind === "terminal") return element;
  if (snapshot.kind === "field" && snapshot.editable) return element;
  return null;
}

/**
 * Watch focus so a dictation started from a button still lands in the field.
 *
 * Capture phase, because the panes stop `focusin` from bubbling in places.
 * Returns the removal function; mounted once from `<App />`.
 */
export function installDictationFocusTracker(
  target: Document = document,
): () => void {
  const onFocusIn = (event: Event): void => {
    const found = editableTarget(event.target as Element | null);
    // Only ever REPLACED by another editable target, never cleared by one:
    // clicking a button must not lose the field the user was just typing in.
    if (found) lastEditable = found;
  };
  target.addEventListener("focusin", onFocusIn, true);
  return () => target.removeEventListener("focusin", onFocusIn, true);
}

/** Forget the remembered field. Exists for tests and for a full view teardown. */
export function resetDictationTarget(): void {
  lastEditable = null;
}

/**
 * Write `text` wherever the user was typing. Never throws.
 *
 * The caret position is read HERE rather than when focus was gained — a caret
 * captured at focus time is always at the start of the field, which would make
 * every dictation insert in the wrong place.
 */
export function deliverDictationText(text: string): DictationDelivery {
  if (!text.trim()) return "none";
  if (typeof document === "undefined") return "none";
  for (const candidate of [document.activeElement, lastEditable]) {
    const element = editableTarget(candidate);
    if (!element) continue;
    const snapshot = captureEditSnapshot(element);
    let written = false;
    try {
      written = pasteInto(snapshot, text);
    } catch {
      // A refused insertion is not a lost dictation: try the next candidate,
      // and let the caller report "none" if none of them takes it.
      written = false;
    }
    if (written) return snapshot.kind === "terminal" ? "terminal" : "field";
  }
  return "none";
}
