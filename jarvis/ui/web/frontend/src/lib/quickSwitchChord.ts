/**
 * The quick switcher's chord, kept apart from `./quickSwitch` on purpose: the
 * app shell checks every keystroke against it, so it ships in the startup
 * chunk — and `./quickSwitch` carries all three locale dictionaries, which
 * must stay in the switcher's own lazy chunk.
 */
export interface QuickSwitchKeyEvent {
  key: string;
  code: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
}

/**
 * Ctrl+Space, on every platform.
 *
 * The closest chord to the Mac's ⌘+Space that the app can actually receive:
 * ⌘+Space itself belongs to the OS launcher and never reaches the window, and
 * Ctrl/⌘+K is already the Wiki's and the Docs' own search. Ctrl+Space means
 * nothing to a coding agent's TUI (it would send a NUL byte) and is not one of
 * the voice defaults — those all carry Alt, which disqualifies here.
 *
 * `code` is checked as well as `key` because some layouts and IMEs report the
 * space bar's `key` as something other than " " while a modifier is held.
 */
export function isQuickSwitchChord(event: QuickSwitchKeyEvent): boolean {
  if (!event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return false;
  return event.code === "Space" || event.key === " ";
}
