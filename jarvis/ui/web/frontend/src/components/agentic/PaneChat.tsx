/**
 * A terminal pane read as a chat — what the Agentic IDE's chat stage shows.
 *
 * The pane behind this is a live coding CLI in a PTY, and a TUI is a picture:
 * it cannot be read as a conversation off the screen. What CAN be read is the
 * record the CLI keeps for itself, which the backend serves in the agent
 * chat's own event vocabulary (`GET /terminals/{name}/timeline`). Those events
 * fold through the same reducer and draw through the same `AgentTimeline` as
 * the front page's chat — the thinking where it happened (or how long it took
 * where the vendor redacts it), each tool call with its result, the answer as
 * Markdown, the turn's state and its output tokens. One renderer, so the two
 * chats cannot drift apart (maintainer, 2026-08-26: the terminals should turn
 * into the chat view we built, with one click).
 *
 * The terminal is never gone. Questions and permission prompts are asked in
 * the TUI and answered there, so the stage keeps one button back to the pane
 * itself, and says so out loud the moment the pane's activity reads "asking".
 * The composer types what is written into that pane, verbatim — this is a
 * conversation with the agent, not a brief Jarvis writes on the user's behalf.
 *
 * Polling, not a socket: the record is a file the CLI appends to, and a poll
 * every couple of seconds while the agent works (slower once it stops) is the
 * honest cadence for that. The backend says which of the two it is (`live`).
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import {
  ArrowUp,
  CircleAlert,
  MessageSquare,
  RefreshCw,
  SquareTerminal,
} from "lucide-react";

import { AgentTimeline } from "@/components/agentchat/AgentTimeline";
import {
  EMPTY_TIMELINE,
  reduceEvents,
  type Timeline,
} from "@/components/agentchat/reduce";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  fetchTerminalTimeline,
  promptTerminal,
  type PaneActivity,
  type TerminalTimelineResponse,
} from "@/lib/agenticIdeApi";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** Poll cadence while the agent is working — a tool result every couple of seconds is what it produces. */
export const POLL_WORKING_MS = 2000;
/** Poll cadence once it has stopped — only a new message from elsewhere could change the file. */
export const POLL_IDLE_MS = 6000;

/** How soon the next read follows, from what the last one said about the pane. */
export function pollIntervalFor(live: boolean, activity: PaneActivity): number {
  return live || activity === "working" || activity === "starting"
    ? POLL_WORKING_MS
    : POLL_IDLE_MS;
}

/**
 * A cheap fingerprint of an event list, so an unchanged poll re-renders nothing.
 *
 * The file only ever grows, and a record is written whole (a finished block,
 * a tool result), so length plus the last event's identity plus the text
 * volume tells the two lists apart — without folding sixty turns to find out
 * that nothing happened.
 */
export function eventsSignature(events: AgentChatEvent[], live: boolean): string {
  const last = events[events.length - 1];
  let chars = 0;
  for (const ev of events) {
    const p = ev.payload;
    if (typeof p.text === "string") chars += p.text.length;
    if (typeof p.output === "string") chars += p.output.length;
  }
  return `${events.length}:${last?.seq ?? 0}:${last?.ts_ms ?? 0}:${last?.kind ?? ""}:${chars}:${live ? 1 : 0}`;
}

export interface PaneTimelineState {
  timeline: Timeline;
  /** Null until the first answer; false is a settled "this CLI keeps no record". */
  readable: boolean | null;
  available: boolean;
  live: boolean;
  activity: PaneActivity;
  /** True until the first answer for THIS pane has arrived. */
  loading: boolean;
  error: string;
  /** Read again now — the composer calls it right after a send. */
  reload: () => void;
}

/**
 * Keep one pane's timeline current.
 *
 * Re-armed per pane: switching the stage to another terminal starts from an
 * empty column rather than showing the previous pane's conversation under
 * the new pane's name for a poll interval.
 */
