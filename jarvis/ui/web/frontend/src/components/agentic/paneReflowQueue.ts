/**
 * One pane reflow per frame, workspace-wide.
 *
 * Adding a pane changes the width of EVERY pane: the grid re-divides its bands,
 * so six terminals are handed six new sizes in the same tick. Each of those is
 * an xterm `resize()`, which re-wraps that pane's whole scrollback, followed by
 * a PTY resize the agent answers by repainting its full-screen UI. Run together
 * they add up to one long block of the main thread — measured at 1.5 s, 2.9 s
 * and twice at 9.4 s on the maintainer's machine while two panes were opened by
 * voice (2026-07-28). The window stops answering clicks for that whole time,
 * and the panes that were just opened cannot paint either, so "open two more
 * terminals" looks like it did nothing at all.
 *
 * The total work is unchanged — it cannot be avoided, the sizes really did all
 * change. What changes is that the browser gets the frame BETWEEN two reflows
 * back: it can paint, it can deliver a keystroke, and a pane that has finished
 * its own reflow shows its new size while its neighbours are still working.
 *
 * Deliberately module-global rather than per-grid: two workspaces can be open,
 * and both draw onto the same single thread, so a queue per grid would allow
 * exactly the pile-up this exists to prevent.
 *
 * Ordering is FIFO, and a pane already waiting is never queued twice — the
 * second request would reflow to the same measured size a frame later.
 */

/** Reflows waiting for a frame, in the order they were asked for. */
const pending: Array<() => void> = [];
/** Set membership for `pending`, so a re-queue is O(1) rather than a scan. */
const queued = new Set<() => void>();
let frame: number | null = null;

/**
 * How many reflows may share one frame.
 *
 * One, and that is the point — see the module docstring. Named rather than
 * inlined so the trade-off is visible: raising it trades responsiveness for
 * finishing the whole grid sooner.
 */
const PER_FRAME = 1;

/** Run the next reflow(s), then hand the thread back until the next frame. */
function drain(): void {
  frame = null;
  for (let i = 0; i < PER_FRAME; i += 1) {
    const next = pending.shift();
    if (next === undefined) break;
    queued.delete(next);
    try {
      next();
    } catch {
      /* one pane's fit must never stop the panes behind it in the queue */
    }
  }
  if (pending.length > 0) schedule();
}

function schedule(): void {
  if (frame !== null) return;
  // rAF, not a timer: a reflow paints, so it belongs to the frame budget. In a
  // test environment (jsdom) there is no rAF — run on a macrotask instead, which
  // preserves the ordering this queue exists for without needing a real frame.
  if (typeof requestAnimationFrame === "function") {
    frame = requestAnimationFrame(drain);
  } else {
    frame = setTimeout(drain, 0) as unknown as number;
  }
}

/**
 * Ask for `reflow` to run on a frame of its own.
 *
 * Idempotent per callback: queueing a reflow that is already waiting is a no-op,
 * because it would measure the same element and arrive at the same size.
 */
export function queuePaneReflow(reflow: () => void): void {
  if (queued.has(reflow)) return;
  queued.add(reflow);
  pending.push(reflow);
  schedule();
}

/**
 * Drop a pane's pending reflow — called when it unmounts.
 *
 * Without this a closed pane's `fit()` still runs one frame later, against a
 * disposed terminal and a detached element.
 */
export function cancelPaneReflow(reflow: () => void): void {
  if (!queued.delete(reflow)) return;
  const at = pending.indexOf(reflow);
  if (at >= 0) pending.splice(at, 1);
}
