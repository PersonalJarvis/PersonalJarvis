/**
 * Data hooks for the Automations section (react-query over `/api/tasks`).
 *
 * The list polls every 3 s, the same cadence the old Tasks view used, so a
 * countdown and a "running" dot stay honest without a socket. The template
 * catalogue is the one endpoint that may not exist yet on an older backend
 * (the routes ship with this campaign and only go live after a restart) —
 * a 404 there is reported as `unavailable`, never as an error, so the view
 * can say so inline instead of crashing.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useUiLanguage } from "@/i18n";
import type {
  TaskDetail,
  TasksListResponse,
  TemplateSchedule,
  TemplatesResponse,
} from "@/views/automations/automationsModel";

export const TASKS_QUERY_KEY = ["tasks"] as const;
export const TEMPLATES_QUERY_KEY = ["tasks", "templates"] as const;

/** An HTTP failure with its status attached, so callers can tell a 404
 * (route not live yet) from a 409 (already running) and word the notice. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message?: string) {
    super(message ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}

async function readError(res: Response): Promise<ApiError> {
  let detail = "";
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    // A non-JSON error body carries no extra message; the status is enough.
  }
  return new ApiError(res.status, detail || `HTTP ${res.status}`);
}

export async function fetchTasks(): Promise<TasksListResponse> {
  const res = await fetch("/api/tasks", { cache: "no-store" });
  if (!res.ok) throw await readError(res);
  return res.json();
}

export async function fetchTask(id: string): Promise<TaskDetail> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(id)}`, { cache: "no-store" });
  if (!res.ok) throw await readError(res);
  return res.json();
}

export interface TemplatesResult extends TemplatesResponse {
  /** `true` when the backend has no catalogue route yet (pre-restart). */
  unavailable: boolean;
}

export async function fetchTemplates(locale: string): Promise<TemplatesResult> {
  const res = await fetch(`/api/tasks/templates?locale=${encodeURIComponent(locale)}`, {
    cache: "no-store",
  });
  if (res.status === 404 || res.status === 405) {
    return { templates: [], categories: [], unavailable: true };
  }
  if (!res.ok) throw await readError(res);
  const body = (await res.json()) as TemplatesResponse;
  return { ...body, unavailable: false };
}

export async function runTaskNow(id: string): Promise<void> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(id)}/run`, { method: "POST" });
  if (!res.ok) throw await readError(res);
}

export async function setTaskEnabled(id: string, enabled: boolean): Promise<void> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw await readError(res);
}

export async function cancelTask(id: string): Promise<void> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  if (!res.ok) throw await readError(res);
}

export async function deleteTask(id: string): Promise<void> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw await readError(res);
}

/**
 * One-click delete: an active task must be cancelled before the backend
 * accepts a DELETE, so cancel first (a 4xx there means it was already
 * terminal — fine) and then delete.
 */
export async function cancelAndDeleteTask(id: string, active: boolean): Promise<void> {
  if (active) {
    try {
      await cancelTask(id);
    } catch (err) {
      // Already terminal (409/404) — the delete below decides. Anything else
      // (a 5xx) is a real failure and must surface.
      if (!(err instanceof ApiError) || err.status >= 500) throw err;
    }
  }
  await deleteTask(id);
}

export interface AddTemplatePayload {
  inputs: Record<string, string>;
  schedule?: TemplateSchedule;
  title?: string;
  locale: string;
}

export async function addTemplate(key: string, payload: AddTemplatePayload): Promise<{ id: string }> {
  const res = await fetch(`/api/tasks/templates/${encodeURIComponent(key)}/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await readError(res);
  return res.json();
}

// ---------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------

export function useTasks() {
  return useQuery({
    queryKey: TASKS_QUERY_KEY,
    queryFn: fetchTasks,
    refetchInterval: 3000,
  });
}

export function useTaskDetail(id: string, enabled = true) {
  return useQuery({
    queryKey: [...TASKS_QUERY_KEY, "detail", id],
    queryFn: () => fetchTask(id),
    refetchInterval: 3000,
    enabled,
  });
}

export function useTemplates() {
  const locale = useUiLanguage();
  return useQuery({
    queryKey: [...TEMPLATES_QUERY_KEY, locale],
    queryFn: () => fetchTemplates(locale),
    // The catalogue is static per backend; readiness can change when a plugin
    // connects, so a slow poll keeps the "needs Gmail" hints truthful.
    refetchInterval: 15_000,
  });
}

function useInvalidateTasks() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: TASKS_QUERY_KEY });
}

export function useRunNow() {
  const invalidate = useInvalidateTasks();
  return useMutation({ mutationFn: runTaskNow, onSettled: invalidate });
}

export function useSetEnabled() {
  const invalidate = useInvalidateTasks();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => setTaskEnabled(id, enabled),
    onSettled: invalidate,
  });
}

export function useCancelTask() {
  const invalidate = useInvalidateTasks();
  return useMutation({ mutationFn: cancelTask, onSettled: invalidate });
}

export function useDeleteTask() {
  const invalidate = useInvalidateTasks();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => cancelAndDeleteTask(id, active),
    onSettled: invalidate,
  });
}

export function useAddTemplate() {
  const invalidate = useInvalidateTasks();
  return useMutation({
    mutationFn: ({ key, payload }: { key: string; payload: AddTemplatePayload }) =>
      addTemplate(key, payload),
    onSuccess: invalidate,
  });
}