export function usePaneTimeline(
  terminal: string,
  workspaceId: string | undefined,
  enabled: boolean,
): PaneTimelineState {
  const [timeline, setTimeline] = useState<Timeline>(EMPTY_TIMELINE);
  const [readable, setReadable] = useState<boolean | null>(null);
  const [available, setAvailable] = useState(false);
  const [live, setLive] = useState(false);
  const [activity, setActivity] = useState<PaneActivity>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);
  const signatureRef = useRef("");

  useEffect(() => {
    // A new pane: forget the old one's answer before the first read lands.
    setTimeline(EMPTY_TIMELINE);
    setReadable(null);
    setAvailable(false);
    setLive(false);
    setActivity("");
    setLoading(true);
    setError("");
    signatureRef.current = "";
  }, [terminal, workspaceId]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: number | null = null;

    const apply = (res: TerminalTimelineResponse) => {
      setReadable(res.readable);
      setAvailable(res.available);
      setLive(res.live);
      setActivity(res.activity);
      setError("");
      setLoading(false);
      const signature = eventsSignature(res.events, res.live);
      if (signature !== signatureRef.current) {
        signatureRef.current = signature;
        setTimeline(reduceEvents(EMPTY_TIMELINE, res.events));
      }
      return pollIntervalFor(res.live, res.activity);
    };

    const tick = async () => {
      let next = POLL_IDLE_MS;
      try {
        const res = await fetchTerminalTimeline(terminal, workspaceId);
        if (cancelled) return;
        next = apply(res);
      } catch (reason: unknown) {
        if (cancelled) return;
        // Keep what is on screen: a column that empties itself on one failed
        // poll would read as "the conversation is gone".
        setError(reason instanceof Error ? reason.message : String(reason));
        setLoading(false);
      }
      if (!cancelled) timer = window.setTimeout(() => void tick(), next);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [enabled, terminal, workspaceId, reloadToken]);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);
  return { timeline, readable, available, live, activity, loading, error, reload };
}

export interface PaneChatProps {
  /** The pane's call-sign — T1, or a name the user gave it. */
  terminal: string;
  workspaceId: string;
  /** What the CLI is called — "Claude Code", "Codex" — the turns' byline. */
  agentLabel: string;
  /** The grid's own reading of the pane: working, waiting, asking… */
  activity: PaneActivity;
  /** Can this pane be typed into at all? A plain shell cannot. */
  promptable: boolean;
  /** Back to the terminal itself — the question it is asking lives there. */
  onShowTerminal: () => void;
}

export function PaneChat({
  terminal,
  workspaceId,
  agentLabel,
  activity,
  promptable,
  onShowTerminal,
}: PaneChatProps) {
  const t = useT();
  const state = usePaneTimeline(terminal, workspaceId, true);
  const items = state.timeline.items;
  const providerLabel = useCallback(() => agentLabel, [agentLabel]);
  // Approvals never come out of a transcript — the CLI asks in its own TUI.
  const noDecision = useCallback(() => undefined, []);
  // The grid's reading is fresher than the poll's (it has the socket); the
  // poll's is the fallback for a pane the grid has not read yet.
  const paneActivity = activity || state.activity;
  const asking = paneActivity === "asking";

  return (
    <div
      className="absolute inset-0 z-20 flex min-h-0 flex-col rounded-lg bg-background"
      data-testid={`pane-chat-${terminal}`}
      data-pane={terminal}
      data-activity={paneActivity}
    >
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-4">
        <MessageSquare className="h-4 w-4 shrink-0 text-primary" aria-hidden />
        <span className="font-mono text-xs font-semibold text-foreground">{terminal}</span>
        <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">{agentLabel}</span>
        <button
          type="button"
          onClick={state.reload}
          title={t("agentic_grid.pane_chat.reload")}
          aria-label={t("agentic_grid.pane_chat.reload")}
          data-testid="pane-chat-reload"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <RefreshCw className={cn("h-4 w-4", state.loading && "animate-spin")} aria-hidden />
        </button>
        <button
          type="button"
          onClick={onShowTerminal}
          data-testid="pane-chat-show-terminal"
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-lg border border-border px-2.5 text-xs font-medium transition-colors",
            asking
              ? "border-primary/50 bg-primary/10 text-foreground hover:bg-primary/15"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <SquareTerminal className="h-3.5 w-3.5" aria-hidden />
          {t("agentic_grid.pane_chat.show_terminal")}
        </button>
      </header>

      {asking && (
        <div
          role="status"
          data-testid="pane-chat-asking"
          className="flex shrink-0 items-start gap-2 border-b border-primary/30 bg-primary/[0.07] px-4 py-2 text-xs"
        >
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
          <div className="min-w-0">
            <div className="font-medium text-foreground">{t("agentic_grid.pane_chat.asking_title")}</div>
            <div className="text-muted-foreground">{t("agentic_grid.pane_chat.asking_detail")}</div>
          </div>
        </div>
      )}

      <PaneChatBody
        state={state}
        terminal={terminal}
        agentLabel={agentLabel}
        providerLabel={providerLabel}
        onDecide={noDecision}
        onShowTerminal={onShowTerminal}
      />

      {state.readable !== false && (
        <div className="w-full max-w-[760px] self-center px-6 pb-5 pt-2">
          <PaneComposer
            terminal={terminal}
            promptable={promptable}
            onSent={state.reload}
            hasContent={items.length > 0}
          />
        </div>
      )}
    </div>
  );
}

