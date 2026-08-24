/**
 * The Runs tab of the Automations section — the run history.
 *
 * This file used to be the whole "Tasks" section. The section became
 * Automations (`AutomationsView.tsx`) and the task list folded into its
 * Runs tab; the file keeps its name so history and the route stay traceable.
 *
 * Every task that ran or is running (recurring and one-shot alike) plus the
 * still-pending one-shots, newest activity first, with filter chips. A row
 * expands to the readable result text and the step timeline.
 */
import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, History, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useCancelTask, useDeleteTask, useTaskDetail } from "@/hooks/useAutomations";
import {
  filterRuns,
  formatDuration,
  isActive,
  isRecurringTrigger,
  selectRuns,
  type RunFilter,
  type TaskSummary,
} from "./automations/automationsModel";
import {
  ResultText,
  SectionLabel,
  StateDot,
  StepTimeline,
  formatWhen,
  useStateLabels,
} from "./automations/shared";

const FILTERS: RunFilter[] = ["all", "running", "done", "problems"];

export interface RunsTabProps {
  tasks: TaskSummary[];
  onNotice: (text: string, kind?: "info" | "error") => void;
}

export function RunsTab({ tasks, onNotice }: RunsTabProps) {
  const t = useT();
  const [filter, setFilter] = useState<RunFilter>("all");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const cancelMut = useCancelTask();
  const deleteMut = useDeleteTask();

  const runs = useMemo(() => filterRuns(selectRuns(tasks), filter), [tasks, filter]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            aria-pressed={filter === f}
            className={cn(
              "rounded-md border px-2.5 py-1 text-xs transition-colors",
              filter === f
                ? "border-primary/60 bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:border-primary/30 hover:text-foreground",
            )}
          >
            {t(`automations_view.runs_filter.${f}`)}
          </button>
        ))}
        <div className="ml-auto text-xs text-muted-foreground">
          {runs.length} {t("tasks_view.entries")}
        </div>
      </div>

      {runs.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/60 p-10 text-center">
          <History className="h-7 w-7 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">{t("automations_view.runs_empty")}</p>
        </div>
      ) : (
        <ul className="space-y-2">
          {runs.map((run) => (
            <RunRow
              key={run.id}
              run={run}
              expanded={!!expanded[run.id]}
              onToggle={() => setExpanded((prev) => ({ ...prev, [run.id]: !prev[run.id] }))}
              onCancel={() =>
                cancelMut.mutate(run.id, {
                  onError: (err) => onNotice(`${t("common.error")}: ${(err as Error).message}`, "error"),
                })
              }
              onDelete={() =>
                deleteMut.mutate(
                  { id: run.id, active: isActive(run.state) },
                  { onError: (err) => onNotice(`${t("common.error")}: ${(err as Error).message}`, "error") },
                )
              }
              busy={{
                cancel: cancelMut.isPending && cancelMut.variables === run.id,
                delete: deleteMut.isPending && deleteMut.variables?.id === run.id,
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function RunRow({
  run,
  expanded,
  onToggle,
  onCancel,
  onDelete,
  busy,
}: {
  run: TaskSummary;
  expanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  onDelete: () => void;
  busy: { cancel: boolean; delete: boolean };
}) {
  const t = useT();
  const labels = useStateLabels();
  const when = formatWhen(run.finished_at_ns ?? run.started_at_ns ?? run.due_at_ns ?? run.created_at_ns);
  const duration = formatDuration(run.started_at_ns, run.finished_at_ns);
  const active = isActive(run.state);
  // A recurring automation's row is its latest run; deleting it is the
  // card's job on the Automations tab, so only one-shots offer Delete here.
  const canDelete = !isRecurringTrigger(run.trigger_type) || !active;

  return (
    <li className="card-outline">
      <div className="flex items-center gap-3 px-4 py-3">
        <button
          type="button"
          onClick={onToggle}
          className="rounded-md p-1 text-muted-foreground transition-colors hover:text-foreground"
          aria-label={expanded ? t("tasks_view.collapse") : t("tasks_view.expand")}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        <StateDot state={run.state} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{run.title || t("tasks_view.untitled")}</span>
            <span className="shrink-0 text-[11px] text-muted-foreground">{labels[run.state]}</span>
          </div>
          {(run.last_result || run.last_error) && (
            <p
              className={cn(
                "mt-0.5 truncate text-xs",
                run.last_error && !run.last_result ? "text-destructive/90" : "text-muted-foreground",
              )}
            >
              {run.last_result || run.last_error}
            </p>
          )}
        </div>
        <div className="shrink-0 text-right text-[11px] text-muted-foreground">
          <div>{when}</div>
          {duration && <div className="font-mono">{duration}</div>}
        </div>
        <div className="flex shrink-0 items-center">
          {active && (
            <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy.cancel} title={t("tasks_view.cancel")}>
              <X className="h-4 w-4" />
            </Button>
          )}
          {canDelete && (
            <Button size="sm" variant="ghost" onClick={onDelete} disabled={busy.delete} title={t("tasks_view.delete")}>
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
      {expanded && <RunDetail taskId={run.id} fallbackResult={run.last_result} />}
    </li>
  );
}

function RunDetail({ taskId, fallbackResult }: { taskId: string; fallbackResult?: string | null }) {
  const t = useT();
  const { data, isLoading, error } = useTaskDetail(taskId);
  if (isLoading) {
    return (
      <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
        {t("tasks_view.loading_details")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="border-t border-border px-4 py-3 text-xs text-destructive">
        {t("common.error")}: {(error as Error).message}
      </div>
    );
  }
  const steps = data?.steps ?? [];
  return (
    <div className="space-y-3 border-t border-border px-4 py-3">
      <div>
        <SectionLabel className="mb-1.5">{t("automations_view.result")}</SectionLabel>
        <ResultText steps={steps} fallback={fallbackResult} />
      </div>
      <div>
        <SectionLabel className="mb-1.5">
          {t("automations_view.timeline")} ({steps.length})
        </SectionLabel>
        <StepTimeline steps={steps} />
      </div>
    </div>
  );
}
