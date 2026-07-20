import { useCallback, useEffect, useState } from "react";

/** A KeyboardEvent-shaped object — accepts both a DOM and a React event. */
export interface KeyEventLike {
  code: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
  // Method syntax (not a property) so the parameter is bivariant — this lets a
  // React KeyboardEvent (whose getModifierState takes the narrower ModifierKey)
  // be passed without a TS2345 contravariance error.
  getModifierState?(k: string): boolean;
}

/**
 * Named-key map: DOM ``event.code`` → the EXACT key name the global-hotkeys
 * backend registers (see ``vk_key_names`` in the ``global_hotkeys`` package).
 * Emitting a name the backend does not know makes the whole combo
 * unregisterable, which (all-or-nothing registration) used to disable EVERY
 * hotkey — so the right-hand side here must match the library verbatim.
 *
 * Punctuation / OEM keys are deliberately omitted: ``event.code`` is keyed to
 * physical US-layout positions, so on a German keyboard "BracketLeft" is "ü" — (i18n-allow: umlaut char referenced in English prose)
 * binding by position would surprise the user. We stick to the keys whose
 * identity is layout-independent (arrows, the nav/edit cluster, the numpad).
 */
const _NAMED_KEY_TOKENS: Record<string, string> = {
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
  NumpadAdd: "add_key",
  NumpadSubtract: "subtract_key",
  NumpadMultiply: "multiply_key",
  NumpadDivide: "divide_key",
  NumpadDecimal: "decimal_key",
};

/**
 * Convert a single physical ``event.code`` into the jarvis main-key token
 * ("KeyJ" → "j", "F7" → "f7", "Space" → "space", "Numpad3" → "numpad_3",
 * "ArrowUp" → "up", "PageDown" → "page_down").
 *
 * Returns null for pure modifiers (Control/Alt/Shift/Meta), for Escape (the
 * recorder reserves it to cancel) and for layout-ambiguous punctuation — the
 * caller treats those as "no real key yet".
 */
export function codeToKeyToken(code: string): string | null {
  if (
    code.startsWith("Control") ||
    code.startsWith("Alt") ||
    code.startsWith("Shift") ||
    code.startsWith("Meta") ||
    code === "AltGraph" ||
    code === "Escape"
  ) {
    return null;
  }
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F[0-9]{1,2}$/.test(code)) return code.toLowerCase();
  if (code === "Space") return "space";
  // Numpad digits are `numpad_N` in the backend library (NOT `num_N`).
  if (/^Numpad[0-9]$/.test(code)) return "numpad_" + code.slice(6);
  if (code in _NAMED_KEY_TOKENS) return _NAMED_KEY_TOKENS[code];
  return null; // unsupported key (punctuation, media keys, etc.)
}

/**
 * Modifier tokens for an event, in canonical order (ctrl, alt/right_alt, shift,
 * win).
 *
 * NOTE: Windows reports AltGr (the right Alt key) as Ctrl+Alt. We therefore
 * trust event.code: AltRight → right_alt, and only emit "ctrl" from an actual
 * ControlLeft/ControlRight press, not from the synthetic Ctrl that AltGr adds.
 */
function modifierTokens(e: KeyEventLike): string[] {
  const mods: string[] = [];
  const altGr =
    e.getModifierState?.("AltGraph") === true || e.code === "AltRight";
  // Ctrl: real only — AltGr injects a phantom ctrlKey on Windows, so when the
  // pressed key is AltRight/AltGraph we do NOT add ctrl.
  if (e.ctrlKey && !altGr) mods.push("ctrl");
  if (altGr) {
    mods.push("right_alt");
  } else if (e.altKey) {
    mods.push("alt");
  }
  if (e.shiftKey) mods.push("shift");
  if (e.metaKey) mods.push("win");
  return mods;
}

/**
 * Convert a browser KeyboardEvent into the jarvis hotkey-combo string
 * (e.g. "ctrl+right_alt+j") using the SINGLE key in the event.
 *
 * Returns null if the event carries no non-modifier key yet (the user is still
 * only holding modifiers) — the caller keeps capturing until a real key lands.
 */
export function eventToCombo(e: KeyEventLike): string | null {
  const key = codeToKeyToken(e.code);
  if (key === null) return null;
  return [...modifierTokens(e), key].join("+");
}

