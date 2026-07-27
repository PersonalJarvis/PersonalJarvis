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
 */

/**
 * How much output one hidden pane parks before it stops holding and writes.
 *
 * Comfortably several full repaints of a full-screen agent UI, so an ordinary
 * spell off screen is still one single write on return — and small enough that
 * a hundred hidden panes stay a rounding error.
 */
export const OFFSCREEN_LIMIT_CHARS = 256 * 1024;

export class OffscreenBuffer {
  private chunks: string[] = [];
  private size = 0;

  constructor(private readonly limit: number = OFFSCREEN_LIMIT_CHARS) {}

  /** Park one chunk of agent output. */
  push(text: string): void {
    if (!text) return;
    this.chunks.push(text);
    this.size += text.length;
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
