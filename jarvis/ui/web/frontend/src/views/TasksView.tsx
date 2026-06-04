import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ListTodo,
  RefreshCw,
  X,
  Trash2,
  ChevronDown,
  ChevronRight,
  Clock,
  Zap,
  CalendarClock,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

type TaskState =
  | "pending"
  | "scheduled"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

type TriggerType = "after_delay" | "at_time" | "on_event";

interface TaskSummary {
  id: string;
  title: string;
  state: TaskState;
  trigger_type: TriggerType;
  due_at_ns: number | null;
  created_at_ns: number | null;
  started_at_ns: number | null;
  finished_at_ns: number | null;
  attempts: number;
  last_error: string | null;
}

interface TaskStep {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  timestamp_ns: number;
}

interface TaskDetail extends TaskSummary {
  spec: Record<string, unknown> | null;
  steps: TaskStep[];
}

interface TasksListResponse {
  tasks: TaskSummary[];
  total: number;
}

async function fetchTasks(state?: string): Promise<TasksListResponse> {
  const url = state ? `/api/tasks?state=${encodeURIComponent(state)}` : "/api/tasks";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchTask(id: string): Promise<TaskDetail> {
  const res = await fetch(`/api/tasks/${id}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function cancelTask(id: string): Promise<void> {
  const res = await fetch(`/api/tasks/${id}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

async function deleteTask(id: string): Promise<void> {
  const res = await fetch(`/api/tasks/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

function makeStateLabels(t: (k: string) => string): Record<TaskState, string> {
  return {
    pending: t("tasks_view.state.pending"),
    scheduled: t("tasks_view.state.scheduled"),
    running: t("tasks_view.state.running"),
    completed: t("tasks_view.state.completed"),
    failed: t("tasks_view.state.failed"),
    cancelled: t("tasks_view.state.cancelled"),
    interrupted: t("tasks_view.state.interrupted"),
  };
}

const STATE_VARIANT: Record<
  TaskState,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "outline",
  scheduled: "secondary",
  running: "default",
  completed: "secondary",
  failed: "destructive",
  cancelled: "outline",
  interrupted: "destructive",
};

const TRIGGER_ICON: Record<TriggerType, typeof Clock> = {
  after_delay: Clock,
  at_time: CalendarClock,
  on_event: Zap,
};

function makeTriggerLabels(t: (k: string) => string): Record<TriggerType, string> {
  return {
    after_delay: t("tasks_view.trigger_label.after_delay"),
    at_time: t("tasks_view.trigger_label.at_time"),
    on_event: t("tasks_view.trigger_label.on_event"),
  };
}

function makeStateFilters(t: (k: string) => string): { id: string; label: string }[] {
  return [
    { id: "all", label: t("tasks_view.filter_all") },
    { id: "scheduled,running", label: t("tasks_view.filter_active") },
    { id: "completed", label: t("tasks_view.filter_done") },
    { id: "failed,cancelled,interrupted", label: t("tasks_view.filter_problems") },
  ];
}

export function TasksView() {
  const t = useT();
  const STATE_FILTERS = makeStateFilters(t);
  const [stateFilter, setStateFilter] = useState<string>("all");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const qc = useQueryClient();

  const effectiveFilter = stateFilter === "all" ? undefined : stateFilter;
  const { data, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["tasks", stateFilter],
    queryFn: () => fetchTasks(effectiveFilter),
    refetchInterval: 3000,
  });

  const cancelMut = useMutation({
    mutationFn: cancelTask,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });
  const deleteMut = useMutation({
    mutationFn: deleteTask,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });

  const tasks = data?.tasks ?? [];

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<ListTodo className="h-4 w-4 text-primary" />}
        title={t("tasks_view.title")}
        subtitle={t("tasks_view.subtitle")}
        right={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => refetch()}
            disabled={isRefetching}
          >
            <RefreshCw
              className={isRefetching ? "h-4 w-4 animate-spin" : "h-4 w-4"}
            />
          </Button>
        }
      />

      <div className="flex items-center gap-2 border-b border-border px-6 py-3">
        {STATE_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setStateFilter(f.id)}
            className={cn(
              "rounded-md border px-2.5 py-1 text-xs transition-colors",
              stateFilter === f.id
                ? "border-primary/60 bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:border-primary/30 hover:text-foreground",
            )}
          >
            {f.label}
          </button>
        ))}
        <div className="ml-auto text-xs text-muted-foreground">
          {data ? `${data.total} ${t("tasks_view.entries")}` : ""}
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-2 p-6">
          {isLoading && (
            <div className="text-sm text-muted-foreground">{t("tasks_view.loading")}</div>
          )}
          {error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
              {t("tasks_view.load_error")}: {(error as Error).message}
            </div>
          )}
          {!isLoading && !error && tasks.length === 0 && (
            <EmptyState />
          )}
          {tasks.map((t) => (
            <TaskCard
              key={t.id}
              task={t}
              isExpanded={!!expanded[t.id]}
              onToggle={() =>
                setExpanded((prev) => ({ ...prev, [t.id]: !prev[t.id] }))
              }
              onCancel={() => cancelMut.mutate(t.id)}
              onDelete={() => deleteMut.mutate(t.id)}
              isCancelling={cancelMut.isPending && cancelMut.variables === t.id}
              isDeleting={deleteMut.isPending && deleteMut.variables === t.id}
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

function EmptyState() {
  const t = useT();
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border/60 bg-background/30 p-10 text-center">
      <ListTodo className="h-7 w-7 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">{t("tasks_view.empty_text")}</p>
      <p className="max-w-xl text-[11px] leading-relaxed text-muted-foreground/70">
        {t("tasks_view.empty_hint")}
      </p>
    </div>
  );
}

function TaskCard({
  task,
  isExpanded,
  onToggle,
  onCancel,
  onDelete,
  isCancelling,
  isDeleting,
}: {
  task: TaskSummary;
  isExpanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  onDelete: () => void;
  isCancelling: boolean;
  isDeleting: boolean;
}) {
  const t = useT();
  const STATE_LABEL = makeStateLabels(t);
  const TRIGGER_LABEL = makeTriggerLabels(t);
  const Icon = TRIGGER_ICON[task.trigger_type] ?? Clock;
  const canCancel =
    task.state === "scheduled" ||
    task.state === "running" ||
    task.state === "pending";
  const canDelete =
    task.state === "completed" ||
    task.state === "failed" ||
    task.state === "cancelled" ||
    task.state === "interrupted";

  return (
    <article className="card-outline overflow-hidden transition-all hover:shadow-[0_0_24px_rgba(255,214,10,0.06)]">
      <header className="flex items-start gap-3 p-4">
        <button
          type="button"
          onClick={onToggle}
          className="mt-0.5 rounded-md p-1 text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground"
          aria-label={isExpanded ? t("tasks_view.collapse") : t("tasks_view.expand")}
        >
          {isExpanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border bg-secondary/40">
          <Icon className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{task.title || "(ohne Titel)"}</h3>
            <Badge variant={STATE_VARIANT[task.state]}>{STATE_LABEL[task.state]}</Badge>
            {task.attempts > 1 && (
              <span className="text-[10px] font-mono text-muted-foreground">
                #{task.attempts}
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
            <span>{TRIGGER_LABEL[task.trigger_type]}</span>
            <TriggerWhen task={task} />
            <span className="font-mono">{task.id.slice(0, 8)}</span>
          </div>
          {task.last_error && (
            <p className="mt-1.5 line-clamp-2 text-xs text-destructive/90">
              {task.last_error}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {canCancel && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onCancel}
              disabled={isCancelling}
              title={t("tasks_view.cancel")}
            >
              <X className="h-4 w-4" />
            </Button>
          )}
          {canDelete && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onDelete}
              disabled={isDeleting}
              title={t("tasks_view.delete")}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </header>

      {isExpanded && <TaskDetailBody taskId={task.id} />}
    </article>
  );
}

function TaskDetailBody({ taskId }: { taskId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["tasks", "detail", taskId],
    queryFn: () => fetchTask(taskId),
    refetchInterval: 3000,
  });

  if (isLoading) {
    return (
      <div className="border-t border-border bg-background/30 px-5 py-3 text-xs text-muted-foreground">
        Lade Details…
      </div>
    );
  }
  if (error) {
    return (
      <div className="border-t border-border bg-destructive/10 px-5 py-3 text-xs text-destructive">
        Fehler: {(error as Error).message}
      </div>
    );
  }
  if (!data) return null;

  const steps = data.steps ?? [];

  return (
    <div className="space-y-3 border-t border-border bg-background/30 px-5 py-4">
      {data.spec ? (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            Spec
          </div>
          <pre className="max-h-40 overflow-auto rounded-md border border-border bg-card/60 p-3 text-[11px] leading-relaxed">
            {JSON.stringify(data.spec, null, 2)}
          </pre>
        </div>
      ) : null}

      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
          Timeline ({steps.length})
        </div>
        {steps.length === 0 ? (
          <div className="text-xs text-muted-foreground">Noch keine Steps aufgezeichnet.</div>
        ) : (
          <ul className="space-y-1.5">
            {steps.map((s) => (
              <li
                key={s.seq}
                className="flex items-start gap-3 rounded-md border border-border/60 bg-card/40 p-2 text-xs"
              >
                <span className="w-6 font-mono text-muted-foreground">{s.seq}</span>
                <span className="w-20 text-primary">{s.kind}</span>
                <span className="flex-1 font-mono text-muted-foreground break-all">
                  {summarizePayload(s.payload)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function TriggerWhen({ task }: { task: TaskSummary }) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const label = useMemo(() => {
    if (task.trigger_type === "on_event") return "wartet auf Event";
    if (task.state === "running") return "läuft gerade";
    if (task.finished_at_ns) return `fertig ${formatWhen(task.finished_at_ns)}`;
    if (task.due_at_ns) {
      const delta = task.due_at_ns - Date.now() * 1e6;
      if (delta > 0) return `in ${formatDelta(delta)}`;
      return "fällig";
    }
    return "—";
    // `tick` erzwingt Re-Render fuer den Countdown
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task, tick]);

  return <span>{label}</span>;
}

function formatDelta(ns: number): string {
  const sec = Math.max(0, Math.round(ns / 1e9));
  if (sec < 60) return `${sec}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}min`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr}h`;
  const day = Math.round(hr / 24);
  return `${day}d`;
}

function formatWhen(ns: number): string {
  try {
    const d = new Date(ns / 1e6);
    return d.toLocaleTimeString();
  } catch {
    return "";
  }
}

function summarizePayload(p: Record<string, unknown>): string {
  try {
    const s = JSON.stringify(p);
    return s.length > 140 ? s.slice(0, 139) + "…" : s;
  } catch {
    return "(unlesbar)";
  }
}
