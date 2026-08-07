/**
 * Ctrl/Cmd + `+` / `-` / `0` resizes the terminal text, the way a browser zooms.
 *
 * ## Why this file has to exist
 *
 * The workspace toolbar already carries a text-size stepper, but nobody looks
 * up at a toolbar to make the text they are reading bigger — they press the
 * chord every other application on their machine binds to it. A terminal is the
 * worst place to be missing it, because a pane's text is the smallest text on
 * the screen and the grid keeps shrinking it as more agents are opened.
 *
 * The chord cannot be left to the surrounding app either. The desktop shell is
 * an embedded WebView with no address bar and no menu, so the browser's own
 * zoom would scale the WHOLE window — toolbar, prompt box, every pane at once —
 * with no visible control to undo it again. Claiming the keystroke here both
 * gives it the meaning the user wants and keeps the app's own layout still: the
 * `preventDefault` below is what stops the page zoom from happening as well.
 *
 * ## Which chords count, and which must NOT
 *
 * * **The platform modifier only** — Cmd on macOS, Ctrl on Windows and Linux.
 *   The other one is left alone: Ctrl+<key> is a control code inside a terminal
 *   on macOS, and Win+`+` belongs to the Windows magnifier.
 * * **Alt is disqualifying.** European layouts deliver AltGr as Ctrl+Alt, and
 *   on a German keyboard AltGr+`+` is `~` — the tilde a user types into a shell
 *   path all day. A zoom that swallowed it would be a worse bug than the
 *   missing shortcut (same reasoning as ./terminalNewline).
 * * **Both spellings of each step.** `+` is its own key on a German layout but
 *   Shift+`=` on a US one, so `=` counts as zoom-in and `_` as zoom-out; the
 *   numeric keypad is matched by `code`, which is the only thing that still
 *   identifies it when NumLock is off.
 */

/** Which way the user asked the terminal text to go. */
export type ZoomIntent = "in" | "out" | "reset";

export interface ZoomChordOptions {
  /** Cmd is the modifier on Apple keyboards, Ctrl everywhere else. */
  isMac: boolean;
}

/** Physical keypad keys, which `key` alone stops identifying without NumLock. */
const KEYPAD_IN = "NumpadAdd";
const KEYPAD_OUT = "NumpadSubtract";
const KEYPAD_RESET = "Numpad0";

/**
 * What this keystroke means for the terminal text size, or `null` for anything
 * that is not one of the three zoom chords.
 */
export function zoomIntentFor(
  event: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "metaKey" | "altKey">,
  { isMac }: ZoomChordOptions,
): ZoomIntent | null {
  // AltGr (Ctrl+Alt on European layouts) types characters people need in a
  // shell — it must never be read as a modifier here.
  if (event.altKey) return null;
  if (isMac ? !event.metaKey || event.ctrlKey : !event.ctrlKey || event.metaKey) {
    return null;
  }
  if (event.key === "+" || event.key === "=" || event.code === KEYPAD_IN) return "in";
  if (event.key === "-" || event.key === "_" || event.code === KEYPAD_OUT) return "out";
  if (event.key === "0" || event.code === KEYPAD_RESET) return "reset";
  return null;
}

export interface ZoomKeyBridgeOptions extends ZoomChordOptions {
  /**
   * Is the workspace the user is actually looking at?
   *
   * Asked per keystroke rather than captured once, because the Agentic IDE is
   * hidden instead of unmounted when another section is opened — a grid parked
   * behind the settings screen must not resize its panes while the user zooms
   * something else.
   */
  enabled: () => boolean;
  /** Apply the step. Clamping and remembering it belong to the caller. */
  apply: (intent: ZoomIntent) => void;
}

/**
 * Claim the zoom chords on `target`, returning a cleanup function.
 *
 * Registered in the CAPTURE phase, and that is the load-bearing detail: the
 * keystroke is typed into an xterm pane, whose textarea both reads the key and
 * cancels it, so a listener waiting for the event to bubble would never see it.
 * Capturing on the window puts this ahead of every pane, and `stopPropagation`
 * then keeps the chord from also arriving at the agent as a control code.
 */
export function installZoomKeyBridge(
  target: Pick<EventTarget, "addEventListener" | "removeEventListener">,
  { isMac, enabled, apply }: ZoomKeyBridgeOptions,
): () => void {
  const onKeyDown = (event: Event) => {
    const key = event as KeyboardEvent;
    if (!enabled()) return;
    const intent = zoomIntentFor(key, { isMac });
    if (intent === null) return;
    // Stops the WebView's own page zoom (the reason this is a shortcut the app
    // has to claim rather than inherit) and keeps the pane from typing it.
    event.preventDefault();
    event.stopPropagation();
    apply(intent);
  };

  target.addEventListener("keydown", onKeyDown, true);
  return () => target.removeEventListener("keydown", onKeyDown, true);
}
