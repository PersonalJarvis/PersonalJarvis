/**
 * The task API under agent routines (`/api/tasks`).
 *
 * An agent's routine is a tagged task in the backend scheduler
 * (`jarvis/society/routines.py`); the agent card reads one and runs, pauses,
 * stops or deletes it through these calls. Types mirror
 * `jarvis/tasks/schema.py` (`TASK_STATES`) and `jarvis/ui/web/tasks_routes.py`.
 */

export type TaskState =
  | "pending"
  | "scheduled"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type TriggerType = "after_delay" | "at_time" | "on_event" | "every" | "calendar" | "webhook" | "event_hook" | "source" | "cron";

export interface TaskSummary {
  id: string;
  title: string;
  state: TaskState;
  trigger_type: TriggerType;
  trigger?: unknown;
  due_at_ns: number | null;
  created_at_ns: number | null;
  started_at_ns: number | null;
  finished_at_ns: number | null;
  attempts: number;
  last_error: string | null;
  // Older backends omit these, so every field is optional.
  tags?: string[];
  created_by?: string | null;
  interval_seconds?: number | null;
  next_due_at_ns?: number | null;
  last_run_state?: TaskState | null;
  last_result?: string | null;
}

export interface TaskStep {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  timestamp_ns: number;
}

export interface TaskDetail extends TaskSummary {
  spec: Record<string, unknown> | null;
  steps: TaskStep[];
}

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

export async function fetchTask(id: string): Promise<TaskDetail> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(id)}`, { cache: "no-store" });
  if (!res.ok) throw await readError(res);
  return res.json();
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
