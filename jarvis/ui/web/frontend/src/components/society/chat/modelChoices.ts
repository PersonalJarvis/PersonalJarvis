import type { CuratedModel } from "@/lib/agentChatApi";
import type { SocietyProviderRow } from "@/lib/societyApi";
import type { ProviderOption } from "@/store/agentChat";
import { brainSeats, effortsFor, type BrainSeat } from "../create/brainPicker";

/** Presentation order: plans, OpenCode's mixed catalog, APIs, local models. */
export function modelGroupOrder(seat: BrainSeat): number {
  if (seat.provider.runner === "opencode-cli") return 1;
  return { subscription: 0, api: 2, local: 3 }[seat.kind];
}

export function collapsibleModels(seat: BrainSeat): boolean {
  return seat.provider.runner === "opencode-cli" || seat.provider.family === "openrouter";
}

/** Only explicit free variants; a cheap model or a free-sounding label is not proof. */
export function isFreeOpenCodeModel(model: CuratedModel): boolean {
  // Zen's one free alias without a suffix. Pricing verified 2026-09-09:
  // https://opencode.ai/docs/zen/#pricing. Never apply it to another endpoint.
  return model.id === "opencode/big-pickle" || /(?:-free|:free)$/i.test(model.id);
}

/** Search reveals matching hidden entries without changing the saved fold state. */
export function visibleModels(seat: BrainSeat, models: CuratedModel[], expanded: boolean, search: string): CuratedModel[] {
  if (expanded || search.trim() || !collapsibleModels(seat)) return models;
  return seat.provider.runner === "opencode-cli" ? models.filter((model) => model.id === "" || isFreeOpenCodeModel(model)) : [];
}

/** CLI-owned credentials need no duplicate account entry in the app. */
export function modelSeats(options: ProviderOption[], providers: SocietyProviderRow[], live: Record<string, CuratedModel[]>, defaultModelLabel = "Default model"): BrainSeat[] {
  const usable = options.map((option) => ({ ...option,
    connected: option.connected || (option.cli_installed === true && providers.some((row) => row.id === option.id && row.subscription && row.accounts.some((account) => account.connected))),
  }));
  const mergedLive = Object.fromEntries(Object.entries(live).map(([id, models]) => {
    const metadata = new Map(options.find((option) => option.id === id)?.curated_models.map((model) => [model.id, model]));
    return [id, models.map((model) => ({ ...metadata.get(model.id), ...model }))];
  }));
  // The shared join rejects a known disconnected CLI and missing binaries.
  // Installed CLIs without an account reader (such as OpenCode) own their login.
  const known = new Set(usable.filter((option) => option.connected).map((option) => option.id));
  return brainSeats(usable, providers, mergedLive, known).map((seat) => {
    const models = [...new Map(seat.provider.curated_models.map((model) => [model.id, model])).values()];
    // The IDE can launch a CLI without a model override. Preserve that same
    // choice when the vendor keeps its catalog private or its discovery fails.
    // API/local seats still need their own model ids; an empty id is not one.
    if (seat.kind === "subscription" && models.length === 0) {
      models.push({ id: "", label: defaultModelLabel });
    }
    return { ...seat, provider: { ...seat.provider, curated_models: models } };
  });
}

/**
 * The seats a Hermes / OpenClaw agent can sit on: an API key, a local model or
 * a subscription Jarvis' model gateway serves (`gateway`); never a
 * subscription CLI's own loop. A dual row (Claude: subscription CLI or API
 * key) runs on its API key there — or, without one, on the Claude Code login
 * (`login`), billed as extra usage — so the CLI's own aliases ("opusplan",
 * "default") are not models to offer. A gateway subscription keeps its
 * accounts: the login decides who pays.
 */
export function runtimeSeats(
  all: BrainSeat[],
  supported: readonly string[],
  gateway: readonly string[] = [],
  login: readonly string[] = [],
  blocked: Record<string, Record<string, string>> = {},
): BrainSeat[] {
  const usable = new Set(supported);
  const served = new Set(gateway);
  const onLogin = new Set(login);
  // A connected seat the runtime cannot use yet stays listed, with its reason
  // (`access_blocked`): every provider the person switched on is shown.
  const refused = (seat: BrainSeat) => !usable.has(seat.provider.id) && Boolean(blocked[seat.provider.id]?.[seat.kind]);
  return all.filter((seat) => usable.has(seat.provider.id) || refused(seat))
    .map((seat) => seat.kind === "subscription" && !served.has(seat.provider.id) && !refused(seat) ? {
      ...seat,
      kind: onLogin.has(seat.provider.id) ? "subscription" as const : "api" as const,
      accounts: [],
      extraUsage: onLogin.has(seat.provider.id),
      provider: { ...seat.provider, curated_models: apiModels(seat.provider.curated_models) },
    } : seat);
}

/**
 * Why a Hermes / OpenClaw seat cannot answer right now ("" when it can): the
 * provider refuses the way this seat pays (`/api/agent-runtimes` →
 * `access_blocked`). Claude's login is refused while its Extra Usage is off;
 * the seat pays that way when it is the login seat (`extraUsage`) or the
 * agent is pinned to its subscription (`account_id` "subscription").
 */
export function seatBlocked(
  seat: BrainSeat,
  blocked: Record<string, Record<string, string>> | undefined,
  pinnedAccount = "",
): string {
  const refused = blocked?.[seat.provider.id];
  if (!refused || typeof refused !== "object") return "";
  const way = seat.extraUsage || pinnedAccount === "subscription" ? "subscription" : seat.kind;
  return typeof refused[way] === "string" ? refused[way] : "";
}

/** A CLI catalog narrowed to real model ids: its aliases mean nothing to an API. */
export function apiModels(models: CuratedModel[]): CuratedModel[] {
  return models.filter((model) => /\d/.test(model.id));
}

/** Display names only; availability and routing always come from the catalog. */
export function providerTitle(seat: BrainSeat, t: (key: string) => string): string {
  const names: Record<string, string> = {
    "claude-cli": "model_group_anthropic", "codex-cli": "model_group_chatgpt",
    "grok-cli": "model_group_grok", "agy-cli": "model_group_google",
  };
  if (seat.kind === "subscription") {
    const key = names[seat.provider.runner];
    const title = key ? t(`society.chat.${key}`) : seat.provider.label;
    return seat.extraUsage ? `${title} · ${t("society.chat.model_extra_usage")}` : title;
  }
  return `${seat.provider.label} · ${t(`society.create.kind_${seat.kind}`)}`;
}

export function modelEffort(seat: BrainSeat, model: string, preferred: string): string {
  const levels = effortsFor(seat, model);
  return levels.includes(preferred) ? preferred
    : levels.includes(seat.provider.default_effort) ? seat.provider.default_effort : levels[0] ?? "";
}

export function matchesModel(seat: BrainSeat, model: CuratedModel, query: string, title: string): boolean {
  if (!query.trim()) return true;
  const fold = (value: string) => value.toLocaleLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "").replace(/[-_./]/g, " ");
  const haystack = fold([title, seat.provider.id, seat.provider.label, model.id, model.label,
    ...seat.accounts.map((account) => `${account.label} ${account.email ?? ""}`)].join(" "));
  return fold(query).trim().split(/\s+/).every((word) => haystack.includes(word));
}
