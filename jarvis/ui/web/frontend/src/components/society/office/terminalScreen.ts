/**
 * A coding agent's desk monitor, drawn the way the Agentic IDE draws a pane.
 *
 * The same title bar (status dot, the CLI's mark, the pane's title, what it is
 * doing and for how long, the quiet action icons on the right) over the same
 * terminal: the pane's own font, weight, ground and ink from the IDE's
 * appearance tables, so a light IDE gets light monitors and a dark one dark.
 * The font is fitted to the terminal's column count, the way the IDE fits its
 * pane, so a narrow TUI fills the monitor instead of hugging its left edge.
 *
 * Pure drawing and layout: the caller owns the canvas, the texture and the
 * logo bitmap. The screen text arrives with ANSI already stripped server-side
 * (the screen feed replays layout, not colour), so the rows are drawn in the
 * pane's ink; only the TUI's structure is toned — its rules and the status
 * bar under the composer read muted, as the CLIs draw them. TUIs keep their
 * composer at the bottom, so the monitor shows the bottom block that fits.
 */
import type { PaneScreen } from "@/lib/paneScreensApi";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { TERMINAL_FONT_STACK, TERMINAL_FONT_WEIGHT } from "@/lib/terminalFont";
import { PANE_BRAND, PANE_CHROME, themeFor, type TerminalAppearance } from "@/components/agentic/terminalThemes";

/** Canvas size in pixels; the aspect matches the 0.66 x 0.38 m screen plane. */
export const TERMINAL_W = 1024, TERMINAL_H = 590;

/** The title bar: the IDE's 36 px header, scaled to the monitor. */
const TITLE_H = 56;
const PAD = 16;
/** Font size bounds: a wide TUI shrinks the text, a narrow one never blows it up. */
const MIN_FONT = 12, MAX_FONT = 40;
/** Wider terminals than this are cut on the right rather than shrunk further. */
const MAX_FIT_COLS = 120;
const SANS = "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif";
/** A monospace glyph is about this wide per pixel of font size. */
const CHAR_RATIO = 0.6;
const LINE_RATIO = 1.2;
/** The header's action icons, right to left: close, add, maximize, more. */
const ICONS = ["×", "+", "⤢", "⋯"];
const ICON_STEP = 40;

export type ScreenDot = "working" | "asking" | "idle" | "error";

/** Every colour a monitor uses, resolved for one pane appearance. */
export interface MonitorPalette {
  ground: string;
  rule: string;
  ink: string;
  inkMuted: string;
  inkFaint: string;
  /** The terminal's own foreground and cursor. */
  text: string;
  cursor: string;
  dot: Record<ScreenDot, string>;
}

/** The pane's opaque ground: PANE_SOLID without its alpha, since a monitor has nothing behind it. */
const GROUND: Record<TerminalAppearance, string> = { light: "#fcfbf8", dark: "#121212" };

/** The IDE's pane colours for `appearance`. Pure. */
export function monitorPalette(appearance: TerminalAppearance): MonitorPalette {
  const brand = PANE_BRAND[appearance], chrome = PANE_CHROME[appearance], ansi = themeFor(appearance);
  return {
    ground: GROUND[appearance],
    rule: chrome.border,
    ink: brand.ink,
    inkMuted: brand.inkMuted,
    inkFaint: brand.inkFaint,
    text: ansi.foreground ?? brand.ink,
    cursor: ansi.cursor ?? brand.ink,
    // The header dot reads like the IDE's: live green, asking yellow, failed red, idle faint.
    dot: { working: ansi.green ?? "#4ade80", asking: ansi.yellow ?? "#fbbf24", error: ansi.red ?? "#f87171", idle: brand.inkFaint },
  };
}

/** What the title bar says about the pane. */
export interface MonitorFace {
  /** The pane's title, as its IDE header shows it. */
  title: string;
  /** What it is doing and for how long ("Working · 3 min"); may be empty. */
  meta: string;
  dot: ScreenDot;
  /** The CLI's mark, already tinted for the ground; null draws the terminal glyph. */
  mark: CanvasImageSource | null;
  palette: MonitorPalette;
}

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

/** Font and grid for a terminal `cols` wide on the monitor: fitted to the width, like the IDE's pane. Pure. */
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

/** A row the TUI draws as a horizontal rule (the composer's frame). Pure. */
export function isRuleRow(row: string): boolean {
  const text = row.trim();
  return text.length >= 8 && /^[─━═╌┄-]+$/.test(text);
}

/**
 * How each shown row is toned: "rule" for the TUI's frame lines, "status" for
 * the bar under the composer (everything after the last rule), "text" for the
 * rest. Only a rule in the lower half starts a status bar, so a separator in
 * the middle of the output never mutes the answer under it. Pure.
 */
export function rowTones(rows: readonly string[]): ("text" | "rule" | "status")[] {
  let last = -1;
  rows.forEach((row, i) => { if (isRuleRow(row)) last = i; });
  const statusFrom = last >= rows.length / 2 ? last + 1 : rows.length;
  return rows.map((row, i) => (isRuleRow(row) ? "rule" : i >= statusFrom ? "status" : "text"));
}

