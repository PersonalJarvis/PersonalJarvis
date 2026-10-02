/**
 * The SOUL.md page's data layer — the shapes `soul_routes.py` returns and the
 * three writes the page makes (save SOUL.md, save or clear the standing
 * instructions, forget one note).
 *
 * Every write answers with the whole profile, so the page swaps it in place
 * and never shows a stale count next to a fresh list.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// ----------------------------------------------------------------------
// Response shapes — mirror jarvis/ui/web/soul_routes.py
// ----------------------------------------------------------------------

export type SoulFileId = "soul" | "instructions" | "memory" | "user";
export type NoteTarget = "soul" | "memory" | "user";

export interface SoulEntry {
  id: string;
  text: string;
  importance: number;
  origin: string;
  /** The person asked for it ("remember …"), as opposed to a review's inference. */
  explicit: boolean;
}

export interface SoulRow {
  label: string;
  text: string;
}

interface FileBase {
  filename: string;
  exists: boolean;
  editable: boolean;
  updated_ms: number | null;
  chars: number;
}

export interface CharacterFile extends FileBase {
  id: "soul";
  content: string;
  character: { who: SoulRow[]; tone: SoulRow[]; limits: SoulRow[] };
  learned: SoulEntry[];
  name_in_file: string;
}

export interface InstructionsFile extends FileBase {
  id: "instructions";
  content: string;
  template: string;
}

export interface NotebookFile extends FileBase {
  id: "memory" | "user";
  entries: SoulEntry[];
}

export type SoulFile = CharacterFile | InstructionsFile | NotebookFile;

export interface SoulActivity {
  ts: string;
  source: string;
  target: string;
  operation: string;
  text: string;
  evidence: string;
}

export interface SoulProfile {
  name: string;
  named: boolean;
  product: string;
  wake_phrase: string;
  /** The learning loop runs in this session — notes can be forgotten. */
  learning: boolean;
  files: SoulFile[];
  activity: SoulActivity[];
}

export const SOUL_QUERY_KEY = ["soul"] as const;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return body as T;
}

export function useSoulProfile() {
  return useQuery<SoulProfile, Error>({
    queryKey: SOUL_QUERY_KEY,
    queryFn: () => request<SoulProfile>("/api/soul"),
    retry: false,
  });
}

interface FileById {
  soul: CharacterFile;
  instructions: InstructionsFile;
  memory: NotebookFile;
  user: NotebookFile;
}

/** The one file of each kind; `undefined` while the profile is loading. */
export function fileOf<K extends SoulFileId>(
  profile: SoulProfile | undefined,
  id: K,
): FileById[K] | undefined {
  return profile?.files.find((f) => f.id === id) as FileById[K] | undefined;
}

export function useSaveSoul() {
  const client = useQueryClient();
  return useMutation<SoulProfile, Error, string>({
    mutationFn: (content) =>
      request<SoulProfile>("/api/soul/file", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      }),
    onSuccess: (profile) => client.setQueryData(SOUL_QUERY_KEY, profile),
  });
}

/** Saves the standing instructions; an empty text clears the file. */
export function useSaveInstructions() {
  const client = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (content) =>
      request(
        "/api/settings/agent-instructions",
        content.trim()
          ? {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ content }),
            }
          : { method: "DELETE" },
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: SOUL_QUERY_KEY }),
  });
}

export function useForgetNote() {
  const client = useQueryClient();
  return useMutation<SoulProfile, Error, { target: NoteTarget; id: string }>({
    mutationFn: ({ target, id }) =>
      request<SoulProfile>(
        `/api/soul/entries/${encodeURIComponent(target)}/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      ),
    onSuccess: (profile) => client.setQueryData(SOUL_QUERY_KEY, profile),
  });
}
