/**
 * The quick switcher's chord: which keys open it, and which keys never could.
 *
 * Kept apart from `./quickSwitch` on purpose: the app shell checks every
 * keystroke against it, so it ships in the startup chunk — and `./quickSwitch`
 * carries all three locale dictionaries, which must stay in the switcher's own
 * lazy chunk. For the same reason it does not import `@/hooks/useHotkey` (the
 * recorder and the keyboard layout table): the few token rules it needs are
 * restated here and pinned to the recorder's by `quickSwitchChord.test.ts`.
 *
 * Combos use the recorder's spelling ("ctrl+space", "alt+space", "cmd+shift+k"),
 * because the Settings row that edits this chord IS the voice keybind recorder.
 *
 * ## Why the default differs on a Mac
 *
 * The switcher only ever sees keys the operating system lets through to the
 * window. On a Mac, ⌘+Space is Spotlight and ⌃+Space switches the input source,
 * so neither would ever arrive — the switcher would simply look broken. ⌥+Space
 * is not reserved by the system, so it gets through. Every
 * other system gets Ctrl+Space. The keys an OS is known to swallow are refused
 * outright when someone records them (`quickSwitchComboProblem`), with a reason.
 */

export type ChordPlatform = "mac" | "pc";

export function hostChordPlatform(): ChordPlatform {
  if (typeof navigator === "undefined") return "pc";
  const hint = `${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`;
  return /mac|iphone|ipad/i.test(hint) ? "mac" : "pc";
}

export function defaultQuickSwitchCombo(platform: ChordPlatform = hostChordPlatform()): string {
  return platform === "mac" ? "alt+space" : "ctrl+space";
}

/** Every spelling the recorder (or a hand edit) may use, folded to four names. */
const MODIFIER_FOLD: Record<string, "ctrl" | "alt" | "shift" | "meta"> = {
  ctrl: "ctrl",
  control: "ctrl",
  right_ctrl: "ctrl",
  right_control: "ctrl",
  alt: "alt",
  right_alt: "alt",
  left_alt: "alt",
  altgr: "alt",
  shift: "shift",
  win: "meta",
  window: "meta",
  super: "meta",
  meta: "meta",
  cmd: "meta",
  command: "meta",
};

/** The recorder's names for keys whose `event.code` is not a letter, digit or F-key. */
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

/** The recorder's token for a physical key, or null for modifiers and the rest. */
export function chordKeyToken(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F[0-9]{1,2}$/.test(code)) return code.toLowerCase();
  if (/^Numpad[0-9]$/.test(code)) return `numpad_${code.slice(6)}`;
  return NAMED_CODES[code] ?? null;
}

export interface ParsedChord {
  mods: Set<"ctrl" | "alt" | "shift" | "meta">;
  keys: string[];
}

export function parseChord(combo: string): ParsedChord {
  const mods = new Set<"ctrl" | "alt" | "shift" | "meta">();
  const keys: string[] = [];
  for (const raw of (combo ?? "").split("+")) {
    const token = raw.trim().toLowerCase();
    if (!token) continue;
    const mod = MODIFIER_FOLD[token];
    if (mod) mods.add(mod);
    else keys.push(token);
  }
  return { mods, keys };
}

export interface ChordKeyEvent {
  code: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  getModifierState?(key: string): boolean;
}

/**
 * Does this keydown press exactly `combo`? Exactly: Ctrl+Shift+Space does not
 * fire a Ctrl+Space binding, so a chord can be kept free for something else.
 * AltGr reports a phantom Ctrl on Windows; like the recorder, it counts as Alt.
 */
export function eventMatchesChord(event: ChordKeyEvent, combo: string): boolean {
  const { mods, keys } = parseChord(combo);
  if (keys.length !== 1) return false;
  if (chordKeyToken(event.code) !== keys[0]) return false;
  const altGr = event.getModifierState?.("AltGraph") === true;
  const held = {
    ctrl: event.ctrlKey && !altGr,
    alt: event.altKey || altGr,
    shift: event.shiftKey,
    meta: event.metaKey,
  };
  return (Object.keys(held) as (keyof typeof held)[]).every((m) => held[m] === mods.has(m));
}

