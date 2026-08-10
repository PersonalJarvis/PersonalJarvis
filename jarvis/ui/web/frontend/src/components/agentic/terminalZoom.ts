/**
 * Ctrl/Cmd + `+` / `-` / `0` — and Ctrl + wheel — resize the terminal text, the
 * way a browser zooms.
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

/**
 * How much accumulated wheel travel counts as one step of text size.
 *
 * A mouse notch reports 100-120 px in one event, so it steps immediately. A
 * trackpad pinch reports a stream of 1-10 px deltas instead, and treating each
 * as a step took a pane from 10 px to 20 px in a single gesture — past both
 * clamps before the fingers had stopped moving. Accumulating to a threshold
 * makes both devices step at roughly the same rate.
 */
const WHEEL_STEP_DELTA = 40;

/**
 * What this wheel event means for the terminal text size, or `null` when it is
 * ordinary scrolling.
 *
 * ## Why `ctrlKey` on every platform, including macOS
 *
 * This is the one place the key chord's rule is deliberately NOT mirrored. The
 * chord table above refuses Ctrl on macOS because Ctrl+<key> is a control code
 * inside a terminal — but that is a property of KEYS, and a wheel notch is
 * never a control code. What Ctrl+wheel is on every platform is the zoom
 * gesture: the browser's own, VS Code's `editor.mouseWheelZoom`, and — the part
 * that decides it — a macOS trackpad PINCH, which the engine delivers as a
 * synthetic wheel event with `ctrlKey` set and no key pressed at all. Requiring
 * Cmd there would leave pinch-to-zoom, the gesture a Mac user reaches for
 * first, doing nothing.
 *
 * Cmd is therefore disqualifying rather than accepted: on macOS Cmd+scroll is
 * not a zoom in any application, and on Windows Win+wheel belongs to the
 * system magnifier. Alt is disqualifying for the same reason as above — AltGr
 * arrives as Ctrl+Alt on European layouts.
 */
export function zoomIntentForWheel(
  event: Pick<WheelEvent, "deltaY" | "ctrlKey" | "metaKey" | "altKey">,
): Extract<ZoomIntent, "in" | "out"> | null {
  if (!event.ctrlKey || event.metaKey || event.altKey) return null;
  if (event.deltaY === 0) return null;
  // Away from the user is "bigger", the same direction every browser zooms.
  return event.deltaY < 0 ? "in" : "out";
}

export interface ZoomBridgeOptions {
  /**
   * Is the workspace the user is actually looking at?
   *
   * Asked per keystroke rather than captured once, because the Agentic IDE is
   * hidden instead of unmounted when another section is opened — a grid parked
   * behind the settings screen must not resize its panes while the user zooms
   * something else.
   *
   * Optional for a bridge bound to one pane's own surface: an element that
   * cannot be seen cannot be scrolled over either, so there is nothing left to
   * ask.
   */
  enabled?: () => boolean;
  /** Apply the step. Clamping and remembering it belong to the caller. */
  apply: (intent: ZoomIntent) => void;
}

export interface ZoomKeyBridgeOptions
  extends ZoomChordOptions, ZoomBridgeOptions {
  /** The key bridge is installed once for a whole grid, so it must be asked. */
  enabled: () => boolean;
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

/**
 * Claim Ctrl+wheel on `target` as a text-size gesture, returning a cleanup
 * function.
 *
 * Bound to one pane's terminal surface rather than to the window, unlike the
 * key bridge: a chord typed anywhere in the workspace plausibly means "the text
 * I am reading", but a wheel gesture has a definite place under the pointer, and
 * zooming every terminal because someone pinched over the file tree would be a
 * surprise. Whatever the gesture lands on is what it is about.
 *
 * Three details are load-bearing:
 *
 * * **`passive: false`.** Browsers make `wheel` listeners on window/document
 *   passive by DEFAULT, and a passive listener may not cancel anything — the
 *   WebView would zoom the whole app underneath the pane. Declared explicitly so
 *   the same call is safe wherever it is bound.
 * * **Capture phase.** The pane's own surface stops wheel events from reaching
 *   the workspace scroller (see ./terminalScrollSurface) and xterm scrolls its
 *   history from a listener of its own. Capturing puts this ahead of both.
 * * **Cancelled even below the step threshold.** A pinch that has not yet
 *   travelled far enough to change the size is still a zoom gesture, and letting
 *   that one through would page-zoom the app on the way to the size the user
 *   asked for.
 */
export function installZoomWheelBridge(
  target: Pick<EventTarget, "addEventListener" | "removeEventListener">,
  { enabled, apply }: ZoomBridgeOptions,
): () => void {
  /** Signed wheel travel not yet spent on a step. */
  let travel = 0;

  const onWheel = (event: Event) => {
    const wheel = event as WheelEvent;
    if (enabled && !enabled()) return;
    const intent = zoomIntentForWheel(wheel);
    if (intent === null) {
      travel = 0;
      return;
    }
    // Before the threshold, deliberately — see the note above.
    event.preventDefault();
    event.stopPropagation();
    // A device that reports lines or pages has already quantised the gesture
    // for us; only pixel deltas need accumulating.
    if (wheel.deltaMode !== 0) {
      travel = 0;
      apply(intent);
      return;
    }
    // Turning around mid-gesture spends nothing it had banked the other way,
    // so a correction takes effect on the next notch rather than two later.
    if (travel !== 0 && Math.sign(travel) !== Math.sign(wheel.deltaY))
      travel = 0;
    travel += wheel.deltaY;
    const steps = Math.floor(Math.abs(travel) / WHEEL_STEP_DELTA);
    if (steps === 0) return;
    travel -= Math.sign(travel) * steps * WHEEL_STEP_DELTA;
    // One step per event however far a coalesced burst travelled. The clamp
    // lives in the caller, and a pinch that jumped four steps at once reads as
    // the size having been yanked rather than adjusted.
    apply(intent);
  };

  target.addEventListener("wheel", onWheel, { capture: true, passive: false });
  return () =>
    target.removeEventListener("wheel", onWheel, { capture: true } as never);
}
