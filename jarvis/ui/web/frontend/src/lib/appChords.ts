/**
 * The in-app chords that used to be compiled in: the terminal text size steps,
 * the `?` shortcut overview, the Agentic IDE key menu and its command
 * palette. Each one now has a
 * default and can be recorded anew under Settings → Keyboard shortcuts.
 *
 * They are matched and recorded by CHARACTER exactly like the whole-app zoom
 * (see ./appZoom): `+`, `-` and `?` sit on different keys on a German and a US
 * keyboard, so a chord saved as a key position would mean another key on the
 * next keyboard.
 *
 * Kept free of React: the app shell and the terminal grid check keystrokes
 * against these on every press.
 */
import { appZoomComboProblem, type AppZoomComboProblem } from "@/lib/appZoom";
import { hostChordPlatform, parseChord, type ChordPlatform } from "@/lib/quickSwitchChord";

export type AppChordId =
  | "terminal_zoom_in"
  | "terminal_zoom_out"
  | "terminal_zoom_reset"
  | "shortcut_overlay"
  | "ide_menu"
  | "ide_commands";

export const APP_CHORD_IDS: readonly AppChordId[] = [
  "terminal_zoom_in",
  "terminal_zoom_out",
  "terminal_zoom_reset",
  "shortcut_overlay",
  "ide_menu",
  "ide_commands",
];

export type AppChordBindings = Record<AppChordId, string>;

/**
 * The shipped chords. The terminal steps use the platform modifier (⌘ on a
 * Mac, Ctrl elsewhere); the IDE key menu is Ctrl+B on every OS, the terminal
 * key tmux users already have in their hands. The IDE command palette is
 * Ctrl+Shift+P (⌘⇧P on a Mac), the chord editors already use for theirs.
 */
export function defaultAppChords(platform: ChordPlatform = hostChordPlatform()): AppChordBindings {
  const mod = platform === "mac" ? "cmd" : "ctrl";
  return {
    terminal_zoom_in: `${mod}+plus`,
    terminal_zoom_out: `${mod}+minus`,
    terminal_zoom_reset: `${mod}+0`,
    shortcut_overlay: "question",
    ide_menu: "ctrl+b",
    ide_commands: `${mod}+shift+p`,
  };
}

/**
 * Chords that may be a single key without Ctrl, Alt or ⌘. Only the overview:
 * its listener ignores keys typed into a text field, so a bare `?` never eats
 * a question mark out of a message.
 */
export const BARE_KEY_CHORDS: ReadonlySet<AppChordId> = new Set(["shortcut_overlay"]);

/** True when the chord holds no Ctrl, Alt or ⌘ — it must stay out of text fields. */
export function isBareChord(combo: string): boolean {
  const { mods } = parseChord(combo);
  return !mods.has("ctrl") && !mods.has("alt") && !mods.has("meta");
}

/** Why a recorded chord cannot be used for `id`; null when it can. */
export function appChordProblem(
  id: AppChordId,
  combo: string,
  others: readonly string[],
  platform: ChordPlatform = hostChordPlatform(),
): AppZoomComboProblem | null {
  const problem = appZoomComboProblem(combo, others, platform);
  if (problem === "typing_key" && BARE_KEY_CHORDS.has(id)) return null;
  return problem;
}
