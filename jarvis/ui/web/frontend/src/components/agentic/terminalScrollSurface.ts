/**
 * Keep terminal scrolling on the side that actually owns it.
 *
 * A normal xterm buffer has exact, browser-scrollable history. A full-screen
 * application takes the alternate buffer and/or mouse reporting, so xterm must
 * relay the wheel to that application and an xterm scrollbar would be inert.
 * The host's `data-scroll-owner` lets CSS expose the native bar only for the
 * first case. No position is guessed for the second case.
 */
import type { Terminal } from "@xterm/xterm";

export type TerminalScrollOwner = "terminal" | "application";

export function terminalScrollOwner(term: Terminal): TerminalScrollOwner {
  // The public fields exist in xterm 5.5. Optional reads keep older test
  // doubles and a partially constructed terminal on the safe native path.
  const tracking = term.modes?.mouseTrackingMode ?? "none";
  const bufferType = term.buffer?.active?.type ?? "normal";
  return bufferType === "alternate" || tracking !== "none"
    ? "application"
    : "terminal";
}

/**
 * Synchronize the host marker and keep a terminal wheel from falling through
 * to the workspace scroller after xterm has had its chance to handle it.
 */
export function bindTerminalScrollSurface(
  host: HTMLElement,
  term: Terminal,
): () => void {
  let owner: TerminalScrollOwner | null = null;
  const syncOwner = () => {
    const next = terminalScrollOwner(term);
    if (next === owner) return;
    owner = next;
    host.dataset.scrollOwner = next;
  };

  // This listener is on xterm's PARENT. The event reaches xterm first; only
  // afterwards is its unused browser default contained here, so the terminal
  // keeps every native wheel/mouse behavior and the workspace does not move too.
  const containWheel = (event: WheelEvent) => {
    event.stopPropagation();
    if (!event.defaultPrevented) event.preventDefault();
  };

  syncOwner();
  host.addEventListener("wheel", containWheel, { passive: false });
  const subscriptions = [
    term.onWriteParsed?.(syncOwner),
    term.buffer?.onBufferChange?.(syncOwner),
  ];

  return () => {
    host.removeEventListener("wheel", containWheel);
    for (const subscription of subscriptions) subscription?.dispose();
    delete host.dataset.scrollOwner;
  };
}
