/**
 * The terminal text size the reader has chosen, and where it is kept.
 *
 * Extracted from ./AgenticGrid, which owned all of this while it was the only
 * thing that needed it. It is no longer: the workspace WIZARD has to answer
 * "how many terminals does this window fit?" before a single pane exists, and
 * that question cannot be answered without the text size — 145 px is roomy at
 * 10 px text and unusable at 20 (see `workableColumnCount` in ./layout).
 *
 * A leaf module on purpose. The wizard importing the running grid to read one
 * number would drag a terminal emulator into a screen that shows none, so the
 * number moved to where both can reach it and neither owns it.
 *
 * The three bounds mirror `jarvis/agentic_ide/ui_prefs.py`, which is what the
 * backend clamps a saved size to. They must not drift: a size the frontend
 * offers and the backend refuses comes back as a control that silently does
 * nothing.
 */

export const FONT_MIN = 10;
export const FONT_MAX = 20;
/** Where a fresh install starts, and where Ctrl/Cmd+0 lands. */
export const FONT_DEFAULT = 13;

export const FONT_KEY = "jarvis.agenticIde.terminalFontSize";

/**
 * The stored size, or `null` when there is none to read.
 *
 * `null` rather than the default, so a caller can tell "the reader has never
 * chosen" from "the reader chose 13" — the grid follows the backend's preference
 * only in the first case.
 *
 * Never throws: private mode and disabled storage both read as "no preference",
 * which is a workspace that opens rather than one that fails over a setting.
 */
export function storedFontSize(): number | null {
  try {
    const raw = window.localStorage.getItem(FONT_KEY);
    if (raw === null) return null;
    const size = Number.parseInt(raw, 10);
    return Number.isFinite(size) && size >= FONT_MIN && size <= FONT_MAX ? size : null;
  } catch {
    return null;
  }
}

/** The size a pane will actually open at — the stored one, or the default. */
export function paneFontSize(): number {
  return storedFontSize() ?? FONT_DEFAULT;
}
