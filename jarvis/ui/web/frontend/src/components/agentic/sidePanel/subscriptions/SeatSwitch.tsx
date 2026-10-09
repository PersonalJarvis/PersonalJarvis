import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { fill, useT, useUiLanguage } from "@/i18n";
import {
  fetchAutoSwitch,
  switchSeat,
  updateAutoSwitch,
  type AutoSwitchState,
  type SeatSwitchEvent,
} from "@/lib/agentAccountsApi";
import { cn } from "@/lib/utils";
import { BrandedSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useEventStore } from "@/store/events";
import type { SubscriptionRow } from "./subscriptionsModel";

/**
 * The switch state is local to the backend (no provider is asked), so it can
 * be polled more often than usage: an automatic switch should show up in the
 * panel within moments, not at the next usage tick.
 */
const POLL_MS = 15_000;
const POLL_JITTER_MS = 5_000;

/** The spent share at which work moves on, offered as "how much is left". */
const THRESHOLDS = [90, 95, 97, 99, 100] as const;

interface Notice {
  text: string;
  error?: boolean;
}

interface SeatSwitchValue {
  state: AutoSwitchState | null;
  /** The account a one-click switch is moving to right now. */
  pending: string | null;
  notice: Notice | null;
  switchTo: (platform: string, accountId: string, label: string) => Promise<void>;
  setAuto: (change: { enabled?: boolean; at_percent?: number }) => Promise<void>;
  toolName: (platform: string) => string;
}

const SeatSwitchContext = createContext<SeatSwitchValue | null>(null);

/**
 * One-click and automatic subscription switching for the Subscriptions tab.
 *
 * ``onSwitched`` re-reads the account list, so the Active badge follows a
 * switch at once — including one the backend made on its own.
 */
export function SeatSwitchProvider({
  children,
  onSwitched,
  toolNames,
}: {
  children: ReactNode;
  onSwitched: () => void;
  toolNames: Record<string, string>;
}) {
  const t = useT();
  const [state, setState] = useState<AutoSwitchState | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [revision, setRevision] = useState(0);
  const lastEvent = useRef<number | null>(null);
  const switched = useRef(onSwitched);
  switched.current = onSwitched;

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      if (useEventStore.getState().activeSection === "agentic-ide") {
        try {
          const next = await fetchAutoSwitch();
          if (alive) {
            setState(next);
            const newest = next?.events[0]?.at ?? 0;
            // An automatic switch moved the badge: re-read the accounts. The
            // first reading only records where the history stands.
            if (lastEvent.current !== null && newest > lastEvent.current) switched.current();
            lastEvent.current = newest;
          }
        } catch {
          // Keep the last answer on screen; the next tick tries again.
        }
      }
      if (alive) timer = window.setTimeout(tick, POLL_MS + Math.random() * POLL_JITTER_MS);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [revision]);

  const switchTo = useCallback(
    async (platform: string, accountId: string, label: string) => {
      setPending(accountId);
      setNotice(null);
      try {
        const result = await switchSeat(platform, accountId);
        const parts = [fill(t("ide_side_panel.subscriptions.switch.done"), { label: result.active_label || label })];
        if (result.moved) parts.push(fill(t("ide_side_panel.subscriptions.switch.moved"), { n: result.moved }));
        if (result.queued) parts.push(fill(t("ide_side_panel.subscriptions.switch.queued"), { n: result.queued }));
        setNotice({ text: parts.join(" ") });
        switched.current();
        setRevision((value) => value + 1);
      } catch (err) {
        setNotice({ text: fill(t("ide_side_panel.subscriptions.switch.failed"), { error: (err as Error).message }), error: true });
      } finally {
        setPending(null);
      }
    },
    [t],
  );

  const setAuto = useCallback(async (change: { enabled?: boolean; at_percent?: number }) => {
    try {
      setState(await updateAutoSwitch(change));
    } catch (err) {
      setNotice({ text: fill(t("ide_side_panel.subscriptions.switch.failed"), { error: (err as Error).message }), error: true });
    }
  }, [t]);

  const toolName = useCallback((platform: string) => toolNames[platform] || platform, [toolNames]);
  const value = useMemo(
    () => ({ state, pending, notice, switchTo, setAuto, toolName }),
    [state, pending, notice, switchTo, setAuto, toolName],
  );
  return <SeatSwitchContext.Provider value={value}>{children}</SeatSwitchContext.Provider>;
}

function useSeatSwitch(): SeatSwitchValue | null {
  return useContext(SeatSwitchContext);
}

function clock(at: number, lang: string): string {
  return new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" }).format(at * 1000);
}

