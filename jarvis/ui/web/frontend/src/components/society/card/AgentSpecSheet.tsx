/**
 * The model card's spec column — a champion card of the island.
 *
 * It used to be a stack of grey headings and sentences, and the sentence that
 * mattered most read "No focus tools" above an empty list: `GRANTED TOOLS`
 * only ever filled for an allow-list agent, and almost every agent runs
 * `grant_mode = all`. So the card said nothing about the one thing that makes
 * a Gmail agent a Gmail agent.
 *
 * It now leads with HANDS — what this agent reaches for first, as tiles
 * carrying the services' real marks — and says in one quiet line that
 * everything else stays in reach, because it does (agent-definition §3.2).
 * Below that: the ceiling and the reach as meters, the brain, the standing
 * orders, routines, and the lifetime record.
 *
 * The skin is the world's, not the app's (maintainer, 2026-09-03): the card
 * wears the island's daylight the way the 3D column beside it does. How that
 * works without any component writing a literal colour is explained at the
 * top of `agentCard.css`. Jarvis keeps its two special sections — its brain
 * is the app's chat brain and its orders are the user's own instructions
 * file — and inherits the skin through the same token scope.
 *
 * Every string goes through the locale files.
 */
import { useMemo, useState } from "react";
import { MessageSquare, Pause, Play, Send } from "lucide-react";

import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { CapabilityChip } from "../CapabilityChip";
import {
  useSetAgentPaused,
  useSocietyCapabilities,
  type AgentRunState,
  type Capability,
  type PermissionCeiling,
  type SocietyAgent,
} from "../data";
import { CapabilityTile } from "./CapabilityTile";
import { LeadBrain, LeadInstructions } from "./LeadSections";

import "./agentCard.css";

/** How far up the three-step ladder a ceiling sits. `block` never reaches a card. */
const CEILING_STEP: Record<PermissionCeiling, number> = { safe: 1, monitor: 2, ask: 3 };

/** The state dot's fill — the same three status jobs the rest of the app uses. */
const STATE_FILL: Record<AgentRunState, string> = {
  idle: "bg-muted-foreground/40",
  working: "bg-success",
  waiting: "bg-warning",
  paused: "bg-muted-foreground/25",
};

const REACH_STEPS = 6;

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

export interface AgentSpecSheetProps {
  agent: SocietyAgent;
  /**
   * Hands the card back to its chat. The profile is the card's second face,
   * so "Chat" here is a real way back rather than the placeholder it was
   * while the chat had no place of its own.
   */
  onOpenChat?: () => void;
}

