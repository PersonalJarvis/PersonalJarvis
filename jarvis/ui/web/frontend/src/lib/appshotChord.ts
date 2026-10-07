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

export type ChordResult =
  | { combo: string }
  | { problem: "modifier_only" | "empty" | "unmapped" };

const GESTURES: ReadonlyArray<[string, string, string]> = [
  ["AltLeft", "AltRight", "alt+alt"],
  ["ShiftLeft", "ShiftRight", "shift+shift"],
  ["ControlLeft", "ControlRight", "ctrl+ctrl"],
];

/** Every key that was down during the gesture → the shortcut it means. */
export function chordFromCodes(
  codes: Iterable<string>,
  characterTokens: ReadonlyMap<string, string> = new Map(),
): ChordResult {
  const set = new Set(codes);
  set.delete("Escape");
  // Some browsers name the right Alt key AltGraph. The both-Alt gesture and
  // the right_alt token both look for AltRight.
  if (set.delete("AltGraph")) set.add("AltRight");
  if (set.size === 0) return { problem: "empty" };
  // Windows AltGr can emit an extra ControlLeft key event. The backend's
  // both-Alt gesture watches the two Alt keys, so accept that exact sequence
  // too. Keep Ctrl intact in ordinary shortcuts and other modifier pairs.
  if (set.size === 3 && set.has("AltLeft") && set.has("AltRight") && set.has("ControlLeft")) {
    return { combo: "alt+alt" };
  }
  if (set.size === 2) {
    for (const [left, right, gesture] of GESTURES) {
      if (set.has(left) && set.has(right)) return { combo: gesture };
    }
  }
  const tokens = new Set<string>();
  let unmapped = false;
  for (const code of set) {
    const token = codeToModifierToken(code) ?? characterTokens.get(code) ?? codeToKeyToken(code);
    if (token) tokens.add(token);
    else unmapped = true;
  }
  // A key this shortcut vocabulary cannot name (Print Screen, a media key)
  // must not be reported as "you only pressed Shift". That sentence sent
  // people looking for a modifier they never touched.
  if (![...tokens].some((token) => !isModifierToken(token))) {
    return { problem: unmapped ? "unmapped" : "modifier_only" };
  }
  return { combo: composeCombo(tokens) };
}

/** "shift+shift" → "shift", for the "press both … keys" hints. */
export function gestureFamily(hotkey: string): "alt" | "shift" | "ctrl" | null {
  if (hotkey === "alt+alt") return "alt";
  if (hotkey === "shift+shift") return "shift";
  if (hotkey === "ctrl+ctrl") return "ctrl";
  return null;
}
