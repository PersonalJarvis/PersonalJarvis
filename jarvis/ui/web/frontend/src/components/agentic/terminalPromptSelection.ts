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
}

const segmenter = typeof Intl.Segmenter === "function"
  ? new Intl.Segmenter(undefined, { granularity: "grapheme" }) : null;
const graphemes = (text: string) => [...segmenter!.segment(text)].length;

const PROMPT_MARKER = /^ *[❯›>] /;
/** Rows below the draft that close an editor block: blank, or a box rule. */
const CLOSING_ROW = /^\s*$|^\s*[─━═╌┄╰└]/;
const MAX_PROMPT_ROWS = 200;

/**
 * Only the logical input containing the live cursor is editable. A terminal
 * selection otherwise belongs to output, not to the CLI's editor. Recognise
 * the same input markers as agentic_ide/session.py, without using provider
 * names.
 *
 * A long draft spans several rows in one of two ways. The terminal's own soft
 * wrap marks rows `isWrapped` and fills them edge to edge. A TUI editor (Claude
 * Code, Codex) wraps the draft itself: every continuation row is a separate
 * line indented to the width of the marker, and the break character — the
 * space at a word wrap or a typed newline — is not drawn but still costs one
 * arrow press. Rows above the caret are only accepted as input when every one
 * of them up to the marker row is such a continuation; rows below it only
 * when the block then closes like an editor box, so a footer, menu or
 * permission question is never mistaken for draft text.
 */
function readPrompt(term: PromptSelectionTerminal): PromptLine | null {
  const buffer = term.buffer.active;
  if (!segmenter) return null;
  const rowText = (row: number) => buffer.getLine(row)?.translateToString(false, 0, term.cols);
  const cursorRow = buffer.baseY + buffer.cursorY;
  let first = cursorRow;
  let prefix: string | undefined;
  for (;;) {
    const line = buffer.getLine(first);
    const shown = rowText(first);
    if (!line || shown === undefined) return null;
    if (!line.isWrapped) {
      prefix = shown.match(PROMPT_MARKER)?.[0];
      if (prefix) break;
      if (!/^ +\S/.test(shown)) return null;
    }
    if (first === 0 || cursorRow - first >= MAX_PROMPT_ROWS) return null;
    first--;
  }
  const indent = " ".repeat(prefix.length);
  const continuation = (row: number) => {
    const shown = rowText(row);
    return shown !== undefined && shown.startsWith(indent) && /\S/.test(shown[indent.length] ?? "");
  };
  for (let row = first + 1; row <= cursorRow; row++) {
    if (!buffer.getLine(row)?.isWrapped && !continuation(row)) return null;
  }
  let last = cursorRow;
  for (let row = cursorRow + 1; row - cursorRow <= MAX_PROMPT_ROWS; row++) {
    const line = buffer.getLine(row);
    if (line?.isWrapped || (line && continuation(row))) continue;
    const closing = rowText(row);
    if (closing !== undefined && CLOSING_ROW.test(closing)) last = row - 1;
    else while (buffer.getLine(last + 1)?.isWrapped) last++;
    break;
  }

  const offsets = new Map<number, number>();
  let text = "";
  const start = { x: prefix.length, y: first };
  let end = start;
  for (let row = first; row <= last; row++) {
    const line = buffer.getLine(row);
    if (!line) return null;
    const left = row === first || !line.isWrapped ? prefix.length : 0;
    const softWrapped = row < last && buffer.getLine(row + 1)?.isWrapped;
    // Keep typed spaces through the caret, but omit empty cells after input.
    let right = term.cols;
    if (!softWrapped) {
      right = left;
      for (let col = left; col < term.cols; col++) {
        const cell = line.getCell(col);
        if (cell?.getChars().trim()) right = col + cell.getWidth();
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
    if (row < last && !softWrapped && right < term.cols) text += " ";
    end = { x: right, y: row };
  }
  const cursor = offsets.get(cursorRow * term.cols + buffer.cursorX);
  // Collapsed paste/attachment tokens have editor-specific cursor widths.
  // Sending one Backspace per displayed letter could erase adjacent input.
  if (cursor === undefined || /\[(?:Pasted|Image|Attachment)\b/i.test(text)) return null;
  return { text, start, end, cursor, offsets };
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
    return false;
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
      if (!prompt) return true;
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
    if (!selectedPrompt) return false;
    const prompt = readPrompt(term);
    if (prompt && samePrompt(selectedPrompt, prompt)) return erase(prompt, selection);
    // Wait for one completed frame instead of silently discarding Delete in
    // the middle of a synchronized repaint. Never retry a changed draft.
    if (samePrompt(selectedPrompt, paintedPrompt)) {
      pending = { prompt: selectedPrompt, selection };
      pendingTimer = setTimeout(cancelPending, 1_500);
    }
    return false;
  });
  return () => {
    cancelPending();
    selectionListener.dispose();
    renderListener.dispose();
    removeKeyHandler();
  };
}
