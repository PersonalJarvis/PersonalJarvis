import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type WorkflowTriggerType = "manual" | "cron";

export interface WorkflowSummary {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  trigger_type: WorkflowTriggerType;
  cron_expression: string | null;
  created_at_ns: number | null;
  created_by: "user" | "seed" | "brain" | string;
  last_run_at_ns: number | null;
  last_run_state: "completed" | "failed" | null;
  next_run_at_ns: number | null;
  step_count: number;
  tags: string[];
}

export interface WorkflowRunStep {
  seq: number;
  kind: string;
  label: string;
  started_at_ns: number;
  finished_at_ns: number | null;
  success: 0 | 1 | null;
  output: string;
  error: string | null;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  state: "pending" | "running" | "completed" | "failed" | "cancelled";
  trigger: string;
  started_at_ns: number;
  finished_at_ns: number;
  error: string | null;
  input_json: string;
  steps?: WorkflowRunStep[];
}

export interface WorkflowDashboard {
  workflows: WorkflowSummary[];
  summary: {
    total: number;
    enabled: number;
    cron_enabled: number;
    next_run_at_ns: number | null;
  };
  recent_runs: WorkflowRun[];
}

export interface WorkflowDetail extends WorkflowSummary {
  definition: Record<string, unknown>;
  recent_runs: WorkflowRun[];
}

async function fetchDashboard(): Promise<WorkflowDashboard> {
  const res = await fetch("/api/workflows");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchWorkflow(id: string): Promise<WorkflowDetail> {
  const res = await fetch(`/api/workflows/${id}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchRun(runId: string): Promise<WorkflowRun> {
  const res = await fetch(`/api/workflows/runs/${runId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function runWorkflow(
  id: string,
  input: Record<string, unknown> = {},
): Promise<{ run_id: string; workflow_id: string }> {
  const res = await fetch(`/api/workflows/${id}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${body}`);
  }
  return res.json();
}

async function toggleWorkflow(id: string, enabled: boolean): Promise<void> {
  const res = await fetch(`/api/workflows/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

async function deleteWorkflow(id: string): Promise<void> {
  const res = await fetch(`/api/workflows/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export interface IntegrationStatus {
  configured: boolean;
  setup_hint: string;
  [key: string]: unknown;
}

export interface IntegrationsResponse {
  telegram: IntegrationStatus & { has_token: boolean; has_chat_id: boolean };
  gws_cli: IntegrationStatus & { path: string | null };
}

async function fetchIntegrations(): Promise<IntegrationsResponse> {
  const res = await fetch("/api/workflows/integrations");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function useWorkflows() {
  return useQuery({
    queryKey: ["workflows"],
    queryFn: fetchDashboard,
    refetchInterval: 4000,
  });
}

export function useIntegrations() {
  return useQuery({
    queryKey: ["workflows", "integrations"],
    queryFn: fetchIntegrations,
    refetchInterval: 30000,
  });
}

export function useWorkflowDetail(id: string | null) {
  return useQuery({
    queryKey: ["workflows", "detail", id],
    queryFn: () => fetchWorkflow(id as string),
    enabled: !!id,
    refetchInterval: 3000,
  });
}

export function useRunDetail(runId: string | null) {
  return useQuery({
    queryKey: ["workflows", "run", runId],
    queryFn: () => fetchRun(runId as string),
    enabled: !!runId,
    refetchInterval: 1500,
  });
}

export function useRunWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
    }: {
      id: string;
      input?: Record<string, unknown>;
    }) => runWorkflow(id, input ?? {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflows"] }),
  });
}

export function useToggleWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toggleWorkflow(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflows"] }),
  });
}

export function useDeleteWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteWorkflow(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflows"] }),
  });
}
