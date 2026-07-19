/**
 * useOutputs — stub after the 2026-04-25 filesystem reset.
 *
 * Provides the OutputSummary list + plan detail from the FastAPI endpoints.
 * The full hook (with polling stop on a completed run) is coming; for now
 * this is minimal but functional.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type PlanStepStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "skipped";

export interface PlanStep {
  step_id: string;
  name: string;
  status: PlanStepStatus;
  output?: string;
  error?: string | null;
  duration_s?: number;
  attempts?: number;
  tool_name?: string | null;
  depends_on?: string[];
  parallel_ok?: boolean;
}

export interface PlanSummary {
  plan_id: string;
  vision: string;
  status: string;
  total_steps?: number;
}

export interface PlanResponse {
  plan: PlanSummary | null;
  steps: PlanStep[];
}

/** Must stay in parity with `OUTPUT_STATUSES` in `outputs_routes.py`. */
export const OUTPUT_STATUSES = [
  "running",
  "success",
  "error",
  "cancelled",
  "unknown",
] as const;
export type OutputStatus = (typeof OUTPUT_STATUSES)[number];

export interface OutputSummary {
  slug: string;
  utterance?: string;
  status?: OutputStatus;
  /** Full missions.id when the dir resolved to a DB row — enables cancel. */
  mission_id?: string | null;
  summary?: string;
  duration_s?: number;
  completed_at?: number;
  started_at?: number;
  github_url?: string | null;
  error?: string | null;
  /** Canonical terminal-event reason, when one was recorded. */
  terminal_reason?: string | null;
  terminal_event?: string | null;
  artifact_count?: number;
  /** A non-approved/cancelled mission retained genuine deliverable files. */
  has_partial_output?: boolean;
  /** Review ended without approval, but a genuine deliverable was retained. */
  needs_review?: boolean;
  /** When this terminal mission has already been continued/restarted and that
   *  re-run is still running, the full id + slug of that live child. The card
   *  then shows a "running" indicator pointing at the child instead of a
   *  redundant Continue/Restart button. */
  active_child_id?: string | null;
  active_child_slug?: string | null;
}

export function useOutputsList() {
  return useQuery<OutputSummary[]>({
    queryKey: ["outputs"],
    queryFn: async () => {
      const r = await fetch("/api/outputs");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as
        | OutputSummary[]
        | { sessions?: OutputSummary[] };
      return Array.isArray(data) ? data : data.sessions ?? [];
    },
    staleTime: 5_000,
    refetchInterval: 3_000,
  });
}

export interface CancelMissionResponse {
  ok: boolean;
  mission_id: string;
  state: string;
  worker_killed: boolean;
}

/**
 * Cancels a running mission (hold-to-abort). Flips the mission to
 * CANCELLED server-side and kills the in-flight orchestrator run; the
 * outputs list refetches so the badge flips without waiting for the
 * 3s poll.
 */
export function useCancelMission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (missionId: string) => {
      const r = await fetch(
        `/api/missions/${encodeURIComponent(missionId)}/cancel`,
        { method: "POST" },
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json()) as CancelMissionResponse;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["outputs"] }),
  });
}

export interface RerunMissionResponse {
  ok: boolean;
  parent_mission_id: string;
  mission_id: string;
  action: "continue" | "restart";
  started: boolean;
}

/** Thrown by {@link useRerunMission} when the stored prompt looks destructive
 *  and the server wants an explicit confirmation before re-running. */
export interface RerunRequiresConfirm {
  requiresConfirm: true;
  pattern_id?: string;
  matched_text?: string;
  target_hint?: string;
  warning?: string;
}

/**
 * Re-runs a terminal mission by re-dispatching its original prompt as a new
 * linked mission. Used for "Continue" (cancelled) and "Restart"
 * (failed/timed-out) on the Outputs cards. The source mission is untouched;
 * the new run appears as a fresh card on the next poll, so we invalidate the
 * outputs query on success.
 *
 * A destructive stored prompt yields a 409 `requires_confirm` — re-thrown as a
 * {@link RerunRequiresConfirm} so the button can ask for a second confirming
 * click (no native dialog — those freeze the desktop webview).
 */
export function useRerunMission() {
  const qc = useQueryClient();
  return useMutation<
    RerunMissionResponse,
    RerunRequiresConfirm | Error,
    { missionId: string; confirmed?: boolean }
  >({
    mutationFn: async ({ missionId, confirmed = false }) => {
      const r = await fetch(
        `/api/missions/${encodeURIComponent(missionId)}/rerun`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirmed }),
        },
      );
      if (r.status === 409) {
        const data = await r.json().catch(() => ({}));
        if (data?.requires_confirm) {
          throw { requiresConfirm: true, ...data } as RerunRequiresConfirm;
        }
        throw new Error(data?.detail ?? "HTTP 409");
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json()) as RerunMissionResponse;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["outputs"] }),
  });
}

