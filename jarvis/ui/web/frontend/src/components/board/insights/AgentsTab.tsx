import { useState } from "react";

import type { BoardInsights, InsightsAgent } from "@/hooks/useBoardInsights";
import { agentLabel, chatProviderLabel, compactNumber, formatDay, plural } from "@/lib/boardInsights";
import {
  BOARD_CATEGORY_KEYS,
  CATEGORY_META,
  categoryLabelKey,
  type BoardCategoryKey,
} from "@/lib/boardCategories";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { TabBar } from "@/components/layout/SectionTabBar";
import {
  BigNumber,
  CardTitleRow,
  Eyebrow,
  InsightCard,
} from "@/components/board/insights/primitives";
import { useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";

type Range = "30d" | "all";

export function AgentsTab({ data }: { data: BoardInsights }) {
  const t = useT();
  const lang = useUiLanguage();
  const best = data.records.best_agent_day;
  const sessions30 = data.agents.items.reduce((s, a) => s + a.sessions_30d, 0);
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label={t("board_insights.agents.stat_sessions")} value={data.agents.sessions.toLocaleString()} />
        <StatCard label={t("board_insights.agents.stat_sessions_30d")} value={sessions30.toLocaleString()} />
        <StatCard label={t("board_insights.agents.stat_tokens")} value={compactNumber(data.agents.tokens)} />
        <StatCard
          label={t("board_insights.agents.stat_busiest")}
          value={best ? best.value.toLocaleString() : "—"}
          sub={best ? formatDay(best.date, lang) : undefined}
        />
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <CodingAgentsCard data={data} />
        <div className="flex min-w-0 flex-col gap-4">
          <ChatsCard data={data} />
          <CategoriesCard data={data} />
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <InsightCard>
      <BigNumber>{value}</BigNumber>
      <div className="mt-2">
        <Eyebrow>{label}</Eyebrow>
      </div>
      {sub && <div className="mt-1 text-sm text-muted-foreground">{sub}</div>}
    </InsightCard>
  );
}

function CodingAgentsCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const lang = useUiLanguage();
  const [range, setRange] = useState<Range>("30d");
  const sessionsOf = (a: InsightsAgent) => (range === "30d" ? a.sessions_30d : a.sessions);
  const turnsOf = (a: InsightsAgent) => (range === "30d" ? a.turns_30d : a.turns);
  const rows = [...data.agents.items].sort((a, b) => sessionsOf(b) - sessionsOf(a));
  const total = rows.reduce((s, a) => s + sessionsOf(a), 0);
  const max = Math.max(1, ...rows.map(sessionsOf));
  const relative = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });

  return (
    <InsightCard testId="board-coding-agents">
      <CardTitleRow
        title={t("board_insights.agents.title")}
        fact={plural(t, "board_insights.agents.used", rows.filter((a) => sessionsOf(a) > 0).length)}
      >
        <p className="mt-1 text-sm text-muted-foreground">{t("board_insights.agents.subtitle")}</p>
      </CardTitleRow>
      <TabBar
        className="mb-2"
        tabs={[
          { id: "30d", label: t("board_insights.agents.range_30d") },
          { id: "all", label: t("board_insights.agents.range_all") },
        ]}
        active={range}
        onChange={(id) => setRange(id as Range)}
      />
      {rows.length === 0 ? (
        <p className="py-6 text-sm text-muted-foreground">{t("board_insights.agents.empty")}</p>
      ) : (
        <table className="w-full table-fixed text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-[0.06em] text-muted-foreground">
              <th className="py-2 font-medium">{t("board_insights.agents.col_agent")}</th>
              <th className="w-[32%] py-2 font-medium">{t("board_insights.agents.col_share")}</th>
              <th className="w-[13%] py-2 text-right font-medium">{t("board_insights.agents.col_sessions")}</th>
              <th className="w-[13%] py-2 text-right font-medium">{t("board_insights.agents.col_turns")}</th>
              <th className="hidden w-[17%] py-2 text-right font-medium md:table-cell">
                {t("board_insights.agents.col_last")}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((agent) => {
              const sessions = sessionsOf(agent);
              const pct = total ? Math.round((sessions / total) * 100) : 0;
              return (
                <tr key={agent.agent} className={cn("border-t border-border", sessions === 0 && "opacity-50")}>
                  <td className="py-2.5">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <ProviderLogo providerId={agent.agent} label={agentLabel(agent.agent)} />
                      <span className="truncate font-medium text-foreground-strong">{agentLabel(agent.agent)}</span>
                    </div>
                  </td>
                  <td className="py-2.5 pr-3">
                    <div className="flex items-center gap-2">
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-sheen/[0.06]">
                        <div
                          className="h-full rounded-full bg-accent"
                          style={{ width: sessions > 0 ? `${Math.max((sessions / max) * 100, 3)}%` : "0%" }}
                        />
                      </div>
                      <span className="w-9 text-right text-xs tabular-nums text-muted-foreground">{pct}%</span>
                    </div>
                  </td>
                  <td className="py-2.5 text-right tabular-nums text-foreground-strong">{sessions.toLocaleString()}</td>
                  <td className="py-2.5 text-right tabular-nums text-muted-foreground">
                    {compactNumber(turnsOf(agent))}
                  </td>
                  <td className="hidden py-2.5 text-right text-muted-foreground md:table-cell">
                    {agent.last_ms ? relativeDays(agent.last_ms, relative) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </InsightCard>
  );
}

function ChatsCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const rows = data.chats.providers.slice(0, 6);
  const max = Math.max(1, ...rows.map((r) => r.messages));
  return (
    <InsightCard testId="board-chats">
      <CardTitleRow
        title={t("board_insights.chats.title")}
        fact={plural(t, "board_insights.chats.messages", data.chats.messages)}
      />
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("board_insights.chats.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {rows.map((row) => (
            <li key={row.provider} className="flex items-center gap-2.5 text-sm">
              <ProviderLogo providerId={row.provider} label={chatProviderLabel(row.provider)} size="sm" />
              <span className="w-24 shrink-0 truncate text-foreground">{chatProviderLabel(row.provider)}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-sheen/[0.06]">
                <div
                  className="h-full rounded-full bg-accent/70"
                  style={{ width: row.messages > 0 ? `${Math.max((row.messages / max) * 100, 3)}%` : "0%" }}
                />
              </div>
              <span className="w-10 text-right tabular-nums text-foreground-strong">{row.messages.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </InsightCard>
  );
}

function CategoriesCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const counts = new Map(data.categories.categories.map((c) => [c.category, c.count]));
  const total = data.categories.total;
  const rows = BOARD_CATEGORY_KEYS.map((key) => ({ key, count: counts.get(key) ?? 0 }))
    .filter((r) => r.count > 0)
    .sort((a, b) => b.count - a.count);
  return (
    <InsightCard testId="board-categories">
      <CardTitleRow title={t("board_insights.categories.title")}>
        <p className="mt-1 text-sm text-muted-foreground">{t("board_insights.categories.subtitle")}</p>
      </CardTitleRow>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("board_insights.categories.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map(({ key, count }) => {
            const Icon = CATEGORY_META[key as BoardCategoryKey].icon;
            const pct = total ? Math.round((count / total) * 100) : 0;
            return (
              <li key={key} className="flex items-center gap-2.5 text-sm">
                <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="flex-1 truncate text-foreground">{t(categoryLabelKey(key))}</span>
                <span className="tabular-nums text-muted-foreground">{count.toLocaleString()}</span>
                <span className="w-10 text-right text-xs tabular-nums text-foreground-strong">{pct}%</span>
              </li>
            );
          })}
        </ul>
      )}
    </InsightCard>
  );
}

function relativeDays(ms: number, fmt: Intl.RelativeTimeFormat): string {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const then = new Date(ms);
  then.setHours(0, 0, 0, 0);
  const days = Math.round((then.getTime() - startOfToday.getTime()) / 86_400_000);
  return fmt.format(days, "day");
}

