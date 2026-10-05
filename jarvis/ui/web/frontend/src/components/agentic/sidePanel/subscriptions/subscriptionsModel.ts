import type {
  AccountUsage,
  AgentAccount,
  AgentAccountsResponse,
  UsageWindow,
} from "@/lib/agentAccountsApi";

/** One signed-in subscription, ready to draw. */
export interface SubscriptionRow {
  account: AgentAccount;
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
 * no signed-in seat gets no block at all. The backend's order is kept — both
 * for the blocks and inside them — so a row never jumps when another one
 * becomes the active seat.
 */
export function groupSubscriptions(
  accounts: AgentAccountsResponse | null,
  usage: readonly AccountUsage[],
): SubscriptionGroup[] {
  const byId = new Map(usage.map((reading) => [reading.account_id, reading]));
  return (accounts?.platforms ?? []).flatMap((group) => {
    const rows = (group.accounts ?? [])
      .filter((account) => account.connected)
      .map((account) => ({
        account,
        active: account.id === group.active_account,
        usage: byId.get(account.id) ?? null,
      }));
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
