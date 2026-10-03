/**
 * Whole-app zoom: which keys zoom the window in, out and back to 100 %, and
 * which zoom level comes next.
 *
 * ## Why the keys are read as characters, not as key positions
 *
 * Every other in-app chord (the quick switcher, the voice keys) is matched by
 * physical key position, `event.code`. That cannot work for zoom: `+` sits on
 * its own key on a German keyboard (the one US calls `]`) but is Shift+`=` on
 * a US one, and `-` is US `/` on a German board. A chord saved as a position
 * would mean a different key on the next keyboard. So `+` and `-` are matched
 * by the CHARACTER the key types (`event.key`), with both spellings of each
 * step accepted the way browsers do:
 *
 * * zoom in: `+` (any layout), `=` on the key US calls Equal, keypad `+`;
 * * zoom out: `-` (any layout), `_` on the key US calls Minus, keypad `-`;
 * * Shift is ignored for `+` and `-`, because typing `+` needs Shift on many
 *   layouts and not on others.
 *
 * Letters, digits and F-keys keep the position rule of the other in-app chords,
 * so a recorded Ctrl+Z means the same key it did when it was recorded.
 *
 * ## AltGr
 *
 * Windows reports AltGr as Ctrl+Alt. AltGr+`+` is `~` on a German keyboard, a
 * character people type all day; it counts as Alt here and so never matches a
 * plain Ctrl chord.
 *
 * Kept free of React and of the recorder modules: the app shell checks every
 * keystroke against it, so it ships in the startup chunk.
 */
import { hostChordPlatform, parseChord, type ChordPlatform } from "@/lib/quickSwitchChord";

export type AppZoomIntent = "in" | "out" | "reset";

export const APP_ZOOM_INTENTS: readonly AppZoomIntent[] = ["in", "out", "reset"];

export type AppZoomBindings = Record<AppZoomIntent, string>;

/** The zoom ladder: every step lands on a familiar number. */
export const APP_ZOOM_LEVELS: readonly number[] = [
  0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3,
];

export function defaultAppZoomBindings(platform: ChordPlatform = hostChordPlatform()): AppZoomBindings {
  const mod = platform === "mac" ? "cmd" : "ctrl";
  return { in: `${mod}+plus`, out: `${mod}+minus`, reset: `${mod}+0` };
}

/** Snap any stored value onto the ladder, so a hand-edited level still steps cleanly. */
export function snapAppZoom(level: number): number {
  if (!Number.isFinite(level)) return 1;
  return APP_ZOOM_LEVELS.reduce((best, step) =>
    Math.abs(step - level) < Math.abs(best - level) ? step : best,
  );
}

export function nextAppZoom(level: number, intent: AppZoomIntent): number {
  if (intent === "reset") return 1;
  const index = APP_ZOOM_LEVELS.indexOf(snapAppZoom(level));
  const next = intent === "in" ? index + 1 : index - 1;
  return APP_ZOOM_LEVELS[Math.min(APP_ZOOM_LEVELS.length - 1, Math.max(0, next))];
}

/** Keys whose identity is the character they type, not their position. */
const CHARACTER_KEYS = new Set(["plus", "minus"]);

const NAMED_CODES: Record<string, string> = {
  Space: "space",
  ArrowUp: "up",
  ArrowDown: "down",
  ArrowLeft: "left",
  ArrowRight: "right",
  Insert: "insert",
  Delete: "delete",
  Home: "home",
  End: "end",
  PageUp: "page_up",
  PageDown: "page_down",
  Enter: "enter",
  NumpadEnter: "enter",
  Tab: "tab",
  Backspace: "backspace",
};

export interface AppZoomKeyEvent {
  key: string;
  code: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  getModifierState?(key: string): boolean;
}

/**
 * The token for the key this event pressed, or null for modifiers and keys a
 * zoom chord cannot use. Numpad digits count as the digit, so keypad 0 resets
 * whether NumLock is on or not.
 */
export function appZoomKeyToken(event: Pick<AppZoomKeyEvent, "key" | "code">): string | null {
  const { key, code } = event;
  if (code === "NumpadAdd") return "plus";
  if (code === "NumpadSubtract") return "minus";
  if (/^Numpad[0-9]$/.test(code)) return code.slice(6);
  if (code === "NumpadInsert") return "0"; // keypad 0 with NumLock off
  if (key === "+" || (key === "=" && code === "Equal")) return "plus";
  if (key === "-" || (key === "_" && code === "Minus")) return "minus";
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F[0-9]{1,2}$/.test(code)) return code.toLowerCase();
  return NAMED_CODES[code] ?? null;
}

