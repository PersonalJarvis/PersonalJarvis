/**
 * What the creator's "Runs on" picker lists: the seats an agent can sit on
 * RIGHT NOW on this machine, and nothing else (maintainer, 2026-09-02: a
 * provider that is not connected is not shown, one sentence says where to
 * connect it).
 *
 * A seat is one catalog row joined with the Agents tab's credential truth
 * (`joinProviderOptions`, the same join the chat's composer uses) plus, for a
 * vendor CLI on a plan, the subscription logins stored for it
 * (GET /api/society/providers). Three kinds, listed in this order:
 *
 *   subscription — a vendor CLI signed in on a plan (Claude Max, ChatGPT,
 *                  SuperGrok, Google): billed to the plan, not per token;
 *   api          — a provider's own endpoint behind a saved key;
 *   local        — a model on this machine (Ollama, a local server).
 *
 * Pure: no fetch, no store, so it is unit-tested with plain rows.
 */
import { isApiRunner, type CuratedModel } from "@/lib/agentChatApi";
import type { SocietyAccount, SocietyProviderRow } from "@/lib/societyApi";
import type { ProviderOption } from "@/store/agentChat";

export type BrainKind = "subscription" | "api" | "local";

export interface BrainSeat {
  provider: ProviderOption;
  kind: BrainKind;
  /** Signed-in logins of that CLI; empty on an API or local seat. */
  accounts: SocietyAccount[];
}

const KIND_ORDER: Record<BrainKind, number> = { subscription: 0, api: 1, local: 2 };

function kindOf(option: ProviderOption, row: SocietyProviderRow | undefined): BrainKind {
  if (row ? row.subscription : !isApiRunner(option.runner)) return "subscription";
  return option.keyless ? "local" : "api";
}

/** The connected seats, subscriptions first, each kind sorted by label. */
export function brainSeats(
  options: ProviderOption[],
  society: SocietyProviderRow[],
): BrainSeat[] {
  const byId = new Map(society.map((r) => [r.id, r]));
  return options
    .filter((o) => o.connected)
    .map((provider) => {
      const row = byId.get(provider.id);
      const kind = kindOf(provider, row);
      const accounts = kind === "subscription" ? (row?.accounts ?? []).filter((a) => a.connected) : [];
      return { provider, kind, accounts };
    })
    .sort(
      (a, b) =>
        KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.provider.label.localeCompare(b.provider.label),
    );
}

/** The seat a fresh agent starts on: the brain marked active, else the first listed. */
export function defaultSeat(seats: BrainSeat[]): BrainSeat | null {
  return seats.find((s) => s.provider.active) ?? seats[0] ?? null;
}

/** The models this seat offers; empty means "type one" (a live list not yet fetched). */
export function modelsFor(seat: BrainSeat | null): CuratedModel[] {
  return seat?.provider.curated_models ?? [];
}

/** The seat's effort ladder, narrowed to the picked model's own when it has one. */
export function effortsFor(seat: BrainSeat | null, modelId: string): string[] {
  if (!seat) return [];
  const model = seat.provider.curated_models.find((m) => m.id === modelId);
  if (model && Array.isArray(model.efforts)) return model.efforts;
  return seat.provider.effort_levels ?? [];
}

/** One signed-in login: the picker is shown only when there is a choice. */
export function accountChoice(seat: BrainSeat | null): SocietyAccount[] {
  return seat && seat.accounts.length > 1 ? seat.accounts : [];
}

/** The line under an account's label: its e-mail, else the plan, else nothing. */
export function accountHint(account: SocietyAccount): string {
  return account.email || account.tier || "";
}
