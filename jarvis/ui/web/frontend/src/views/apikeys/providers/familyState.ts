import type { ProviderDescriptor, SectionHealth } from "@/hooks/useProviders";
import {
  subscriptionAccount,
  subscriptionConnected,
  type AgentStatus,
  type ProviderFamily,
  type SubscriptionKind,
  type SubscriptionStatus,
} from "@/lib/providerFamilies";

/** The jobs a company can do for the assistant, in the order the page lists them. */
export const FAMILY_USES = ["voice", "agents", "speech", "hearing", "dictation"] as const;
export type FamilyUse = (typeof FAMILY_USES)[number];

/** Which provider tier answers for each job (agents come from the worker rows). */
const USE_TIER: Record<Exclude<FamilyUse, "agents">, ProviderDescriptor["tier"]> = {
  voice: "realtime",
  speech: "tts",
  hearing: "stt",
  dictation: "dictation",
};

/** Section-health keys whose failing subject points at a member of a family. */
const HEALTH_SECTIONS: Record<FamilyUse, string> = {
  voice: "realtime",
  agents: "subagents",
  speech: "tts",
  hearing: "stt",
  dictation: "dictation",
};

export interface FamilyState {
  members: ProviderDescriptor[];
  subscription: SubscriptionStatus | null;
  /** The subscription login is what powers the CLI right now. */
  subscriptionOn: boolean;
  /** "ruben@… · Claude Max", when the login reports it. */
  account: string | null;
  keyOn: boolean;
  /** Authenticated without a key (a Google Cloud project). */
  viaProject: boolean;
  /** Ready to use for at least one job. */
  connected: boolean;
  /** Jobs this company powers right now. */
  uses: FamilyUse[];
  /** Jobs this company could take over (it has a member card for them). */
  canDo: FamilyUse[];
  /** A job it powers is failing its real calls; the backend's one-line reason. */
  failing: string | null;
}

export interface FamilyContext {
  providers: ProviderDescriptor[];
  subscriptions: Partial<Record<SubscriptionKind, SubscriptionStatus | null>>;
  agents: AgentStatus | null;
  health: Record<string, SectionHealth | undefined>;
}

export function familyState(family: ProviderFamily, ctx: FamilyContext): FamilyState {
  const members = ctx.providers.filter((p) => family.provider_ids.includes(p.id));
  const subscription = family.subscription ? ctx.subscriptions[family.subscription.kind] ?? null : null;
  const subscriptionOn = subscriptionConnected(subscription);
  const viaProject =
    !family.key_present && members.some((m) => Boolean(m.credential_note) && m.configured);
  const agentRows = (ctx.agents?.mapping ?? []).filter((row) => family.agent_ids.includes(row.jarvis));

  const uses: FamilyUse[] = [];
  const canDo: FamilyUse[] = [];
  for (const use of FAMILY_USES) {
    if (use === "agents") {
      if (agentRows.length) canDo.push(use);
      if (agentRows.some((row) => row.is_active_brain)) uses.push(use);
      continue;
    }
    const tier = USE_TIER[use];
    const cards = members.filter((m) => m.tier === tier);
    if (cards.length) canDo.push(use);
    if (cards.some((m) => m.active)) uses.push(use);
  }

  const memberIds = new Set([...family.provider_ids, ...family.hidden_ids, ...family.agent_ids]);
  let failing: string | null = null;
  for (const use of uses) {
    const entry = ctx.health[HEALTH_SECTIONS[use]];
    if (entry?.status === "error" && entry.subject_id && memberIds.has(entry.subject_id)) {
      failing = entry.detail || entry.reason;
      break;
    }
  }

  return {
    members,
    subscription,
    subscriptionOn,
    account: subscriptionOn ? subscriptionAccount(subscription) : null,
    keyOn: family.key_present,
    viaProject,
    connected: subscriptionOn || family.key_present || viaProject || family.local,
    uses,
    canDo,
    failing,
  };
}

/**
 * The one status line under a company's name. `t` is the page translator;
 * every branch says exactly what is known, never more.
 */
export function familyStatusLine(
  family: ProviderFamily,
  state: FamilyState,
  t: (key: string) => string,
): string {
  if (family.local) return t("providers_page.status_local");
  if (state.subscriptionOn && state.keyOn) {
    return t("providers_page.status_both");
  }
  if (state.subscriptionOn) {
    return state.account
      ? t("providers_page.status_subscription_as").replace("{0}", state.account)
      : t("providers_page.status_subscription");
  }
  if (state.keyOn) return t("providers_page.status_key");
  if (state.viaProject) return t("providers_page.status_project");
  return t("providers_page.status_none");
}