export function usePlanForOutput(slug: string | null) {
  return useQuery<PlanResponse>({
    queryKey: ["output-plan", slug],
    queryFn: async () => {
      const r = await fetch(`/api/outputs/${slug}/plan`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    enabled: !!slug,
    staleTime: 3_000,
  });
}

export interface ArtifactSummary {
  path: string;
  size: number;
  mtime: number;
  is_text: boolean;
  preview: string | null;
}

export interface ArtifactsResponse {
  files: ArtifactSummary[];
}

export function useArtifactsForOutput(slug: string | null) {
  return useQuery<ArtifactsResponse>({
    queryKey: ["output-artifacts", slug],
    queryFn: async () => {
      const r = await fetch(`/api/outputs/${slug}/artifacts`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    enabled: !!slug,
    staleTime: 3_000,
    refetchInterval: 5_000,
  });
}

export interface ArtifactFileResponse {
  path: string;
  size: number;
  text: string;
  truncated: boolean;
}

/**
 * Encode an artifact relative-path for a URL, segment by segment. `encodeURI`
 * is wrong here: it leaves `#`, `?`, `&` raw, so a filename like `report#2.md`
 * would be silently truncated at the `#` (the server then 404s). Each path
 * component is encoded with `encodeURIComponent`; the `/` separators stay literal.
 */
function encodeArtifactPath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export function useArtifactFile(
  slug: string | null,
  path: string | null,
) {
  return useQuery<ArtifactFileResponse>({
    queryKey: ["output-artifact-file", slug, path],
    queryFn: async () => {
      const r = await fetch(
        `/api/outputs/${slug}/files/${encodeArtifactPath(path ?? "")}/raw`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    enabled: !!slug && !!path,
    staleTime: 5_000,
  });
}

// --- Capabilities hook ----------------------------------------------------

export interface OutputsCapabilities {
  native_file_actions: boolean;
  platform: "win32" | "darwin" | "linux";
}

export function useOutputsCapabilities() {
  return useQuery<OutputsCapabilities>({
    queryKey: ["outputs-capabilities"],
    queryFn: async () => {
      const r = await fetch("/api/outputs/capabilities");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    staleTime: 60_000,
  });
}

// --- Artifact download / open URL helpers ---------------------------------

export function artifactDownloadUrl(slug: string, path: string): string {
  return `/api/outputs/${slug}/files/${encodeArtifactPath(
    path,
  )}/download?disposition=attachment`;
}

export type ArtifactOpenKind = "rendered" | "inline" | "opaque";

const _INLINE_EXT = [
  ".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
];
const _RENDERED_EXT = [
  ".md", ".markdown", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml",
  ".toml", ".log", ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".sh", ".ps1",
];

/** Decide how an artifact opens in the browser, by extension. */
export function classifyArtifact(name: string): ArtifactOpenKind {
  const lower = name.toLowerCase();
  if (_INLINE_EXT.some((e) => lower.endsWith(e))) return "inline";
  if (_RENDERED_EXT.some((e) => lower.endsWith(e))) return "rendered";
  return "opaque";
}

/** The URL the "open in browser" button targets, or null for opaque files. */
export function artifactOpenUrl(slug: string, path: string): string | null {
  const kind = classifyArtifact(path);
  const enc = encodeArtifactPath(path);
  if (kind === "rendered") return `/api/outputs/${slug}/files/${enc}/view`;
  if (kind === "inline")
    return `/api/outputs/${slug}/files/${enc}/download?disposition=inline`;
  return null;
}

export async function revealArtifact(slug: string, path: string): Promise<void> {
  const r = await fetch(
    `/api/outputs/${slug}/files/${encodeArtifactPath(path)}/reveal`,
    { method: "POST" },
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

// --- "Open with" chooser --------------------------------------------------

/** One launchable app in the "open with" chooser. `id` is "default",
 *  "browser", or an editor key like "code"; `label` is the display name. */
export interface OpenerInfo {
  id: string;
  label: string;
}

/** The apps that can open an artifact on this host (desktop only; empty on a
 *  headless VPS, where the UI falls back to opening the render URL in a tab). */
export function useOpeners() {
  return useQuery<OpenerInfo[]>({
    queryKey: ["outputs-openers"],
    queryFn: async () => {
      const r = await fetch("/api/outputs/openers");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as { openers?: OpenerInfo[] };
      return data.openers ?? [];
    },
    staleTime: 60_000,
  });
}

/** The remembered opener id ("" = ask via the chooser on first open). */
export function usePreferredOpener() {
  return useQuery<string>({
    queryKey: ["outputs-preferred-opener"],
    queryFn: async () => {
      const r = await fetch("/api/outputs/preferred-opener");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as { opener?: string };
      return data.opener ?? "";
    },
    staleTime: 30_000,
  });
}

/** Persist the remembered opener id (or "" to clear it). */
export function useSetPreferredOpener() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (opener: string) => {
      const r = await fetch("/api/outputs/preferred-opener", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ opener }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json()) as { opener: string };
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["outputs-preferred-opener"] }),
  });
}

/** Open an artifact in the chosen app (desktop only). The backend resolves the
 *  opener id to an absolute executable and starts a real process. */
export async function openArtifactWith(
  slug: string,
  path: string,
  opener: string,
): Promise<void> {
  const r = await fetch(
    `/api/outputs/${slug}/files/${encodeArtifactPath(path)}/open-with`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ opener }),
    },
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}
