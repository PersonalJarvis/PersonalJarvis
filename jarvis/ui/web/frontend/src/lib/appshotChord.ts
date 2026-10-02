/**
 * Turn the physical keys of one recorded gesture into an appshot shortcut.
 *
 * Besides ordinary combos ("ctrl+shift+s") the appshot shortcuts accept the
 * two-sided gestures the backend watches itself (`jarvis/appshot/gesture.py`):
 * both Alt, both Shift or both Ctrl keys pressed together. Those are told
 * apart by `event.code` (left vs right), which the shared combo helpers fold
 * into one modifier token.
 */
import {
  codeToKeyToken,
  codeToModifierToken,
  composeCombo,
  isModifierToken,
} from "@/hooks/useHotkey";

export type ChordResult = { combo: string } | { problem: "modifier_only" | "empty" };

const GESTURES: ReadonlyArray<[string, string, string]> = [
  ["AltLeft", "AltRight", "alt+alt"],
  ["ShiftLeft", "ShiftRight", "shift+shift"],
  ["ControlLeft", "ControlRight", "ctrl+ctrl"],
];

/** Every key that was down during the gesture → the shortcut it means. */
export function chordFromCodes(codes: Iterable<string>): ChordResult {
  const set = new Set(codes);
  set.delete("Escape");
  if (set.size === 0) return { problem: "empty" };
  if (set.size === 2) {
    for (const [left, right, gesture] of GESTURES) {
      if (set.has(left) && set.has(right)) return { combo: gesture };
    }
  }
  const tokens = new Set<string>();
  for (const code of set) {
    const token = codeToModifierToken(code) ?? codeToKeyToken(code);
    if (token) tokens.add(token);
  }
  if (![...tokens].some((token) => !isModifierToken(token))) return { problem: "modifier_only" };
  return { combo: composeCombo(tokens) };
}

/** "shift+shift" → "shift", for the "press both … keys" hints. */
export function gestureFamily(hotkey: string): "alt" | "shift" | "ctrl" | null {
  if (hotkey === "alt+alt") return "alt";
  if (hotkey === "shift+shift") return "shift";
  if (hotkey === "ctrl+ctrl") return "ctrl";
  return null;
}
