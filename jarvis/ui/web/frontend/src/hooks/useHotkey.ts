import { useCallback, useEffect, useState } from "react";

import {
  MOUSE_BUTTON_TOKENS,
  detectKeyboardPlatform,
  type KeyboardPlatform,
} from "@/views/settings/keyboardLayout";

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
 * The token for the Meta/Command/Windows key ON THIS HOST.
 *
 * The same physical key, two names the backends genuinely tell apart: the
 * Quartz backend decodes ``kCGEventFlagMaskCommand`` as ``cmd``, the Windows
 * and X11 backends match ``win``/``window``. Emitting ``win`` on a Mac (what
 * this file used to do unconditionally) produced a combo whose modifier the
 * Mac user's keyboard cannot express, and made ``cmd`` unreachable from the UI
 * altogether.
 */
function metaToken(platform: KeyboardPlatform): string {
  return platform === "mac" ? "cmd" : "win";
}

/**
 * Modifier tokens for an event, in canonical order (ctrl, alt/right_alt, shift,
 * meta).
 *
 * NOTE: Windows reports AltGr (the right Alt key) as Ctrl+Alt. We therefore
 * trust event.code: AltRight → right_alt, and only emit "ctrl" from an actual
 * ControlLeft/ControlRight press, not from the synthetic Ctrl that AltGr adds.
 *
 * ``platform`` is injectable so the pure functions stay deterministic in tests;
 * it defaults to the host the app is running on.
 */
export function modifierTokens(
  e: KeyEventLike,
  platform: KeyboardPlatform = detectKeyboardPlatform(),
): string[] {
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
  if (e.metaKey) mods.push(metaToken(platform));
  return mods;
}

/**
 * Convert a browser KeyboardEvent into the jarvis hotkey-combo string
 * (e.g. "ctrl+right_alt+j") using the SINGLE key in the event.
 *
 * Returns null if the event carries no non-modifier key yet (the user is still
 * only holding modifiers) — the caller keeps capturing until a real key lands.
 */
export function eventToCombo(
  e: KeyEventLike,
  platform: KeyboardPlatform = detectKeyboardPlatform(),
): string | null {
  const key = codeToKeyToken(e.code);
  if (key === null) return null;
  return [...modifierTokens(e, platform), key].join("+");
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
 * check).
 *
 * A MODIFIER-ONLY chord is a real result, not null. It used to return null the
 * moment the held key set was empty, which meant the recorder could never
 * PRODUCE "ctrl+win": holding those two keys recorded literally nothing, the
 * field stayed blank and the gesture expired on the idle timer with no
 * feedback. Nothing downstream needed a change — ``composeCombo`` already
 * round-trips a modifier-only selection for the click-to-assign path, and the
 * backends register such a combo happily. Returns null only when NOTHING at all
 * is held.
 */
export function chordToCombo(
  e: KeyEventLike,
  heldTokens: Iterable<string>,
  platform: KeyboardPlatform = detectKeyboardPlatform(),
): string | null {
  const keys = [...new Set(heldTokens)].sort();
  const mods = modifierTokens(e, platform);
  if (keys.length === 0 && mods.length === 0) return null;
  return [...mods, ...keys].join("+");
}

/**
 * Canonical modifier order — modifiers always render/serialize before the
 * non-modifier keys, matching ``modifierTokens`` (ctrl, alt-family, shift,
 * meta-family). ``right_alt`` and ``alt`` never co-occur in practice, so their
 * relative order is moot; keeping both keeps the lookup a plain membership
 * test.
 *
 * The list is the full alias vocabulary the backend's ``_MODIFIER_TOKENS``
 * knows, not just the tokens this UI emits: a jarvis.toml written by hand (or
 * on another OS) may spell the same key ``super``/``meta``/``command``, and a
 * spelling missing here would be classified as a KEY — rendering it as a
 * keycap, sorting it among the letters and mis-firing the "a real key is
 * present" rules.
 */
