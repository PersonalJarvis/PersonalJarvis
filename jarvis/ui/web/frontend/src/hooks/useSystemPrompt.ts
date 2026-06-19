import { useCallback, useEffect, useState } from "react";

/**
 * System-prompt config from GET /api/settings/system-prompt.
 * `content` is the effective prompt in use (custom override if set, else the
 * packaged default); `default` is always the packaged default so the UI can
 * offer "reset"; `is_custom` drives the Custom/Default badge.
 */
export interface SystemPromptConfig {
  content: string;
  is_custom: boolean;
  default: string;
  char_count: number;
}

export interface SystemPromptSaveResult extends SystemPromptConfig {
  ok: boolean;
  restart_required: boolean;
}

/**
 * Loads /api/settings/system-prompt and exposes savePrompt()/resetPrompt().
 * Mirrors useAssistantName/useWakeWord. The override applies on the assistant's
 * next turn (no restart), so there is no restart plumbing here.
 */
export function useSystemPrompt() {
  const [config, setConfig] = useState<SystemPromptConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/system-prompt");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: SystemPromptConfig = await res.json();
      setConfig(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const savePrompt = useCallback(
    async (content: string): Promise<SystemPromptSaveResult> => {
      const res = await fetch("/api/settings/system-prompt", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const result = body as SystemPromptSaveResult;
      setConfig(result);
      return result;
    },
    [],
  );

  const resetPrompt = useCallback(async (): Promise<SystemPromptSaveResult> => {
    const res = await fetch("/api/settings/system-prompt", { method: "DELETE" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    const result = body as SystemPromptSaveResult;
    setConfig(result);
    return result;
  }, []);

  return { config, loading, error, refetch, savePrompt, resetPrompt };
}
