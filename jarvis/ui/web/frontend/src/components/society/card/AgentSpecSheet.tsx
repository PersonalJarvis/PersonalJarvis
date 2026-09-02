/**
 * The model card's left column: what the agent IS, as a spec sheet —
 * brain, tier, permissions, budget, tools, routines, lifetime stats, and
 * its standing instructions (MASTERPLAN §4.2, agent-definition §2).
 * Ink & Paper chrome; every string through the locale files.
 */
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";

import type { SocietyAgent } from "../data";

function formatDate(ms: number | null, fallback: string): string {
  if (ms === null) return fallback;
  return new Date(ms).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

export function AgentSpecSheet({ agent }: { agent: SocietyAgent }) {
  const t = useT();
  return (
    <ScrollArea className="h-full min-h-0">
      <div className="flex flex-col gap-5 p-5">
        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("society.card.brain")}
          </h3>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge>{agent.providerLabel}</Badge>
            <Badge variant="secondary" className="font-mono text-[11px]">
              {agent.model}
            </Badge>
            <Badge variant="outline">{t(`society.tier.${agent.tier}`)}</Badge>
          </div>
        </section>

        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("society.card.description")}
          </h3>
          <p className="whitespace-pre-line text-[13px] leading-relaxed text-foreground">{agent.description}</p>
        </section>

        <section className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Fact label={t("society.card.permission")} value={t(`society.ceiling.${agent.permissionCeiling}`)} />
          <Fact label={t("society.card.budget")} value={`$${agent.dailyBudgetUsd.toFixed(2)}`} />
          <Fact label={t("society.card.grant_mode")} value={t(`society.grant.${agent.grantMode}`)} />
          <Fact label={t("society.card.place")} value={t(`society.checkpoint.${agent.checkpoint}`)} />
        </section>

        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("society.card.tools")}
          </h3>
          {agent.toolGrants.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("society.card.no_tools")}</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {agent.toolGrants.map((tool) => (
                <Badge key={tool} variant="secondary" className="font-mono text-[11px]">
                  {tool}
                </Badge>
              ))}
            </div>
          )}
        </section>

        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("society.card.routines")}
          </h3>
          {agent.routines.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("society.card.no_routines")}</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {agent.routines.map((routine) => (
                <li key={routine.id} className="flex items-baseline justify-between gap-3 text-[13px]">
                  <span className="truncate text-foreground">{routine.label}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{routine.schedule}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="grid grid-cols-3 gap-3">
          <Fact label={t("society.card.runs")} value={String(agent.stats.runs)} />
          <Fact label={t("society.card.cost")} value={`$${agent.stats.totalCostUsd.toFixed(2)}`} />
          <Fact
            label={t("society.card.last_active")}
            value={formatDate(agent.stats.lastActiveMs, t("society.card.never"))}
          />
        </section>
      </div>
    </ScrollArea>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="truncate text-[13px] font-medium text-foreground">{value}</div>
    </div>
  );
}
