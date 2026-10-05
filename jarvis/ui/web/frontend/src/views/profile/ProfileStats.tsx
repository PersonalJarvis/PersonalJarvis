/**
 * Four figures under the profile header: conversations, agent runs, the
 * longest streak and the current one. Each comes from a real source (the
 * activity board); a figure that has not loaded shows a skeleton, never a
 * zero, because a zero reads as a fact.
 */
import { useBoardSummary } from "@/hooks/useBoard";
import { useBoardInsights } from "@/hooks/useBoardInsights";
import { fill, useT, useUiLanguage } from "@/i18n";

function Stat({
  label,
  value,
  note,
  testId,
}: {
  label: string;
  value: string | null;
  note?: string | null;
  testId?: string;
}) {
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-1 px-5 py-3.5">
      <span className="truncate text-sm text-muted-foreground">{label}</span>
      {value === null ? (
        <span className="h-6 w-16 animate-pulse rounded-md bg-secondary" />
      ) : (
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-xl font-semibold tabular-nums tracking-tight text-foreground-strong">
            {value}
          </span>
          {note && <span className="shrink-0 text-sm text-accent">{note}</span>}
        </span>
      )}
    </div>
  );
}

export function ProfileStats() {
  const t = useT();
  const ui = useUiLanguage();
  const summary = useBoardSummary();
  const insights = useBoardInsights();

  const num = (n: number) => n.toLocaleString(ui);
  const days = (n: number) => fill(t("profile_view.stat_days"), { 0: num(n) });

  const s = summary.data;
  const current = s ? s.streak_days : null;

  return (
    <section
      data-testid="profile-stats"
      aria-label={t("profile_view.activity_title")}
      className="grid grid-cols-2 overflow-hidden rounded-xl border border-border bg-card sm:grid-cols-4 [&>*]:border-border [&>*:nth-child(even)]:border-l sm:[&>*:not(:first-child)]:border-l [&>*:nth-child(n+3)]:border-t sm:[&>*:nth-child(n+3)]:border-t-0"
    >
      <Stat
        label={t("profile_view.stat_conversations")}
        value={summary.isLoading ? null : s ? num(s.totals.session_count) : "–"}
      />
      <Stat
        label={t("profile_view.stat_agent_runs")}
        value={insights.isLoading ? null : insights.data ? num(insights.data.agents.sessions) : "–"}
      />
      <Stat
        label={t("profile_view.stat_longest_streak")}
        value={summary.isLoading ? null : s ? days(s.longest_streak) : "–"}
      />
      <Stat
        testId="stat-current-streak"
        label={t("profile_view.stat_current_streak")}
        value={summary.isLoading ? null : current !== null ? days(current) : "–"}
        note={current ? t("profile_view.streak_running") : null}
      />
    </section>
  );
}
