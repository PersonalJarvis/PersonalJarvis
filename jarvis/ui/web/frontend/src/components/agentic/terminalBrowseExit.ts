import type { Terminal } from "@xterm/xterm";

type BrowseTerminal = Pick<Terminal,
  "rows" | "buffer" | "input" | "focus" | "clearSelection" | "onRender"
>;

// Match the live navigation footer, never just the word in transcript output.
const BROWSE_FOOTER = /^\s*Browsing\s+·\s+↑↓\/jk\s+←→\/hl\s+·\s+↵ rewind\s+·\s+esc\s*$/;

/** The dormant composer above a confirmed transcript-navigation footer. */
function browsePromptRow(term: BrowseTerminal): number | null {
  const buffer = term.buffer.active;
  if (buffer.viewportY !== buffer.baseY) return null;
  const line = (row: number) => {
    const index = buffer.baseY + row;
    return index >= 0 && index < buffer.length
      ? buffer.getLine(index)?.translateToString(true) ?? "" : "";
  };
  let footer = term.rows - 1;
  while (footer >= term.rows - 3 && footer >= 0 && !line(footer).trim()) footer--;
  if (footer < 0 || !BROWSE_FOOTER.test(line(footer))) return null;
  // The observed composer is three rows above the footer. Bound the search
  // to its immediate neighborhood so transcript prompts cannot become input.
  for (let row = footer - 1; row >= Math.max(0, footer - 5); row--) {
    if (/^\s*› /u.test(line(row))) return row;
  }
  return null;
}

/**
 * Clicking the dormant composer leaves transcript browsing, like Escape.
 * A plain drag still selects text; output clicks and keyboard navigation keep
 * their existing meaning. No provider identity, polling or synthetic submit.
 */
export function installBrowseExit(container: HTMLElement, term: BrowseTerminal): () => void {
  let press: { id: number; x: number; y: number; row: number } | null = null;
  let awaitingExit = false;
  const plain = (event: PointerEvent) => event.button === 0 &&
    !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey;
  const rowAt = (event: PointerEvent): number | null => {
    const screen = container.querySelector(".xterm-screen");
    if (!screen || !(event.target instanceof Node) || !screen.contains(event.target)) return null;
    const box = screen.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0 || event.clientX < box.left ||
        event.clientX >= box.right || event.clientY < box.top || event.clientY >= box.bottom) return null;
    return Math.floor((event.clientY - box.top) * term.rows / box.height);
  };
  const onDown = (event: PointerEvent) => {
    press = null;
    if (awaitingExit || !plain(event) || !event.isPrimary) return;
    const row = browsePromptRow(term);
    if (row !== null && rowAt(event) === row) {
      press = { id: event.pointerId, x: event.clientX, y: event.clientY, row };
    }
  };
  const onMove = (event: PointerEvent) => {
    if (press && press.id === event.pointerId &&
        Math.hypot(event.clientX - press.x, event.clientY - press.y) > 4) press = null;
  };
  const onUp = (event: PointerEvent) => {
    if (!press || press.id !== event.pointerId) return;
    const start = press;
    press = null;
    if (!plain(event) || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 4 ||
        rowAt(event) !== start.row || browsePromptRow(term) !== start.row) return;
    // A second click before the redraw must not send another Escape: outside
    // browsing that could interrupt work or open the navigation mode again.
    awaitingExit = true;
    term.clearSelection();
    term.focus();
    term.input("\x1b");
  };
  const onCancel = () => { press = null; };
  const render = term.onRender(() => {
    if (awaitingExit && browsePromptRow(term) === null) awaitingExit = false;
  });
  const owner = container.ownerDocument;
  container.addEventListener("pointerdown", onDown, true);
  owner.addEventListener("pointermove", onMove, true);
  owner.addEventListener("pointerup", onUp, true);
  owner.addEventListener("pointercancel", onCancel, true);
  owner.defaultView?.addEventListener("blur", onCancel);
  return () => {
    container.removeEventListener("pointerdown", onDown, true);
    owner.removeEventListener("pointermove", onMove, true);
    owner.removeEventListener("pointerup", onUp, true);
    owner.removeEventListener("pointercancel", onCancel, true);
    owner.defaultView?.removeEventListener("blur", onCancel);
    render.dispose();
  };
}
