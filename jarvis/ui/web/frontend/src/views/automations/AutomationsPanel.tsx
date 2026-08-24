/**
 * The user's recurring automations, as one dense table.
 *
 * This used to be a grid of large cards. On a wide pane those cards stretched
 * to a third of a 4K screen each and a category with a single entry left two
 * thirds of the row empty — a lot of surface saying very little. The section
 * design's table says the same in one line per automation: what it is, when it
 * runs, when it ran last and how that went, with the arm switch and the action
 * menu on the right. A row opens to the latest result and the step timeline.
 */
import { Fragment, useState } from "react";
import { Loader2, MoreHorizontal, Play, Trash2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import {
  ActionMenu,
  Cell,
  EmptyRow,
  IconButton,
  SoftButton,
  Table,
  TableHead,
  TableRow,
  type Column,
} from "@/components/extensions/primitives";
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

export interface AutomationsPanelProps {
  automations: TaskSummary[];
  /** Template key → template, so a row can borrow its catalogue description. */
  templatesByKey: Map<string, AutomationTemplate>;
  highlightId: string | null;
  onRunNow: (id: string) => void;
  onSetEnabled: (id: string, enabled: boolean) => void;
  onDelete: (id: string, active: boolean) => void;
  onCreate: () => void;
  onBrowseCatalogue: () => void;
  busy: {
    runId?: string;
    toggleId?: string;
    deleteId?: string;
  };
  loading: boolean;
}

export function AutomationsPanel({
  automations,
  templatesByKey,
  highlightId,
  onRunNow,
  onSetEnabled,
  onDelete,
  onCreate,
  onBrowseCatalogue,
  busy,
  loading,
}: AutomationsPanelProps) {
  const t = useT();
  const [openId, setOpenId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const columns: Column[] = [
    { id: "name", label: t("automations_view.col_automation") },
    { id: "schedule", label: t("automations_view.schedule_label"), width: "minmax(0, 150px)" },
    { id: "next", label: t("automations_view.next_run"), width: "minmax(0, 120px)" },
    { id: "last", label: t("automations_view.last_run"), width: "minmax(0, 190px)" },
    { id: "toggle", label: t("automations_view.armed"), width: "48px", align: "center", srOnly: true },
    { id: "actions", label: t("automations_view.col_actions"), width: "36px", align: "right", srOnly: true },
  ];

  if (!loading && automations.length === 0) {
    return (
      <EmptyRow>
        <p>{t("automations_view.yours_empty")}</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground/80">
          {t("automations_view.yours_empty_hint")}
        </p>
        <div className="mt-4 flex items-center justify-center gap-2">
          <SoftButton primary onClick={onCreate}>
            {t("automations_view.new_button")}
          </SoftButton>
          <SoftButton onClick={onBrowseCatalogue}>
            {t("automations_view.browse_catalogue")}
          </SoftButton>
        </div>
      </EmptyRow>
    );
  }

  return (
    <Table label={t("automations_view.tab_automations")}>
      <TableHead columns={columns} />
      {automations.map((task) => {
        const key = templateKeyOf(task);
        const template = key ? templatesByKey.get(key) : undefined;
        return (
          <Fragment key={task.id}>
            <AutomationRow
              columns={columns}
              task={task}
              template={template}
              highlighted={highlightId === task.id}
              open={openId === task.id}
              onToggleOpen={() => setOpenId((cur) => (cur === task.id ? null : task.id))}
              onRunNow={() => onRunNow(task.id)}
              onSetEnabled={(on) => onSetEnabled(task.id, on)}
              onAskDelete={() => setConfirmId(task.id)}
              busy={{
                run: busy.runId === task.id,
                toggle: busy.toggleId === task.id,
              }}
            />
            {confirmId === task.id && (
              <ConfirmDeleteRow
                busy={busy.deleteId === task.id}
                onConfirm={() => {
                  onDelete(task.id, isActive(task.state));
                  setConfirmId(null);
                }}
                onKeep={() => setConfirmId(null)}
              />
            )}
            {openId === task.id && (
              <AutomationDetail taskId={task.id} fallbackResult={task.last_result} />
            )}
          </Fragment>
        );
      })}
    </Table>
  );
}

function AutomationRow({
  columns,
  task,
  template,
  highlighted,
  open,
  onToggleOpen,
  onRunNow,
  onSetEnabled,
  onAskDelete,
  busy,
}: {
  columns: Column[];
  task: TaskSummary;
  template?: AutomationTemplate;
  highlighted: boolean;
  open: boolean;
  onToggleOpen: () => void;
  onRunNow: () => void;
  onSetEnabled: (on: boolean) => void;
  onAskDelete: () => void;
  busy: { run: boolean; toggle: boolean };
}) {
  const t = useT();
  const words = useScheduleWords();
  const stateLabels = useStateLabels();
  const Icon = templateIcon(template?.icon);
  const paused = task.state === "paused";
  const running = task.state === "running";
  const lastState = task.last_run_state ?? null;

  return (
    <TableRow
      columns={columns}
      id={`automation-${task.id}`}
      onClick={onToggleOpen}
      selected={open}
      ariaLabel={task.title || t("tasks_view.untitled")}
      className={cn(
        highlighted && "bg-primary/5 ring-1 ring-inset ring-primary/40",
        paused && "opacity-70",
      )}
    >
      <Cell>
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-border bg-secondary/40">
            <Icon className="h-4 w-4 text-primary" />
          </span>
          <span className="min-w-0">
            <span className="block truncate font-medium text-foreground">
              {task.title || t("tasks_view.untitled")}
            </span>
            <AutomationSubline task={task} fallback={template?.description ?? ""} />
          </span>
        </div>
      </Cell>
      <Cell muted className="truncate">
        {scheduleLineForTask(task, words)}
      </Cell>
      <Cell muted>
        {running ? (
          <span className="text-primary">{t("tasks_view.running_now")}</span>
        ) : paused ? (
          <span>{stateLabels.paused}</span>
        ) : (
          <Countdown dueNs={task.next_due_at_ns ?? task.due_at_ns} />
        )}
      </Cell>
      <Cell muted>
        <span className="flex min-w-0 items-center gap-2">
          <StateDot state={lastState} />
          <span className="truncate">
            {lastState ? stateLabels[lastState] : t("automations_view.never_ran")}
            {task.finished_at_ns ? ` · ${formatWhen(task.finished_at_ns)}` : ""}
          </span>
        </span>
      </Cell>
      <Cell align="center" stop>
        <Switch
          checked={!paused}
          disabled={busy.toggle || running}
          onCheckedChange={onSetEnabled}
          aria-label={paused ? t("automations_view.resume") : t("automations_view.pause")}
          title={paused ? t("automations_view.resume") : t("automations_view.pause")}
        />
      </Cell>
      <Cell align="right" stop>
        <ActionMenu
          label={t("automations_view.col_actions")}
          actions={[
            {
              id: "run",
              label: t("automations_view.run_now"),
              icon: busy.run ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />,
              disabled: busy.run || running,
              onSelect: onRunNow,
            },
            {
              id: "delete",
              label: t("tasks_view.delete"),
              icon: <Trash2 className="h-3.5 w-3.5" />,
              destructive: true,
              separatorAbove: true,
              onSelect: onAskDelete,
            },
          ]}
          trigger={({ open: menuOpen, toggle }) => (
            <IconButton
              label={t("automations_view.col_actions")}
              active={menuOpen}
              onClick={toggle}
              className="h-7 w-7"
            >
              <MoreHorizontal className="h-4 w-4" />
            </IconButton>
          )}
        />
      </Cell>
    </TableRow>
  );
}

/** Template description, else the prompt's first line (fetched lazily from
 * the detail spec only when there is no template to describe the task). */
function AutomationSubline({ task, fallback }: { task: TaskSummary; fallback: string }) {
  const needsSpec = !fallback;
  const { data } = useTaskDetail(task.id, needsSpec);
  const text = fallback || firstLine(promptOfSpec(data?.spec));
  if (!text) return null;
  return <span className="block truncate text-xs text-muted-foreground">{text}</span>;
}

/** The one-question delete confirmation, in place under its row. */
function ConfirmDeleteRow({
  busy,
  onConfirm,
  onKeep,
}: {
  busy: boolean;
  onConfirm: () => void;
  onKeep: () => void;
}) {
  const t = useT();
  return (
    <div
      role="presentation"
      className="flex items-center gap-2 border-b border-border/70 bg-destructive/5 px-3 py-2.5 text-sm last:border-b-0"
    >
      <span className="flex-1 text-muted-foreground">{t("automations_view.delete_confirm")}</span>
      <button
        type="button"
        disabled={busy}
        onClick={onConfirm}
        className="inline-flex h-7 items-center gap-1.5 rounded-md bg-destructive px-3 text-xs font-medium text-destructive-foreground transition-colors hover:bg-destructive/90 disabled:opacity-50"
      >
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        {t("tasks_view.delete")}
      </button>
      <SoftButton onClick={onKeep}>{t("automations_view.keep")}</SoftButton>
    </div>
  );
}

function AutomationDetail({
  taskId,
  fallbackResult,
}: {
  taskId: string;
  fallbackResult?: string | null;
}) {
  const t = useT();
  const { data, isLoading, error } = useTaskDetail(taskId);
  if (isLoading) {
    return (
      <div role="presentation" className="border-b border-border/70 bg-sheen/[0.03] px-4 py-3 text-xs text-muted-foreground last:border-b-0">
        {t("tasks_view.loading_details")}
      </div>
    );
  }
  if (error) {
    return (
      <div role="presentation" className="border-b border-border/70 bg-sheen/[0.03] px-4 py-3 text-xs text-destructive last:border-b-0">
        {t("common.error")}: {(error as Error).message}
      </div>
    );
  }
  const steps = data?.steps ?? [];
  return (
    <div role="presentation" className="space-y-3 border-b border-border/70 bg-sheen/[0.03] px-4 py-4 last:border-b-0">
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
