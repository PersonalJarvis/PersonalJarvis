import { useCallback, useEffect, useState } from "react";

/**
 * Live state of dictation mode from GET /api/dictation/status.
 *
 * `insertion` is the honest part: on Wayland, on a headless host, or in front
 * of an elevated window the transcript cannot be pasted into another app, and
 * the UI says so up front instead of letting the user discover it by dictating
 * into nothing.
 */
export interface DictationStatus {
  available: boolean;
  active: boolean;
  reason: string;
  hotkey: string;
  mode: string;
  target: string;
  insertion: {
    can_insert: boolean;
    reason: string;
    detail: string;
  };
}

/** One recorded dictation — raw transcript alongside what was inserted. */
export interface DictationEntry {
  id: string;
  created_at: string;
  raw_text: string;
  text: string;
  language: string;
  duration_s: number;
  outcome: string;
  method: string;
  removed_words: number;
  cleanup_reason: string;
}

export interface DictationSettings {
  mode: string;
  target: string;
  insert_method: string;
  paste_chord: string;
  paste_delay_ms: number;
  paste_delay_after_ms: number;
  restore_clipboard: boolean;
  remove_fillers: boolean;
  filler_max_removed_fraction: number;
  max_seconds: number;
  partial_interval_s: number;
  segment_seconds: number;
  history_enabled: boolean;
  history_max_entries: number;
  history_retention_days: number;
}

export interface DictationChoices {
  mode: string[];
  target: string[];
  insert_method: string[];
  paste_chord: string[];
}

async function unwrap<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return body as T;
}

/**
 * Loads dictation status, settings and history, and exposes start/stop plus a
 * partial settings save. Mirrors useDictionary's fetch/error/loading shape.
 */
export function useDictation() {
  const [status, setStatus] = useState<DictationStatus | null>(null);
  const [settings, setSettings] = useState<DictationSettings | null>(null);
  const [choices, setChoices] = useState<DictationChoices | null>(null);
  const [entries, setEntries] = useState<DictationEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetchStatus = useCallback(async () => {
    try {
      const data = await unwrap<DictationStatus>(
        await fetch("/api/dictation/status"),
      );
      setStatus(data);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const refetchHistory = useCallback(async () => {
    try {
      const data = await unwrap<{ entries: DictationEntry[] }>(
        await fetch("/api/dictation/history?limit=50"),
      );
      setEntries(data.entries ?? []);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const data = await unwrap<{
        settings: DictationSettings;
        choices: DictationChoices;
      }>(await fetch("/api/dictation/settings"));
      setSettings(data.settings);
      setChoices(data.choices);
      await Promise.all([refetchStatus(), refetchHistory()]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [refetchStatus, refetchHistory]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const start = useCallback(
    async (target: "auto" | "insert" | "chat" = "auto") => {
      await unwrap(
        await fetch("/api/dictation/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target }),
        }),
      );
      await refetchStatus();
    },
    [refetchStatus],
  );

  const stop = useCallback(async () => {
    await unwrap(await fetch("/api/dictation/stop", { method: "POST" }));
    await refetchStatus();
    await refetchHistory();
  }, [refetchStatus, refetchHistory]);

  const saveSettings = useCallback(
    async (patch: Partial<DictationSettings>) => {
      const data = await unwrap<{ settings: DictationSettings }>(
        await fetch("/api/dictation/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...patch, persist: true }),
        }),
      );
      setSettings(data.settings);
      await refetchStatus();
    },
    [refetchStatus],
  );

  const deleteEntry = useCallback(async (id: string) => {
    await unwrap(
      await fetch(`/api/dictation/history/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
    );
    setEntries((prev) => prev.filter((e) => e.id !== id));
  }, []);

  const clearHistory = useCallback(async () => {
    await unwrap(await fetch("/api/dictation/history", { method: "DELETE" }));
    setEntries([]);
  }, []);

  return {
    status,
    settings,
    choices,
    entries,
    loading,
    error,
    start,
    stop,
    saveSettings,
    deleteEntry,
    clearHistory,
    refetch,
    refetchStatus,
    refetchHistory,
  };
}