interface HeldModifiers {
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  meta: boolean;
}

function heldModifiers(event: AppZoomKeyEvent): HeldModifiers {
  const altGr = event.getModifierState?.("AltGraph") === true;
  return {
    ctrl: event.ctrlKey && !altGr,
    alt: event.altKey || altGr,
    shift: event.shiftKey,
    meta: event.metaKey,
  };
}

/** Does this keydown press exactly `combo`? Shift is free for `+` and `-`. */
export function appZoomChordMatches(event: AppZoomKeyEvent, combo: string): boolean {
  const { mods, keys } = parseChord(combo);
  if (keys.length !== 1) return false;
  const token = appZoomKeyToken(event);
  if (token === null || token !== keys[0]) return false;
  const held = heldModifiers(event);
  return (Object.keys(held) as (keyof HeldModifiers)[]).every((m) => {
    if (m === "shift" && CHARACTER_KEYS.has(token)) return true;
    return held[m] === mods.has(m);
  });
}

/** Which zoom step this keydown asks for, or null when it is none of them. */
export function appZoomIntentFor(event: AppZoomKeyEvent, bindings: AppZoomBindings): AppZoomIntent | null {
  for (const intent of APP_ZOOM_INTENTS) {
    const combo = bindings[intent];
    if (combo && appZoomChordMatches(event, combo)) return intent;
  }
  return null;
}

/**
 * The combo a recorder should store for this keydown, or null while only
 * modifiers are held. Shift is dropped for `+` and `-` (see above).
 */
export function appZoomComboFromEvent(
  event: AppZoomKeyEvent,
  platform: ChordPlatform = hostChordPlatform(),
): string | null {
  const token = appZoomKeyToken(event);
  if (token === null) return null;
  const held = heldModifiers(event);
  const mods: string[] = [];
  if (held.ctrl) mods.push("ctrl");
  if (held.alt) mods.push("alt");
  if (held.shift && !CHARACTER_KEYS.has(token)) mods.push("shift");
  if (held.meta) mods.push(platform === "mac" ? "cmd" : "win");
  return [...mods, token].join("+");
}

/** Why a recorded chord cannot zoom; null when it can. */
export type AppZoomComboProblem =
  /** No Ctrl, Alt or ⌘ (and not an F-key): it would fire while typing. */
  | "typing_key"
  /** The Windows key belongs to the desktop shell (Win+`+` is the magnifier). */
  | "os_reserved"
  /** Another zoom step already uses exactly this chord. */
  | "duplicate";

function foldedChord(combo: string): string {
  const { mods, keys } = parseChord(combo);
  return [...[...mods].sort(), ...keys].join("+");
}

export function appZoomComboProblem(
  combo: string,
  others: readonly string[],
  platform: ChordPlatform = hostChordPlatform(),
): AppZoomComboProblem | null {
  const { mods, keys } = parseChord(combo);
  const key = keys[0] ?? "";
  if (platform === "pc" && mods.has("meta")) return "os_reserved";
  const realModifier = mods.has("ctrl") || mods.has("alt") || mods.has("meta");
  if (!realModifier && !/^f[0-9]{1,2}$/.test(key)) return "typing_key";
  const mine = foldedChord(combo);
  if (others.some((other) => other && foldedChord(other) === mine)) return "duplicate";
  return null;
}

const KEY_LABEL: Record<string, string> = {
  plus: "+",
  minus: "-",
  space: "Space",
  up: "↑",
  down: "↓",
  left: "←",
  right: "→",
  page_up: "PgUp",
  page_down: "PgDn",
};

const MAC_GLYPH: Record<string, string> = { ctrl: "⌃", alt: "⌥", shift: "⇧", meta: "⌘" };
const PC_NAME: Record<string, string> = { ctrl: "Ctrl", alt: "Alt", shift: "Shift", meta: "Win" };
const MOD_ORDER = ["ctrl", "alt", "shift", "meta"] as const;

/** The chord as keycap labels ("Ctrl", "+"; "⌘", "-" on a Mac). */
export function appZoomCaps(combo: string, platform: ChordPlatform = hostChordPlatform()): string[] {
  const { mods, keys } = parseChord(combo);
  const names = platform === "mac" ? MAC_GLYPH : PC_NAME;
  const label = (token: string) =>
    KEY_LABEL[token] ?? (token.length === 1 ? token.toUpperCase() : token[0].toUpperCase() + token.slice(1));
  return [...MOD_ORDER.filter((m) => mods.has(m)).map((m) => names[m]), ...keys.map(label)];
}
