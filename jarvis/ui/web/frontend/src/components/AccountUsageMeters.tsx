/**
 * How much of one subscription's plan is already spent.
 *
 * The number that decides WHICH seat to open a terminal on used to be invisible
 * here: it required starting the CLI and typing its own status command, once
 * per seat, in a pane that was then thrown away. These meters put the same
 * figures next to the row you switch with.
 *
 * Three deliberate choices, each one a thing that would otherwise mislead:
 *
 * 1. **A stale reading never looks live.** A percentage can come from the
 *    provider just now or from what the CLI last wrote to disk, and for an idle
 *    seat the second can be days old. The freshness is stated on every block
 *    rather than left to be assumed — this is the number a decision is made on.
 * 2. **A scoped limit is shown beside the overall one.** A week that reads 55%
 *    while one model's own weekly budget sits at 99% is the case where "plenty
 *    left" is the wrong conclusion, so both bars are drawn.
 * 3. **Severity comes from the backend, not from the bar's own arithmetic.**
 *    The provider knows about grace periods and soft caps that a bare
 *    percentage does not express, so the colour follows what it said.
 *
 * `now` is a prop rather than a timer per meter: one clock in the panel means
 * every countdown on screen agrees, and a card with four seats does not run
 * twelve intervals.
 */

import { Gauge } from "lucide-react";

import { useT } from "@/i18n";
import type { AccountUsage, UsageWindow } from "@/lib/agentAccountsApi";
import { cn } from "@/lib/utils";

/**
 * Bar colour per severity.
 *
 * `bg-primary` and `bg-destructive` are theme tokens, so they follow light and
 * dark mode on their own. The middle step is a fixed orange on purpose: the
 * brand accent IS amber, so a token-based "warning" would be nearly the same
 * hue as "normal" and the one transition that has to be noticed would be the
 * one you cannot see.
 */
const BAR_CLASS: Record<string, string> = {
  normal: "bg-primary",
  warning: "bg-orange-500",
  critical: "bg-destructive",
};

const PERCENT_CLASS: Record<string, string> = {
  normal: "text-foreground",
  warning: "text-orange-500",
  critical: "text-destructive",
};

/** Minutes as a short human duration — "5 h", "45 min", "7 d". */
function windowLabel(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / (60 * 24))} d`;
}

/**
 * A span of milliseconds as a short countdown — "2 h 14 min", "18 min", "3 d".
 *
 * Deliberately two units at most. "1 d 4 h 12 min 9 s" is not more useful for
 * deciding whether to keep working on this seat, and it wraps the line.
 */
function durationLabel(ms: number): string {
  const totalMinutes = Math.max(0, Math.round(ms / 60000));
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 24) return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days} d ${restHours} h` : `${days} d`;
}

/** What to call one window, in the user's language. */
function useWindowLabel(): (window: UsageWindow) => string {
  const t = useT();
  return (window) => {
    if (window.kind === "weekly_scoped") {
      const base = t("agent_accounts.usage.kind.weekly");
      return window.scope_label ? `${base} · ${window.scope_label}` : base;
    }
    if (window.kind === "session") {
      // The short window is 5 hours on both CLIs today, so the translation says
      // so. If a provider ever ships a different one, the measured length wins
      // over the wording rather than the label quietly becoming a lie.
      if (window.window_minutes && window.window_minutes !== 300) {
        return `${windowLabel(window.window_minutes)} · ${t("agent_accounts.usage.kind.rolling")}`;
      }
      return t("agent_accounts.usage.kind.session");
    }
    if (window.kind === "weekly") return t("agent_accounts.usage.kind.weekly");
    if (window.kind === "monthly") return t("agent_accounts.usage.kind.monthly");
    if (window.window_minutes) {
      return `${windowLabel(window.window_minutes)} · ${t("agent_accounts.usage.kind.rolling")}`;
    }
    return window.raw_label || t("agent_accounts.usage.kind.other");
  };
}

