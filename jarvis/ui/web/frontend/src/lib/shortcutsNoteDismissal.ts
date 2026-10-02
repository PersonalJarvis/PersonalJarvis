/**
 * "Not now" for the global-shortcuts note, remembered per surface.
 *
 * The note says what global shortcuts need from macOS (Input Monitoring) and is
 * on every page that lists a shortcut. On a Mac that has not allowed it yet that
 * is a permanent line on each of them, so each one can be closed and stays closed.
 *
 * - One key per SURFACE (the Shortcuts page, the Dictation page, ...), never per
 *   episode: closing it on the Dictation page does not hide the Shortcuts page,
 *   and a new episode does not bring a closed note back.
 * - A closed note comes back only after the shortcuts worked once: when the status
 *   reads `ready` every surface's key is cleared, so a later loss of the grant is
 *   explained again instead of staying hidden forever.
 * - Storage can fail (private window, blocked site data): every access is guarded.
 *   An unreadable store means "not closed" (the note stays useful) and the caller
 *   keeps the closing in its own state for the life of the page.
 */

/** Every surface that can show the note; clearing walks this list. */
export const SHORTCUTS_NOTE_SURFACES = [
  "shortcuts",
  "voice-shortcuts",
  "settings-keybinds",
  "dictation",
  "appshots",
] as const;

export type ShortcutsNoteSurface = (typeof SHORTCUTS_NOTE_SURFACES)[number];

const KEY_PREFIX = "jarvis.shortcuts.note.dismissed.v1.";

export function shortcutsNoteKey(surface: ShortcutsNoteSurface): string {
  return `${KEY_PREFIX}${surface}`;
}

export function isShortcutsNoteDismissed(surface: ShortcutsNoteSurface): boolean {
  try {
    return window.localStorage.getItem(shortcutsNoteKey(surface)) === "1";
  } catch {
    // Unreadable storage: show the note rather than lose the explanation.
    return false;
  }
}

export function dismissShortcutsNote(surface: ShortcutsNoteSurface): void {
  try {
    window.localStorage.setItem(shortcutsNoteKey(surface), "1");
  } catch {
    // Nothing to persist to; the note stays closed until the page is reloaded.
  }
}

/** The shortcuts work: forget every closing so a later loss of the grant is explained again. */
export function clearShortcutsNoteDismissals(): void {
  for (const surface of SHORTCUTS_NOTE_SURFACES) {
    try {
      window.localStorage.removeItem(shortcutsNoteKey(surface));
    } catch {
      // Nothing stored, nothing to clear.
    }
  }
}
