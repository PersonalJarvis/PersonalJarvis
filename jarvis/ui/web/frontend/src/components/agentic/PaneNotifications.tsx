/**
 * The bell in the Agentic IDE header — "which of these terminals stopped?"
 *
 * ## The gap it closes
 *
 * A workspace is up to a dozen coding agents in panes the size of a postcard,
 * each working for anything between twenty seconds and half an hour. None of
 * them announces that it is done: the spinner row simply stops being drawn, and
 * a finished pane looks exactly like the eleven around it that are still
 * thinking. So the only way to know is to keep watching the wall — and the real
 * outcome is that work sits finished and unread until somebody happens to look.
 *
 * The bell counts what the backend noticed while nobody was looking; the panel
 * behind it lists the entries and takes you to the pane that raised each one.
 *
 * ## Why it opens as an anchored panel and not a modal
 *
 * The list exists to send you somewhere. A centred dialog with an overlay hides
 * the grid it is about, so "jump to T7" would mean reading a name, dismissing
 * the dialog, and finding T7 yourself. Anchored under the bell, the grid stays
 * visible behind it and the jump lands somewhere you can see happening.
 *
 * Rendered in a PORTAL for the same reason `PaneRecap` is: the header row is a
 * flex line of buttons that clips its own overflow, and a panel bounded by it
 * would be a few centimetres wide.
 *
 * ## What "read" means here
 *
 * Opening the panel marks everything read — the count is a "look at this",
 * and looking at it is the whole condition. The entries stay in the list until
 * they are discarded or their workspace closes, so nothing is lost by opening
 * it; only the badge goes quiet.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  CircleHelp,
  PowerOff,
  Trash2,
  X,
} from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import {
  clearPaneNotifications,
  fetchPaneNotifications,
  markPaneNotificationsRead,
  type PaneNotification,
  type PaneNotificationKind,
  type PaneNotificationsState,
} from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";

/**
 * How often the bell asks.
 *
 * Faster than the interrupted-panes poll (15 s) because this one is about
 * REACTION: an agent finishing is the moment the user is waiting for, and a
 * quarter-minute of silence after it is a quarter-minute of them still
 * watching the wall. The backend's own sweep runs every 2 s, so this is the
 * slower half of the pair either way.
 */
export const POLL_MS = 5_000;

/** How wide the panel gets, and the margin it keeps from the window edge. */
const PANEL_WIDTH = 400;
const VIEWPORT_MARGIN = 12;

const EMPTY: PaneNotificationsState = {
  enabled: true,
  unread: 0,
  notifications: [],
};

