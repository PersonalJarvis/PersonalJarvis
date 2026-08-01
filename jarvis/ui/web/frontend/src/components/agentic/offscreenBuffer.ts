/**
 * What a pane nobody is looking at does with its agent's output.
 *
 * A workspace may hold dozens of panes, and every one of them is a terminal
 * emulator: `term.write` parses the escape stream, updates a cell grid, and
 * schedules a repaint — all on the ONE browser main thread that also has to
 * notice the user pressing a key. Off-screen panes therefore compete directly
 * with the pane being typed into, and they win often enough that keystrokes
 * arrive in delayed bursts.
 *
 * They also do it for nothing. A pane scrolled out of the grid, or hidden
 * behind a maximized sibling, draws to a surface with no pixels on screen. So
 * its output is parked here instead and written in one call when the pane comes
 * back — same bytes, same order, one parse instead of hundreds.
 *
 * ## Why nothing is ever thrown away
 *
 * This buffer used to drop its oldest output once it grew past a limit, on the
 * reasoning that a terminal UI repaints itself continuously and the newest
 * bytes therefore ARE the screen. That reasoning is wrong, and it put empty
 * rectangles in two panes of a live workspace (reported 2026-07-27): a coding
 * agent built on Ink paints its interface ONCE and afterwards rewrites only the
 * row that changed, addressed by RELATIVE cursor moves. A stream missing its
 * front replays the spinner row the agent wrote last onto rows where the prompt
 * box it drew twenty minutes ago is simply missing — and nothing later redraws
 * it, so the pane stays broken until the agent is finished.
 *
 * So the limit still bounds MEMORY, but it is answered by writing rather than
 * by discarding: {@link OffscreenBuffer.full} tells the pane to hand what it
 * holds to xterm even though nobody is watching. That costs one parse on a
 * surface the browser is not painting — which is the cheap half of the work
 * this class exists to avoid, and far cheaper than a screen that never recovers.
 *
 * ## Why holding is also bounded by TIME, not only by size
 *
 * Everything above assumes the pane finds out when it is looked at again. That
 * assumption has now failed three times, each in a state the browser reports no
 * way out of — a hidden document, a pane that came back at exactly the size it
 * left, a measurement taken while the window could not be measured at all — and
 * each was answered by teaching the pane one more question to ask
 * ({@link boxOnScreen}, `visibilitychange`, the resize path, the re-check on
 * output). Every one of those is a patch on the same shape of bug, and the list
 * is not provably complete: the failure is always "the pane believes nobody is
 * watching while somebody is", and from inside it is indistinguishable from
 * being right.
 *
 * The cost of being wrong used to be unbounded. A pane could hold its agent's
 * screen until 256 KB had piled up — minutes for a chatty CLI, forever for one
 * that printed a line and went quiet — and the user watched a terminal that had
 * simply stopped moving while the work behind it ran on (reported 2026-07-31:
 * two live panes, one frozen at a time, and clicking the frozen one handed the
 * freeze to its neighbour).
 *
 * So parking is capped at {@link OFFSCREEN_MAX_HOLD_MS}. That turns this class
 * from "withhold until somebody proves you are visible" into "COALESCE": a pane
 * still merges a burst of output into one write instead of sixty, which is the
 * entire performance win, but it can never be more than a moment behind — no
 * matter which visibility question it got wrong. A pane that really is hidden
 * pays one parse per interval onto a surface the browser does not paint, which
 * is the cheap half of the work; a pane that is wrongly hidden stops being a
 * frozen screen.
 */

/**
 * How much output one hidden pane parks before it stops holding and writes.
 *
 * Comfortably several full repaints of a full-screen agent UI, so an ordinary
 * spell off screen is still one single write on return — and small enough that
 * a hundred hidden panes stay a rounding error.
 */
export const OFFSCREEN_LIMIT_CHARS = 256 * 1024;

/**
 * How often a parked pane re-measures itself while its agent keeps talking.
 *
 * The two moments {@link boxOnScreen} is asked about — the document becoming
 * visible, the pane being resized — are the ones we KNOW the observer cannot
 * report its way out of. They are not a proof that no third one exists: the
 * observer lives in the browser engine, an embedded WebView is not a browser
 * tab, and every state it fails to report looks identical from here (a pane
 * holding its agent's screen while the user watches an empty rectangle and is
 * told the work was handed over).
 *
 * So a parked pane that is still RECEIVING output — the only parked pane whose
 * silence a user can notice — checks for itself, at most this often. It costs
 * one rectangle measurement per second per parked, chatty pane, and it bounds
 * every unknown member of this bug class at one second instead of "until the
 * agent finishes".
 */
export const PARKED_RECHECK_MS = 1000;

