import type {
  AccountUsage,
  AgentAccount,
  AgentAccountsResponse,
  UsageWindow,
} from "@/lib/agentAccountsApi";

/** One signed-in subscription, ready to draw. */
export interface SubscriptionRow {
  /** The seat the row speaks for: the active one when it is among them. */
  account: AgentAccount;
  /** Every seat signed in to this subscription, in the backend's order. */
  seats: AgentAccount[];
  /** Whether new terminals of this CLI start on this subscription. */
  active: boolean;
  usage: AccountUsage | null;
}

/** Every signed-in subscription of one coding CLI — one block in the tab. */
export interface SubscriptionGroup {
  platform: string;
  displayName: string;
  rows: SubscriptionRow[];
}

/**
 * The tab's blocks: one per CLI, holding only subscriptions that are signed in.
 *
 * A registered seat that never finished signing in is not a subscription the
 * user has, so it is left to the settings dialog that manages seats. A CLI with
 * no signed-in seat gets no block at all. Seats signed in as the same email are
 * one subscription with one usage meter, so they share one row that names every
 * seat; seats without a readable email are never merged. The backend's order is kept — both
 * for the blocks and inside them — so a row never jumps when another one
 * becomes the active seat.
 */
export function groupSubscriptions(
  accounts: AgentAccountsResponse | null,
  usage: readonly AccountUsage[],
): SubscriptionGroup[] {
  const byId = new Map(usage.map((reading) => [reading.account_id, reading]));
  return (accounts?.platforms ?? []).flatMap((group) => {
    const rows: SubscriptionRow[] = [];
    const byEmail = new Map<string, SubscriptionRow>();
    for (const account of group.accounts ?? []) {
      if (!account.connected) continue;
      const active = account.id === group.active_account;
      const usage = byId.get(account.id) ?? null;
      const email = (account.email ?? "").trim().toLowerCase();
      const same = email ? byEmail.get(email) : undefined;
      if (same) {
        same.seats.push(account);
        if (active) Object.assign(same, { account, active: true, usage: usage ?? same.usage });
        else if (!same.usage) same.usage = usage;
        if (!same.account.warning && account.warning) same.account = { ...same.account, warning: account.warning };
        continue;
      }
      const row: SubscriptionRow = { account, seats: [account], active, usage };
      rows.push(row);
      if (email) byEmail.set(email, row);
    }
    if (rows.length === 0) return [];
    return [{ platform: group.platform, displayName: group.display_name || group.platform, rows }];
  });
}

/** The plan name to show: the usage reading's, else the one the login reports. */
export function planName(row: SubscriptionRow): string {
  return row.usage?.plan?.trim() || row.account.tier?.trim() || "";
}

/** The limit closest to running out, or null when nothing was measured. */
export function tightestWindow(usage: AccountUsage | null): UsageWindow | null {
  if (!usage || usage.status !== "ok" || usage.windows.length === 0) return null;
  return usage.windows.reduce((worst, window) => (window.percent > worst.percent ? window : worst));
}

export type ResetText =
  | { key: "reset_soon" }
  | { key: "reset_in_minutes"; vars: { m: number } }
  | { key: "reset_in_hours"; vars: { h: number; m: number } }
  | { key: "reset_on"; vars: { when: string } };

/**
 * When a limit refills, worded for the panel: a countdown inside the next day,
 * a weekday and time beyond it. Null for a missing or unreadable timestamp —
 * no reset line is better than an invented one.
 */
export function resetText(resetsAt: string | null, nowMs: number, locale: string): ResetText | null {
  if (!resetsAt) return null;
  const at = Date.parse(resetsAt);
  if (Number.isNaN(at)) return null;
  const minutes = Math.round((at - nowMs) / 60_000);
  if (minutes < 1) return { key: "reset_soon" };
  if (minutes < 60) return { key: "reset_in_minutes", vars: { m: minutes } };
  if (minutes < 24 * 60) return { key: "reset_in_hours", vars: { h: Math.floor(minutes / 60), m: minutes % 60 } };
  const when = new Intl.DateTimeFormat(locale, { weekday: "short", hour: "2-digit", minute: "2-digit" }).format(at);
  return { key: "reset_on", vars: { when } };
}

/** "5 h" or "30 min" for a window length the provider stated, else "". */
export function windowLength(minutes: number | null): string {
  if (!minutes || minutes <= 0) return "";
  if (minutes % (24 * 60) === 0) return `${minutes / (24 * 60)} d`;
  if (minutes % 60 === 0) return `${minutes / 60} h`;
  return `${minutes} min`;
}