/** `text` cut with an ellipsis so it fits `max` pixels in the current font. */
function fitText(ctx: CanvasRenderingContext2D, text: string, max: number): string {
  if (max <= 0) return "";
  if (ctx.measureText(text).width <= max) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (ctx.measureText(`${text.slice(0, mid)}…`).width <= max) lo = mid; else hi = mid - 1;
  }
  return `${text.slice(0, lo)}…`;
}

function mark(ctx: CanvasRenderingContext2D, face: MonitorFace, x: number, size: number): void {
  const y = (TITLE_H - size) / 2;
  if (face.mark) {
    ctx.drawImage(face.mark, x, y, size, size);
    return;
  }
  ctx.fillStyle = face.palette.inkMuted;
  ctx.font = `${TERMINAL_FONT_WEIGHT} ${Math.round(size * 0.8)}px ${TERMINAL_FONT_STACK}`;
  ctx.fillText(">_", x, TITLE_H / 2 + 1);
}

/** The IDE's pane header: dot, mark, title, what it is doing, and the action icons. */
function titleBar(ctx: CanvasRenderingContext2D, face: MonitorFace): void {
  const { palette } = face;
  ctx.fillStyle = palette.ground;
  ctx.fillRect(0, 0, TERMINAL_W, TITLE_H);
  ctx.fillStyle = palette.rule;
  ctx.fillRect(0, TITLE_H - 2, TERMINAL_W, 2);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillStyle = palette.dot[face.dot];
  ctx.beginPath(); ctx.arc(24, TITLE_H / 2, 5, 0, Math.PI * 2); ctx.fill();
  mark(ctx, face, 40, 26);
  // Icons first, so the title knows where it has to stop.
  ctx.fillStyle = palette.inkMuted;
  ctx.font = `400 26px ${SANS}`;
  ctx.textAlign = "center";
  ICONS.forEach((icon, i) => ctx.fillText(icon, TERMINAL_W - 26 - i * ICON_STEP, TITLE_H / 2 + 1));
  ctx.textAlign = "left";
  const left = 80, right = TERMINAL_W - 26 - ICONS.length * ICON_STEP;
  ctx.font = `500 22px ${SANS}`;
  // The title yields to the meta text up to 40 % of the bar, never more.
  const metaW = face.meta ? Math.min(ctx.measureText(face.meta).width, (right - left) * 0.4) : 0;
  const title = fitText(ctx, face.title, right - left - (metaW ? metaW + 16 : 0));
  ctx.fillStyle = palette.ink;
  ctx.fillText(title, left, TITLE_H / 2 + 1);
  if (face.meta) {
    const titleW = ctx.measureText(title).width;
    ctx.font = `400 19px ${SANS}`;
    ctx.fillStyle = palette.inkMuted;
    ctx.fillText(fitText(ctx, face.meta, right - left - titleW - 16), left + titleW + 14, TITLE_H / 2 + 2);
  }
  ctx.textBaseline = "alphabetic";
}

/** Draw the terminal screen of one pane, or its screensaver when `screen` is absent. */
export function drawTerminalScreen(ctx: CanvasRenderingContext2D, face: MonitorFace, screen: PaneScreen | undefined): void {
  if (!screen) {
    drawScreensaver(ctx, face);
    return;
  }
  const { palette } = face;
  ctx.fillStyle = palette.ground;
  ctx.fillRect(0, 0, TERMINAL_W, TERMINAL_H);
  titleBar(ctx, face);
  const layout = terminalLayout(screen.cols);
  const { rows, first } = visibleRows(screen.lines, layout.fit, screen.cursor);
  const tones = rowTones(rows);
  ctx.font = `${TERMINAL_FONT_WEIGHT} ${layout.font}px ${TERMINAL_FONT_STACK}`;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  const top = TITLE_H + PAD / 2;
  const charW = layout.font * CHAR_RATIO;
  rows.forEach((row, i) => {
    if (!row) return;
    ctx.fillStyle = tones[i] === "rule" ? palette.rule : tones[i] === "status" ? palette.inkMuted : palette.text;
    ctx.fillText(truncateRow(row, layout.cols), PAD, top + (i + 1) * layout.lineH - layout.lineH * 0.24);
  });
  if (screen.cursor) {
    const [row, col] = screen.cursor;
    const at = row - first;
    if (at >= 0 && at < rows.length && col < layout.cols) {
      ctx.fillStyle = palette.cursor;
      ctx.fillRect(PAD + col * charW, top + at * layout.lineH + 2, Math.max(2, charW), layout.lineH - 4);
    }
  }
}

/** A monitor whose agent is away, or whose screen has not arrived: the pane's header over a quiet ground. */
export function drawScreensaver(ctx: CanvasRenderingContext2D, face: MonitorFace): void {
  const { palette } = face;
  ctx.fillStyle = palette.ground;
  ctx.fillRect(0, 0, TERMINAL_W, TERMINAL_H);
  titleBar(ctx, face);
  ctx.fillStyle = face.dot === "error" ? palette.dot.error : palette.inkFaint;
  ctx.font = `${TERMINAL_FONT_WEIGHT} 44px ${TERMINAL_FONT_STACK}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(">_", TERMINAL_W / 2, (TERMINAL_H + TITLE_H) / 2);
  ctx.textAlign = "start";
  ctx.textBaseline = "alphabetic";
}
