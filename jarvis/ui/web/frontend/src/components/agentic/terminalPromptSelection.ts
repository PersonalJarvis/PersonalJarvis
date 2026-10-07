import type { IBuffer, IBufferCellPosition, IBufferRange } from "@xterm/xterm";

/** The public xterm surface needed to edit a selected, visible prompt. */
export interface PromptSelectionTerminal {
  cols: number;
  buffer: { active: IBuffer };
  getSelectionPosition(): IBufferRange | undefined;
  select(column: number, row: number, length: number): void;
  clearSelection(): void;
  input(data: string): void;
  onSelectionChange(listener: () => void): { dispose(): void };
  onRender(listener: () => void): { dispose(): void };
}

interface PromptLine {
  text: string;
  start: IBufferCellPosition;
  end: IBufferCellPosition;
  cursor: number;
  offsets: Map<number, number>;
  /** Found through an input marker, so its exact extent is known. */
  anchored: boolean;
}

const segmenter = typeof Intl.Segmenter === "function"
  ? new Intl.Segmenter(undefined, { granularity: "grapheme" }) : null;
const graphemes = (text: string) => [...segmenter!.segment(text)].length;

const PROMPT_MARKER = /^ *[❯›>] /;
/** Rows below the draft that close an editor block: blank, or a box rule. */
const CLOSING_ROW = /^\s*$|^\s*[─━═╌┄╰└]/;
/** Box-drawing and block characters: an input box's frame, never typed text. */
const FRAME = /^[\u2500-\u259f]$/;
const MAX_PROMPT_ROWS = 200;

/** Which buffer rows hold the input, and where each row's text begins. */
interface PromptRows {
  first: number;
  last: number;
  /** Column where input starts on `row`. */
  left(row: number): number;
  /** Whether the CLI (not the terminal) broke the line after `row`. */
  editorWrap: boolean;
  anchored: boolean;
}

/**
 * Only the logical input containing the live cursor is editable. A terminal
 * selection otherwise belongs to output, not to the CLI's editor.
 *
 * The input is found in one of two ways:
 *
 * - **Anchored** by an input marker (the same markers as
 *   agentic_ide/session.py, never provider names). A TUI editor (Claude Code,
 *   Codex) wraps a long draft itself: every continuation row is a separate
 *   line indented to the width of the marker, and the break character — the
 *   space at a word wrap or a typed newline — is not drawn but still costs one
 *   arrow press. Rows above the caret count only when every one of them up to
 *   the marker row is such a continuation; rows below it only when the block
 *   then closes like an editor box, so a footer, menu or permission question
 *   is never mistaken for draft text.
 * - **The caret's line** everywhere else — a shell prompt, an editor drawn in
 *   a frame (OpenCode), any CLI without a marker: the row holding the cursor
 *   plus the rows the terminal soft-wrapped it onto. A frame drawn in box
 *   characters is left out at both ends. Text before the input on that row (a
 *   shell's `PS C:\>`) may be selected too; the surplus Backspaces then land
 *   at the start of the input, where every line editor ignores them.
 */