/**
 * The longest a parked pane may hold its agent's output before writing it.
 *
 * This is the backstop for every visibility question this module gets wrong —
 * see the module docstring for why a complete list of those cannot be assumed.
 * Past it the pane writes what it holds without asking anything: it may still
 * be off screen, and writing costs one parse onto a surface nobody paints,
 * which is far cheaper than a terminal that has visibly stopped.
 *
 * Chosen so a burst of output still coalesces into one write — the whole point
 * of parking — while the worst case a user can observe is a screen that is one
 * and a half seconds behind rather than one that never moves again. Raising it
 * trades the same performance win for a longer freeze when a pane is wrong
 * about being watched.
 */
export const OFFSCREEN_MAX_HOLD_MS = 1500;

/**
 * How far outside the window a pane still counts as worth drawing.
 *
 * Generous on purpose: a pane one flick of the wheel away catches up before it
 * is looked at, so scrolling never shows a pane mid-write. Shared by the
 * `IntersectionObserver` that watches a pane and by {@link boxOnScreen}, which
 * answers the same question directly — the two must agree, or a pane parks by
 * one rule and un-parks by another.
 */
export const OFFSCREEN_MARGIN_PX = 300;

/**
 * Is this box on screen right now, by the same rule the observer applies?
 *
 * An `IntersectionObserver` is the right tool for *changes* and the wrong one
 * for *questions*: it only recomputes when geometry or scrolling moves, so
 * there are states it will never report its way out of. The one that put empty
 * rectangles in a live workspace (2026-07-27): while the document is HIDDEN —
 * the app window behind another, minimized, or its tab in the background —
 * every element reads as intersecting nothing, and the callback that says so
 * is the last one delivered. Showing the window again changes no geometry, so
 * nothing fires, and a pane that parked its agent's output in that moment
 * keeps parking it forever. It comes back only once 256 KB have piled up
 * ({@link OffscreenBuffer.full}), which for a chatty CLI is minutes and for a
 * freshly started one that printed its prompt and went quiet is never.
 *
 * So the pane asks this instead at the two moments the observer cannot cover:
 * when the document becomes visible again, and when the pane is resized.
 */
export function boxOnScreen(
  box: { top: number; bottom: number; left: number; right: number; width: number; height: number },
  viewport: { width: number; height: number },
  margin: number = OFFSCREEN_MARGIN_PX,
): boolean {
  // A pane with no area intersects nothing — that is what an element being laid
  // out, or hidden behind a maximized sibling, looks like.
  if (box.width < 1 || box.height < 1) return false;
  return (
    box.bottom >= -margin &&
    box.right >= -margin &&
    box.top <= viewport.height + margin &&
    box.left <= viewport.width + margin
  );
}

export class OffscreenBuffer {
  private chunks: string[] = [];
  private size = 0;
  /**
   * When the oldest chunk still held here arrived, or null while empty.
   *
   * Measured from the FIRST chunk rather than the newest, because the age that
   * matters is the age of the oldest thing the user cannot see — a pane fed a
   * steady trickle would otherwise reset its own deadline forever and never
   * come due.
   */
  private heldSince: number | null = null;

  constructor(
    private readonly limit: number = OFFSCREEN_LIMIT_CHARS,
    private readonly maxHoldMs: number = OFFSCREEN_MAX_HOLD_MS,
  ) {}

  /**
   * Park one chunk of agent output.
   *
   * ``now`` is injectable so the pane and its tests can drive the deadline from
   * one clock; it defaults to the real one.
   */
  push(text: string, now: number = Date.now()): void {
    if (!text) return;
    this.chunks.push(text);
    this.size += text.length;
    if (this.heldSince === null) this.heldSince = now;
  }

  /**
   * Has this pane held what it has for long enough to write it regardless?
   *
   * The backstop for a pane that is wrong about being watched — see the module
   * docstring. Like {@link full} this means "write now", never "discard".
   */
  stale(now: number = Date.now()): boolean {
    return this.heldSince !== null && now - this.heldSince >= this.maxHoldMs;
  }

  /**
   * Milliseconds until {@link stale} turns true, or null while nothing is held.
   *
   * The pane schedules its own flush on this, so output that stops arriving is
   * still written: a deadline only checked when the next chunk lands would keep
   * the LAST thing an agent said parked for as long as it stays quiet, which is
   * exactly the screen a user reads as frozen.
   */
  dueIn(now: number = Date.now()): number | null {
    if (this.heldSince === null) return null;
    return Math.max(0, this.maxHoldMs - (now - this.heldSince));
  }

  /**
   * Has this pane parked as much as it may hold?
   *
   * True means "write what you are holding now" — not "discard it". See the
   * module docstring for why the difference is the whole point.
   */
  get full(): boolean {
    return this.size >= this.limit;
  }

  /** Everything kept, oldest first, ready for a single `term.write`. */
  drain(): string {
    this.heldSince = null;
    if (this.chunks.length === 0) return "";
    const text = this.chunks.join("");
    this.chunks = [];
    this.size = 0;
    return text;
  }

  /** Characters waiting to be written when this pane is looked at again. */
  get pending(): number {
    return this.size;
  }
}