function PaneChatBody({
  state,
  terminal,
  agentLabel,
  providerLabel,
  onDecide,
  onShowTerminal,
}: {
  state: PaneTimelineState;
  terminal: string;
  agentLabel: string;
  providerLabel: (id: string) => string;
  onDecide: () => void;
  onShowTerminal: () => void;
}) {
  const t = useT();
  const items = state.timeline.items;
  const rootRef = useRef<HTMLDivElement | null>(null);
  // Follow the conversation unless the person scrolled up to read: the
  // column sticks to its end while it is at the end, and stays put otherwise.
  const stickRef = useRef(true);
  const lastId = items[items.length - 1]?.id ?? null;
  const lastCount = useMemo(() => {
    const last = items[items.length - 1];
    return last && last.type === "turn" ? last.blocks.length : 0;
  }, [items]);

  useLayoutEffect(() => {
    const viewport = viewportOf(rootRef.current);
    if (!viewport || !stickRef.current) return;
    viewport.scrollTop = viewport.scrollHeight;
  }, [lastId, lastCount, terminal]);

  useEffect(() => {
    stickRef.current = true;
  }, [terminal]);

  const onScroll = useCallback(() => {
    const viewport = viewportOf(rootRef.current);
    if (!viewport) return;
    const gap = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    stickRef.current = gap < 80;
  }, []);

  if (state.readable === false) {
    return (
      <Empty
        icon={<SquareTerminal className="h-5 w-5 text-muted-foreground" aria-hidden />}
        title={t("agentic_grid.pane_chat.not_readable_title")}
        detail={t("agentic_grid.pane_chat.not_readable_detail")}
        testId="pane-chat-not-readable"
      >
        <button
          type="button"
          onClick={onShowTerminal}
          className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg border border-border px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted"
        >
          <SquareTerminal className="h-3.5 w-3.5" aria-hidden />
          {t("agentic_grid.pane_chat.show_terminal")}
        </button>
      </Empty>
    );
  }

  if (state.loading) {
    return (
      <div className="mx-auto w-full max-w-[760px] flex-1 space-y-4 overflow-hidden px-6 py-8" data-testid="pane-chat-loading">
        {["w-2/3", "w-full", "w-5/6", "w-1/2", "w-full", "w-4/5"].map((width, row) => (
          <div key={row} className={cn("h-3 animate-pulse rounded bg-muted/70", width)} />
        ))}
      </div>
    );
  }

  if (state.error && items.length === 0) {
    return (
      <Empty
        icon={<CircleAlert className="h-5 w-5 text-destructive" aria-hidden />}
        title={t("agentic_grid.pane_chat.load_failed")}
        detail={state.error}
        testId="pane-chat-error"
      >
        <button
          type="button"
          onClick={state.reload}
          className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg border border-border px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          {t("agentic_grid.pane_chat.retry")}
        </button>
      </Empty>
    );
  }

  if (!state.available || items.length === 0) {
    return (
      <Empty
        icon={<MessageSquare className="h-5 w-5 text-muted-foreground" aria-hidden />}
        title={t("agentic_grid.pane_chat.waiting_title")}
        detail={t("agentic_grid.pane_chat.waiting_detail")}
        testId="pane-chat-waiting"
      />
    );
  }

  return (
    <ScrollArea ref={rootRef} className="min-h-0 w-full flex-1" onScrollCapture={onScroll}>
      <div
        className="relative mx-auto flex w-full max-w-[760px] flex-col gap-5 px-6 pb-6 pt-8"
        data-testid="pane-chat-timeline"
      >
        <AgentTimeline
          items={items}
          assistantName={agentLabel}
          providerLabel={providerLabel}
          onDecide={onDecide}
        />
      </div>
    </ScrollArea>
  );
}

