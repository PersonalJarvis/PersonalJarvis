import { useCallback, useEffect, useId, useState } from "react";
import { AlertTriangle, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { fill, useT, useUiLanguage } from "@/i18n";
import {
  fetchAgentAccounts,
  fetchAgentUsage,
  type AccountUsage,
  type AgentAccountsResponse,
  type UsageWindow,
} from "@/lib/agentAccountsApi";
import { cn } from "@/lib/utils";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useEventStore } from "@/store/events";
import { AutoSwitchBar, SeatSwitchProvider, SwitchSeatButton } from "./SeatSwitch";
import {
  groupSubscriptions,
  planName,
  resetText,
  tightestWindow,
  windowLength,
  type SubscriptionGroup,
  type SubscriptionRow,
} from "./subscriptionsModel";

/**
 * The floor of the poll interval. The server states its usage cache lifetime
 * and the poll follows it, but never faster than this: a reading that changes
 * every few minutes does not need a request every few seconds.
 */
const MIN_POLL_MS = 60_000;
const POLL_JITTER_MS = 10_000;

const BAR_TONE: Record<string, string> = {
  normal: "bg-primary",
  warning: "bg-warning",
  critical: "bg-destructive",
};

const TEXT_TONE: Record<string, string> = {
  normal: "text-muted-foreground",
  warning: "text-warning",
  critical: "text-destructive",
};

/**
 * Accounts and their plan usage, polled while the Agentic IDE is in front.
 * Every tick is jittered (AP-33); a failed tick keeps the last answer on screen.
 */