function eventText(t: (key: string) => string, event: SeatSwitchEvent, tool: string, lang: string): string {
  const base = "ide_side_panel.subscriptions.switch";
  const vars = { time: clock(event.at, lang), tool, from: event.from_label, to: event.to_label };
  if (event.reason === "limit") return fill(t(`${base}.event_limit`), vars);
  if (event.reason === "threshold") return fill(t(`${base}.event_threshold`), vars);
  if (event.reason === "no_seat") return fill(t(`${base}.event_no_seat`), vars);
  if (event.reason === "refilled") return fill(t(`${base}.event_refilled`), vars);
  return fill(t(`${base}.event_manual`), vars);
}

/**
 * The bar under the tab header: whether work moves on by itself, when, and
 * the last thing that happened. Absent on a backend without the feature.
 */
export function AutoSwitchBar() {
  const t = useT();
  const lang = useUiLanguage();
  const seat = useSeatSwitch();
  const state = seat?.state;
  const options = useMemo(
    () =>
      THRESHOLDS.map((percent) => ({
        value: String(percent),
        label:
          percent >= 100
            ? t("ide_side_panel.subscriptions.switch.at_limit")
            : fill(t("ide_side_panel.subscriptions.switch.at_left"), { left: 100 - percent }),
      })),
    [t],
  );
  if (!seat || !state) return null;
  const latest = state.events[0];
  const current = THRESHOLDS.reduce((best, value) =>
    Math.abs(value - state.at_percent) < Math.abs(best - state.at_percent) ? value : best,
  );
  return (
    <div data-testid="auto-switch-bar" className="shrink-0 space-y-1.5 border-b border-border/60 px-3 py-2">
      <div className="flex min-h-7 items-center gap-2">
        <Switch
          id="auto-switch-toggle"
          data-testid="auto-switch-toggle"
          checked={state.enabled}
          onCheckedChange={(enabled) => void seat.setAuto({ enabled })}
          aria-label={t("ide_side_panel.subscriptions.switch.auto")}
        />
        <QuickTooltip content={t("ide_side_panel.subscriptions.switch.auto_tip")} side="bottom" className="min-w-0 flex-1">
          <label htmlFor="auto-switch-toggle" className="block cursor-pointer truncate text-[11.5px] text-foreground">
            {t("ide_side_panel.subscriptions.switch.auto")}
          </label>
        </QuickTooltip>
        <BrandedSelect
          value={String(current)}
          options={options}
          onValueChange={(value) => void seat.setAuto({ at_percent: Number(value) })}
          ariaLabel={t("ide_side_panel.subscriptions.switch.threshold")}
          disabled={!state.enabled}
          testId="auto-switch-threshold"
          className="h-7 w-auto shrink-0 text-[11px]"
        />
      </div>
      {seat.notice && (
        <p
          data-testid="seat-switch-notice"
          role="status"
          className={cn("text-[10.5px]", seat.notice.error ? "text-destructive" : "text-muted-foreground")}
        >
          {seat.notice.text}
        </p>
      )}
      {!seat.notice && latest && (
        <p data-testid="seat-switch-event" className="truncate text-[10.5px] text-muted-foreground" title={eventText(t, latest, seat.toolName(latest.platform), lang)}>
          {eventText(t, latest, seat.toolName(latest.platform), lang)}
        </p>
      )}
    </div>
  );
}

/**
 * One click: this subscription becomes the active one and every running agent
 * of the tool moves onto it, each continuing its own conversation. Shown on
 * every signed-in row that is not already active.
 */
export function SwitchSeatButton({ row }: { row: SubscriptionRow }) {
  const t = useT();
  const lang = useUiLanguage();
  const seat = useSeatSwitch();
  if (!seat?.state || row.active) return null;
  const { account } = row;
  const ids = new Set(row.seats.map((item) => item.id));
  const empty = seat.state.exhausted
    .filter((item) => ids.has(item.account_id))
    .reduce((latest, item) => Math.max(latest, item.until), 0);
  const busy = seat.pending !== null;
  const mine = seat.pending === account.id;
  const tool = seat.toolName(account.platform);
  return (
    <span className="flex shrink-0 items-center gap-1.5">
      {empty > 0 && (
        <span data-testid="seat-empty" className="text-[10px] tabular-nums text-muted-foreground">
          {fill(t("ide_side_panel.subscriptions.switch.empty_until"), { when: clock(empty, lang) })}
        </span>
      )}
      <QuickTooltip content={fill(t("ide_side_panel.subscriptions.switch.use_tip"), { tool })} side="bottom" className="inline-flex">
        <button
          type="button"
          data-testid="seat-switch"
          disabled={busy}
          onClick={() => void seat.switchTo(account.platform, account.id, account.email || account.label)}
          className="inline-flex h-6 items-center gap-1 rounded-md border border-border px-2 text-[11px] text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
        >
          {mine && <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden />}
          {mine ? t("ide_side_panel.subscriptions.switch.switching") : t("ide_side_panel.subscriptions.switch.use")}
        </button>
      </QuickTooltip>
    </span>
  );
}