function Empty({
  icon,
  title,
  detail,
  testId,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
  testId: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className="flex flex-1 flex-col items-center justify-center px-6 text-center"
      data-testid={testId}
    >
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-muted/30">
        {icon}
      </div>
      <p className="text-sm font-medium text-foreground">{title}</p>
      <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">{detail}</p>
      {children}
    </div>
  );
}

/**
 * The stage's composer: what is written is typed into the pane, as written.
 *
 * The same card the agent chat's composer draws — border, radius, shadow, the
 * arrow — minus the picks that make no sense for a PTY (provider, model,
 * effort, permission mode: the CLI in the pane decided those when it
 * started). Enter sends, Shift+Enter breaks the line, like every chat.
 */
function PaneComposer({
  terminal,
  promptable,
  onSent,
  hasContent,
}: {
  terminal: string;
  promptable: boolean;
  onSent: () => void;
  hasContent: boolean;
}) {
  const t = useT();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "error" | "warning"; text: string } | null>(null);
  const canSend = promptable && !busy && value.trim().length > 0;

  const send = useCallback(async () => {
    const text = value.trim();
    if (!text || busy || !promptable) return;
    setBusy(true);
    setNotice(null);
    try {
      // Verbatim: `compose` off. A brief written FOR the person belongs to the
      // voice path; here the person is talking to the agent themselves.
      const result = await promptTerminal(terminal, text, { compose: false });
      setValue("");
      if (result.submitted === false) {
        setNotice({
          tone: "warning",
          text: t("agentic_grid.pane_chat.not_taken")
            .replace("{0}", terminal)
            .replace("{1}", result.detail ?? ""),
        });
      }
      onSent();
    } catch (reason: unknown) {
      setNotice({
        tone: "error",
        text: t("agentic_grid.pane_chat.send_failed")
          .replace("{0}", terminal)
          .replace("{1}", reason instanceof Error ? reason.message : String(reason)),
      });
    } finally {
      setBusy(false);
    }
  }, [value, busy, promptable, terminal, onSent, t]);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div
      data-testid="pane-chat-composer"
      className={cn(
        "relative flex flex-col gap-2 rounded-2xl border border-border bg-card p-3 shadow-[0_1px_2px_rgb(var(--scrim-rgb)/0.05),0_8px_24px_rgb(var(--scrim-rgb)/0.06)] transition-[border-color,box-shadow]",
        "focus-within:border-primary/40",
      )}
    >
      <textarea
        data-jarvis-chat-input=""
        autoFocus={!hasContent}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={
          promptable
            ? t("agentic_grid.pane_chat.placeholder").replace("{0}", terminal)
            : t("agentic_grid.pane_chat.not_promptable")
        }
        disabled={!promptable}
        rows={2}
        className="max-h-48 w-full resize-none bg-transparent px-1 py-1 text-[15px] leading-relaxed text-foreground placeholder:text-muted-foreground focus-visible:outline-none disabled:opacity-50"
      />
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate px-1 text-xs text-muted-foreground" role="status">
          {busy
            ? t("agentic_grid.pane_chat.sending")
            : notice && (
                <span
                  data-testid={`pane-chat-notice-${notice.tone}`}
                  className={cn(notice.tone === "error" ? "text-destructive" : "text-foreground")}
                >
                  {notice.text}
                </span>
              )}
        </span>
        <button
          type="button"
          onClick={() => void send()}
          disabled={!canSend}
          aria-label={t("agentic_grid.pane_chat.send").replace("{0}", terminal)}
          title={t("agentic_grid.pane_chat.send").replace("{0}", terminal)}
          data-testid="pane-chat-send"
          className={cn(
            "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors",
            canSend
              ? "bg-primary text-primary-foreground hover:bg-primary/90"
              : "bg-secondary text-muted-foreground/60",
          )}
        >
          <ArrowUp className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

/** Radix renders the scrolling element as the viewport inside our ScrollArea root. */
function viewportOf(root: HTMLElement | null): HTMLElement | null {
  if (!root) return null;
  return (root.querySelector("[data-radix-scroll-area-viewport]") as HTMLElement | null) ?? root;
}
