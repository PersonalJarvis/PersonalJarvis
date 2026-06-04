import { useCallback, useEffect, useState } from "react";

/**
 * Assistant-name config from GET /api/settings/assistant-name.
 * `name` is the explicit override ("" = derive from wake phrase); `resolved`
 * is what the assistant actually calls itself right now.
 */
export interface AssistantNameConfig {
  name: string;
  resolved: string;
  default: string;
}

export interface AssistantNameSaveResult {
  ok: boolean;
  name: string;
  resolved: string;
  persisted: boolean;
  restart_required: boolean;
}

/**
 * Loads /api/settings/assistant-name and exposes saveName(). Mirrors
 * useHotkey/useWakeWord. After a successful save it dispatches
 * 'jarvis:assistant-name-changed'.
 */
export function useAssistantName() {
  const [config, setConfig] = useState<AssistantNameConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/assistant-name");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AssistantNameConfig = await res.json();
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
    window.addEventListener("jarvis:assistant-name-changed", onChanged);
    return () => {
      window.removeEventListener("jarvis:assistant-name-changed", onChanged);
    };
  }, [refetch]);

  const saveName = useCallback(
    async (name: string): Promise<AssistantNameSaveResult> => {
      const res = await fetch("/api/settings/assistant-name", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const result = body as AssistantNameSaveResult;
      window.dispatchEvent(new CustomEvent("jarvis:assistant-name-changed"));
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, saveName };
}
