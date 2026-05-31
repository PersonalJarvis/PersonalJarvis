import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type JobType = "shell" | "http" | "agent";
export type ScheduleType = "cron" | "interval" | "manual" | "webhook";
export type RunState =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface JobSummary {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  type: JobType;
  schedule_type: ScheduleType;
  schedule_expr: string | null;
  created_at_ns: number | null;
  last_run_at_ns: number | null;
  last_run_state: "completed" | "failed" | null;
  next_run_at_ns: number | null;
  tags: string[];
  spec: Record<string, unknown>;
  schedule: Record<string, unknown>;
  webhook_token: string | null;
}

export interface RunRow {
  id: string;
  job_id: string;
  state: RunState;
  trigger: string;
  started_at_ns: number;
  finished_at_ns: number;
  exit_code: number | null;
  output: string;
  error: string | null;
  input_json: string;
  metrics_json: string;
}

export interface DashboardResponse {
  jobs: JobSummary[];
  summary: {
    total: number;
    enabled: number;
    by_type: Record<string, number>;
  };
  recent_runs: RunRow[];
}

export interface JobDetail extends JobSummary {
  recent_runs: RunRow[];
}

async function fetchDashboard(): Promise<DashboardResponse> {
  const r = await fetch("/api/conductor/jobs");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function fetchJob(id: string): Promise<JobDetail> {
  const r = await fetch(`/api/conductor/jobs/${id}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function fetchRun(id: string): Promise<RunRow> {
  const r = await fetch(`/api/conductor/runs/${id}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function runJob(
  id: string,
  input: Record<string, unknown> = {},
): Promise<{ run_id: string; job_id: string }> {
  const r = await fetch(`/api/conductor/jobs/${id}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status}: ${txt}`);
  }
  return r.json();
}

async function toggleJob(id: string, enabled: boolean): Promise<void> {
  const r = await fetch(`/api/conductor/jobs/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

async function deleteJob(id: string): Promise<void> {
  const r = await fetch(`/api/conductor/jobs/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

async function createJobFromJson(job: unknown): Promise<{ id: string }> {
  const r = await fetch("/api/conductor/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(job),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status}: ${txt}`);
  }
  return r.json();
}

export function useConductorDashboard() {
  return useQuery({
    queryKey: ["conductor", "dashboard"],
    queryFn: fetchDashboard,
    refetchInterval: 3000,
  });
}

export function useConductorJob(id: string | null) {
  return useQuery({
    queryKey: ["conductor", "job", id],
    queryFn: () => fetchJob(id as string),
    enabled: !!id,
    refetchInterval: 3000,
  });
}

export function useConductorRun(id: string | null) {
  return useQuery({
    queryKey: ["conductor", "run", id],
    queryFn: () => fetchRun(id as string),
    enabled: !!id,
    refetchInterval: 1500,
  });
}

export function useRunConductorJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
    }: {
      id: string;
      input?: Record<string, unknown>;
    }) => runJob(id, input ?? {}),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["conductor"] }),
  });
}

export function useToggleConductorJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toggleJob(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conductor"] }),
  });
}

export function useDeleteConductorJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteJob(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conductor"] }),
  });
}

export function useCreateConductorJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (job: unknown) => createJobFromJson(job),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conductor"] }),
  });
}
