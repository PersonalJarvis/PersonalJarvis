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
 * an access that does not work is not shown, never greyed out.
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
}

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

function optionsFor(seat: BrainSeat, ways: readonly string[] | undefined, external: boolean): AccessOption[] {
  const single: AccessOption = { kind: seat.kind, seat, accountId: "", extraUsage: Boolean(seat.extraUsage) };
  if (!ways) return [single];
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
  return options.length ? options : [single];
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
    for (const option of optionsFor(seat, access[seat.provider.id], external)) {
      // One row per way of paying: the first seat of a kind wins.
      if (!choice.options.some((known) => known.kind === option.kind)) choice.options.push(option);
    }
  }
  const best = (choice: ProviderChoice) => Math.min(...choice.options.map((option) => KIND_ORDER[option.kind]));
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