/** "3m ago" / "2h ago" — the same shape the pane recap card uses. */
export function ago(at: number, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round(now / 1000 - at));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86_400)}d ago`;
}

/**
 * How one kind reads and looks.
 *
 * The label is short and upper-cased in the list, because it is scanned rather
 * than read — the sentence that says what actually happened is the entry's own
 * `title`, which the backend writes so the CLI and this panel never drift.
 */
const KINDS: Record<
  PaneNotificationKind,
  { key: string; icon: typeof CheckCircle2; tone: string }
> = {
  completed: {
    key: "agentic_grid.notifications.kind_completed",
    icon: CheckCircle2,
    tone: "text-emerald-400",
  },
  needs_input: {
    key: "agentic_grid.notifications.kind_needs_input",
    icon: CircleHelp,
    tone: "text-amber-400",
  },
  exited: {
    key: "agentic_grid.notifications.kind_exited",
    icon: PowerOff,
    tone: "text-muted-foreground",
  },
  failed: {
    key: "agentic_grid.notifications.kind_failed",
    icon: AlertTriangle,
    tone: "text-destructive",
  },
};

interface Anchor {
  top: number;
  right: number;
  width: number;
  maxHeight: number;
}

/** Where the panel goes, measured off the bell it hangs from. */
function anchorTo(node: HTMLElement | null): Anchor | null {
  if (!node || typeof window === "undefined") return null;
  const rect = node.getBoundingClientRect();
  const width = Math.min(PANEL_WIDTH, window.innerWidth - VIEWPORT_MARGIN * 2);
  const top = rect.bottom + 8;
  return {
    top,
    // Right-aligned to the bell, then held inside the window — the bell sits at
    // the far end of the header, so a left-anchored panel would hang off it.
    right: Math.max(VIEWPORT_MARGIN, window.innerWidth - rect.right),
    width,
    maxHeight: Math.max(180, window.innerHeight - top - VIEWPORT_MARGIN),
  };
}

export interface PaneNotificationsProps {
  /**
   * Take the user to the pane an entry came from. The parent owns this because
   * it owns the grid: switching workspace, un-maximizing whatever was
   * maximized, and scrolling the pane into view are all its state.
   */
  onJump: (entry: PaneNotification) => void;
  /**
   * Is the section holding this control the one on screen? The workspace stays
   * mounted behind other sections (see MainView), and this poll runs for as
   * long as it is open. Defaults to true — the behaviour before it could hide.
   */
  onScreen?: boolean;
}

export function PaneNotifications({ onJump, onScreen = true }: PaneNotificationsProps) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [state, setState] = useState<PaneNotificationsState>(EMPTY);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const bellRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  /*
   * A counter, not a clock. It only exists to force a re-render on a slow beat
   * so "3m ago" does not sit at "3m ago" for an hour while the panel stays
   * open; the time itself is read at render, from `Date.now()`.
   *
   * It used to hold the timestamp, and that was the bug behind every entry
   * reading "0s ago" however old it was: the value was taken once when the
   * workspace mounted and refreshed only every 30 s while the panel was open,
   * so anything filed after the workspace opened was compared against a moment
   * BEFORE it happened. That is a negative age, which the clamp in `ago` turns
   * into zero. A counter cannot go stale, because it is never used as a time.
   */
  const [, setTick] = useState(0);

  const refresh = useCallback(async (): Promise<PaneNotificationsState> => {
    try {
      const next = await fetchPaneNotifications();
      setState(next);
      return next;
    } catch {
      // A closed workspace, a backend still warming, a dropped request. The
      // bell is additive — showing the last known count beats an error toast
      // every few seconds for something nobody asked for.
      return EMPTY;
    }
  }, []);

  /*
   * Poll only while somebody could see the count: not in another section
   * (`onScreen`), not behind another window (`documentVisible`). Binding the
   * effect to that answer rather than checking inside the tick is also the
   * catch-up — coming back rebuilds the effect, and a rebuilt effect reads.
   */
  const documentVisible = useDocumentVisible();
  const polling = onScreen && documentVisible;
  useEffect(() => {
    if (!polling) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh, polling]);

  useEffect(() => {
    if (!open) return;
    // Every 15 s rather than 30: under a minute the panel counts in seconds,
    // and a seconds display that moves in half-minute jumps reads as frozen —
    // which is exactly what the bug above looked like.
    const timer = window.setInterval(() => setTick((n) => n + 1), 15_000);
    return () => window.clearInterval(timer);
  }, [open]);

  // Measured before paint, so the panel never appears in the wrong place for a
  // frame and then jumps.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => setAnchor(anchorTo(bellRef.current));
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  // Click anywhere else, or Escape, closes it. A panel that can only be closed
  // by its own X is a panel in the way of the grid it is about.
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (panelRef.current?.contains(target ?? null)) return;
      if (bellRef.current?.contains(target ?? null)) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next) return;
    // Read on open, so the list is never the poll's stale copy.
    const fresh = await refresh();
    if (fresh.unread === 0) return;
    try {
      await markPaneNotificationsRead();
      setState((current) => ({
        ...current,
        unread: 0,
        notifications: current.notifications.map((n) => ({ ...n, read: true })),
      }));
    } catch {
      // The badge stays up and clears on the next open. Nothing is lost, and a
      // toast for a failed bookkeeping call would be noise about noise.
    }
  };

  const discard = async (id?: string) => {
    // Cleared on screen first: the user pressed a delete button, and that
    // should not wait for a round-trip. A failed call is corrected by the very
    // next poll, five seconds away.
    setState((current) => ({
      ...current,
      notifications: id
        ? current.notifications.filter((n) => n.id !== id)
        : [],
      unread: id
        ? current.notifications.filter((n) => n.id !== id && !n.read).length
        : 0,
    }));
    try {
      await clearPaneNotifications(id);
    } catch (e) {
      pushToast("error", (e as Error).message);
      void refresh();
    }
  };

  const jump = (entry: PaneNotification) => {
    setOpen(false);
    onJump(entry);
  };

  const unread = state.unread;
  const entries = state.notifications;

  return (
    <>
      <button
        ref={bellRef}
        type="button"
        data-testid="pane-notifications-bell"
        aria-label={t("agentic_grid.notifications.title")}
        aria-expanded={open}
        title={
          unread > 0
            ? t("agentic_grid.notifications.hint_unread").replace("{0}", String(unread))
            : t("agentic_grid.notifications.hint")
        }
        onClick={() => void toggle()}
        className={cn(
          // Quiet glyph at rest, loud only while something actually waits —
          // the toolbar's shared rule (see TOOLBAR_BTN in AgenticGrid).
          "relative flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors",
          unread > 0
            ? "bg-primary/15 text-primary hover:bg-primary/25"
            : "text-muted-foreground hover:bg-secondary hover:text-foreground",
        )}
      >
        <Bell className={cn("h-4 w-4", unread > 0 && "animate-[pulse_2s_ease-in-out_2]")} />
        {unread > 0 && (
          <span
            data-testid="pane-notifications-count"
            className="absolute -right-1.5 -top-1.5 min-w-[1rem] rounded-full bg-primary px-1 text-center font-mono text-[10px] font-semibold leading-4 text-primary-foreground"
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open &&
        anchor &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={panelRef}
            data-testid="pane-notifications-panel"
            role="dialog"
            aria-label={t("agentic_grid.notifications.title")}
            style={{
              position: "fixed",
              top: anchor.top,
              right: anchor.right,
              width: anchor.width,
              maxHeight: anchor.maxHeight,
            }}
            className="z-[60] flex flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl"
          >
            <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
              <Bell className="h-4 w-4 shrink-0 text-muted-foreground" />
              <h2 className="min-w-0 flex-1 truncate font-display text-sm font-semibold">
                {t("agentic_grid.notifications.title")}
              </h2>
              <button
                type="button"
                data-testid="pane-notifications-clear"
                aria-label={t("agentic_grid.notifications.clear_all")}
                title={t("agentic_grid.notifications.clear_all")}
                disabled={entries.length === 0}
                onClick={() => void discard()}
                className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-30"
              >
                <Trash2 className="h-4 w-4" />
              </button>
              <button
                type="button"
                aria-label={t("agentic_grid.notifications.close")}
                onClick={() => setOpen(false)}
                className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
              {entries.length === 0 ? (
                <p
                  data-testid="pane-notifications-empty"
                  className="px-4 py-6 text-xs leading-relaxed text-muted-foreground"
                >
                  {state.enabled
                    ? t("agentic_grid.notifications.empty")
                    : t("agentic_grid.notifications.disabled")}
                </p>
              ) : (
                <ul className="divide-y divide-border/60">
                  {entries.map((entry) => (
                    <Row
                      key={entry.id}
                      entry={entry}
                      // Read here, at render, so it is the time the user is
                      // looking at the list rather than the time something
                      // last happened to schedule a render.
                      now={Date.now()}
                      onJump={() => jump(entry)}
                      onDiscard={() => void discard(entry.id)}
                    />
                  ))}
                </ul>
              )}
            </div>

            {!state.enabled && entries.length > 0 && (
              <p className="shrink-0 border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
                {t("agentic_grid.notifications.disabled")}
              </p>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}

function Row({
  entry,
  now,
  onJump,
  onDiscard,
}: {
  entry: PaneNotification;
  now: number;
  onJump: () => void;
  onDiscard: () => void;
}) {
  const t = useT();
  const kind = KINDS[entry.kind] ?? KINDS.completed;
  const Icon = kind.icon;

  return (
    <li
      data-testid="pane-notification"
      data-kind={entry.kind}
      className={cn("group px-4 py-3", !entry.read && "bg-primary/[0.06]")}
    >
      <div className="flex items-start gap-3">
        <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", kind.tone)} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate font-semibold text-foreground">{entry.pane}</span>
            <span className="truncate text-[11px] uppercase tracking-wide text-muted-foreground">
              {t(kind.key)}
            </span>
            <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">
              {ago(entry.created_at, now)}
            </span>
          </div>
          {/* What actually happened, in the backend's own words — and the
              workspace it happened in, because the list spans every open tab
              and a call-sign counts from T1 in each of them. */}
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {entry.display_name}
            {entry.workspace ? ` · ${entry.workspace}` : ""}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-foreground/80">{entry.title}</p>
          {entry.detail && (
            <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
              {t("agentic_grid.notifications.last_task").replace("{0}", entry.detail)}
            </p>
          )}
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              data-testid="pane-notification-jump"
              onClick={onJump}
              className="rounded-md border border-primary/50 bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/20"
            >
              {t("agentic_grid.notifications.jump")}
            </button>
            <button
              type="button"
              data-testid="pane-notification-discard"
              aria-label={t("agentic_grid.notifications.discard")}
              title={t("agentic_grid.notifications.discard")}
              onClick={onDiscard}
              className="rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </li>
  );
}

export default PaneNotifications;
