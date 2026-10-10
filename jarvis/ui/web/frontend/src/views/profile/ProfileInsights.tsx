/**
 * The lower half of the profile: the Jarvis agents used most, and a plain
 * list of facts drawn from the activity data.
 *
 * Favourite agents are the society roster's own figures (the lead first,
 * then by runs). Insights are a label on the left and the figure on the
 * right; every row is a real number from `/api/board/insights`, and a row
 * whose source is empty is left out rather than shown as a zero.
 */
import { rankAgents } from "@/components/layout/SidebarAgents";
import { AgentSwatch } from "@/components/society/AgentSwatch";
import { useSocietyRoster } from "@/components/society/data";
import { useBoardInsights, type BoardInsights } from "@/hooks/useBoardInsights";
import { fill, useT } from "@/i18n";
import { useRunLocale } from "@/components/runs/format";
import { societyDisplayName } from "@/lib/societyDisplayName";
import { useEventStore } from "@/store/events";
import { agentLabel, chatProviderLabel, formatDay } from "@/lib/boardInsights";
import { summarize, weekDelta } from "@/views/profile/activity";

/** A Monday, so a weekday index 0…6 maps onto Monday…Sunday. */
const A_MONDAY = new Date(2024, 0, 1);

const FAVOURITES_MAX = 8;

export function FavoriteAgents() {
  const t = useT();
  const ui = useRunLocale();
  const assistantName = useEventStore((s) => s.assistantName);
  const roster = useSocietyRoster();

  // The sample roster is a stand-in for an unreachable backend; showing its
  // invented agents here would read as the person's own.
  const agents = roster.data && !roster.data.sample ? roster.data.agents : [];
  const lead = agents.find((a) => a.tier === "lead" && a.lifecycle !== "archived");
  const shown = [...(lead ? [lead] : []), ...rankAgents(agents)].slice(0, FAVOURITES_MAX);
  if (!roster.isLoading && shown.length === 0) return null;

  return (
    <section data-testid="profile-agents" aria-labelledby="profile-agents-title" className="flex flex-col gap-3">
      <h2 id="profile-agents-title" className="text-base font-semibold text-foreground-strong">
        {t("profile_view.top_agents_title")}
      </h2>
      {roster.isLoading ? (
        <div aria-hidden className="flex gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-12 w-12 animate-pulse rounded-xl bg-secondary" />
          ))}
        </div>
      ) : (
        <ul className="flex flex-wrap gap-x-3 gap-y-4">
          {shown.map((agent) => {
            const name = societyDisplayName(agent, assistantName);
            const runs = agent.stats?.runs ?? 0;
            return (
              <li
                key={agent.agentId}
                title={fill(t(runs === 1 ? "profile_view.top_agents_runs_one" : "profile_view.top_agents_runs"), {
                  0: runs.toLocaleString(ui),
                })}
                className="flex w-20 flex-col items-center gap-1.5"
              >
                <span className="flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-card">
                  <AgentSwatch agent={agent} size={30} />
                </span>
                <span className="w-full truncate text-center text-xs text-muted-foreground">{name}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

interface Fact {
  label: string;
  value: string;
}

function deltaText(t: (key: string) => string, delta: number | null, last7: number): string {
  if (delta === null) return last7 > 0 ? t("profile_view.delta_new") : "";
  if (delta === 0) return t("profile_view.delta_same");
  return fill(t(delta > 0 ? "profile_view.delta_up" : "profile_view.delta_down"), {
    0: Math.abs(delta),
  });
}

export function profileFacts(data: BoardInsights, t: (key: string) => string, ui: string): Fact[] {
  const out: Fact[] = [];
  const num = (n: number) => n.toLocaleString(ui);
  const week = summarize(data.days, "all");

  if (week.weeks.length > 0) {
    const delta = deltaText(t, weekDelta(week.last7, week.prev7), week.last7);
    out.push({
      label: t("profile_view.activity_last7"),
      value: `${num(week.last7)} ${t("profile_view.unit_all")}${delta ? ` · ${delta}` : ""}`,
    });
  }
  if (week.avgWeek !== null) {
    out.push({ label: t("profile_view.activity_avg_week"), value: num(week.avgWeek) });
  }
  if (week.topWeekday !== null) {
    const day = new Date(A_MONDAY.getFullYear(), A_MONDAY.getMonth(), A_MONDAY.getDate() + week.topWeekday);
    out.push({
      label: t("profile_view.activity_top_day"),
      value: day.toLocaleDateString(ui, { weekday: "long" }),
    });
  }
  if (data.streak.active_days > 0) {
    out.push({ label: t("profile_view.insight_active_days"), value: num(data.streak.active_days) });
  }

  const words = data.voice.user_words + data.dictation.words;
  if (words > 0) out.push({ label: t("profile_view.insight_words"), value: num(words) });

  if (data.voice.seconds >= 60) {
    const minutes = Math.round(data.voice.seconds / 60);
    out.push({
      label: t("profile_view.insight_voice_time"),
      value: fill(t("profile_view.duration_hm"), { 0: num(Math.floor(minutes / 60)), 1: minutes % 60 }),
    });
  }

  const coder = data.agents.items.find((a) => a.sessions > 0);
  if (coder) {
    out.push({ label: t("profile_view.insight_top_coder"), value: agentLabel(coder.agent) });
  }

  const chat = [...data.chats.providers].sort((a, b) => b.messages - a.messages)[0];
  if (chat && chat.messages > 0) {
    out.push({ label: t("profile_view.insight_top_chat"), value: chatProviderLabel(chat.provider) });
  }

  const record = data.records.best_agent_day;
  if (record && record.value > 0) {
    out.push({
      label: t("profile_view.insight_record"),
      value: fill(t("profile_view.insight_record_value"), {
        0: formatDay(record.date, ui),
        1: num(record.value),
      }),
    });
  }
  return out;
}

export function InsightList() {
  const t = useT();
  const ui = useRunLocale();
  const insights = useBoardInsights();
  const rows = insights.data ? profileFacts(insights.data, t, ui) : [];

  return (
    <section data-testid="profile-insights" aria-labelledby="profile-insights-title" className="flex flex-col gap-2">
      <h2 id="profile-insights-title" className="text-base font-semibold text-foreground-strong">
        {t("profile_view.insights_title")}
      </h2>
      {insights.isLoading ? (
        <div aria-hidden className="flex flex-col">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="flex items-center justify-between py-2">
              <div className="h-3.5 w-28 animate-pulse rounded-full bg-secondary" />
              <div className="h-3.5 w-12 animate-pulse rounded-full bg-secondary" />
            </div>
          ))}
        </div>
      ) : (
        <dl className="flex flex-col">
          {rows.map((row) => (
            <div key={row.label} className="flex items-baseline justify-between gap-4 py-1.5">
              <dt className="truncate text-sm text-muted-foreground">{row.label}</dt>
              <dd className="shrink-0 text-sm font-medium tabular-nums text-foreground-strong">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
