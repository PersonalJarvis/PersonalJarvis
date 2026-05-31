/**
 * useOutputs — Stub nach Filesystem-Reset 2026-04-25.
 *
 * Liefert die OutputSummary-Liste + Plan-Detail aus den FastAPI-Endpoints.
 * Voller Hook (mit Polling-Stop bei abgeschlossenem Run) folgt; aktuell
 * minimal aber funktional.
 */
import { useQuery } from "@tanstack/react-query";

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

export interface OutputSummary {
  slug: string;
  utterance?: string;
  status?: string;
  summary?: string;
  duration_s?: number;
  completed_at?: number;
  started_at?: number;
  github_url?: string | null;
  error?: string | null;
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

export function useArtifactFile(
  slug: string | null,
  path: string | null,
) {
  return useQuery<ArtifactFileResponse>({
    queryKey: ["output-artifact-file", slug, path],
    queryFn: async () => {
      const r = await fetch(
        `/api/outputs/${slug}/files/${encodeURI(path ?? "")}/raw`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    enabled: !!slug && !!path,
    staleTime: 5_000,
  });
}