/** Why a chord cannot open the switcher; null when it can. */
export type ChordProblem =
  /** Not exactly one real key (modifiers only, a mouse button, a key pair). */
  | "one_key"
  /** A typing key with no Ctrl/Alt/⌘ — it would fire on every keystroke. */
  | "typing_key"
  /** The operating system takes it before the window ever sees it. */
  | "os_reserved"
  /** Already means something inside the app (copy, paste, Wiki search …). */
  | "app_reserved";

/** Combos each OS keeps for itself, in folded spelling ("meta+space"). */
const OS_RESERVED: Record<ChordPlatform, readonly string[]> = {
  mac: [
    "meta+space", // Spotlight
    "alt+meta+space", // Finder search
    "ctrl+space", // previous input source
    "alt+ctrl+space", // next input source
    "meta+tab",
    "meta+q",
    "meta+h",
    "meta+m",
    "meta+w",
  ],
  pc: [
    "alt+space", // the window's system menu
    "alt+tab",
    "alt+f4",
    "alt+ctrl+delete",
    "ctrl+shift+escape",
  ],
};

/** Chords the app already gives a meaning to, on every platform. */
const APP_RESERVED = ["k", "c", "v", "x", "a", "z", "y", "0"];

function foldedChord({ mods, keys }: ParsedChord): string {
  return [...[...mods].sort(), ...keys].join("+");
}

export function quickSwitchComboProblem(
  combo: string,
  platform: ChordPlatform = hostChordPlatform(),
): ChordProblem | null {
  const parsed = parseChord(combo);
  const { mods, keys } = parsed;
  if (keys.length !== 1 || keys[0].startsWith("mouse_")) return "one_key";
  const key = keys[0];
  // The Windows key and Super belong to the desktop shell on a PC; the shell
  // swallows most of those chords before any window sees them.
  if (platform === "pc" && mods.has("meta")) return "os_reserved";
  if (OS_RESERVED[platform].includes(foldedChord(parsed))) return "os_reserved";
  const realModifier = mods.has("ctrl") || mods.has("alt") || mods.has("meta");
  if (!realModifier && !/^f[0-9]{1,2}$/.test(key)) return "typing_key";
  // Ctrl/⌘ + a letter the editors, terminals or the Wiki/Docs search use.
  const primaryOnly =
    mods.size === 1 && (mods.has("ctrl") || mods.has("meta")) && APP_RESERVED.includes(key);
  if (primaryOnly) return "app_reserved";
  return null;
}

const MAC_GLYPH: Record<string, string> = { ctrl: "⌃", alt: "⌥", shift: "⇧", meta: "⌘" };
const PC_NAME: Record<string, string> = { ctrl: "Ctrl", alt: "Alt", shift: "Shift", meta: "Win" };
const MOD_ORDER = ["ctrl", "alt", "shift", "meta"] as const;

/**
 * The chord as keycap labels, for a hint next to the sidebar search bar
 * ("Ctrl", "Space" — or "⌥", "Space" on a Mac). Small on purpose: the full
 * formatter lives with the recorder, outside the startup chunk.
 */
export function chordCaps(combo: string, platform: ChordPlatform = hostChordPlatform()): string[] {
  const { mods, keys } = parseChord(combo);
  const names = platform === "mac" ? MAC_GLYPH : PC_NAME;
  const key = (token: string) =>
    token === "space" ? "Space" : token.length === 1 ? token.toUpperCase() : token[0].toUpperCase() + token.slice(1);
  return [...MOD_ORDER.filter((m) => mods.has(m)).map((m) => names[m]), ...keys.map(key)];
}