function Meter({ window: usageWindow, now }: { window: UsageWindow; now: number }) {
  const t = useT();
  const label = useWindowLabel()(usageWindow);
  const severity = usageWindow.severity in BAR_CLASS ? usageWindow.severity : "normal";
  const percent = Math.max(0, Math.min(100, usageWindow.percent));
  const resetsInMs = usageWindow.resets_at
    ? new Date(usageWindow.resets_at).getTime() - now
    : null;
  // A reset that has already passed means the window rolled over and the
  // reading predates it. Showing "in -3 h" would be nonsense; showing nothing
  // lets the freshness line carry the story.
  const countdown =
    resetsInMs !== null && Number.isFinite(resetsInMs) && resetsInMs > 0
      ? `${t("agent_accounts.usage.resets_in")} ${durationLabel(resetsInMs)}`
      : null;

  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {label}
          {countdown && (
            <span className="ml-1.5 normal-case tracking-normal opacity-80">
              · {countdown}
            </span>
          )}
        </span>
        <span
          className={cn(
            "shrink-0 text-[11px] font-semibold tabular-nums",
            PERCENT_CLASS[severity],
          )}
        >
          {Math.round(percent)}%
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuenow={Math.round(percent)}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1.5 w-full overflow-hidden rounded-full bg-foreground/10"
      >
        <div
          className={cn("h-full rounded-full transition-all duration-500", BAR_CLASS[severity])}
          style={{ width: `${Math.max(percent, percent > 0 ? 2 : 0)}%` }}
        />
      </div>
    </div>
  );
}

export function AccountUsageMeters({
  usage,
  now,
}: {
  usage: AccountUsage | undefined;
  now: number;
}) {
  const t = useT();
  if (!usage) return null;

  // A seat with no login already says so on the row above in plain words.
  // Repeating it as a usage state would be the same sentence twice.
  if (usage.status === "signed_out") return null;

  if (usage.status !== "ok" || usage.windows.length === 0) {
    return (
      <p className="pl-6 text-[10px] leading-relaxed text-muted-foreground">
        {usage.status === "unsupported"
          ? t("agent_accounts.usage.state.unsupported")
          : t("agent_accounts.usage.state.unavailable")}
      </p>
    );
  }

  const live = usage.source === "live";
  const ageMs = usage.as_of ? now - usage.as_of * 1000 : null;

  return (
    <div className="ml-6 space-y-2 rounded-xl border border-border/60 bg-background/40 px-3 py-2.5">
      <div className="flex items-center gap-1.5">
        <Gauge className="h-3 w-3 shrink-0 text-muted-foreground" />
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("agent_accounts.usage.title")}
        </span>
        {usage.plan && (
          <span className="shrink-0 rounded-full border border-border bg-secondary/60 px-1.5 py-px text-[9px] font-medium text-muted-foreground">
            {usage.plan}
          </span>
        )}
        <span
          className="ml-auto flex shrink-0 items-center gap-1 text-[9px] text-muted-foreground"
          // The whole point of this line: a cached weekly figure for an idle
          // seat can be days old, and it is the number a subscription is picked
          // on. The exact timestamp is one hover away.
          title={usage.as_of ? new Date(usage.as_of * 1000).toLocaleString() : undefined}
        >
          <span
            aria-hidden="true"
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              live ? "bg-emerald-500" : "bg-muted-foreground/50",
            )}
          />
          {live
            ? t("agent_accounts.usage.live")
            : ageMs !== null && ageMs > 0
              ? `${t("agent_accounts.usage.as_of")} ${durationLabel(ageMs)}`
              : t("agent_accounts.usage.cached")}
        </span>
      </div>

      <div className="space-y-1.5">
        {usage.windows.map((window, index) => (
          <Meter
            key={`${window.kind}:${window.scope_label ?? ""}:${index}`}
            window={window}
            now={now}
          />
        ))}
      </div>
    </div>
  );
}

export default AccountUsageMeters;
