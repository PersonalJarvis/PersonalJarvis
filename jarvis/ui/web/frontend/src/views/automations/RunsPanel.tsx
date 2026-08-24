/**
 * The run history — every task that ran or is running, newest first.
 *
 * Was a stack of full-width cards in `TasksView.tsx`; it is a table now, like
 * the two tabs beside it, so a long history stays scannable. A row opens to
 * the readable result text and the step timeline. Still-waiting one-shots are
 * NOT here any more — they belong to the Schedules tab, which is the whole
 * point of that tab existing.
 */
import { Fragment, useMemo, useState } from "react";
import { Loader2, MoreHorizontal, Trash2, X } from "lucide-react";
import {
  ActionMenu,
  Cell,
  EmptyRow,
  IconButton,
  Table,
  TableHead,
  TableRow,
  type Column,
} from "@/components/extensions/primitives";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useCancelTask, useDeleteTask, useTaskDetail } from "@/hooks/useAutomations";
import {
  filterRuns,
  formatDuration,
  isActive,
  isRecurringTrigger,
  runStateOf,
  selectRuns,
  type RunFilter,
  type TaskSummary,
} from "./automationsModel";
import {
  ResultText,
  SectionLabel,
  StateDot,
  StepTimeline,
  formatWhen,
  useStateLabels,
} from "./shared";

export const RUN_FILTERS: RunFilter[] = ["all", "running", "done", "problems"];

export interface RunsPanelProps {
  tasks: TaskSummary[];
  filter: RunFilter;
  onNotice: (text: string, kind?: "info" | "error") => void;
}

export function RunsPanel({ tasks, filter, onNotice }: RunsPanelProps) {
  const t = useT();
  const stateLabels = useStateLabels();
  const [openId, setOpenId] = useState<string | null>(null);
  const cancelMut = useCancelTask();
  const deleteMut = useDeleteTask();

  const runs = useMemo(() => filterRuns(selectRuns(tasks), filter), [tasks, filter]);

  const columns: Column[] = [
    { id: "name", label: t("automations_view.col_run") },
    { id: "result", label: t("automations_view.result"), width: "minmax(0, 1.1fr)" },
    { id: "when", label: t("automations_view.col_when"), width: "minmax(0, 130px)" },
    { id: "duration", label: t("automations_view.col_duration"), width: "minmax(0, 90px)", align: "right" },
    { id: "actions", label: t("automations_view.col_actions"), width: "36px", align: "right", srOnly: true },
  ];

  if (runs.length === 0) {
    return <EmptyRow>{t("automations_view.runs_empty")}</EmptyRow>;
  }

  return (
    <Table label={t("automations_view.tab_runs")}>
      <TableHead columns={columns} />
      {runs.map((run) => {
        // A recurring automation sits at "scheduled" between runs; the row is
        // about the run, so it reads the derived state, not the task's.
        const state = runStateOf(run);
        const active = run.state === "running";
        // A recurring automation's row is its latest run; deleting it is the
        // Automations tab's job, so only one-shots offer Delete here.
        const canDelete = !isRecurringTrigger(run.trigger_type) || !active;
        const duration = formatDuration(run.started_at_ns, run.finished_at_ns);
        const open = openId === run.id;
        return (
          <Fragment key={run.id}>
            <TableRow
              columns={columns}
              onClick={() => setOpenId((cur) => (cur === run.id ? null : run.id))}
              selected={open}
              ariaLabel={run.title || t("tasks_view.untitled")}
            >
              <Cell>
                <span className="flex min-w-0 items-center gap-2.5">
                  <StateDot state={state} />
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-foreground">
                      {run.title || t("tasks_view.untitled")}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {stateLabels[state]}
                    </span>
                  </span>
                </span>
              </Cell>
              <Cell muted>
                <span
                  className={cn(
                    "block truncate",
                    run.last_error && !run.last_result && "text-destructive/90",
                  )}
                >
                  {run.last_result || run.last_error || "—"}
                </span>
              </Cell>
              <Cell muted className="truncate">
                {formatWhen(
                  run.finished_at_ns ?? run.started_at_ns ?? run.due_at_ns ?? run.created_at_ns,
                )}
              </Cell>
              <Cell muted align="right" className="font-mono">
                {duration ?? "—"}
              </Cell>
              <Cell align="right" stop>
                <ActionMenu
                  label={t("automations_view.col_actions")}
                  actions={[
                    ...(active
                      ? [
                          {
                            id: "cancel",
                            label: t("tasks_view.cancel"),
                            icon:
                              cancelMut.isPending && cancelMut.variables === run.id ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <X className="h-3.5 w-3.5" />
                              ),
                            onSelect: () =>
                              cancelMut.mutate(run.id, {
                                onError: (err) =>
                                  onNotice(
                                    `${t("common.error")}: ${(err as Error).message}`,
                                    "error",
                                  ),
                              }),
                          },
                        ]
                      : []),
                    ...(canDelete
                      ? [
                          {
                            id: "delete",
                            label: t("tasks_view.delete"),
                            icon: <Trash2 className="h-3.5 w-3.5" />,
                            destructive: true,
                            separatorAbove: active,
                            onSelect: () =>
                              deleteMut.mutate(
                                { id: run.id, active: isActive(run.state) },
                                {
                                  onError: (err) =>
                                    onNotice(
                                      `${t("common.error")}: ${(err as Error).message}`,
                                      "error",
                                    ),
                                },
                              ),
                          },
                        ]
                      : []),
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
            {open && <RunDetail taskId={run.id} fallbackResult={run.last_result} />}
          </Fragment>
        );
      })}
    </Table>
  );
}

function RunDetail({ taskId, fallbackResult }: { taskId: string; fallbackResult?: string | null }) {
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
