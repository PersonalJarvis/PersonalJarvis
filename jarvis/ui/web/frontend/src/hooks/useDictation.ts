import { useCallback, useEffect, useState } from "react";

import { robustCopy } from "@/lib/clipboard";

/**
 * The complete outcome vocabulary of one dictation, mirroring
 * `jarvis.dictation.outcomes.DICTATION_OUTCOMES`.
 *
 * This array is the TypeScript half of a cross-layer parity test — the Python
 * tuple and this list must stay set-equal, and every entry must have a
 * `dictation.outcome.{name}` key in every locale. Never render a raw outcome
 * string in the UI; always translate through that key.
 */
export const DICTATION_OUTCOMES = [
  "inserted",
  "clipboard_only",
  "unavailable",
  "chat",
  "empty",
  "cancelled",
  "failed",
] as const;

export type DictationOutcome = (typeof DICTATION_OUTCOMES)[number];

/** Languages dictation can be pinned to; `auto` lets the provider decide. */
export type DictationLanguage = "auto" | "de" | "en" | "es";

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
  /** Hands-free (press once to start, again to stop) combo, "" when unbound. */
  hotkey_toggle?: string;
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
  /**
   * One of DICTATION_OUTCOMES. Typed as a union *or* string on purpose: a
   * newer backend must never crash an older bundle, so an unknown value falls
   * through to a neutral badge instead of a type error.
   */
  outcome: DictationOutcome | string;
  method: string;
  removed_words: number;
  cleanup_reason: string;
  word_count: number;
  /** Soft-deleted: hidden from the default list, still restorable. */
  discarded: boolean;
  /** Audio was kept for this entry, so Restore can transcribe it again. */
  audio_available: boolean;
  error: string | null;
}

/**
 * Aggregate dictation numbers from GET /api/dictation/stats.
 *
 * `source` decides the panel's honesty: `"lifetime"` means the never-pruned
 * sidecar answered and the totals really are all-time; `"window"` means they
 * were derived from the rolling history window, and the UI must say so rather
 * than calling a 30-day slice "all time".
 */
export interface DictationStats {
  source: "lifetime" | "window";
  window: { days: number; max_entries: number };
  totals: { dictations: number; words: number; seconds: number; wpm: number };
  today: { dictations: number; words: number };
  streak: { current_days: number; longest_days: number };
  by_day: { date: string; dictations: number; words: number; seconds: number }[];
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
  language: DictationLanguage | string;
  keep_failed_audio: boolean;
  audio_retention_days: number;
  audio_max_files: number;
}

export interface DictationChoices {
  mode: string[];
  target: string[];
  insert_method: string[];
  paste_chord: string[];
  language: string[];
}

/** Result of POST /api/dictation/history/{id}/restore. */
export interface DictationRestoreResult {
  ok: boolean;
  entry: DictationEntry;
  retranscribed: boolean;
  detail: string | null;
}

async function unwrap<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return body as T;
}

/**
 * Loads dictation status, settings, history and stats, and exposes start/stop,
 * a partial settings save, and the four per-entry actions (copy, discard,
 * restore, hard delete). Mirrors useDictionary's fetch/error/loading shape.
 */
export function useDictation() {
  const [status, setStatus] = useState<DictationStatus | null>(null);
  const [settings, setSettings] = useState<DictationSettings | null>(null);
  const [choices, setChoices] = useState<DictationChoices | null>(null);
  const [entries, setEntries] = useState<DictationEntry[]>([]);
  const [stats, setStats] = useState<DictationStats | null>(null);
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
      // Discarded entries stay in the list: they are the ones the Restore
      // button exists for, and filtering them out would make Restore
      // unreachable from the UI that owns it.
      const data = await unwrap<{ entries: DictationEntry[] }>(
        await fetch("/api/dictation/history?limit=50&include_discarded=true"),
      );
      setEntries(data.entries ?? []);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const refetchStats = useCallback(async () => {
    // Stats are an informational strip. A backend that cannot answer must not
    // blank the whole view with a red error line — the strip just stays away.
    try {
      const data = await unwrap<DictationStats>(
        await fetch("/api/dictation/stats"),
      );
      setStats(data);
    } catch {
      setStats(null);
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
      await Promise.all([refetchStatus(), refetchHistory(), refetchStats()]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [refetchStatus, refetchHistory, refetchStats]);

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
    await refetchStats();
  }, [refetchStatus, refetchHistory, refetchStats]);

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

  /**
   * Soft delete. The entry stays in the list wearing a "Discarded" badge —
   * filtering it out here would strand the Restore button that is the whole
   * point of a recoverable delete.
   */
  const discardEntry = useCallback(async (id: string) => {
    const data = await unwrap<{ entry: DictationEntry }>(
      await fetch(`/api/dictation/history/${encodeURIComponent(id)}/discard`, {
        method: "POST",
      }),
    );
    setEntries((prev) =>
      prev.map((e) =>
        e.id === id ? (data.entry ?? { ...e, discarded: true }) : e,
      ),
    );
  }, []);

  /** Un-discards, and re-transcribes from the kept audio when there is text to win back. */
  const restoreEntry = useCallback(async (id: string) => {
    const data = await unwrap<DictationRestoreResult>(
      await fetch(`/api/dictation/history/${encodeURIComponent(id)}/restore`, {
        method: "POST",
      }),
    );
    setEntries((prev) =>
      prev.map((e) =>
        e.id === id ? (data.entry ?? { ...e, discarded: false }) : e,
      ),
    );
    return data;
  }, []);

  /** Hard delete — gone from disk, audio sidecar included. */
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
    await refetchStats();
  }, [refetchStats]);

  /**
   * Copies one entry's delivered text (falling back to the raw transcript).
   * Returns false when every clipboard path failed, so the caller can say so
   * instead of claiming a copy that never happened.
   */
  const copyEntry = useCallback(
    async (id: string) => {
      const entry = entries.find((e) => e.id === id);
      if (!entry) return false;
      return robustCopy(entry.text || entry.raw_text);
    },
    [entries],
  );

  return {
    status,
    settings,
    choices,
    entries,
    stats,
    loading,
    error,
    start,
    stop,
    saveSettings,
    copyEntry,
    discardEntry,
    restoreEntry,
    deleteEntry,
    clearHistory,
    refetch,
    refetchStatus,
    refetchHistory,
    refetchStats,
  };
}
