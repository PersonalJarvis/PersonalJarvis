/**
 * The model card's left column: what the agent IS, as a spec sheet —
 * brain with the provider's mark, tier, its standing instructions, the
 * tools it reaches for first and the ones it may use (each with the
 * service's real logo), approval rules, permission ceiling and budget,
 * routines with the next run, lifetime stats, and the actions
 * (MASTERPLAN §4.2, agent-definition §2). Ink & Paper chrome; every string
 * through the locale files.
 */
import { useMemo, useState } from "react";
import { MessageSquare, Pause, Play, Send } from "lucide-react";

import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";

import { CapabilityChip } from "../CapabilityChip";
import { useSetAgentPaused, useSocietyCapabilities, type Capability, type SocietyAgent } from "../data";
import { LeadBrain, LeadInstructions } from "./LeadSections";

function formatDate(ms: number | null, fallback: string): string {
  if (ms === null) return fallback;
  return new Date(ms).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

/** "in 3 h", "in 2 d", "now" — from an ISO time; the schedule text stays beside it. */
export function relativeUntil(iso: string | null, t: (key: string) => string): string | null {
  if (!iso) return null;
  const ms = Date.parse(iso) - Date.now();
  if (Number.isNaN(ms)) return null;
  if (ms <= 60_000) return t("society.card.due_now");
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return t("society.card.due_in").replace("{0}", `${minutes} min`);
  const hours = Math.round(minutes / 60);
  if (hours < 48) return t("society.card.due_in").replace("{0}", `${hours} h`);
  return t("society.card.due_in").replace("{0}", `${Math.round(hours / 24)} d`);
}

export function AgentSpecSheet({ agent }: { agent: SocietyAgent }) {
  const t = useT();
  const capabilities = useSocietyCapabilities();
  const setPaused = useSetAgentPaused();
  const [busy, setBusy] = useState(false);
  const byId = useMemo(() => {
    const map = new Map<string, Capability>();
    for (const c of capabilities.data ?? []) map.set(c.id, c);
    return map;
  }, [capabilities.data]);

  const granted = agent.grantMode === "allowlist" ? agent.toolGrants : [];
  const paused = agent.lifecycle === "paused" || agent.state === "paused";

  const togglePause = async () => {
    setBusy(true);
    try {
      await setPaused(agent, !paused);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-5 p-5">
          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("society.card.brain")}
            </h3>
            {agent.tier === "lead" ? (
              <LeadBrain />
            ) : (
            <div className="flex flex-wrap items-center gap-2">
              {agent.provider ? (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs text-foreground">
                  <ProviderLogo providerId={agent.provider} label={agent.providerLabel || agent.provider} size="sm" />
                  {agent.providerLabel || agent.provider}
                </span>
              ) : (
                <Badge variant="outline">{t("society.card.default_brain")}</Badge>
              )}
              {agent.model ? (
                <Badge variant="secondary" className="font-mono text-xs">
                  {agent.model}
                </Badge>
              ) : null}
              {agent.effort ? <Badge variant="outline">{agent.effort}</Badge> : null}
              <Badge variant="outline">{t(`society.tier.${agent.tier}`)}</Badge>
            </div>
            )}
          </section>

          <section>
            {agent.tier === "lead" ? (
              <LeadInstructions />
            ) : (
              <>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("society.card.description")}
                </h3>
                {agent.description ? (
                  <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">{agent.description}</p>
                ) : (
                  <p className="text-xs text-muted-foreground">{t("society.card.no_description")}</p>
                )}
              </>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("society.card.focus")}
            </h3>
            {agent.focus.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("society.card.no_focus")}</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {agent.focus.map((id) => (
                  <CapabilityChip key={id} id={id} capability={byId.get(id)} disconnectedHint={t("society.card.not_connected")} />
                ))}
              </div>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("society.card.tools")}
            </h3>
            <p className="mb-2 text-xs text-muted-foreground">
              {agent.grantMode === "all" ? t("society.card.tools_all") : t("society.card.tools_allowlist")}
            </p>
            {granted.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {granted.map((id) => (
                  <CapabilityChip key={id} id={id} capability={byId.get(id)} disconnectedHint={t("society.card.not_connected")} />
                ))}
              </div>
            ) : null}
            {agent.denies.length > 0 ? (
              <div className="mt-2">
                <p className="mb-1 text-xs text-muted-foreground">{t("society.card.denied")}</p>
                <div className="flex flex-wrap gap-1.5">
                  {agent.denies.map((id) => (
                    <CapabilityChip key={id} id={id} capability={byId.get(id)} className="line-through opacity-60" />
                  ))}
                </div>
              </div>
            ) : null}
          </section>

          <section className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Fact label={t("society.card.permission")} value={t(`society.ceiling.${agent.permissionCeiling}`)} />
            <Fact label={t("society.card.budget")} value={`$${agent.dailyBudgetUsd.toFixed(2)}`} />
            <Fact label={t("society.card.place")} value={t(`society.checkpoint.${agent.checkpoint}`)} />
            <Fact label={t("society.card.state")} value={t(`society.state.${agent.state}`)} />
          </section>

          {agent.approvalRules.requireApproval.length > 0 || agent.approvalRules.alwaysAllow.length > 0 ? (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("society.card.approval_rules")}
              </h3>
              {agent.approvalRules.requireApproval.length > 0 ? (
                <RuleRow label={t("society.card.require_approval")} ids={agent.approvalRules.requireApproval} byId={byId} />
              ) : null}
              {agent.approvalRules.alwaysAllow.length > 0 ? (
                <RuleRow label={t("society.card.always_allow")} ids={agent.approvalRules.alwaysAllow} byId={byId} />
              ) : null}
            </section>
          ) : null}

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("society.card.routines")}
            </h3>
            {agent.routines.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("society.card.no_routines")}</p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {agent.routines.map((routine) => {
                  const due = relativeUntil(routine.nextFire, t);
                  return (
                    <li key={routine.id} className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="truncate text-foreground">{routine.label}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {routine.schedule}
                        {due ? ` · ${due}` : ""}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="grid grid-cols-3 gap-3">
            <Fact label={t("society.card.runs")} value={String(agent.stats.runs)} />
            <Fact label={t("society.card.cost")} value={`$${agent.stats.totalCostUsd.toFixed(2)}`} />
            <Fact label={t("society.card.last_active")} value={formatDate(agent.stats.lastActiveMs, t("society.card.never"))} />
          </section>
        </div>
      </ScrollArea>
      <div className="flex shrink-0 items-center gap-2 border-t border-border px-4 py-3">
        <Button size="sm" variant="secondary" disabled title={t("society.card.chat_soon")}>
          <MessageSquare className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          {t("society.card.action_chat")}
        </Button>
        <Button size="sm" variant="secondary" disabled title={t("society.card.assign_soon")}>
          <Send className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          {t("society.card.action_assign")}
        </Button>
        <Button size="sm" variant="outline" className="ml-auto" disabled={busy} onClick={() => void togglePause()}>
          {paused ? <Play className="mr-1.5 h-3.5 w-3.5" aria-hidden /> : <Pause className="mr-1.5 h-3.5 w-3.5" aria-hidden />}
          {paused ? t("society.card.action_resume") : t("society.card.action_pause")}
        </Button>
      </div>
    </div>
  );
}

function RuleRow({ label, ids, byId }: { label: string; ids: string[]; byId: Map<string, Capability> }) {
  return (
    <div className="mb-2">
      <p className="mb-1 text-xs text-muted-foreground">{label}</p>
      <div className="flex flex-wrap gap-1.5">
        {ids.map((id) => (
          <CapabilityChip key={id} id={id} capability={byId.get(id.split(":").slice(0, 2).join(":"))} />
        ))}
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="truncate text-sm font-medium text-foreground">{value}</div>
    </div>
  );
}
