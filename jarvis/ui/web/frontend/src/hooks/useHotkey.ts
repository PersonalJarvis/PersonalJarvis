import { useCallback, useEffect, useState } from "react";

/**
 * Current push-to-talk hotkey config from GET /api/settings/ptt-hotkey.
 * Mirrors the backend response in jarvis/ui/web/settings_routes.py.
 */
export interface HotkeyConfig {
  hotkey: string;
  push_to_talk: boolean;
  default: string;
  suggestions: string[];
}

/** Result of a successful PUT /api/settings/ptt-hotkey. */
export interface HotkeySaveResult {
  ok: boolean;
  hotkey: string;
  persisted: boolean;
  restart_required: boolean;
}

/**
 * Loads /api/settings/ptt-hotkey and exposes saveHotkey() that PUTs a new
 * combo and returns the result. Mirrors useWakeWord's fetch/error/loading
 * shape. A failed save (e.g. an unsafe combo rejected by the backend
 * validator) throws with the backend's reason so the UI can surface it. After
 * a successful save it dispatches 'jarvis:ptt-hotkey-changed'.
 */
export function useHotkey() {
  const [config, setConfig] = useState<HotkeyConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/ptt-hotkey");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: HotkeyConfig = await res.json();
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
    window.addEventListener("jarvis:ptt-hotkey-changed", onChanged);
    return () => {
      window.removeEventListener("jarvis:ptt-hotkey-changed", onChanged);
    };
  }, [refetch]);

  const saveHotkey = useCallback(
    async (hotkey: string): Promise<HotkeySaveResult> => {
      const res = await fetch("/api/settings/ptt-hotkey", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hotkey, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const result = body as HotkeySaveResult;
      window.dispatchEvent(new CustomEvent("jarvis:ptt-hotkey-changed"));
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, saveHotkey };
}

/**
 * Convert a browser KeyboardEvent into the jarvis hotkey-combo string
 * (e.g. "ctrl+right_alt+j"). Modifiers come from event.code so left/right
 * Alt is distinguished; the main key is the first non-modifier code.
 *
 * Returns null if the event carries no non-modifier key yet (the user is still
 * only holding modifiers) — the caller keeps capturing until a real key lands.
 *
 * NOTE: Windows reports AltGr (the right Alt key) as Ctrl+Alt. We therefore
 * trust event.code: AltRight → right_alt, and only emit "ctrl" from an actual
 * ControlLeft/ControlRight press, not from the synthetic Ctrl that AltGr adds.
 */
export function eventToCombo(e: {
  code: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
  // Method syntax (not a property) so the parameter is bivariant — this lets a
  // React KeyboardEvent (whose getModifierState takes the narrower ModifierKey)
  // be passed without a TS2345 contravariance error.
  getModifierState?(k: string): boolean;
}): string | null {
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

  const code = e.code;
  // Skip pure-modifier presses — wait for the real key.
  if (
    code.startsWith("Control") ||
    code.startsWith("Alt") ||
    code.startsWith("Shift") ||
    code.startsWith("Meta") ||
    code === "AltGraph"
  ) {
    return null;
  }

  let key: string | null = null;
  if (/^Key[A-Z]$/.test(code)) key = code.slice(3).toLowerCase();
  else if (/^Digit[0-9]$/.test(code)) key = code.slice(5);
  else if (/^F[0-9]{1,2}$/.test(code)) key = code.toLowerCase();
  else if (code === "Space") key = "space";
  else if (/^Numpad[0-9]$/.test(code)) key = "num_" + code.slice(6);
  else return null; // unsupported key (arrows, punctuation, etc.)

  return [...mods, key].join("+");
}
