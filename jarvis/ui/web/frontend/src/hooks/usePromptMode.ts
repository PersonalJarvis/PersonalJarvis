import { useCallback, useEffect, useState } from "react";

/**
 * The dictation Prompt Mode switch, as the Jarvis bar sees it.
 *
 * Prompt Mode is one `[dictation]` key (`prompt_mode`): while on, every
 * dictation comes out as a finished prompt for a coding agent instead of the
 * cleaned-up transcript. The settings screen owns the full card; the bar
 * needs only the bit and a way to flip it, so this hook reads that one key
 * and writes it back through the same PUT the settings screen uses — one
 * route, one validation, one file write, whichever surface asks.
 *
 * Two surfaces can show the same switch at once (the bar on the front page,
 * the card under Voice), so a flip on either side is announced on `window`
 * as `jarvis:dictation-settings` carrying the settings block the backend
 * returned, and both listen. The alternative — each surface re-fetching on
 * focus — leaves a window in which the two disagree about a value the user
 * just set, which is the kind of lie a toggle must never tell.
 *
 * `enabled` is `null` until the backend has answered, and stays `null` when
 * it cannot: an indicator that does not know the state renders nothing
 * rather than claiming one (an older backend without the key, a request
 * that failed).
 */
export const DICTATION_SETTINGS_EVENT = "jarvis:dictation-settings";

export interface DictationSettingsEventDetail {
  settings: { prompt_mode?: boolean } & Record<string, unknown>;
}

/** Tell every listening surface what the backend now holds. */
export function announceDictationSettings(settings: Record<string, unknown>): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<DictationSettingsEventDetail>(DICTATION_SETTINGS_EVENT, {
      detail: { settings },
    }),
  );
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body && typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      // The body was not JSON; the status line is the whole message.
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function usePromptMode(): {
  enabled: boolean | null;
  busy: boolean;
  toggle: () => Promise<void>;
} {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await readJson<{ settings?: { prompt_mode?: unknown } }>(
          await fetch("/api/dictation/settings"),
        );
        if (cancelled) return;
        const value = data.settings?.prompt_mode;
        setEnabled(typeof value === "boolean" ? value : null);
      } catch {
        if (!cancelled) setEnabled(null);
      }
    })();
    const onSettings = (ev: Event) => {
      const detail = (ev as CustomEvent<DictationSettingsEventDetail>).detail;
      const value = detail?.settings?.prompt_mode;
      if (typeof value === "boolean") setEnabled(value);
    };
    window.addEventListener(DICTATION_SETTINGS_EVENT, onSettings);
    return () => {
      cancelled = true;
      window.removeEventListener(DICTATION_SETTINGS_EVENT, onSettings);
    };
  }, []);

  /** Flip the switch. Throws with the backend's own sentence when the save failed. */
  const toggle = useCallback(async () => {
    if (busy || enabled === null) return;
    const next = !enabled;
    setBusy(true);
    try {
      const data = await readJson<{ settings?: Record<string, unknown> }>(
        await fetch("/api/dictation/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt_mode: next, persist: true }),
        }),
      );
      const settings = data.settings ?? { prompt_mode: next };
      const value = settings.prompt_mode;
      setEnabled(typeof value === "boolean" ? value : next);
      announceDictationSettings(settings);
    } finally {
      setBusy(false);
    }
  }, [busy, enabled]);

  return { enabled, busy, toggle };
}