function findRows(term: PromptSelectionTerminal, cursorRow: number): PromptRows | null {
  const buffer = term.buffer.active;
  // xterm's `getLine` does not stop at the end of the buffer: past the last
  // row it wraps around the scrollback ring and hands back old rows. A pane
  // whose whole scrollback is one long soft-wrapped line therefore never
  // yields a non-wrapped row, and an unbounded walk spins forever inside a
  // render callback — the hung window of 2026-10-02 (captured stack:
  // findRows <- readPrompt <- onRender). Every walk stops at the real end.
  const lastRow = buffer.length - 1;
  const wrappedBelow = (row: number) => row < lastRow && buffer.getLine(row + 1)?.isWrapped;
  // Claude Code draws its marker as "❯" + NO-BREAK SPACE; read it as a space.
  const rowText = (row: number) =>
    buffer.getLine(row)?.translateToString(false, 0, term.cols).replace(/\u00a0/g, " ");
  let first = cursorRow;
  let prefix: string | undefined;
  for (;;) {
    const line = buffer.getLine(first);
    const shown = rowText(first);
    if (!line || shown === undefined) return null;
    if (!line.isWrapped) {
      prefix = shown.match(PROMPT_MARKER)?.[0];
      if (prefix || !/^ +\S/.test(shown)) break;
    }
    if (first === 0 || cursorRow - first >= MAX_PROMPT_ROWS) break;
    first--;
  }
  if (prefix) {
    const indent = " ".repeat(prefix.length);
    const continuation = (row: number) => {
      const shown = rowText(row);
      return shown !== undefined && shown.startsWith(indent) && /\S/.test(shown[indent.length] ?? "");
    };
    let anchored = true;
    for (let row = first + 1; row <= cursorRow; row++) {
      if (!buffer.getLine(row)?.isWrapped && !continuation(row)) anchored = false;
    }
    if (anchored) {
      let last = cursorRow;
      for (let row = cursorRow + 1; row - cursorRow <= MAX_PROMPT_ROWS && row <= lastRow; row++) {
        const line = buffer.getLine(row);
        if (line?.isWrapped || (line && continuation(row))) continue;
        const closing = rowText(row);
        if (closing !== undefined && CLOSING_ROW.test(closing)) last = row - 1;
        else while (wrappedBelow(last)) last++;
        break;
      }
      const markerWidth = prefix.length;
      return {
        first, last, anchored: true, editorWrap: true,
        left: (row) => row === first || !buffer.getLine(row)?.isWrapped ? markerWidth : 0,
      };
    }
  }

  first = cursorRow;
  while (first > 0 && buffer.getLine(first)?.isWrapped) first--;
  let last = cursorRow;
  while (wrappedBelow(last)) last++;
  const head = buffer.getLine(first);
  if (!head) return null;
  let frame = 0;
  for (; frame < term.cols; frame++) {
    const chars = head.getCell(frame)?.getChars() ?? "";
    if (chars.trim() && !FRAME.test(chars)) break;
  }
  return {
    first, last, anchored: false, editorWrap: false,
    left: (row) => (row === first ? frame : 0),
  };
}

