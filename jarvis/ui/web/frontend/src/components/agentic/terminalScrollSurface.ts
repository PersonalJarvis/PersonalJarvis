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

/** Keep terminal wheel input from falling through to the workspace scroller. */
export function bindTerminalScrollRegion(region: HTMLElement): () => void {
  const containWheel = (event: WheelEvent) => {
    event.stopPropagation();
    if (!event.defaultPrevented) event.preventDefault();
  };
  region.addEventListener("wheel", containWheel, { passive: false });
  return () => region.removeEventListener("wheel", containWheel);
}
