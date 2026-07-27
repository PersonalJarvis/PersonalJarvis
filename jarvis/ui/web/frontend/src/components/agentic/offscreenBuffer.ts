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
 * Bounded, and the OLDEST goes first, for the same reason the server bounds its
 * replay buffer (`jarvis/agentic_ide/transcript.py`): a terminal UI repaints
 * itself continuously, so the newest bytes ARE the screen and stale ones are
 * overwritten within a frame. Keeping the oldest instead would replay a dead
 * screen; keeping everything would trade a bounded delay for unbounded memory
 * across every pane at once.
 */

/**
 * How much output one hidden pane keeps. Comfortably several full repaints of
 * a full-screen agent UI, and small enough that a hundred hidden panes stay a
 * rounding error.
 */
export const OFFSCREEN_LIMIT_CHARS = 256 * 1024;

export class OffscreenBuffer {
  private chunks: string[] = [];
  private size = 0;
  private droppedChars = 0;

  constructor(private readonly limit: number = OFFSCREEN_LIMIT_CHARS) {}

  /** Park one chunk of agent output. */
  push(text: string): void {
    if (!text) return;
    this.chunks.push(text);
    this.size += text.length;
    while (this.size > this.limit && this.chunks.length > 1) {
      const dropped = this.chunks.shift() as string;
      this.size -= dropped.length;
      this.droppedChars += dropped.length;
    }
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

  /** Characters this pane never got to draw because it fell too far behind. */
  get dropped(): number {
    return this.droppedChars;
  }
}