/**
 * Build a jarvis combo string from the live modifier state plus the SET of
 * non-modifier key tokens held during the chord (e.g. ["f7","f8"] → "f7+f8").
 *
 * Unlike ``eventToCombo`` (one key only), this supports multi-key chords — the
 * form the global-hotkeys backend natively registers (the Call default is
 * "f3+f4"). Modifiers come first in canonical order, then the non-modifier keys
 * sorted for stability so "f4+f3" and "f3+f4" normalise to the same string
 * (matching the backend's order-preserving combo + string-based collision
 * check). Returns null while only modifiers are held (no real key yet).
 */
export function chordToCombo(
  e: KeyEventLike,
  heldTokens: Iterable<string>,
): string | null {
  const keys = [...new Set(heldTokens)].sort();
  if (keys.length === 0) return null;
  return [...modifierTokens(e), ...keys].join("+");
}

/**
 * Canonical modifier order — modifiers always render/serialize before the
 * non-modifier keys, matching ``modifierTokens`` (ctrl, alt-family, shift, win).
 * ``right_alt`` and ``alt`` never co-occur in practice, so their relative order
 * is moot; keeping both keeps the lookup a plain membership test.
 */
export const MODIFIER_TOKENS = [
  "ctrl",
  "right_alt",
  "alt",
  "shift",
  "win",
] as const;

/**
 * Map a physical modifier ``event.code`` to its jarvis token
 * ("ControlLeft" → "ctrl", "AltRight" → "right_alt", "MetaLeft" → "win").
 * Returns null for any non-modifier code. Complements ``codeToKeyToken`` (which
 * returns null for modifiers); together they classify every key on the visual
 * keyboard.
 */
export function codeToModifierToken(code: string): string | null {
  switch (code) {
    case "ControlLeft":
    case "ControlRight":
      return "ctrl";
    case "ShiftLeft":
    case "ShiftRight":
      return "shift";
    case "AltLeft":
      return "alt";
    case "AltRight":
      return "right_alt";
    case "MetaLeft":
    case "MetaRight":
      return "win";
    default:
      return null;
  }
}

/**
 * Build a combo string from a flat set of tokens (modifiers + non-modifier
 * keys), e.g. {"shift","ctrl","f5"} → "ctrl+shift+f5". Modifiers come first in
 * canonical order, the rest sorted — so a combo built by CLICKING the on-screen
 * keyboard normalises to the exact same string a physical chord produces
 * (``chordToCombo``).
 *
 * A modifier-only selection round-trips ("ctrl+shift") instead of collapsing
 * to "" — dropping it made a clicked modifier invisible (the key never lit up)
 * and silently lost on the next click. ``validateCombo`` is what blocks
 * SAVING a modifier-only combo; composing it is a legitimate interim state.
 */
export function composeCombo(tokens: Iterable<string>): string {
  const set = new Set(tokens);
  const mods = MODIFIER_TOKENS.filter((m) => set.has(m));
  const keys = [...set].filter((t) => !MODIFIER_TOKENS.includes(t as never)).sort();
  return [...mods, ...keys].join("+");
}

/** Split a combo string back into its token set ("ctrl+f5" → {"ctrl","f5"}). */
export function comboTokens(combo: string): Set<string> {
  return new Set(
    combo
      .split("+")
      .map((p) => p.trim().toLowerCase())
      .filter(Boolean),
  );
}

/**
 * Keys that are safe to bind SOLO — mirrors ``_SOLO_SAFE_KEYS`` in
 * ``jarvis/trigger/hotkey.py`` (the backend stays the authority; this copy
 * only powers the LIVE feedback so the user never has to hit Save to learn a
 * rule). Function keys never fire while typing; the navigation cluster is
 * allowed solo but carries a warning (it fires during text navigation).
 */
const _NAV_SOLO_TOKENS = new Set([
  "up", "down", "left", "right",
  "home", "end", "page_up", "page_down",
  "insert", "delete",
]);

const _SOLO_SAFE_TOKENS = new Set([
  ...Array.from({ length: 24 }, (_, i) => `f${i + 1}`),
  ..._NAV_SOLO_TOKENS,
]);

