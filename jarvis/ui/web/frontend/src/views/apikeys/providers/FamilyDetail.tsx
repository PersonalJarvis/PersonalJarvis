import { useState } from "react";
import { ArrowUpRight, Check, Loader2 } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { Button } from "@/components/ui/button";
import {
  switchSubagentProvider,
  type ProviderTier,
  type SectionHealth,
} from "@/hooks/useProviders";
import { useT } from "@/i18n";
import type { AgentRowStatus, AgentStatus, ProviderFamily } from "@/lib/providerFamilies";
import { useEventStore } from "@/store/events";
import { RealtimeTab } from "@/views/apikeys/RealtimeTab";
import { ProfileGroup, SettingRow } from "@/views/profile/ProfileGroup";
import { familyStatusLine, type FamilyState } from "./familyState";
import { UseTag } from "./FamilyList";
import { KeySection } from "./KeySection";
import { SubscriptionSection } from "./SubscriptionSection";

/** Voice features configured in the voice section, shown here as facts. */
const VOICE_TIERS: { tier: ProviderTier; use: "speech" | "hearing" | "dictation" }[] = [
  { tier: "tts", use: "speech" },
  { tier: "stt", use: "hearing" },
  { tier: "dictation", use: "dictation" },
];

/**
 * Everything about one company, top to bottom in the order a person sets it
 * up: sign in with the plan they pay for, or paste one key — then what the
 * assistant uses the company for, each job switchable right where it is shown.
 */
export function FamilyDetail({
  family,
  state,
  agents,
  realtimeHealth,
  onChanged,
  onProvidersChanged,
  onActivateOptimistic,
}: {
  family: ProviderFamily;
  state: FamilyState;
  agents: AgentStatus | null;
  realtimeHealth?: SectionHealth;
  onChanged: () => void | Promise<void>;
  onProvidersChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
}) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const voiceCards = state.members.filter((m) => m.tier === "realtime");
  const agentRows = (agents?.mapping ?? []).filter((row) => family.agent_ids.includes(row.jarvis));
  const voiceFeatures = VOICE_TIERS.flatMap(({ tier, use }) =>
    state.members.filter((m) => m.tier === tier).map((card) => ({ card, use })),
  );

  return (
    <div data-testid={`provider-family-detail-${family.id}`} className="flex flex-col gap-group">
      <header className="flex items-center gap-4">
        <ProviderLogo providerId={family.logo_id} label={family.label} className="h-11 w-11" />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-xl font-semibold text-foreground-strong">{family.label}</h2>
          <p className="mt-0.5 truncate text-base text-muted-foreground">{familyStatusLine(family, state, t)}</p>
        </div>
        {state.uses.length > 0 && (
          <div className="flex shrink-0 flex-wrap justify-end gap-1">
            {state.uses.map((use) => (
              <UseTag key={use} use={use} />
            ))}
          </div>
        )}
      </header>

      {state.failing && (
        <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/10 px-5 py-3 text-sm text-destructive">
          {state.failing}
        </p>
      )}

      {family.subscription && <SubscriptionSection family={family} state={state} onChanged={onChanged} />}

      {family.key_slot && <KeySection family={family} state={state} onChanged={onChanged} />}

      {voiceCards.length > 0 && (
        <div data-testid="provider-voice">
          <RealtimeTab
            embedded
            providers={voiceCards}
            loading={false}
            error={null}
            onChanged={onProvidersChanged}
            onActivateOptimistic={onActivateOptimistic}
            health={realtimeHealth}
          />
        </div>
      )}

      {agentRows.length > 0 && (
        <AgentsSection family={family} state={state} rows={agentRows} onChanged={onChanged} />
      )}

      {voiceFeatures.length > 0 && (
        <ProfileGroup
          title={t("providers_page.voice_features_title")}
          description={t("providers_page.voice_features_desc")}
          testId="provider-voice-features"
          aside={
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => setActiveSection("voice-api-keys")}
            >
              {t("providers_page.voice_features_open")}
              <ArrowUpRight />
            </Button>
          }
        >
          {voiceFeatures.map(({ card, use }) => (
            <SettingRow
              key={card.id}
              label={card.label}
              hint={t(`providers_page.use_${use}_long`)}
              control={<InUse active={card.active} ready={card.configured} />}
            />
          ))}
        </ProfileGroup>
      )}
    </div>
  );
}

function InUse({ active, ready }: { active: boolean; ready: boolean }) {
  const t = useT();
  if (active) {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
        <Check aria-hidden="true" className="h-4 w-4 text-accent" />
        {t("providers_page.in_use")}
      </span>
    );
  }
  return (
    <span className="text-sm text-muted-foreground">
      {ready ? t("providers_page.available") : t("providers_page.not_ready")}
    </span>
  );
}

/**
 * Which worker runs the assistant's background agents. Each of the company's
 * worker routes is one row; the one in use says so, the others switch with
 * one click once their sign-in or key is in place.
 */
function AgentsSection({
  family,
  state,
  rows,
  onChanged,
}: {
  family: ProviderFamily;
  state: FamilyState;
  rows: AgentRowStatus[];
  onChanged: () => void | Promise<void>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [switching, setSwitching] = useState<string | null>(null);

  async function use(row: AgentRowStatus, label: string) {
    setSwitching(row.jarvis);
    window.dispatchEvent(
      new CustomEvent("jarvis:provider-selection-pending", {
        detail: { section: "subagents", provider: row.jarvis },
      }),
    );
    try {
      const result = await switchSubagentProvider(row.jarvis);
      pushToast(
        "success",
        t(result.restart_required ? "providers_page.agents_switched_restart" : "providers_page.agents_switched")
          .replace("{0}", label),
      );
      window.dispatchEvent(new CustomEvent("jarvis:agent-switched"));
      await onChanged();
    } catch (cause) {
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-switch-failed", {
          detail: { section: "subagents", provider: row.jarvis },
        }),
      );
      pushToast("error", (cause as Error).message);
    } finally {
      setSwitching(null);
    }
  }

  return (
    <ProfileGroup
      title={t("providers_page.agents_title")}
      description={t("providers_page.agents_desc")}
      testId="provider-agents"
    >
      {rows.map((row) => {
        const label = row.label ?? row.jarvis;
        // A CLI row that can run on the plan login or a key reports which one
        // it would use right now.
        const billing =
          row.billing === "subscription_or_api"
            ? state.subscriptionOn
              ? "subscription"
              : "api"
            : row.billing;
        return (
          <SettingRow
            key={row.jarvis}
            testId={`provider-agent-${row.jarvis}`}
            label={label}
            hint={
              row.key_set
                ? t(`provider_billing.${billing}`)
                : t(family.subscription ? "providers_page.agents_needs_login_or_key" : "providers_page.agents_needs_key")
            }
            control={
              row.is_active_brain ? (
                <InUse active ready />
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  data-testid={`provider-agent-use-${row.jarvis}`}
                  disabled={!row.key_set || switching !== null}
                  onClick={() => void use(row, label)}
                >
                  {switching === row.jarvis && <Loader2 className="animate-spin" />}
                  {t("providers_page.agents_use")}
                </Button>
              )
            }
          />
        );
      })}
    </ProfileGroup>
  );
}

