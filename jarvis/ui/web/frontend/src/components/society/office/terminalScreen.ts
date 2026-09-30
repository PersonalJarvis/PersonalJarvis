/**
 * A coding agent's desk monitor, drawn like a real terminal on a 2D canvas.
 *
 * Pure drawing and layout: the caller owns the canvas and the texture. The
 * screen text arrives with ANSI already stripped server-side; TUIs keep their
 * composer at the bottom, so the monitor shows the bottom block that fits.
 */
import type { PaneScreen } from "@/lib/paneScreensApi";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

/** Canvas size in pixels; the aspect matches the 0.66 x 0.38 m screen plane. */
export const TERMINAL_W = 768, TERMINAL_H = 442;

const TITLE_H = 40;
const PAD = 10;
/** Font size bounds: a wide TUI shrinks the text, a narrow one never blows it up. */
const MIN_FONT = 9, MAX_FONT = 15;
/** Wider terminals than this are cut on the right rather than shrunk further. */
const MAX_FIT_COLS = 110;
const MONO = "ui-monospace, 'Cascadia Mono', Menlo, Consolas, monospace";
const SANS = "system-ui, -apple-system, 'Segoe UI', sans-serif";
/** A monospace glyph is about this wide per pixel of font size. */
const CHAR_RATIO = 0.6;
const LINE_RATIO = 1.25;

export type ScreenDot = "working" | "asking" | "idle" | "error";

const DOT_COLOUR: Record<ScreenDot, string> = {
  working: "#4ade80",
  asking: "#fbbf24",
  idle: "#94a3b8",
  error: "#f87171",
};

/** The title-bar dot for a pane, the same reading the grid's badge gives. */
export function paneDot(pane: Pick<WorkspacePaneRow, "status" | "activity">): ScreenDot {
  if (pane.status === "error" || pane.activity === "failed") return "error";
  if (pane.activity === "asking") return "asking";
  if (pane.status === "pending" || pane.activity === "working" || pane.activity === "starting") return "working";
  return "idle";
}

/** Does `next` need a redraw over `prev`? The feed stamps every change with `at`. */
export function screenChanged(prev: PaneScreen | undefined, next: PaneScreen | undefined): boolean {
  if (!prev || !next) return prev !== next;
  if (prev.at !== next.at) return true;
  const [a, b] = [prev.cursor, next.cursor];
  return (a?.[0] ?? -1) !== (b?.[0] ?? -1) || (a?.[1] ?? -1) !== (b?.[1] ?? -1);
}

export interface TerminalLayout {
  font: number;
  lineH: number;
  /** Columns that fit on one row. */
  cols: number;
  /** Rows that fit below the title bar. */
  fit: number;
}

/** Font and grid for a terminal `cols` wide on the monitor. Pure. */
export function terminalLayout(cols: number): TerminalLayout {
  const want = Math.max(20, Math.min(cols || 80, MAX_FIT_COLS));
  const font = Math.max(MIN_FONT, Math.min(MAX_FONT, Math.floor((TERMINAL_W - 2 * PAD) / (want * CHAR_RATIO))));
  const lineH = Math.round(font * LINE_RATIO);
  return {
    font,
    lineH,
    cols: Math.floor((TERMINAL_W - 2 * PAD) / (font * CHAR_RATIO)),
    fit: Math.floor((TERMINAL_H - TITLE_H - PAD) / lineH),
  };
}

/**
 * The block of rows to show: the last `fit` rows ending at the last
 * non-empty row (or the cursor row, if that is lower). `first` is the screen
 * row index of `rows[0]`, so the cursor can be placed. Pure.
 */
export function visibleRows(lines: readonly string[], fit: number, cursor: [number, number] | null): { rows: string[]; first: number } {
  let end = lines.length;
  while (end > 0 && !lines[end - 1].trim()) end -= 1;
  if (cursor && cursor[0] + 1 > end) end = cursor[0] + 1;
  const first = Math.max(0, end - Math.max(1, fit));
  const rows: string[] = [];
  for (let i = first; i < end; i += 1) rows.push(lines[i] ?? "");
  return { rows, first };
}

/** Cut a row to `cols` characters, marking the cut with an ellipsis. Pure. */
export function truncateRow(row: string, cols: number): string {
  return row.length > cols ? `${row.slice(0, Math.max(0, cols - 1))}…` : row;
}

function titleBar(ctx: CanvasRenderingContext2D, name: string, dot: ScreenDot): void {
  ctx.fillStyle = "#161b24";
  ctx.fillRect(0, 0, TERMINAL_W, TITLE_H);
  ctx.fillStyle = DOT_COLOUR[dot];
  ctx.beginPath(); ctx.arc(20, TITLE_H / 2, 7, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#e5e7eb";
  ctx.font = `600 19px ${SANS}`;
  ctx.textBaseline = "middle";
  ctx.fillText(truncateRow(name, 52), 36, TITLE_H / 2 + 1);
  ctx.textBaseline = "alphabetic";
}

/** Draw the terminal screen of one pane, or its screensaver when `screen` is absent. */
export function drawTerminalScreen(
  ctx: CanvasRenderingContext2D,
  agent: { name: string; dot: ScreenDot },
  screen: PaneScreen | undefined,
): void {
  if (!screen) {
    drawScreensaver(ctx, agent);
    return;
  }
  ctx.fillStyle = "#0b0e14";
  ctx.fillRect(0, 0, TERMINAL_W, TERMINAL_H);
  titleBar(ctx, agent.name, agent.dot);
  const layout = terminalLayout(screen.cols);
  const { rows, first } = visibleRows(screen.lines, layout.fit, screen.cursor);
  ctx.font = `${layout.font}px ${MONO}`;
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "#d1d5db";
  const top = TITLE_H + PAD / 2;
  const charW = layout.font * CHAR_RATIO;
  rows.forEach((row, i) => {
    if (row) ctx.fillText(truncateRow(row, layout.cols), PAD, top + (i + 1) * layout.lineH - layout.lineH * 0.22);
  });
  if (screen.cursor) {
    const [row, col] = screen.cursor;
    const at = row - first;
    if (at >= 0 && at < rows.length && col < layout.cols) {
      ctx.fillStyle = "rgba(229, 231, 235, 0.85)";
      ctx.fillRect(PAD + col * charW, top + at * layout.lineH + 1, Math.max(2, charW), layout.lineH - 2);
    }
  }
}

/** A dim face for a monitor whose agent is away or whose screen has not arrived. */
export function drawScreensaver(ctx: CanvasRenderingContext2D, agent: { name: string; dot: ScreenDot }): void {
  ctx.fillStyle = "#07090d";
  ctx.fillRect(0, 0, TERMINAL_W, TERMINAL_H);
  ctx.fillStyle = "rgba(148, 163, 184, 0.35)";
  ctx.font = `600 22px ${SANS}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(truncateRow(agent.name, 40), TERMINAL_W / 2, TERMINAL_H / 2 - 18);
  ctx.font = `28px ${MONO}`;
  ctx.fillStyle = agent.dot === "error" ? "rgba(248, 113, 113, 0.45)" : "rgba(148, 163, 184, 0.3)";
  ctx.fillText(">_", TERMINAL_W / 2, TERMINAL_H / 2 + 22);
  ctx.textAlign = "start";
  ctx.textBaseline = "alphabetic";
}
