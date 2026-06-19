/**
 * Physical keyboard layout data for the visual keybind picker (KeyboardMap).
 *
 * The layout is a compact TKL ("tenkeyless") arrangement — the form factor that
 * still carries everything a voice keybind can use: the function row, the main
 * alpha block, the modifier row, the nav cluster and the arrows. (A 60 % board
 * has no F-keys or arrows, so it would hide half the bindable surface.)
 *
 * Each cap carries its DOM ``event.code`` — the SAME identifier the recorder
 * sees on a real key press — so the live highlight is a plain
 * ``pressedCodes.has(cap.code)`` and the bindable token is derived centrally via
 * ``codeToKeyToken`` / ``codeToModifierToken`` (single source of truth). Caps
 * whose code is neither (punctuation, CapsLock) are ``dead``: drawn for spatial
 * context but not bindable, matching what the backend can actually register.
 *
 * Mac vs PC only changes the modifier row labels/arrangement; the codes stay the
 * physical ones the browser reports on either platform.
 */

export type KeyboardPlatform = "mac" | "pc";

export interface KeyCap {
  /** DOM ``event.code`` — used for the live press highlight + token lookup. */
  code: string;
  /** Display label. */
  label: string;
  /** Relative width in key units (1 = a standard letter key). */
  width?: number;
  /** Drawn for context but not bindable (punctuation / CapsLock). */
  dead?: boolean;
}

export type KeyRow = KeyCap[];

const FUNCTION_ROW: KeyRow = [
  { code: "Escape", label: "Esc", dead: true },
  { code: "F1", label: "F1" },
  { code: "F2", label: "F2" },
  { code: "F3", label: "F3" },
  { code: "F4", label: "F4" },
  { code: "F5", label: "F5" },
  { code: "F6", label: "F6" },
  { code: "F7", label: "F7" },
  { code: "F8", label: "F8" },
  { code: "F9", label: "F9" },
  { code: "F10", label: "F10" },
  { code: "F11", label: "F11" },
  { code: "F12", label: "F12" },
];

const NUMBER_ROW: KeyRow = [
  { code: "Backquote", label: "`", dead: true },
  { code: "Digit1", label: "1" },
  { code: "Digit2", label: "2" },
  { code: "Digit3", label: "3" },
  { code: "Digit4", label: "4" },
  { code: "Digit5", label: "5" },
  { code: "Digit6", label: "6" },
  { code: "Digit7", label: "7" },
  { code: "Digit8", label: "8" },
  { code: "Digit9", label: "9" },
  { code: "Digit0", label: "0" },
  { code: "Minus", label: "-", dead: true },
  { code: "Equal", label: "=", dead: true },
  { code: "Backspace", label: "⌫", width: 2 },
];

const TOP_ROW: KeyRow = [
  { code: "Tab", label: "Tab", width: 1.5 },
  { code: "KeyQ", label: "Q" },
  { code: "KeyW", label: "W" },
  { code: "KeyE", label: "E" },
  { code: "KeyR", label: "R" },
  { code: "KeyT", label: "T" },
  { code: "KeyY", label: "Y" },
  { code: "KeyU", label: "U" },
  { code: "KeyI", label: "I" },
  { code: "KeyO", label: "O" },
  { code: "KeyP", label: "P" },
  { code: "BracketLeft", label: "[", dead: true },
  { code: "BracketRight", label: "]", dead: true },
  { code: "Backslash", label: "\\", width: 1.5, dead: true },
];

const HOME_ROW: KeyRow = [
  { code: "CapsLock", label: "Caps", width: 1.75, dead: true },
  { code: "KeyA", label: "A" },
  { code: "KeyS", label: "S" },
  { code: "KeyD", label: "D" },
  { code: "KeyF", label: "F" },
  { code: "KeyG", label: "G" },
  { code: "KeyH", label: "H" },
  { code: "KeyJ", label: "J" },
  { code: "KeyK", label: "K" },
  { code: "KeyL", label: "L" },
  { code: "Semicolon", label: ";", dead: true },
  { code: "Quote", label: "'", dead: true },
  { code: "Enter", label: "Enter", width: 2.25 },
];

const BOTTOM_ALPHA_ROW: KeyRow = [
  { code: "ShiftLeft", label: "Shift", width: 2.25 },
  { code: "KeyZ", label: "Z" },
  { code: "KeyX", label: "X" },
  { code: "KeyC", label: "C" },
  { code: "KeyV", label: "V" },
  { code: "KeyB", label: "B" },
  { code: "KeyN", label: "N" },
  { code: "KeyM", label: "M" },
  { code: "Comma", label: ",", dead: true },
  { code: "Period", label: ".", dead: true },
  { code: "Slash", label: "/", dead: true },
  { code: "ShiftRight", label: "Shift", width: 2.75 },
];

function modifierRow(platform: KeyboardPlatform): KeyRow {
  if (platform === "mac") {
    return [
      { code: "ControlLeft", label: "⌃", width: 1.25 },
      { code: "AltLeft", label: "⌥", width: 1.25 },
      { code: "MetaLeft", label: "⌘", width: 1.25 },
      { code: "Space", label: "Space", width: 6.25 },
      { code: "MetaRight", label: "⌘", width: 1.25 },
      { code: "AltRight", label: "⌥", width: 1.25 },
      { code: "ControlRight", label: "⌃", width: 1.25 },
    ];
  }
  return [
    { code: "ControlLeft", label: "Ctrl", width: 1.25 },
    { code: "MetaLeft", label: "Win", width: 1.25 },
    { code: "AltLeft", label: "Alt", width: 1.25 },
    { code: "Space", label: "Space", width: 6.25 },
    { code: "AltRight", label: "AltGr", width: 1.25 },
    { code: "MetaRight", label: "Win", width: 1.25 },
    { code: "ControlRight", label: "Ctrl", width: 1.25 },
  ];
}

/** The main alpha block (function row → modifier row). */
export function mainRows(platform: KeyboardPlatform): KeyRow[] {
  return [
    FUNCTION_ROW,
    NUMBER_ROW,
    TOP_ROW,
    HOME_ROW,
    BOTTOM_ALPHA_ROW,
    modifierRow(platform),
  ];
}

/** The nav cluster (Insert/Home/PageUp over Delete/End/PageDown). */
export const NAV_ROWS: KeyRow[] = [
  [
    { code: "Insert", label: "Ins" },
    { code: "Home", label: "Home" },
    { code: "PageUp", label: "PgUp" },
  ],
  [
    { code: "Delete", label: "Del" },
    { code: "End", label: "End" },
    { code: "PageDown", label: "PgDn" },
  ],
];

/** The inverted-T arrow cluster (a blank cell pads the top row). */
export const ARROW_ROWS: (KeyCap | null)[][] = [
  [null, { code: "ArrowUp", label: "↑" }, null],
  [
    { code: "ArrowLeft", label: "←" },
    { code: "ArrowDown", label: "↓" },
    { code: "ArrowRight", label: "→" },
  ],
];

/** Best-effort detect of the user's keyboard family for the modifier labels. */
export function detectKeyboardPlatform(): KeyboardPlatform {
  if (typeof navigator === "undefined") return "pc";
  const probe = `${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`;
  return /Mac|iPhone|iPad|iPod/i.test(probe) ? "mac" : "pc";
}
