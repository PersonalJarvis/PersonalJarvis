/**
 * Small building blocks shared by the Automations cards and the Runs tab:
 * the state dot, the live countdown, the schedule words, the step timeline
 * and the readable result block. All colours come from theme tokens (plus
 * the two semantic tailwind hues the rest of the app already uses for
 * "good" and "attention"), so light mode keeps its ink-outline look.
 */
import { useEffect, useMemo, useState } from "react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  extractAgentResult,
  formatDelta,
  summarizePayload,
  type ScheduleWords,
  type TaskState,
  type TaskStep,
} from "./automationsModel";

export function useScheduleWords(): ScheduleWords {
  const t = useT();
  return useMemo(
    () => ({
      hourly: t("automations_view.schedule.hourly"),
      daily: t("automations_view.schedule.daily"),
      weekly: t("automations_view.schedule.weekly"),
      everyMinutes: t("automations_view.schedule.every_minutes"),
      everyHours: t("automations_view.schedule.every_hours"),
      onEvent: t("automations_view.schedule.on_event"),
      weekdays: [
        t("automations_view.weekday.0"),
        t("automations_view.weekday.1"),
        t("automations_view.weekday.2"),
        t("automations_view.weekday.3"),
        t("automations_view.weekday.4"),
        t("automations_view.weekday.5"),
        t("automations_view.weekday.6"),
      ],
    }),
    [t],
  );
}

export function useStateLabels(): Record<TaskState, string> {
  const t = useT();
  return useMemo(
    () => ({
      pending: t("tasks_view.state.pending"),
      scheduled: t("tasks_view.state.scheduled"),
      running: t("tasks_view.state.running"),
      paused: t("tasks_view.state.paused"),
      completed: t("tasks_view.state.completed"),
      failed: t("tasks_view.state.failed"),
      cancelled: t("tasks_view.state.cancelled"),
      interrupted: t("tasks_view.state.interrupted"),
    }),
    [t],
  );
}

const DOT_CLASS: Record<TaskState, string> = {
  pending: "bg-muted-foreground/50",
  scheduled: "bg-foreground/70",
  running: "bg-foreground/70 animate-pulse",
  paused: "bg-muted-foreground/50",
  completed: "bg-muted-foreground",
  failed: "bg-destructive",
  cancelled: "bg-muted-foreground/50",
  interrupted: "bg-foreground",
};

/** A coloured state dot with the localized state as its accessible name. */
export function StateDot({ state, className }: { state: TaskState | null | undefined; className?: string }) {
  const labels = useStateLabels();
  if (!state) {
    return (
      <span
        aria-hidden
        className={cn("inline-block h-2 w-2 shrink-0 rounded-full border border-border", className)}
      />
    );
  }
  return (
    <span
      role="img"
      aria-label={labels[state]}
      title={labels[state]}
      className={cn("inline-block h-2 w-2 shrink-0 rounded-full", DOT_CLASS[state], className)}
    />
  );
}

/** Re-renders once a second so countdowns tick. */
export function useTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return tick;
}

/** "in 7min" / "due now" for a future ns timestamp. */
export function Countdown({ dueNs }: { dueNs: number | null | undefined }) {
  const t = useT();
  useTick();
  if (!dueNs) return <span>—</span>;
  const delta = dueNs - Date.now() * 1e6;
  if (delta <= 0) return <span>{t("tasks_view.due_now")}</span>;
  return (
    <span>
      {t("tasks_view.in_prefix")} {formatDelta(delta)}
    </span>
  );
}

export function formatWhen(ns: number | null | undefined, locale?: string): string {
  if (!ns) return "";
  try {
    const d = new Date(ns / 1e6);
    const sameDay = d.toDateString() === new Date().toDateString();
    return sameDay
      ? d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleString(locale, {
          day: "2-digit",
          month: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        });
  } catch {
    return "";
  }
}

/**
 * A schedule's moment, written out: "Wed, 27.08., 07:00".
 *
 * `formatWhen` above answers "when did this happen" and drops the weekday for
 * anything today; a schedule is read forwards, and the weekday is the part a
 * person checks first ("is that before or after the weekend?").
 */
export function formatDueAt(ns: number | null | undefined, locale?: string): string {
  if (!ns) return "—";
  try {
    const d = new Date(ns / 1e6);
    return d.toLocaleString(locale, {
      weekday: "short",
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

/** The run's result as readable text (the `agent_result` step), never raw JSON. */
export function ResultText({ steps, fallback }: { steps: TaskStep[] | undefined; fallback?: string | null }) {
  const t = useT();
  const text = extractAgentResult(steps) ?? fallback ?? null;
  if (!text) {
    return <p className="text-xs text-muted-foreground">{t("automations_view.no_result_yet")}</p>;
  }
  return (
    <div
      data-testid="run-result"
      className="whitespace-pre-wrap rounded-xl border border-border/70 bg-background/30 px-4 py-3 text-sm leading-relaxed text-foreground"
    >
      {text}
    </div>
  );
}

/** The step timeline of one run. */
export function StepTimeline({ steps }: { steps: TaskStep[] }) {
  const t = useT();
  if (steps.length === 0) {
    return <div className="text-xs text-muted-foreground">{t("tasks_view.no_steps")}</div>;
  }
  return (
    <ol className="space-y-1">
      {steps.map((s) => (
        <li
          key={s.seq}
          className="flex items-start gap-3 rounded-md border border-border/60 px-2.5 py-1.5 text-[11px]"
        >
          <span className="w-5 shrink-0 font-mono text-muted-foreground">{s.seq}</span>
          <span className="w-20 shrink-0 text-primary">{s.kind}</span>
          <span className="min-w-0 flex-1 break-words text-muted-foreground">
            {summarizePayload(s.payload)}
          </span>
          <span className="shrink-0 font-mono text-muted-foreground/70">
            {formatWhen(s.timestamp_ns)}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Section label — small caps, the way the rest of the app labels groups. */
export function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("text-[11px] font-medium uppercase tracking-wider text-muted-foreground", className)}>
      {children}
    </div>
  );
}