function useSubscriptions() {
  const [accounts, setAccounts] = useState<AgentAccountsResponse | null>(null);
  const [usage, setUsage] = useState<AccountUsage[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [force, setForce] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    let refresh = force > 0;
    let pollMs = MIN_POLL_MS;
    const tick = async () => {
      if (useEventStore.getState().activeSection === "agentic-ide") {
        setLoading(true);
        try {
          const [nextAccounts, nextUsage] = await Promise.all([fetchAgentAccounts(), fetchAgentUsage(refresh)]);
          refresh = false;
          if (nextUsage) pollMs = Math.max(MIN_POLL_MS, nextUsage.ttl_seconds * 1000);
          if (alive) {
            setAccounts(nextAccounts);
            // A backend without the usage route answers null: the rows still
            // show, just without numbers.
            setUsage(nextUsage?.accounts ?? []);
            setError("");
          }
        } catch (err) {
          if (alive) setError((err as Error).message);
        } finally {
          if (alive) setLoading(false);
        }
      }
      if (alive) timer = window.setTimeout(tick, pollMs + Math.random() * POLL_JITTER_MS);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [force]);
  const refresh = useCallback(() => setForce((value) => value + 1), []);
  return { accounts, usage, error, loading, refresh };
}

/** Wall-clock minutes, so reset countdowns stay true between polls. */
function useMinuteClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

function windowLabel(t: (key: string) => string, window: UsageWindow): string {
  const base = "ide_side_panel.subscriptions.window";
  if (window.kind === "session") {
    const length = windowLength(window.window_minutes);
    return length ? fill(t(`${base}.session_length`), { length }) : t(`${base}.session`);
  }
  if (window.kind === "weekly") return t(`${base}.weekly`);
  if (window.kind === "monthly") return t(`${base}.monthly`);
  if (window.kind === "weekly_scoped") {
    return window.scope_label ? fill(t(`${base}.weekly_scoped`), { scope: window.scope_label }) : t(`${base}.weekly`);
  }
  return window.raw_label || t(`${base}.other`);
}

function UsageBar({ window, now }: { window: UsageWindow; now: number }) {
  const t = useT();
  const lang = useUiLanguage();
  const percent = Math.round(Math.min(100, Math.max(0, window.percent)));
  const label = windowLabel(t, window);
  const reset = resetText(window.resets_at, now, lang);
  return (
    <li data-testid="subscription-window" data-kind={window.kind} className="space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-[11.5px]">
        <span className="truncate text-foreground">{label}</span>
        <span className={cn("shrink-0 tabular-nums", TEXT_TONE[window.severity] ?? TEXT_TONE.normal)}>
          {fill(t("ide_side_panel.subscriptions.used"), { percent })}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-1.5 overflow-hidden rounded-full bg-muted"
      >
        <div
          className={cn("h-full rounded-full", BAR_TONE[window.severity] ?? BAR_TONE.normal)}
          style={{ width: `${percent}%` }}
        />
      </div>
      {reset && (
        <p className="text-[10.5px] text-muted-foreground">
          {"vars" in reset
            ? fill(t(`ide_side_panel.subscriptions.${reset.key}`), reset.vars)
            : t(`ide_side_panel.subscriptions.${reset.key}`)}
        </p>
      )}
    </li>
  );
}

/** The expanded part of a row: every limit of the plan, or why there is none. */
function UsageDetails({ usage, now }: { usage: AccountUsage | null; now: number }) {
  const t = useT();
  const lang = useUiLanguage();
  if (!usage) {
    return <p className="text-[11.5px] text-muted-foreground">{t("ide_side_panel.subscriptions.usage_pending")}</p>;
  }
  if (usage.status !== "ok" || usage.windows.length === 0) {
    const key =
      usage.status === "unsupported"
        ? "usage_unsupported"
        : usage.status === "signed_out"
          ? "usage_signed_out"
          : "usage_unavailable";
    return (
      <p data-testid="subscription-usage-status" className="text-[11.5px] text-muted-foreground">
        {t(`ide_side_panel.subscriptions.${key}`)}
      </p>
    );
  }
  const asOf =
    usage.source === "cached" && usage.as_of
      ? new Intl.DateTimeFormat(lang, { weekday: "short", hour: "2-digit", minute: "2-digit" }).format(usage.as_of * 1000)
      : "";
  return (
    <div className="space-y-2">
      <ul className="space-y-2.5">
        {usage.windows.map((window) => (
          <UsageBar key={`${window.kind}:${window.scope_label ?? window.raw_label ?? ""}`} window={window} now={now} />
        ))}
      </ul>
      {asOf && (
        <p data-testid="subscription-cached" className="text-[10.5px] text-muted-foreground">
          {fill(t("ide_side_panel.subscriptions.cached"), { when: asOf })}
        </p>
      )}
    </div>
  );
}

function SubscriptionLine({ row, now }: { row: SubscriptionRow; now: number }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const { account } = row;
  const title = account.email || account.label;
  const plan = planName(row);
  const seats = account.email ? row.seats.map((seat) => seat.label).join(", ") : "";
  const subtitle = [seats, plan].filter(Boolean).join(" · ");
  const tightest = tightestWindow(row.usage);
  return (
    <li data-testid="subscription-row" data-account={account.id} data-active={row.active || undefined}>
      <div className="flex items-center gap-1.5 pr-1">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={detailsId}
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronRight
            className={cn(
              "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
              open && "rotate-90",
            )}
            aria-hidden
          />
          <span className="flex min-w-0 flex-1 flex-col leading-tight">
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="truncate text-[12.5px] font-medium text-foreground">{title}</span>
              {row.active && (
                <QuickTooltip content={t("ide_side_panel.subscriptions.active_tip")} side="bottom" className="inline-flex shrink-0">
                  <span className="rounded border border-border px-1 text-[9.5px] uppercase tracking-wide text-muted-foreground">
                    {t("ide_side_panel.subscriptions.active")}
                  </span>
                </QuickTooltip>
              )}
              {account.warning && (
                <QuickTooltip content={account.warning} side="bottom" className="inline-flex shrink-0">
                  <AlertTriangle
                    data-testid="subscription-warning"
                    className="h-3.5 w-3.5 text-warning"
                    aria-label={account.warning}
                  />
                </QuickTooltip>
              )}
            </span>
            {subtitle && <span className="truncate text-[11px] text-muted-foreground">{subtitle}</span>}
          </span>
          {tightest && (
            <span
              data-testid="subscription-peak"
              className={cn("shrink-0 text-[11px] tabular-nums", TEXT_TONE[tightest.severity] ?? TEXT_TONE.normal)}
            >
              {Math.round(tightest.percent)}%
            </span>
          )}
        </button>
        <SwitchSeatButton row={row} />
      </div>
      {open && (
        <div id={detailsId} data-testid="subscription-details" className="pb-2 pl-7 pr-2 pt-1">
          <UsageDetails usage={row.usage} now={now} />
        </div>
      )}
    </li>
  );
}

function SubscriptionBlock({ group, now }: { group: SubscriptionGroup; now: number }) {
  return (
    <section data-testid="subscription-group" data-platform={group.platform} className="px-2 py-2">
      <header className="flex items-center gap-2 px-2 pb-1">
        <ProviderLogo providerId={group.platform} label={group.displayName} size="sm" />
        <h3 className="min-w-0 flex-1 truncate text-[12px] font-semibold text-foreground">{group.displayName}</h3>
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">{group.rows.length}</span>
      </header>
      <ul className="space-y-0.5">
        {group.rows.map((row) => (
          <SubscriptionLine key={row.account.id} row={row} now={now} />
        ))}
      </ul>
    </section>
  );
}

/**
 * The Subscriptions tab: every signed-in coding-CLI subscription, one block per
 * CLI. A row is the account at a glance; opening it shows how much of the
 * plan's limits is spent and when each one refills.
 */
export function SubscriptionsTab() {
  const t = useT();
  const { accounts, usage, error, loading, refresh } = useSubscriptions();
  const now = useMinuteClock();
  const groups = groupSubscriptions(accounts, usage);
  const toolNames = Object.fromEntries(groups.map((group) => [group.platform, group.displayName]));
  return (
    <SeatSwitchProvider onSwitched={refresh} toolNames={toolNames}>
      <div data-testid="ide-subscriptions-tab" className="flex h-full min-h-0 flex-col">
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border/60 px-3">
          <span className="min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground">
            {t("ide_side_panel.subscriptions.hint")}
          </span>
          <QuickTooltip content={t("ide_side_panel.subscriptions.refresh")} side="bottom" className="inline-flex shrink-0">
            <button
              type="button"
              data-testid="subscriptions-refresh"
              aria-label={t("ide_side_panel.subscriptions.refresh")}
              disabled={loading}
              onClick={refresh}
              className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            >
              {loading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              )}
            </button>
          </QuickTooltip>
        </div>
        <AutoSwitchBar />
        <div className="min-h-0 flex-1 overflow-y-auto">
          {error && (
            <p data-testid="subscriptions-error" className="mx-3 mt-3 text-[11.5px] text-destructive">
              {fill(t("ide_side_panel.subscriptions.error"), { error })}
            </p>
          )}
          {!accounts && !error && (
            <div className="flex justify-center py-6">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden />
            </div>
          )}
          {accounts && groups.length === 0 && (
            <p data-testid="subscriptions-empty" className="mx-3 mt-3 text-[12px] text-muted-foreground">
              {t("ide_side_panel.subscriptions.empty")}
            </p>
          )}
          <div className="divide-y divide-border/60">
            {groups.map((group) => (
              <SubscriptionBlock key={group.platform} group={group} now={now} />
            ))}
          </div>
        </div>
      </div>
    </SeatSwitchProvider>
  );
}