export function AgentSpecSheet({ agent, onOpenChat }: AgentSpecSheetProps) {
  const t = useT();
  const capabilities = useSocietyCapabilities();
  const setPaused = useSetAgentPaused();
  const [busy, setBusy] = useState(false);
  const byId = useMemo(() => {
    const map = new Map<string, Capability>();
    for (const c of capabilities.data ?? []) map.set(c.id, c);
    return map;
  }, [capabilities.data]);

  // The catalog is optional enrichment: the query does not retry, and a card
  // with no catalog still names every tool through the brand resolver. What
  // it must NOT do is print a total it does not have.
  const catalogSize = capabilities.data?.length ?? 0;
  const allowlist = agent.grantMode === "allowlist";

  /** What the agent reaches for first — its allow-list is its hands when it has no focus. */
  const hands = agent.focus.length > 0 ? agent.focus : allowlist ? agent.toolGrants : [];
  const paused = agent.lifecycle === "paused" || agent.state === "paused";

  const reachFilled = allowlist
    ? Math.max(1, Math.round((agent.toolGrants.length / Math.max(catalogSize, 1)) * REACH_STEPS))
    : REACH_STEPS;
  const reachValue = allowlist
    ? t("society.card.reach_allowlist")
        .replace("{0}", String(agent.toolGrants.length))
        .replace("{1}", String(catalogSize))
    : t("society.card.reach_all");

  const togglePause = async () => {
    setBusy(true);
    try {
      await setPaused(agent, !paused);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ac-card" data-testid="agent-card-sheet">
      <header className="ac-band" data-tier={agent.tier}>
        <span className="min-w-0 flex-1">
          <span className="ac-band-name block truncate">{agent.name}</span>
          {/* Jarvis' title IS "Lead", so the tier would read twice. */}
          <span className="ac-band-title block truncate">
            {[t(`society.tier.${agent.tier}`), agent.title]
              .filter((part, i, all) => part && all.indexOf(part) === i)
              .join(" · ")}
          </span>
        </span>
        <span className={cn("ac-dot", STATE_FILL[agent.state])} data-state={agent.state} aria-hidden />
        <span className="shrink-0 text-xs text-muted-foreground">{t(`society.state.${agent.state}`)}</span>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-5 p-5">
          <section>
            <h3 className="ac-head mb-2.5">{t("society.card.hands")}</h3>
            {hands.length === 0 ? (
              <p className="ac-prose text-xs leading-relaxed text-muted-foreground">
                {t("society.card.hands_empty")}
              </p>
            ) : (
              <ul className="flex flex-wrap gap-3">
                {hands.map((id) => (
                  <CapabilityTile
                    key={id}
                    id={id}
                    capability={byId.get(id)}
                    palette={agent.palette}
                    disconnectedHint={t("society.card.not_connected")}
                  />
                ))}
              </ul>
            )}
            <p className="ac-prose mt-3 text-xs leading-relaxed text-muted-foreground">
              {allowlist
                ? t("society.card.hands_rest_allowlist")
                : catalogSize > 0
                  ? t("society.card.hands_rest").replace("{0}", String(catalogSize))
                  : t("society.card.hands_rest_plain")}
            </p>
            {agent.denies.length > 0 ? (
              <div className="mt-3">
                <p className="ac-prose mb-1.5 text-xs text-muted-foreground">{t("society.card.denied")}</p>
                <div className="flex flex-wrap gap-1.5">
                  {agent.denies.map((id) => (
                    <CapabilityChip key={id} id={id} capability={byId.get(id)} className="line-through opacity-60" />
                  ))}
                </div>
              </div>
            ) : null}
          </section>

          <section className="flex flex-col gap-2">
            <StepMeter
              label={t("society.card.permission")}
              steps={3}
              filled={CEILING_STEP[agent.permissionCeiling] ?? 0}
              value={t(`society.ceiling.${agent.permissionCeiling}`)}
            />
            <StepMeter
              label={t("society.card.meter_reach")}
              steps={REACH_STEPS}
              filled={reachFilled}
              value={reachValue}
            />
          </section>

          <section className="grid grid-cols-3 gap-2">
            <Plate label={t("society.card.budget")} value={`$${agent.dailyBudgetUsd.toFixed(2)}`} />
            <Plate label={t("society.card.focus")} value={String(agent.focus.length)} />
            <Plate label={t("society.card.place")} value={t(`society.checkpoint.${agent.checkpoint}`)} />
          </section>

          <section>
            <h3 className="ac-head mb-2">{t("society.card.brain")}</h3>
            {agent.tier === "lead" ? (
              <LeadBrain />
            ) : (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {agent.provider ? (
                  <span className="inline-flex items-center gap-1.5">
                    <ProviderLogo
                      providerId={agent.provider}
                      label={agent.providerLabel || agent.provider}
                      size="sm"
                    />
                    <span className="font-medium">{agent.providerLabel || agent.provider}</span>
                  </span>
                ) : (
                  <span className="font-medium">{t("society.card.default_brain")}</span>
                )}
                {agent.model ? <span className="ac-prose font-mono text-xs">{agent.model}</span> : null}
                {agent.effort ? <span className="text-xs text-muted-foreground">{agent.effort}</span> : null}
              </div>
            )}
          </section>

          <section className="ac-prose">
            {agent.tier === "lead" ? (
              <LeadInstructions />
            ) : (
              <>
                <h3 className="ac-head mb-2">{t("society.card.description")}</h3>
                {agent.description ? (
                  <p className="whitespace-pre-line text-sm leading-relaxed">{agent.description}</p>
                ) : (
                  <p className="text-xs text-muted-foreground">{t("society.card.no_description")}</p>
                )}
              </>
            )}
          </section>

          {agent.approvalRules.requireApproval.length > 0 || agent.approvalRules.alwaysAllow.length > 0 ? (
            <section>
              <h3 className="ac-head mb-2">{t("society.card.approval_rules")}</h3>
              {agent.approvalRules.requireApproval.length > 0 ? (
                <RuleRow label={t("society.card.require_approval")} ids={agent.approvalRules.requireApproval} byId={byId} />
              ) : null}
              {agent.approvalRules.alwaysAllow.length > 0 ? (
                <RuleRow label={t("society.card.always_allow")} ids={agent.approvalRules.alwaysAllow} byId={byId} />
              ) : null}
            </section>
          ) : null}

          <section>
            <h3 className="ac-head mb-2">{t("society.card.routines")}</h3>
            {agent.routines.length === 0 ? (
              <p className="ac-prose text-xs text-muted-foreground">{t("society.card.no_routines")}</p>
            ) : (
              <ul className="ac-prose flex flex-col gap-1.5">
                {agent.routines.map((routine) => {
                  const due = relativeUntil(routine.nextFire, t);
                  return (
                    <li key={routine.id} className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="truncate">{routine.label}</span>
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

          <section>
            <h3 className="ac-head mb-2">{t("society.card.record")}</h3>
            <div className="grid grid-cols-3 gap-2">
              <Plate label={t("society.card.runs")} value={String(agent.stats.runs)} />
              <Plate label={t("society.card.cost")} value={`$${agent.stats.totalCostUsd.toFixed(2)}`} />
              <Plate
                label={t("society.card.last_active")}
                value={formatDate(agent.stats.lastActiveMs, t("society.card.never"))}
              />
            </div>
          </section>
        </div>
      </ScrollArea>

      <div className="ac-actions">
        <button
          type="button"
          className="ac-btn"
          data-accent="1"
          disabled={!onOpenChat}
          title={onOpenChat ? undefined : t("society.card.chat_soon")}
          onClick={onOpenChat}
        >
          <MessageSquare className="h-3.5 w-3.5" aria-hidden />
          {t("society.card.action_chat")}
        </button>
        <button type="button" className="ac-btn" disabled title={t("society.card.assign_soon")}>
          <Send className="h-3.5 w-3.5" aria-hidden />
          {t("society.card.action_assign")}
        </button>
        <button
          type="button"
          className="ac-btn ml-auto"
          disabled={busy}
          onClick={() => void togglePause()}
        >
          {paused ? <Play className="h-3.5 w-3.5" aria-hidden /> : <Pause className="h-3.5 w-3.5" aria-hidden />}
          {paused ? t("society.card.action_resume") : t("society.card.action_pause")}
        </button>
      </div>
    </div>
  );
}

/**
 * A value on a short discrete ladder, drawn as filled notches. Only used for
 * things that really are steps — the three permission ceilings, the share of
 * the catalog an allow-list keeps — never for a number dressed up as one.
 */
function StepMeter({
  label,
  steps,
  filled,
  value,
}: {
  label: string;
  steps: number;
  filled: number;
  value: string;
}) {
  return (
    <div className="ac-meter">
      <span className="ac-meter-label">{label}</span>
      <span className="ac-meter-track" role="img" aria-label={`${label}: ${value}`}>
        {Array.from({ length: steps }, (_, i) => (
          <span key={i} className="ac-meter-seg" data-on={i < filled ? "1" : "0"} />
        ))}
      </span>
      <span className="ac-meter-value">{value}</span>
    </div>
  );
}

function Plate({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="ac-plate">
      <div className="ac-plate-label truncate">{label}</div>
      <div className="ac-plate-value truncate">
        {value}
        {hint ? <span className="ac-plate-label"> {hint}</span> : null}
      </div>
    </div>
  );
}

function RuleRow({ label, ids, byId }: { label: string; ids: string[]; byId: Map<string, Capability> }) {
  return (
    <div className="mb-2">
      <p className="ac-prose mb-1 text-xs text-muted-foreground">{label}</p>
      <div className="flex flex-wrap gap-1.5">
        {ids.map((id) => (
          <CapabilityChip key={id} id={id} capability={byId.get(id.split(":").slice(0, 2).join(":"))} />
        ))}
      </div>
    </div>
  );
}
