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
 * First, the document itself must still be visible and focused. WebSocket
 * events are broadcast to every connected UI client; without that ownership
 * check, an old tab or a second desktop window can paste the same transcript
 * into its own remembered field. Within the one owning document, the target is
 * the element that has focus when the transcript arrives, and — as a fallback —
 * the last editable one that had it. That fallback is allowed only while an
 * explicitly marked dictation control has focus. An unrelated toolbar or pane
 * click must invalidate the old field as a destination; otherwise "current
 * field" quietly becomes "whatever field was used earlier." A real dictation
 * button still moves focus onto itself, and "insert where the caret was" remains
 * available for that deliberate case.
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
 * `inactive` means another visible, focused UI client owns this broadcast, so
 * this client must consume it without inserting or falling back to its chat
 * composer. `none` is the honest answer when the owning document has nowhere
 * to put the text. The caller says so — see `useWebSocket`.
 */
export type DictationDelivery = "field" | "terminal" | "inactive" | "none";

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

/** Whether focus moved away from a field specifically to start dictation. */
function permitsRememberedTarget(element: Element | null): boolean {
  return (
    element instanceof HTMLElement &&
    element.closest("[data-jarvis-dictation-trigger]") !== null
  );
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
    // Keep the field available for an explicitly marked dictation trigger.
    // Ordinary controls cannot use it because `deliverDictationText` checks
    // what currently owns focus before considering this remembered element.
    if (found) lastEditable = found;
  };
  target.addEventListener("focusin", onFocusIn, true);
  return () => target.removeEventListener("focusin", onFocusIn, true);
}

/** Forget the remembered field. Exists for tests and for a full view teardown. */
export function resetDictationTarget(): void {
  lastEditable = null;
}

/** Whether this document is the single UI client allowed to consume dictation. */
export function documentOwnsDictation(target: Document = document): boolean {
  return target.visibilityState === "visible" && target.hasFocus();
}

/** The final-transcript half of a `DictationTranscript` event. */
export interface FinalTranscript {
  text?: string;
  /** The route the pipeline resolved: `insert` (foreign app) or `chat` (here). */
  target?: string;
}

/**
 * May this transcript be written into a field of this window?
 *
 * A predicate rather than an inline condition because getting it wrong is
 * invisible in both directions, and each clause guards a different way of
 * being wrong:
 *
 *  - **`target === "chat"`.** The event fires on BOTH routes. A dictation the
 *    backend is already pasting into another program would otherwise ALSO be
 *    written into whatever Jarvis field last had focus — a duplicate in a
 *    section the user is not even looking at. A backend too old to send a
 *    target says nothing, which reads as "not for us" and leaves that install
 *    behaving exactly as it did before.
 *  - **Words.** An aborted dictation publishes an empty final transcript purely
 *    to end the live tail; there is nothing to insert and nothing to report.
 *  - **The composer is not recording.** Its microphone button mirrors every
 *    partial into itself and owns the commit, so inserting here as well would
 *    deliver the same sentence twice.
 */
export function isForThisWindow(
  payload: FinalTranscript,
  composerIsRecording: boolean,
): boolean {
  if (payload.target !== "chat") return false;
  if (!(payload.text ?? "").trim()) return false;
  return !composerIsRecording;
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
  // A single server event reaches every open Jarvis tab/window. Only the
  // foreground document may turn that broadcast into an edit; all others are
  // observers. Checking again at the last possible moment closes the race where
  // focus moves after the WebSocket handler starts but before insertion.
  if (!documentOwnsDictation()) return "inactive";
  const active = document.activeElement;
  const candidates = permitsRememberedTarget(active)
    ? [active, lastEditable]
    : [active];
  for (const candidate of candidates) {
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
