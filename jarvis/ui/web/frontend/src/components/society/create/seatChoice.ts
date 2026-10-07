/**
 * What the "New agent" dialog offers under "Model": a provider, the way the
 * agent pays for it (subscription, API key or a local model) and a model.
 *
 * The seats come from the same join the chat's model picker uses
 * (`modelSeats`, narrowed by `runtimeSeats` for Hermes / OpenClaw). Seats of
 * one brand are folded into one provider with one "access" choice each:
 * OpenAI's ChatGPT subscription (`openai-codex`) and its API key (`openai`)
 * are two rows in the catalog but one provider to a person. Claude is ONE
 * row that can pay both ways; the backend says which ways work right now
 * (`/api/agent-runtimes` → `access`) and the choice is stored as a reserved
 * `account_id` (`jarvis/agent_chat/catalog.py` → `ACCESS_ACCOUNTS`).
 *
 * Only what is connected on this machine is listed (maintainer, 2026-09-02):
 * an access that does not work is not shown, never greyed out. The one
 * exception is an access the provider is known to refuse right now although
 * it is connected (`access_blocked`: Claude's login on Hermes / OpenClaw
 * while the account's Extra Usage is off or spent). It is listed with its
 * reason and cannot be picked, so the person learns why before the first
 * message fails.
 *
 * Pure: no fetch, no store, so it is unit-tested with plain rows.
 */
import type { CuratedModel } from "@/lib/agentChatApi";
import { rankModels } from "@/lib/modelRanking";
import { apiModels } from "../chat/modelChoices";
import type { BrainKind, BrainSeat } from "./brainPicker";

/** Mirrors `API_KEY_ACCOUNT` in `jarvis/agent_chat/catalog.py`. */
export const API_KEY_ACCOUNT = "api-key";
/** Mirrors `SUBSCRIPTION_ACCOUNT` in `jarvis/agent_chat/catalog.py`. */
export const SUBSCRIPTION_ACCOUNT = "subscription";

export interface AccessOption {
  kind: BrainKind;
  /** The catalog row the agent is created on, with the models it offers. */
  seat: BrainSeat;
  /** The `account_id` this access is stored as: a reserved value on a dual
   *  row, else "" (the CLI's active login, or no login at all). */
  accountId: string;
  /** Billed per use outside the plan (Claude on Hermes / OpenClaw). */
  extraUsage: boolean;
  /** Why the provider refuses this access right now (a refusal code such as
   *  `extra_usage_off`); a blocked access is shown but cannot be picked. */
  blocked?: string;
  /** The subscription runs through the vendor's own CLI (Claude Code on
   *  OpenClaw): the person's own risk, the vendor decides how it bills. */
  viaCli?: boolean;
}

/**
 * The choices once a runtime runs some subscriptions through the vendor's own
 * CLI (`cli_subscriptions`): that subscription is offered, not refused, and
 * says so. Pure.
 */
export function withCliSubscriptions(
  access: Record<string, string[]>,
  blocked: AccessBlocked,
  viaCli: readonly string[],
): { access: Record<string, string[]>; blocked: AccessBlocked } {
  const nextAccess = { ...access };
  const nextBlocked = { ...blocked };
  for (const id of viaCli) {
    nextAccess[id] = [...new Set([...(access[id] ?? []), "subscription"])];
    if (nextBlocked[id]?.subscription) {
      const rest = { ...nextBlocked[id] };
      delete rest.subscription;
      nextBlocked[id] = rest;
    }
  }
  return { access: nextAccess, blocked: nextBlocked };
}

/** Mark the subscriptions a runtime runs through the vendor's own CLI. */
export function markCliSubscriptions(choices: ProviderChoice[], viaCli: readonly string[]): ProviderChoice[] {
  if (!viaCli.length) return choices;
  const ids = new Set(viaCli);
  return choices.map((choice) => ({
    ...choice,
    options: choice.options.map((option) => option.kind === "subscription" && ids.has(option.seat.provider.id)
      ? { ...option, extraUsage: false, viaCli: true }
      : option),
  }));
}

/** Per provider id and access kind, the refusal code (`/api/agent-runtimes`). */
export type AccessBlocked = Record<string, Record<string, string>>;

export interface ProviderChoice {
  id: string;
  label: string;
  /** The provider id whose logo stands for this brand. */
  logo: string;
  /** A coding CLI's own mark (`AgentMark`) when the brand is that CLI. */
  mark?: string;
  logoUrl?: string;
  /** Subscription first, then API key, then local. */
  options: AccessOption[];
}

const KIND_ORDER: Record<BrainKind, number> = { subscription: 0, api: 1, local: 2 };

/** Catalog families that are one brand to a person (Gemini's CLI is Antigravity). */
const SAME_BRAND: Record<string, string> = { antigravity: "gemini" };

/** A brand's own name and logo where its rows name a product instead. */
const BRANDS: Record<string, { label: string; logo: string }> = {
  claude: { label: "Anthropic Claude", logo: "claude-api" },
  openai: { label: "OpenAI", logo: "openai" },
  xai: { label: "xAI Grok", logo: "grok" },
  gemini: { label: "Google Gemini", logo: "gemini" },
};

