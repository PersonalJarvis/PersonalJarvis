import type { CuratedModel } from "@/lib/agentChatApi";
import type { SocietyProviderRow } from "@/lib/societyApi";
import type { ProviderOption } from "@/store/agentChat";
import { brainSeats, effortsFor, type BrainSeat } from "../create/brainPicker";

/** CLI-owned credentials need no duplicate account entry in the app. */
export function modelSeats(options: ProviderOption[], providers: SocietyProviderRow[], live: Record<string, CuratedModel[]>): BrainSeat[] {
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
  return brainSeats(usable, providers, mergedLive, known).map((seat) => ({ ...seat, provider: { ...seat.provider,
    curated_models: [...new Map(seat.provider.curated_models.map((model) => [model.id, model])).values()],
  } }));
}

/** Display names only; availability and routing always come from the catalog. */
export function providerTitle(seat: BrainSeat, t: (key: string) => string): string {
  const names: Record<string, string> = {
    "claude-cli": "model_group_anthropic", "codex-cli": "model_group_chatgpt",
    "grok-cli": "model_group_grok", "agy-cli": "model_group_google",
  };
  if (seat.kind === "subscription") {
    const key = names[seat.provider.runner];
    return key ? t(`society.chat.${key}`) : seat.provider.label;
  }
  return `${seat.provider.label} · ${t(`society.create.kind_${seat.kind}`)}`;
}

export function modelEffort(seat: BrainSeat, model: string, preferred: string): string {
  const levels = effortsFor(seat, model);
  return levels.includes(preferred) ? preferred
    : levels.includes(seat.provider.default_effort) ? seat.provider.default_effort : levels[0] ?? "";
}

export function matchesModel(seat: BrainSeat, model: CuratedModel, query: string, title: string): boolean {
  const fold = (value: string) => value.toLocaleLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "").replace(/[-_./]/g, " ");
  const haystack = fold([title, seat.provider.id, seat.provider.label, model.id, model.label,
    ...seat.accounts.map((account) => `${account.label} ${account.email ?? ""}`)].join(" "));
  return fold(query).trim().split(/\s+/).every((word) => haystack.includes(word));
}
