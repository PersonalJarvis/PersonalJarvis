import { useCallback, useEffect, useState } from "react";

/**
 * One STT-dictionary entry from /api/dictionary: the canonical `word` plus
 * the misheard variants that should be rewritten into it. An empty
 * `misheard` list means "plain vocabulary word" (casing + near-miss repair).
 */
export interface DictionaryEntry {
  id: string;
  word: string;
  misheard: string[];
  created_at: string;
  updated_at: string;
}

export interface DictionaryEntryPayload {
  word: string;
  misheard: string[];
}

async function unwrap<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      (body as { detail?: string }).detail ?? `HTTP ${res.status}`,
    );
  }
  return body as T;
}

/**
 * Loads /api/dictionary and exposes create/update/remove. Corrections apply
 * on the next utterance (the backend corrector live-reloads), so there is no
 * restart plumbing here — mirrors useSystemPrompt.
 */
export function useDictionary() {
  const [entries, setEntries] = useState<DictionaryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/dictionary");
      const data = await unwrap<{ entries: DictionaryEntry[] }>(res);
      setEntries(data.entries ?? []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const createEntry = useCallback(
    async (payload: DictionaryEntryPayload): Promise<DictionaryEntry> => {
      const res = await fetch("/api/dictionary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const created = await unwrap<DictionaryEntry>(res);
      setEntries((prev) => [...prev, created]);
      return created;
    },
    [],
  );

  const updateEntry = useCallback(
    async (
      id: string,
      payload: Partial<DictionaryEntryPayload>,
    ): Promise<DictionaryEntry> => {
      const res = await fetch(`/api/dictionary/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const updated = await unwrap<DictionaryEntry>(res);
      setEntries((prev) => prev.map((e) => (e.id === id ? updated : e)));
      return updated;
    },
    [],
  );

  const removeEntry = useCallback(async (id: string): Promise<void> => {
    const res = await fetch(`/api/dictionary/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    await unwrap<{ ok: boolean }>(res);
    setEntries((prev) => prev.filter((e) => e.id !== id));
  }, []);

  return { entries, loading, error, refetch, createEntry, updateEntry, removeEntry };
}
