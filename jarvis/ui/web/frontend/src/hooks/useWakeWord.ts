import { useCallback, useEffect, useState } from "react";

/**
 * Current wake-word configuration as returned by GET /api/settings/wake-word.
 * Mirrors the backend response in jarvis/ui/web/settings_routes.py.
 */
export interface WakeWordConfig {
  phrase: string;
  engine: string;
  custom_model_path: string;
  sensitivity: number;
  fuzzy_match_ratio: number;
  engines: string[];
  instant_phrases: string[];
  local_whisper_available: boolean;
}

/**
 * Payload for PUT /api/settings/wake-word. Optional fields fall back to the
 * backend defaults when omitted.
 */
export interface WakeWordPayload {
  phrase: string;
  engine: string;
  custom_model_path?: string;
  sensitivity?: number;
  fuzzy_match_ratio?: number;
  persist?: boolean;
}

/**
 * Result of a successful wake-word save. `resolved_engine` may differ from the
 * requested engine when the chosen phrase forces a fallback — `degraded` flags
 * that case so the UI can warn the user.
 */
export interface WakeWordSaveResult {
  ok: boolean;
  phrase: string;
  engine: string;
  resolved_engine: string;
  degraded: boolean;
  message: string;
  persisted: boolean;
  restart_required: boolean;
}

/**
 * Loads /api/settings/wake-word and exposes a saveWakeWord() that PUTs the new
 * config and returns the resolved result. Mirrors the fetch/error/loading shape
 * of useProviders. After a successful save it dispatches the window event
 * 'jarvis:wake-word-changed' (consistent with the existing 'jarvis:*-switched'
 * events) so other components can re-read live state.
 */
export function useWakeWord() {
  const [config, setConfig] = useState<WakeWordConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/wake-word");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: WakeWordConfig = await res.json();
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
    window.addEventListener("jarvis:wake-word-changed", onChanged);
    return () => {
      window.removeEventListener("jarvis:wake-word-changed", onChanged);
    };
  }, [refetch]);

  const saveWakeWord = useCallback(
    async (payload: WakeWordPayload): Promise<WakeWordSaveResult> => {
      const res = await fetch("/api/settings/wake-word", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persist: true, ...payload }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const result = body as WakeWordSaveResult;
      window.dispatchEvent(new CustomEvent("jarvis:wake-word-changed"));
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, saveWakeWord };
}
