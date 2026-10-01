import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock } from "lucide-react";

import { fetchTasks, TASKS_QUERY_KEY } from "@/hooks/useAutomations";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  localTimeHHMM,
  selectAutomations,
  selectSchedules,
  type TaskSummary,
} from "@/views/automations/automationsModel";

/** How many routines the sidebar names before the Automations page takes over. */
export const SIDEBAR_SCHEDULED_MAX = 3;

/**
 * "Scheduled" in the sidebar — the routines that run next, between the
 * sections and the chat history, the way the Claude app lists its scheduled
 * tasks there.
 *
 * Real rows only: the user's recurring automations and waiting one-off
 * schedules from `/api/tasks`, soonest first. With nothing scheduled (or no
 * answer yet) the section is absent — an empty heading would be filler.
 * The list shares the Automations page's query key, read at a slow cadence
 * here because the sidebar is on screen all day.
 */
export function SidebarScheduled() {
  const t = useT();
  const setActive = useEventStore((s) => s.setActiveSection);
  const { data } = useQuery({
    queryKey: TASKS_QUERY_KEY,
    queryFn: fetchTasks,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
  const upcoming = useMemo(() => upcomingRoutines(data?.tasks ?? []), [data]);
  if (upcoming.length === 0) return null;

  return (
    <section className="mt-5 px-2" aria-label={t("sidebar.scheduled")} data-testid="sidebar-scheduled">
      <h2 className="px-3 pb-1.5 text-sm text-muted-foreground">{t("sidebar.scheduled")}</h2>
      <ul className="space-y-px">
        {upcoming.map((task) => {
          const title = routineTitle(task.title);
          const next = nextRunNs(task);
          return (
            <li key={task.id}>
              <button
                type="button"
                onClick={() => setActive("tasks")}
                title={title}
                data-testid="sidebar-scheduled-row"
                className="flex h-8 w-full items-center gap-3 rounded-lg px-3 text-left text-base text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Clock aria-hidden strokeWidth={1.75} className="h-[18px] w-[18px] shrink-0" />
                <span className="min-w-0 flex-1 truncate">{title}</span>
                {next !== null && (
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{whenLabel(next)}</span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function nextRunNs(task: TaskSummary): number | null {
  const ns = task.next_due_at_ns ?? task.due_at_ns;
  return typeof ns === "number" && Number.isFinite(ns) ? ns : null;
}

/** Recurring automations and waiting one-offs, soonest run first. */
export function upcomingRoutines(tasks: TaskSummary[]): TaskSummary[] {
  return [...selectAutomations(tasks), ...selectSchedules(tasks)]
    .filter((task) => task.state !== "paused")
    .sort((a, b) => (nextRunNs(a) ?? Number.MAX_SAFE_INTEGER) - (nextRunNs(b) ?? Number.MAX_SAFE_INTEGER))
    .slice(0, SIDEBAR_SCHEDULED_MAX);
}

/** An agent's routine carries its owner as a "[agent:Name] " prefix; the row shows the job. */
export function routineTitle(title: string): string {
  return title.replace(/^\s*\[agent:[^\]]*\]\s*/i, "").trim() || title.trim();
}

/** The clock for today, a short weekday for anything later. */
function whenLabel(ns: number, now: Date = new Date()): string {
  const at = new Date(ns / 1e6);
  if (at.toDateString() === now.toDateString()) return localTimeHHMM(ns);
  return at.toLocaleDateString(undefined, { weekday: "short" });
}