/** Live validation result for a combo being built in the keybind recorder. */
export type ComboValidation =
  | { status: "empty" }
  | { status: "ok" }
  | { status: "warning"; reason: "solo_nav" }
  | {
      status: "error";
      reason:
        | "only_modifiers"
        | "solo_typing_key"
        | "windows_reserved"
        | "alt_f4"
        | "ctrl_c";
    }
  | {
      status: "error";
      reason: "collision";
      conflict: { action: string; combo: string };
    };

/**
 * Validate a combo AS THE USER BUILDS IT — the frontend mirror of the backend
 * ``validate_hotkey`` rules plus the route's overlap check, so every rule is
 * surfaced live (inline, localized) instead of as a post-Save error toast.
 * The backend remains the authority on save; this never replaces it.
 *
 * ``others`` maps the OTHER actions' names to their current combos; a key-set
 * subset/superset relation with any of them is a collision (the polling
 * backend fires a combo as soon as its keys are down, so f1 alongside f1+f2
 * would trigger both actions on one press).
 */
export function validateCombo(
  combo: string,
  others: Record<string, string> = {},
): ComboValidation {
  const tokens = comboTokens(combo);
  if (tokens.size === 0) return { status: "empty" };

  const mods = [...tokens].filter((t) => MODIFIER_TOKENS.includes(t as never));
  const keys = [...tokens].filter((t) => !MODIFIER_TOKENS.includes(t as never));

  if (keys.length === 0) return { status: "error", reason: "only_modifiers" };
  if (mods.includes("win")) return { status: "error", reason: "windows_reserved" };
  if ((mods.includes("alt") || mods.includes("right_alt")) && keys.includes("f4")) {
    return { status: "error", reason: "alt_f4" };
  }
  if (mods.length > 0 && mods.every((m) => m === "ctrl") && keys.length === 1 && keys[0] === "c") {
    return { status: "error", reason: "ctrl_c" };
  }
  if (mods.length === 0 && keys.length === 1 && !_SOLO_SAFE_TOKENS.has(keys[0])) {
    return { status: "error", reason: "solo_typing_key" };
  }

  const isSubset = (a: Set<string>, b: Set<string>) =>
    [...a].every((t) => b.has(t));
  for (const [action, other] of Object.entries(others)) {
    const otherTokens = comboTokens(other);
    if (otherTokens.size === 0) continue;
    if (isSubset(tokens, otherTokens) || isSubset(otherTokens, tokens)) {
      return {
        status: "error",
        reason: "collision",
        conflict: { action, combo: other.trim().toLowerCase() },
      };
    }
  }

  if (mods.length === 0 && keys.length === 1 && _NAV_SOLO_TOKENS.has(keys[0])) {
    return { status: "warning", reason: "solo_nav" };
  }
  return { status: "ok" };
}

export type KeybindAction = "call" | "hangup";

/** Response of GET /api/settings/keybinds. */
export interface KeybindsConfig {
  keybinds: Record<KeybindAction, string>;
  defaults: Record<KeybindAction, string>;
  suggestions: string[];
  restart_required: boolean;
}

/** Result of a successful PUT /api/settings/keybinds. */
export interface KeybindSaveResult {
  ok: boolean;
  action: KeybindAction;
  hotkey: string;
  persisted: boolean;
  restart_required: boolean;
}

/**
 * Loads /api/settings/keybinds and exposes saveKeybind(action, combo). Mirrors
 * useHotkey's fetch/error/loading shape but covers both voice keybinds (Call and
 * Hangup). A rejected save (unsafe combo or a collision with another action)
 * throws with the backend's reason. After a successful save it dispatches
 * 'jarvis:keybinds-changed'.
 */
export function useKeybinds() {
  const [config, setConfig] = useState<KeybindsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/keybinds");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: KeybindsConfig = await res.json();
      setConfig(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
    const onChanged = () => void refetch();
    window.addEventListener("jarvis:keybinds-changed", onChanged);
    return () => {
      window.removeEventListener("jarvis:keybinds-changed", onChanged);
    };
  }, [refetch]);

  const saveKeybind = useCallback(
    async (action: KeybindAction, hotkey: string): Promise<KeybindSaveResult> => {
      const res = await fetch("/api/settings/keybinds", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, hotkey, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      window.dispatchEvent(new CustomEvent("jarvis:keybinds-changed"));
      return body as KeybindSaveResult;
    },
    [],
  );

  return { config, loading, error, refetch, saveKeybind };
}
