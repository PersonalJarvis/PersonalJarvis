/**
 * Invisible terminal scrolling helpers.
 *
 * Agentic IDE panes intentionally draw no scrollbar. xterm still owns the
 * scroll position, wheel input remains contained inside the pane, and the
 * recorded conversation is opened from the book button in the pane header.
 *
 * `captureWheelForTerminalHistory` covers the one awkward input shape: a
 * NORMAL-buffer CLI that has negotiated mouse tracking. There xterm has real
 * history, so the wheel scrolls it rather than being handed to the app as
 * mouse reports. Alternate-screen apps keep their native wheel protocol.
 */
import type { Terminal } from "@xterm/xterm";

/** Pixels of wheel travel per scrolled row when the wheel reports pixels. */
const WHEEL_PIXELS_PER_ROW = 40;
/** Ceiling per wheel event, so one coalesced trackpad burst cannot teleport. */
const MAX_ROWS_PER_WHEEL = 40;

/**
 * Keep the wheel on xterm's history even when a normal-buffer CLI has
 * negotiated mouse tracking.
 *
 * Wired via `term.attachCustomWheelEventHandler`. Returning true lets xterm
 * handle the event as usual; returning false means this handler already did.
 *
 * The one intercepted case: normal buffer + mouse tracking on. Left to xterm,
 * that wheel would become mouse reports typed at the CLI — scrolling would
 * "work" only while the CLI feels like it, which is the mode-dependent
 * inconsistency this rebuild removes. An alternate-screen app keeps every
 * negotiated protocol: xterm has no history there to scroll instead.
 * Modifier chords (ctrl=zoom, shift=app escape hatch) stay native.
 */
export function captureWheelForTerminalHistory(
  term: Terminal,
): (event: WheelEvent) => boolean {
  let pixelRemainder = 0;

  return (event: WheelEvent): boolean => {
    if (
      event.deltaY === 0 ||
      Math.abs(event.deltaX) > Math.abs(event.deltaY) ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey
    ) {
      pixelRemainder = 0;
      return true;
    }
    const bufferType = term.buffer?.active?.type ?? "normal";
    const tracking = term.modes?.mouseTrackingMode ?? "none";
    if (bufferType === "alternate" || tracking === "none") {
      pixelRemainder = 0;
      return true;
    }

    const direction = event.deltaY < 0 ? -1 : 1;
    let rows: number;
    if (event.deltaMode === WheelEvent.DOM_DELTA_PAGE) {
      rows = Math.max(1, term.rows) * Math.abs(event.deltaY);
      pixelRemainder = 0;
    } else if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) {
      rows = Math.max(1, Math.ceil(Math.abs(event.deltaY)));
      pixelRemainder = 0;
    } else {
      if (pixelRemainder !== 0 && Math.sign(pixelRemainder) !== direction) {
        pixelRemainder = 0;
      }
      pixelRemainder += event.deltaY;
      rows = Math.floor(Math.abs(pixelRemainder) / WHEEL_PIXELS_PER_ROW);
      if (rows > 0) {
        pixelRemainder -= direction * rows * WHEEL_PIXELS_PER_ROW;
      }
    }
    rows = Math.min(MAX_ROWS_PER_WHEEL, rows);
    if (rows > 0) term.scrollLines(direction * rows);
    // Even a sub-row remainder is accounted; xterm must not ALSO emit a
    // mouse report for the same physical notch.
    return false;
  };
}

/**
 * The xterm surface, which owns every wheel inside itself.
 *
 * Matched by class rather than by the pane's own host element because it is the
 * boundary that matters: `captureWheelForTerminalHistory` and xterm's viewport
 * between them decide what a notch means in there, and neither may be
 * second-guessed from a parent listener.
 */
const TERMINAL_SURFACE_CLASS = "xterm";

/** A sub-pixel left at either end is a rounding artefact, not somewhere to go. */
const SCROLL_END_EPSILON_PX = 1;

/** Does this element scroll its own overflow rather than spill it? */
function scrollsItsOwnOverflow(element: Element): boolean {
  // Absent in some test environments; there nothing claims the wheel, which is
  // the behaviour this replaces.
  if (typeof getComputedStyle !== "function") return false;
  const overflowY = getComputedStyle(element).overflowY;
  return overflowY === "auto" || overflowY === "scroll";
}

/** Has `element` anywhere left to go in the direction the wheel is pointing? */
function hasScrollRoom(element: Element, deltaY: number): boolean {
  const { scrollHeight, clientHeight, scrollTop } = element as HTMLElement;
  if (scrollHeight - clientHeight <= SCROLL_END_EPSILON_PX) return false;
  return deltaY < 0
    ? scrollTop > SCROLL_END_EPSILON_PX
    : scrollTop + clientHeight < scrollHeight - SCROLL_END_EPSILON_PX;
}

/**
 * Is something between the wheel and the pane going to scroll on its own?
 *
 * Walked from the event's target outwards, and the terminal wins wherever it is
 * met: xterm's viewport is itself a real scroller, so a candidate found INSIDE
 * the terminal surface would otherwise be answered here as well as by xterm.
 * The answer is therefore held until the walk has passed the whole chain up to
 * the pane, rather than returned at the first scrollable element.
 */
function absorbsWheel(
  target: EventTarget | null,
  region: HTMLElement,
  deltaY: number,
): boolean {
  let node = target instanceof Element ? target : null;
  let absorber: Element | null = null;
  while (node && node !== region) {
    if (node.classList.contains(TERMINAL_SURFACE_CLASS)) return false;
    if (!absorber && scrollsItsOwnOverflow(node) && hasScrollRoom(node, deltaY)) {
      absorber = node;
    }
    node = node.parentElement;
  }
  return absorber !== null;
}

/**
 * Keep terminal wheel input from falling through to the workspace scroller.
 *
 * The containment is unconditional and always was: whatever a notch means
 * inside a pane, the section behind it must not move. A workspace is one
 * screenful by rule, and a pane that let the page scroll under it would carry
 * every other pane away with it.
 *
 * Cancelling the notch is a SEPARATE question, and answering both with one line
 * is what broke the prompt receipt. That card is drawn inside the terminal
 * region — it has to be, it points at the pane it is talking about — and a long
 * delivered prompt scrolls inside it (`max-h-56 overflow-y-auto` in
 * ./PromptReceipt). Cancelling every wheel in the region cancelled that one too,
 * so the receipt could be opened, could show that it had more text, and could
 * not be read past its first fifty-six pixels.
 *
 * So the default is only prevented when nothing between the pointer and the
 * pane is going to act on it. The terminal itself is untouched by the
 * distinction: its subtree is excluded outright (see `absorbsWheel`), which is
 * also why this cannot disturb the scroll contract the panes were tuned for.
 */
export function bindTerminalScrollRegion(region: HTMLElement): () => void {
  const containWheel = (event: WheelEvent) => {
    event.stopPropagation();
    if (event.defaultPrevented) return;
    if (absorbsWheel(event.target, region, event.deltaY)) return;
    event.preventDefault();
  };
  region.addEventListener("wheel", containWheel, { passive: false });
  return () => region.removeEventListener("wheel", containWheel);
}
