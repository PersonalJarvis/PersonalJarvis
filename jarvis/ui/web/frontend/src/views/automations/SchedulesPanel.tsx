/**
 * Schedules — the one-off timed tasks ("remind me at 18:00", "run this in an
 * hour"). They were always creatable, but they had nowhere to live: a waiting
 * one-shot was mixed into the run history, which reads as a list of things
 * that already happened, so nobody found them. They get their own tab here,
 * sorted forwards, with the moment written out and a countdown beside it.
 */
import { Loader2, MoreHorizontal, Play, Trash2 } from "lucide-react";
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
import { useT, useUiLanguage } from "@/i18n";
import { isActive, type TaskSummary } from "./automationsModel";
import { Countdown, StateDot, formatDueAt, useStateLabels } from "./shared";

export interface SchedulesPanelProps {
  schedules: TaskSummary[];
  onRunNow: (id: string) => void;
  onDelete: (id: string, active: boolean) => void;
  onCreate: () => void;
  busy: { runId?: string; deleteId?: string };
  loading: boolean;
}

export function SchedulesPanel({
  schedules,
  onRunNow,
  onDelete,
  onCreate,
  busy,
  loading,
}: SchedulesPanelProps) {
  const t = useT();
  const locale = useUiLanguage();
  const stateLabels = useStateLabels();

  const columns: Column[] = [
    { id: "name", label: t("automations_view.col_schedule") },
    { id: "due", label: t("automations_view.col_when"), width: "minmax(0, 200px)" },
    { id: "in", label: t("automations_view.col_in"), width: "minmax(0, 110px)" },
    { id: "state", label: t("common.status"), width: "minmax(0, 130px)" },
    { id: "actions", label: t("automations_view.col_actions"), width: "36px", align: "right", srOnly: true },
  ];

  if (!loading && schedules.length === 0) {
    return (
      <EmptyRow>
        <p>{t("automations_view.schedules_empty")}</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground/80">
          {t("automations_view.schedules_empty_hint")}
        </p>
        <div className="mt-4 flex justify-center">
          <SoftButton primary onClick={onCreate}>
            {t("automations_view.new_schedule")}
          </SoftButton>
        </div>
      </EmptyRow>
    );
  }

  return (
    <Table label={t("automations_view.tab_schedules")}>
      <TableHead columns={columns} />
      {schedules.map((task) => (
        <TableRow
          key={task.id}
          columns={columns}
          id={`schedule-${task.id}`}
          ariaLabel={task.title || t("tasks_view.untitled")}
        >
          <Cell>
            <span className="block truncate font-medium text-foreground">
              {task.title || t("tasks_view.untitled")}
            </span>
          </Cell>
          <Cell muted className="truncate">
            {formatDueAt(task.due_at_ns, locale)}
          </Cell>
          <Cell muted>
            <Countdown dueNs={task.due_at_ns} />
          </Cell>
          <Cell muted>
            <span className="flex min-w-0 items-center gap-2">
              <StateDot state={task.state} />
              <span className="truncate">{stateLabels[task.state]}</span>
            </span>
          </Cell>
          <Cell align="right" stop>
            <ActionMenu
              label={t("automations_view.col_actions")}
              actions={[
                {
                  id: "run",
                  label: t("automations_view.run_now"),
                  icon:
                    busy.runId === task.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Play className="h-3.5 w-3.5" />
                    ),
                  disabled: busy.runId === task.id,
                  onSelect: () => onRunNow(task.id),
                },
                {
                  id: "delete",
                  label: t("automations_view.cancel_schedule"),
                  icon:
                    busy.deleteId === task.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    ),
                  destructive: true,
                  separatorAbove: true,
                  disabled: busy.deleteId === task.id,
                  onSelect: () => onDelete(task.id, isActive(task.state)),
                },
              ]}
              trigger={({ open, toggle }) => (
                <IconButton
                  label={t("automations_view.col_actions")}
                  active={open}
                  onClick={toggle}
                  className="h-7 w-7"
                >
                  <MoreHorizontal className="h-4 w-4" />
                </IconButton>
              )}
            />
          </Cell>
        </TableRow>
      ))}
    </Table>
  );
}
