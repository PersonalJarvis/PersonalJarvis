/**
 * How N agent panes are laid out in the workspace — the ONE answer, shared by
 * the grid that renders them and the wizard that previews them.
 *
 * It used to be two answers. The grid put every pane of a row side by side
 * (the backend starts a session with all panes in row 0), while the wizard's
 * dot preview had its own formula that stacked them 3-per-line — so picking
 * "4 terminals" showed 3 above and 1 below and then opened 4 side by side. A
 * preview that contradicts the thing it previews is worse than no preview, and
 * the only durable fix is that both call the same function.
 *
 * The rule: panes stay on one line for as long as they stay readable, and wrap
 * into EVEN lines once they don't. Even, because filling the first line to the
 * cap and leaving a remainder (12 → 10 + 2) reads as a broken layout, whereas
 * 6 + 6 reads as a grid. Below the cap nothing wraps, so the common two-,
 * four-, and eight-pane workspaces keep the single row they have today.
 *
 * Note what this returns: a WIDTH, not a list of lines. The grid renders one
 * row's panes as siblings in a CSS grid and lets the browser wrap them, which
 * matters because every pane has to stay mounted — moving a pane into a new
 * parent element would tear down its WebSocket and kill the agent behind it.
 */

/**
 * Most panes that may share one line.
 *
 * Past this, panes get too narrow for their terminal content to survive — an
 * agent's output wraps into an unreadable column and the pane is there in name
 * only. Wrapping keeps every pane wide enough to actually read.
 */
export const MAX_PANES_PER_BAND = 10;

/** Anything with a grid row — the pane fields this module actually needs. */
interface Positioned {
  row: number;
}

/**
 * Panes per line for a row of ``count`` panes: the row's width in the grid.
 *
 * Also the wizard's preview width, which is the point — the dots the user sees
 * before starting are the arrangement they get.
 */
export function paneColumns(count: number, maxPerBand: number = MAX_PANES_PER_BAND): number {
  if (count <= 0) return 0;
  const cap = Math.max(1, maxPerBand);
  const bands = Math.ceil(count / cap);
  return Math.ceil(count / bands);
}

/**
 * Lines a row of ``count`` panes occupies once wrapped.
 *
 * The grid gives a wrapped row proportionally more height, so a row of 12
 * (two lines) does not get squeezed into the same band as a row of 1.
 */
export function paneLines(count: number, maxPerBand: number = MAX_PANES_PER_BAND): number {
  const width = paneColumns(count, maxPerBand);
  return width === 0 ? 0 : Math.ceil(count / width);
}

/**
 * The workspace's rows, in render order, with empty ones dropped.
 *
 * Rows come from the backend and carry the user's own splits ("split down"
 * opens a row). A gap in the row numbers — possible while a close is still
 * being packed away — must not render as a blank stripe.
 */
export function paneRows<T extends Positioned>(panes: readonly T[]): T[][] {
  const rows: T[][] = [];
  const byRow = new Map<number, T[]>();
  for (const pane of panes) {
    let row = byRow.get(pane.row);
    if (!row) {
      row = [];
      byRow.set(pane.row, row);
      rows.push(row);
    }
    row.push(pane);
  }
  return rows;
}
