/**
 * One of the user's automations: icon, name, description, schedule line,
 * next-run countdown, last-run dot + result preview, and the three actions
 * (Run now, Pause/Resume, Delete). Expanding shows the latest run's result
 * and the step timeline from the detail endpoint.
 */
import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Play, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useTaskDetail } from "@/hooks/useAutomations";
import { templateIcon } from "./automationIcons";
import {
  firstLine,
  isActive,
  promptOfSpec,
  scheduleLineForTask,
  templateKeyOf,
  type AutomationTemplate,
  type TaskSummary,
} from "./automationsModel";
import {
  Countdown,
  ResultText,
  SectionLabel,
  StateDot,
  StepTimeline,
  formatWhen,
  useScheduleWords,
  useStateLabels,
} from "./shared";

export interface AutomationCardProps {
  task: TaskSummary;
  /** The template the task came from, when the catalogue knows it. */
  template?: AutomationTemplate;
  highlighted?: boolean;
  onRunNow: (id: string) => void;
  onSetEnabled: (id: string, enabled: boolean) => void;
  onDelete: (id: string, active: boolean) => void;
  busy?: { run?: boolean; toggle?: boolean; delete?: boolean };
}

export function AutomationCard({
  task,
  template,
  highlighted,
  onRunNow,
  onSetEnabled,
  onDelete,
  busy,
}: AutomationCardProps) {
  const t = useT();
  const words = useScheduleWords();
  const stateLabels = useStateLabels();
  const [expanded, setExpanded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const Icon = templateIcon(template?.icon);
  const paused = task.state === "paused";
  const running = task.state === "running";
  const description = template?.description ?? "";
  const schedule = scheduleLineForTask(task, words);
  const lastState = task.last_run_state ?? null;

  return (
    <article
      id={`automation-${task.id}`}
      data-template-key={templateKeyOf(task) ?? undefined}
      className={cn(
        "card-outline flex flex-col transition-shadow",
        highlighted && "ring-2 ring-primary/60",
        paused && "opacity-75",
      )}
    >
      <div className="flex items-start gap-3 p-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-primary/30 bg-primary/10">
          <Icon className="h-4 w-4 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold">{task.title || t("tasks_view.untitled")}</h3>
          <CardDescription task={task} fallback={description} />
        </div>
        <Switch
          checked={!paused}
          disabled={busy?.toggle || running}
          onCheckedChange={(on) => onSetEnabled(task.id, on)}
          aria-label={paused ? t("automations_view.resume") : t("automations_view.pause")}
          title={paused ? t("automations_view.resume") : t("automations_view.pause")}
        />
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 border-t border-border/60 px-4 py-3 text-[11px]">
        <dt className="text-muted-foreground">{t("automations_view.schedule_label")}</dt>
        <dd className="truncate text-right text-foreground">{schedule}</dd>
        <dt className="text-muted-foreground">{t("automations_view.next_run")}</dt>
        <dd className="text-right text-foreground">
          {running ? (
            <span className="text-primary">{t("tasks_view.running_now")}</span>
          ) : paused ? (
            <span>{stateLabels.paused}</span>
          ) : (
            <Countdown dueNs={task.next_due_at_ns ?? task.due_at_ns} />
          )}
        </dd>
        <dt className="text-muted-foreground">{t("automations_view.last_run")}</dt>
        <dd className="flex items-center justify-end gap-1.5 text-right">
          <StateDot state={lastState} />
          <span className="truncate">
            {lastState ? stateLabels[lastState] : t("automations_view.never_ran")}
            {task.finished_at_ns ? ` · ${formatWhen(task.finished_at_ns)}` : ""}
          </span>
        </dd>
      </dl>

      {(task.last_result || task.last_error) && (
        <p
          className={cn(
            "line-clamp-2 border-t border-border/60 px-4 py-2.5 text-xs leading-relaxed",
            task.last_error && !task.last_result ? "text-destructive/90" : "text-muted-foreground",
          )}
        >
          {task.last_result || task.last_error}
        </p>
      )}

      <div className="mt-auto flex items-center gap-1 border-t border-border/60 px-2 py-1.5">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onRunNow(task.id)}
          disabled={busy?.run || running}
          title={t("automations_view.run_now")}
        >
          {busy?.run ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
          <span className="ml-1.5 text-xs">{t("automations_view.run_now")}</span>
        </Button>
        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
          aria-expanded={expanded}
        >
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          {t("automations_view.details")}
        </button>
        <div className="ml-auto flex items-center gap-1">
          {confirmDelete ? (
            <>
              <span className="text-[11px] text-muted-foreground">{t("automations_view.delete_confirm")}</span>
              <Button
                size="sm"
                variant="destructive"
                className="h-7 px-2 text-xs"
                disabled={busy?.delete}
                onClick={() => onDelete(task.id, isActive(task.state))}
              >
                {busy?.delete ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t("tasks_view.delete")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                onClick={() => setConfirmDelete(false)}
              >
                {t("automations_view.keep")}
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirmDelete(true)}
              title={t("tasks_view.delete")}
              aria-label={t("tasks_view.delete")}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {expanded && <AutomationDetail taskId={task.id} fallbackResult={task.last_result} />}
    </article>
  );
}

/** Template description, else the prompt's first line (fetched lazily from
 * the detail spec only when there is no template to describe the task). */
function CardDescription({ task, fallback }: { task: TaskSummary; fallback: string }) {
  const needsSpec = !fallback;
  const { data } = useTaskDetail(task.id, needsSpec);
  const text = fallback || firstLine(promptOfSpec(data?.spec));
  if (!text) return null;
  return <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{text}</p>;
}

function AutomationDetail({ taskId, fallbackResult }: { taskId: string; fallbackResult?: string | null }) {
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
        <SectionLabel className="mb-1.5">{t("automations_view.latest_result")}</SectionLabel>
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