export const MODIFIER_TOKENS = [
  "ctrl",
  "control",
  "right_ctrl",
  "right_control",
  "right_alt",
  "left_alt",
  "altgr",
  "alt",
  "shift",
  "win",
  "window",
  "super",
  "meta",
  "cmd",
  "command",
] as const;

/** Whether a token is a modifier rather than a "real" key. */
export function isModifierToken(token: string): boolean {
  return (MODIFIER_TOKENS as readonly string[]).includes(token);
}

/**
 * Map a physical modifier ``event.code`` to its jarvis token
 * ("ControlLeft" → "ctrl", "AltRight" → "right_alt", "MetaLeft" → "win", or
 * "cmd" on a Mac). Returns null for any non-modifier code. Complements
 * ``codeToKeyToken`` (which returns null for modifiers); together they classify
 * every key on the visual keyboard.
 */
export function codeToModifierToken(
  code: string,
  platform: KeyboardPlatform = detectKeyboardPlatform(),
): string | null {
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
      return metaToken(platform);
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
 * and silently lost on the next click. It is also a perfectly saveable combo
 * in its own right (Ctrl+Win): ``validateCombo`` cautions that it fires on any
 * superset, and does not block it.
 */
export function composeCombo(tokens: Iterable<string>): string {
  const set = new Set(tokens);
  const mods = MODIFIER_TOKENS.filter((m) => set.has(m));
  const keys = [...set].filter((t) => !isModifierToken(t)).sort();
  return [...mods, ...keys].join("+");
}

/**
 * Split a combo string back into its token set ("ctrl+f5" → {"ctrl","f5"}).
 *
 * The `?? ""` is a RUNTIME guard, not redundancy with the `string` type: every
 * combo originates in a backend JSON payload, where TypeScript guarantees
 * nothing. A backend that does not yet know an action returns no combo for it,
 * and the resulting `undefined.split("+")` took the entire Settings view down
 * instead of one row (2026-07-28). Callers still default at their own level;
 * this makes the failure impossible rather than merely unlikely.
 */
export function comboTokens(combo: string): Set<string> {
  return new Set(
    (combo ?? "")
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

/**
 * Keys the OS is documented to keep for itself — mirrors
 * ``_RESERVED_SOLO_KEYS`` in ``jarvis/trigger/hotkey.py``. F12 is claimed by the
 * debugger. It is a CAUTION, not a refusal: the shortcut still fires, the
 * debugger just gets the key too.
 */
const _RESERVED_TOKENS = new Set(["f12"]);

/**
 * The shell shortcuts each OS assigns to its Meta key, mirroring
 * ``_WINDOWS_CRITICAL_KEYS`` / ``_MACOS_CRITICAL_KEYS`` in
 * ``jarvis/trigger/hotkey.py``. Binding one is allowed — the desktop simply
 * keeps doing its thing on top of the shortcut, which the user deserves to be
 * told before they discover it.
 */
const _WINDOWS_CRITICAL_KEYS = new Set([
  "d", "e", "l", "r", "x", "i", "s", "v", "a", "p",
  "tab", "left", "right", "up", "down",
]);

const _MACOS_CRITICAL_KEYS = new Set([
  "c", "v", "x", "z", "q", "w", "a", "s", "space", "tab",
]);

const _CTRL_FAMILY = new Set(["ctrl", "control", "right_ctrl", "right_control"]);
const _ALT_FAMILY = new Set(["alt", "right_alt", "left_alt", "altgr"]);
const _META_FAMILY = new Set(["win", "window", "super", "meta"]);
const _CMD_FAMILY = new Set(["cmd", "command", "meta", "super"]);

/**
 * Token folds the Windows backend applies before it registers a combo
 * (``_KEY_MAP`` in ``jarvis/trigger/backends/global_hotkeys.py``). Two combos
 * that fold onto the same set are ONE registration no matter how differently
 * they are spelled — ``ctrl+left_alt+j`` and ``ctrl+right_alt+j`` were accepted
 * as two shortcuts and the second one then died at register time, leaving a
 * bound-looking row that does nothing.
 */
const _NORMALIZE_FOLD: Record<string, string> = {
  ctrl: "control",
  right_ctrl: "right_control",
  right_alt: "alt",
  left_alt: "alt",
  altgr: "alt",
  win: "window",
  super: "window",
  meta: "window",
  cmd: "command",
};

/** The key set a combo ACTUALLY registers as — mirrors the backend's
 * ``normalized_combo_tokens``. */
export function normalizedComboTokens(combo: string): Set<string> {
  return new Set(
    [...comboTokens(combo)].map((t) => _NORMALIZE_FOLD[t] ?? t),
  );
}

/**
 * A non-blocking note about a combo that is perfectly legal but behaves in a
 * way the user should know about before they save it. Mirrors the
 * ``cautions`` channel of the backend's ``HotkeyVerdict``.
 */
export type ComboCaution =
  | "modifier_only"
  | "solo_typing_key"
  | "solo_nav"
  | "os_shortcut"
  | "overlap"
  | "mouse_button";

/**
 * Live validation result for a combo being built in the keybind recorder.
 *
 * ``status: "error"`` is now reserved for the ONE thing that genuinely cannot
 * be saved — an overlap with another action, which the backend route rejects
 * with a 400. Everything this validator used to refuse (a modifiers-only
 * chord, the Windows key, a bare typing key, an OS shortcut) is accepted and
 * reported through ``cautions``: the user owns their keyboard, and the
 * refusals were both unhelpful and, for the Windows key, factually wrong — the
 * Windows backend polls ``GetAsyncKeyState`` rather than registering with the
 * shell, so it always saw those combos.
 */
export interface ComboValidation {
  status: "empty" | "ok" | "error";
  /** Set only when ``status === "error"``. */
  reason?: "collision";
  /** The other action a collision is with. */
  conflict?: { action: string; combo: string };
  /** Non-blocking notes; may be present on any status. */
  cautions: ComboCaution[];
}

/**
 * Validate a combo AS THE USER BUILDS IT — the frontend mirror of the backend
 * ``validate_hotkey`` verdict plus the route's overlap check, so every rule is
 * surfaced live (inline, localized) instead of as a post-Save error toast.
 * The backend remains the authority on save; this never replaces it.
 *
 * ``others`` maps the OTHER actions' names to their current combos; a key-set
 * subset/superset relation with any of them is a collision (the polling
 * backend fires a combo as soon as its keys are down, so f1 alongside f1+f2
 * would trigger both actions on one press). The comparison runs on the
 * NORMALIZED sets, so two spellings of one registration also collide.
 */
export function validateCombo(
  combo: string,
  others: Record<string, string> = {},
  platform: KeyboardPlatform = detectKeyboardPlatform(),
): ComboValidation {
  const tokens = comboTokens(combo);
  if (tokens.size === 0) return { status: "empty", cautions: [] };

  const mods = [...tokens].filter((t) => isModifierToken(t));
  const keys = [...tokens].filter((t) => !isModifierToken(t));
  const mouse = keys.filter((k) =>
    (MOUSE_BUTTON_TOKENS as readonly string[]).includes(k),
  );
  const cautions: ComboCaution[] = [];

  // Same order and the same conditions as the backend's validate_hotkey.
  if (keys.length === 0) {
    cautions.push("modifier_only");
  } else if (
    mods.length === 0 &&
    keys.length === 1 &&
    mouse.length === 0 &&
    !_SOLO_SAFE_TOKENS.has(keys[0])
  ) {
    cautions.push("solo_typing_key");
  }

  const onlyFrom = (family: Set<string>) =>
    mods.length > 0 && mods.every((m) => family.has(m));
  const altHeld = mods.some((m) => _ALT_FAMILY.has(m));
  if (keys.some((k) => _RESERVED_TOKENS.has(k))) cautions.push("os_shortcut");
  if (altHeld && keys.includes("f4")) cautions.push("os_shortcut");
  if (onlyFrom(_CTRL_FAMILY) && keys.length === 1 && keys[0] === "c") {
    cautions.push("os_shortcut");
  }
  if (
    onlyFrom(_META_FAMILY) &&
    keys.length === 1 &&
    (platform === "mac"
      ? _MACOS_CRITICAL_KEYS.has(keys[0])
      : _WINDOWS_CRITICAL_KEYS.has(keys[0]))
  ) {
    cautions.push("os_shortcut");
  }
  if (onlyFrom(_CMD_FAMILY) && keys.length === 1 && _MACOS_CRITICAL_KEYS.has(keys[0])) {
    cautions.push("os_shortcut");
  }
  if (mouse.length > 0) cautions.push("mouse_button");
  if (mods.length === 0 && keys.length === 1 && _NAV_SOLO_TOKENS.has(keys[0])) {
    cautions.push("solo_nav");
  }
  const unique = [...new Set(cautions)];

  const normalized = normalizedComboTokens(combo);
  let overlap: { action: string; combo: string } | undefined;
  const isSubset = (a: Set<string>, b: Set<string>) =>
    [...a].every((t) => b.has(t));
  // Mirrors the route: the SAME registration twice is the one ambiguity
  // nothing can resolve and stays an error. A merely overlapping pair — one
  // combo contained in the other — is accepted with a caution, because
  // refusing it made almost every modifier-only chord unsavable, which is the
  // opposite of "any combination works".
  for (const [action, other] of Object.entries(others)) {
    const otherTokens = normalizedComboTokens(other);
    if (otherTokens.size === 0) continue;
    const mineInTheirs = isSubset(normalized, otherTokens);
    const theirsInMine = isSubset(otherTokens, normalized);
    if (!mineInTheirs && !theirsInMine) continue;
    if (mineInTheirs && theirsInMine) {
      return {
        status: "error",
        reason: "collision",
        conflict: { action, combo: other.trim().toLowerCase() },
        cautions: unique,
      };
    }
    unique.push("overlap");
    overlap = { action, combo: other.trim().toLowerCase() };
  }

  return { status: "ok", cautions: [...new Set(unique)], conflict: overlap };
}

// Mirrors KEYBIND_ACTIONS in jarvis/core/config_writer.py — keep in sync.
// "dictate" is push-to-talk (hold), "dictate_toggle" is hands-free (press once
// to start, again to stop), "paste_last" re-inserts the last transcription.
export type KeybindAction =
  | "call"
  | "hangup"
  | "dictate"
  | "dictate_toggle"
  | "paste_last";

/**
 * Response of GET /api/settings/keybinds.
 *
 * `Partial` on purpose: the frontend and the backend are updated separately, so
 * a build that already knows a new action can talk to a backend that does not
 * report it yet. Every read must therefore default (`?? ""`) instead of
 * assuming a string — an undefined combo used to crash the whole panel.
 */
export interface KeybindsConfig {
  keybinds: Partial<Record<KeybindAction, string>>;
  defaults: Partial<Record<KeybindAction, string>>;
  suggestions: string[];
  restart_required: boolean;
  /**
   * Whether THIS host can bind a mouse button, mirroring the backend's
   * ``mouse_hotkeys_available()`` capability probe (Windows always; macOS needs
   * pyobjc Quartz; Linux/X11 needs the opt-in pynput extra; Wayland cannot at
   * all). Optional because the route does not serve it yet — while it is
   * absent the picker offers the buttons, and a host that cannot use them
   * degrades through the backend's own honest message on save. The moment the
   * field appears, the picker hides the buttons and shows ``reason`` instead.
   */
  mouse_buttons?: { supported: boolean; reason?: string };
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