function readPrompt(term: PromptSelectionTerminal): PromptLine | null {
  const buffer = term.buffer.active;
  if (!segmenter) return null;
  const cursorRow = buffer.baseY + buffer.cursorY;
  const rows = findRows(term, cursorRow);
  if (!rows) return null;
  const { first, last } = rows;

  const offsets = new Map<number, number>();
  let text = "";
  const start = { x: rows.left(first), y: first };
  let end = start;
  for (let row = first; row <= last; row++) {
    const line = buffer.getLine(row);
    if (!line) return null;
    const left = rows.left(row);
    const softWrapped = row < last && buffer.getLine(row + 1)?.isWrapped;
    // Keep typed spaces through the caret, but omit empty cells after input
    // and a frame closing the row.
    let right = term.cols;
    if (!softWrapped) {
      right = left;
      for (let col = left; col < term.cols; col++) {
        const cell = line.getCell(col);
        const chars = cell?.getChars() ?? "";
        if (cell && chars.trim() && !FRAME.test(chars)) right = col + cell.getWidth();
      }
      if (row === cursorRow) right = Math.max(right, buffer.cursorX);
    }
    for (let col = left; col < right; col++) {
      const cell = line.getCell(col);
      if (!cell || cell.getWidth() === 0) continue;
      offsets.set(row * term.cols + col, text.length);
      text += cell.getChars() || " ";
    }
    offsets.set(row * term.cols + right, text.length);
    // The editor's own wrap swallowed one break character here, unless a
    // single word filled the row and was cut without one.
    if (rows.editorWrap && row < last && !softWrapped && right < term.cols) text += " ";
    end = { x: right, y: row };
  }
  const cursor = offsets.get(cursorRow * term.cols + buffer.cursorX);
  // Collapsed paste/attachment tokens have editor-specific cursor widths.
  // Sending one Backspace per displayed letter could erase adjacent input.
  if (cursor === undefined || /\[(?:Pasted|Image|Attachment)\b/i.test(text)) return null;
  return { text, start, end, cursor, offsets, anchored: rows.anchored };
}

const samePrompt = (a: PromptLine | null, b: PromptLine | null) =>
  a !== null && b !== null && a.text === b.text && a.cursor === b.cursor &&
  a.start.x === b.start.x && a.start.y === b.start.y &&
  a.end.x === b.end.x && a.end.y === b.end.y;

/**
 * Turn mouse selection + Backspace/Delete into editor cursor moves and deletes.
 * Never erase the rendered buffer: the CLI owns the draft and must redraw it.
 */
export function installPromptSelectionBridge(
  term: PromptSelectionTerminal,
  addKeyHandler: (handler: (event: KeyboardEvent) => boolean) => () => void,
  isMac: boolean,
): () => void {
  let selectedPrompt: PromptLine | null = null;
  // A TUI moves the parser cursor while repainting. Selection belongs to the
  // last presented frame, not to that temporary drawing cursor.
  let paintedPrompt = readPrompt(term);
  let pending: { prompt: PromptLine; selection: IBufferRange } | null = null;
  let pendingTimer: ReturnType<typeof setTimeout> | undefined;
  const cancelPending = () => {
    pending = null;
    if (pendingTimer !== undefined) clearTimeout(pendingTimer);
    pendingTimer = undefined;
  };
  const selectionListener = term.onSelectionChange(() => {
    cancelPending();
    selectedPrompt = term.getSelectionPosition() ? paintedPrompt : null;
  });
  const erase = (prompt: PromptLine, selection: IBufferRange) => {
    const key = (pos: IBufferCellPosition) => pos.y * term.cols + pos.x;
    if (selection.start.y < prompt.start.y ||
        key(selection.end) > (prompt.end.y + 1) * term.cols) return false;
    // A drag may begin in a continuation row's indent or end in the empty
    // cells after a row's text; snap both ends onto the draft.
    const cells = [...prompt.offsets.keys()];
    const from = Math.max(key(selection.start), key(prompt.start));
    const to = Math.min(key(selection.end), key(prompt.end));
    const startCell = cells.find((cell) => cell >= from);
    const endCell = cells.filter((cell) => cell <= to).pop();
    const start = startCell === undefined ? undefined : prompt.offsets.get(startCell);
    const end = endCell === undefined ? undefined : prompt.offsets.get(endCell);
    if (start === undefined || end === undefined || start >= end) return false;
    // A cell boundary can be inside an emoji composed of several code points.
    const boundaries = new Set([0, ...[...segmenter!.segment(prompt.text)].map((s) => s.index + s.segment.length)]);
    if (![start, end, prompt.cursor].every((offset) => boundaries.has(offset))) return false;
    const move = end < prompt.cursor
      ? "\x1b[D".repeat(graphemes(prompt.text.slice(end, prompt.cursor)))
      : "\x1b[C".repeat(graphemes(prompt.text.slice(prompt.cursor, end)));
    const backspaces = "\x7f".repeat(graphemes(prompt.text.slice(start, end)));
    term.clearSelection();
    selectedPrompt = null;
    // One input frame, no Enter, no Ctrl+C and no clipboard round trip.
    term.input(move + backspaces);
    return true;
  };
  const renderListener = term.onRender(() => {
    paintedPrompt = readPrompt(term);
    if (!pending || !paintedPrompt) return;
    const request = pending;
    const selection = term.getSelectionPosition();
    cancelPending();
    if (samePrompt(request.prompt, paintedPrompt) && selection &&
        selection.start.x === request.selection.start.x && selection.start.y === request.selection.start.y &&
        selection.end.x === request.selection.end.x && selection.end.y === request.selection.end.y) {
      erase(paintedPrompt, selection);
    }
  });
  const removeKeyHandler = addKeyHandler((event) => {
    if (event.type !== "keydown") return true;
    cancelPending();
    if (event.isComposing || event.altKey) return true;
    const selectAll = event.key.toLowerCase() === "a" && !event.shiftKey &&
      (isMac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey);
    if (selectAll) {
      const prompt = paintedPrompt;
      // Without a marker the input's start is a guess, and shells use this
      // chord for "start of line".
      if (!prompt?.anchored) return true;
      event.preventDefault();
      const { start, end } = prompt;
      term.select(start.x, start.y, (end.y - start.y) * term.cols + end.x - start.x);
      selectedPrompt = prompt;
      return false;
    }
    if (event.ctrlKey || event.metaKey || !["Backspace", "Delete"].includes(event.key)) return true;
    const selection = term.getSelectionPosition();
    if (!selection) return true;
    // A selected output line must never delete unrelated prompt text.
    event.preventDefault();
    const prompt = readPrompt(term);
    // A selection made while no input was recognisable still counts once the
    // screen has settled: what is highlighted now is what the user sees.
    const selected = selectedPrompt ?? (samePrompt(prompt, paintedPrompt) ? prompt : null);
    if (prompt && samePrompt(selected, prompt)) {
      if (!erase(prompt, selection)) term.clearSelection();
      return false;
    }
    // Wait for one completed frame instead of silently discarding Delete in
    // the middle of a synchronized repaint. Never retry a changed draft.
    if (selected && samePrompt(selected, paintedPrompt)) {
      pending = { prompt: selected, selection };
      pendingTimer = setTimeout(cancelPending, 1_500);
      return false;
    }
    // Nothing editable is selected: drop the highlight so the next press
    // edits normally instead of being swallowed again.
    term.clearSelection();
    return false;
  });
  return () => {
    cancelPending();
    selectionListener.dispose();
    renderListener.dispose();
    removeKeyHandler();
  };
}