function brandOf(seat: BrainSeat): string {
  const family = seat.provider.family || seat.provider.id;
  return SAME_BRAND[family] ?? family;
}

function optionsFor(
  seat: BrainSeat,
  named: readonly string[] | undefined,
  external: boolean,
  blocked: Record<string, string> = {},
): AccessOption[] {
  const single: AccessOption = { kind: seat.kind, seat, accountId: "", extraUsage: Boolean(seat.extraUsage) };
  // A single-access seat the runtime cannot use yet (the Grok subscription
  // before its agents' login, Gemini's CLI login): listed with its reason.
  if (!named && blocked[seat.kind] && !seat.extraUsage) return [{ ...single, blocked: blocked[seat.kind] }];
  const refused = Object.keys(blocked).filter((way) => !named?.includes(way));
  if (!named && !refused.length) return [single];
  const ways = [...(named ?? []), ...refused];
  // A dual row: the backend named the ways that work right now.
  const viaKey: BrainSeat = {
    ...seat,
    kind: "api",
    accounts: [],
    extraUsage: false,
    provider: { ...seat.provider, curated_models: apiModels(seat.provider.curated_models) },
  };
  const options: AccessOption[] = [];
  // On Jarvis' own loop the subscription is the vendor CLI, which must be installed.
  if (ways.includes("subscription") && (external || seat.provider.cli_installed !== false)) {
    options.push(external
      ? { kind: "subscription", seat: { ...viaKey, kind: "subscription", extraUsage: true }, accountId: SUBSCRIPTION_ACCOUNT, extraUsage: true }
      : { kind: "subscription", seat: { ...seat, kind: "subscription" }, accountId: SUBSCRIPTION_ACCOUNT, extraUsage: false });
  }
  if (ways.includes("api")) options.push({ kind: "api", seat: viaKey, accountId: API_KEY_ACCOUNT, extraUsage: false });
  for (const option of options) {
    if (blocked[option.kind]) option.blocked = blocked[option.kind];
  }
  return options.length ? options : [single];
}

/** Refusal codes with their own explanation (`provider_errors.py`). */
const BLOCKED_REASONS = new Set([
  "extra_usage_off", "extra_usage_spent", "xai_login_needed", "vendor_forbids_subscription",
]);

/** The i18n key that explains a blocked access in plain words. */
export function blockedReasonKey(code: string): string {
  return BLOCKED_REASONS.has(code)
    ? `society.create_agent.access_blocked_${code}`
    : "society.create_agent.access_blocked";
}

/** The access to use: ``kind`` when it can be picked, else the first that can. */
export function pickableOption(choice: ProviderChoice | null, kind = ""): AccessOption | null {
  const open = choice?.options.filter((option) => !option.blocked) ?? [];
  return open.find((option) => option.kind === kind) ?? open[0] ?? null;
}

/**
 * The providers to offer, best access first: a provider with a subscription
 * comes before one with only an API key (a plan already paid for beats a
 * metered key — the maintainer's stated preference), then by name.
 */
export function providerChoices(
  seats: BrainSeat[],
  access: Record<string, readonly string[]> = {},
  external = false,
  accessBlocked: AccessBlocked = {},
): ProviderChoice[] {
  const byBrand = new Map<string, ProviderChoice>();
  for (const seat of seats) {
    const id = brandOf(seat);
    let choice = byBrand.get(id);
    if (!choice) {
      const brand = BRANDS[id];
      choice = {
        id,
        label: brand?.label ?? seat.provider.label,
        logo: brand?.logo ?? seat.provider.id,
        mark: brand ? undefined : seat.provider.agentMark || undefined,
        logoUrl: brand ? undefined : seat.provider.logoUrl,
        options: [],
      };
      byBrand.set(id, choice);
    }
    const blocked = accessBlocked[seat.provider.id];
    for (const option of optionsFor(seat, access[seat.provider.id], external, blocked && typeof blocked === "object" ? blocked : {})) {
      // One row per way of paying: the first seat of a kind wins.
      if (!choice.options.some((known) => known.kind === option.kind)) choice.options.push(option);
    }
  }
  // A provider whose every access is refused right now sorts last.
  const best = (choice: ProviderChoice) => Math.min(
    ...choice.options.map((option) => (option.blocked ? 10 : 0) + KIND_ORDER[option.kind]));
  return [...byBrand.values()]
    .map((choice) => ({ ...choice, options: [...choice.options].sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind]) }))
    .sort((a, b) => best(a) - best(b) || a.label.localeCompare(b.label));
}

/** The models an access offers, newest of each line first. */
export function accessModels(option: AccessOption | null): CuratedModel[] {
  if (!option) return [];
  const models = option.seat.provider.curated_models;
  const ranked = rankModels(models);
  return [...ranked.current, ...ranked.older];
}

/** The model a fresh agent starts on: the row's own default when it lists it, else the first. */
export function defaultModel(option: AccessOption | null): string {
  const models = accessModels(option);
  const preferred = option?.seat.provider.default_model ?? "";
  if (models.some((model) => model.id === preferred)) return preferred;
  return models[0]?.id ?? "";
}
