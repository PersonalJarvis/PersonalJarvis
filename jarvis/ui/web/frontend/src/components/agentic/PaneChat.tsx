/**
 * A terminal pane read as a chat — what the Agentic IDE's chat stage shows.
 *
 * The pane behind this is a live coding CLI in a PTY, and a TUI is a picture:
 * it cannot be read as a conversation off the screen. What CAN be read is the
 * record the CLI keeps for itself, which the backend serves in the agent
 * chat's own event vocabulary (`GET /terminals/{name}/timeline`). Those events
 * fold through the same reducer and draw through the same `ChatStage` as the
 * front page's chat — the thinking where it happened, each tool call with its
 * result, the answer as Markdown, the composer with its pills — because that
 * chat interface is the one the maintainer built for this and the one they
 * asked for here (2026-08-26: the old interface, not a plain box). The
 * pane's answers behind every field live in `store/paneChat.ts`.
 *
 * The terminal is never gone. Questions and permission prompts are asked in
 * the TUI and answered there, so the stage keeps one button back to the pane
 * itself, and says so out loud the moment the pane's activity reads "asking".
 */
import { useEffect, useMemo, useState } from "react";
import { CircleAlert, MessageSquare, RefreshCw, SquareTerminal } from "lucide-react";

import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import {
  PaneActivityPill,
  paneActivityState,
  type PaneActivityState,
} from "@/components/agentic/PaneActivityPill";
import { PaneRecap } from "@/components/agentic/PaneRecap";
import type { PaneRecapActions, PaneRecapMeta } from "@/components/agentic/AgenticTerminal";
import { ChatStage } from "@/components/home/ChatStage";
import { useThemeValue } from "@/hooks/useTheme";
import { createPaneChatStore, type PaneChatStoreHook } from "@/store/paneChat";
import type { PaneActivity } from "@/lib/agenticIdeApi";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * How often the header's "for 3 min" re-reads the clock.
 *
 * Coarse on purpose: the label is rounded to seconds under a minute and to
 * minutes above, so a faster tick would redraw the header for a number that
 * mostly does not change. Ten seconds keeps "for 40s" honest to within a
 * quarter of what it says.
 */
const CLOCK_TICK_MS = 10_000;

/** A clock that ticks while something is worth timing, and stands still otherwise. */
function useClock(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => window.clearInterval(id);
  }, [ticking]);
  return now;
}

/**
 * The header's own word for a pane's state, and how it is coloured.
 *
 * The pill beside it carries the shape (spinner, dot, ring, beacon); this is
 * the text a header has room for and a list row does not. Two states are
 * lifted into the foreground ink — a working pane and one holding a
 * question — because those are the two the user opened the chat to find out
 * about; the rest sit in muted ink, which is what "nothing to report" looks
 * like. The i18n key is the state itself, so a state added to the pill fails
 * to compile here until every locale has a word for it.
 */
const STATE_INK: Record<PaneActivityState, string> = {
  working: "text-foreground",
  starting: "text-muted-foreground",
  asking: "text-foreground",
  done: "text-foreground",
  idle: "text-muted-foreground",
  live: "text-muted-foreground",
  exited: "text-muted-foreground",
  failed: "text-destructive",
  error: "text-destructive",
};

/**
 * "for 40s", "for 3 min", "for 2 h" — how long the pane has been in this state.
 *
 * A duration rather than a clock time, as on the pill's tooltip: the reader
 * wants to know how long they have been waiting, not do the subtraction.
 */
function elapsedLabel(
  since: number,
  now: number,
  t: (key: string) => string,
): string {
  const seconds = Math.max(0, Math.round(now / 1000 - since));
  if (seconds < 60) return fill(t("agentic_grid.pane_chat.for_seconds"), { n: seconds });
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return fill(t("agentic_grid.pane_chat.for_minutes"), { n: minutes });
  return fill(t("agentic_grid.pane_chat.for_hours"), { n: Math.round(minutes / 60) });
}

export interface PaneChatProps {
  /** The pane's call-sign — T1, or a name the user gave it. */
  terminal: string;
  workspaceId: string;
  /** The pane's lifetime id; the chat's "session id". */
  historyId: string;
  agent: string;
  /** What the CLI is called — "Claude Code", "Codex". */
  agentLabel: string;
  /**
   * What the pane is about — its recap, the sentence the grid draws in the
   * pane's own header. Empty until a header has described the pane; the
   * CLI's name then carries the header alone, as it always did.
   */
  title?: string;
  /**
   * The longer form behind the title — the paragraph the recap card shows.
   * The header line is cut to a grid pane's width on the backend (48
   * characters, hence the "…" a long prompt ends in); this is where the
   * rest of the sentence lives, and the card is the place it can be read.
   */
  titleDetail?: string;
  /** Who wrote the recap and why it is the one on screen — the card's footer. */
  recapMeta?: PaneRecapMeta;
  /** Rewrite, reset or refresh the recap; absent leaves the card read-only. */
  recapActions?: PaneRecapActions;
  /** The workspace folder — the composer's chip and the empty page's headline. */
  folder: string;
  /** The grid's own reading of the pane: working, waiting, asking… */
  activity: PaneActivity;
  /** When the pane entered that state (epoch seconds); 0 when unknown. */
  activitySince?: number;
  /** Has anything ever been asked of this pane? Separates "done" from "idle". */
  worked?: boolean;
  /** The pane's process status, as the grid reads it off the socket. */
  status?: string;
  /** Back to the terminal itself — the question it is asking lives there. */
  onShowTerminal: () => void;
}

export function PaneChat({
  terminal,
  workspaceId,
  historyId,
  agent,
  agentLabel,
  title = "",
  titleDetail = "",
  recapMeta,
  recapActions,
  folder,
  activity,
  activitySince = 0,
  worked = false,
  status = "live",
  onShowTerminal,
}: PaneChatProps) {
  const t = useT();
  // One store per staged pane. The stage is keyed by workspace and pane, so a
  // stage change is a remount and a fresh store — never the last pane's
  // conversation under the new pane's name.
  const store: PaneChatStoreHook = useMemo(
    () =>
      createPaneChatStore({
        terminal,
        workspaceId,
        historyId,
        agent,
        displayName: agentLabel,
        folder,
      }),
    [terminal, workspaceId, historyId, agent, agentLabel, folder],
  );
  useEffect(() => {
    store.getState().start();
    return () => store.getState().stop();
  }, [store]);

  const pane = store((s) => s.pane);
  const reload = store((s) => s.reload);
  // The grid's reading is fresher than the poll's (it has the socket); the
  // poll's is the fallback for a pane the grid has not read yet.
  const paneActivity = activity || pane.activity;
  const asking = paneActivity === "asking";
  /*
   * The one question this stage is opened to answer: is the agent still at
   * it, or is it done? The timeline below says so too, but only at the
   * bottom of a long transcript and only once it has loaded; the header says
   * it at a glance, in words, for the whole time the stage is open. `worked`
   * is what keeps a pane that was never asked anything from claiming a
   * finished job. The clock ticks only while the duration is worth watching
   * — a working pane, or one that has stopped and is now waiting on you.
   */
  const state = paneActivityState(status, paneActivity, worked);
  const timed = activitySince > 0 && state !== "live" && state !== "starting";
  const now = useClock(timed);
  const elapsed = timed ? elapsedLabel(activitySince, now, t) : "";
  // The recap line dresses in the pane brand's ink, keyed to the ground it
  // sits on. In the grid that is the terminal's own appearance; here the
  // header sits on the app's background, so the app's theme is the ground.
  const theme = useThemeValue();
  const titled = title.trim().length > 0;

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
        {/* The pane's title, drawn by the same component the grid draws it
            with in the pane's own header — so the stage and the terminal
            behind it are one pane named one way, and so the title IS the
            same thing here that it is there: a line that opens into a card
            with the whole sentence, the longer recap, who wrote it and why,
            and the two ways to change it. A plain span said less than the
            grid did about the very session the stage was opened to read
            (maintainer report 2026-08-27). Without a title the line names
            the CLI, as the grid's header does; with one, the CLI's name
            steps aside to the right, the logo-sized fact it is.

            The line hugs its text rather than filling the header: the
            recap's chevron trails the sentence, and in a grid pane's
            narrow header that IS the far edge — across a window-wide
            header it would sit half a screen from the words it opens. So
            the wrapper is content-sized (still able to shrink and clip
            when the header is crowded) and a spacer after the CLI's name
            pushes the state and the buttons to the right instead. */}
        <span className="flex min-w-0 items-center" data-testid="pane-chat-title">
          <PaneRecap
            name={terminal}
            displayName={agentLabel}
            recap={title}
            detail={titleDetail}
            source={recapMeta?.source}
            reason={recapMeta?.reason}
            writer={recapMeta?.writer}
            note={recapMeta?.note}
            generatedAt={recapMeta?.generatedAt}
            light={theme === "light"}
            onSave={recapActions?.onSave}
            onClear={recapActions?.onClear}
            onRefresh={recapActions?.onRefresh}
          />
        </span>
        {titled && (
          <span
            className="max-w-[10rem] shrink-0 truncate text-xs text-muted-foreground"
            data-testid="pane-chat-agent"
          >
            {agentLabel}
          </span>
        )}
        <span className="flex-1" aria-hidden />
        {/* The state, spelled out: the pill's shape and a word for it, with
            how long it has been so. A chip rather than loose text so it reads
            as ONE fact beside the title, and never wraps into it. */}
        <span
          data-testid="pane-chat-state"
          data-state={state}
          className={cn(
            "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-border bg-card/60 pl-2 pr-2.5 text-xs font-medium transition-colors",
            STATE_INK[state],
          )}
        >
          <PaneActivityPill
            status={status}
            activity={paneActivity}
            since={activitySince}
            worked={worked}
          />
          <span>{t(`agentic_grid.pane_chat.state.${state}`)}</span>
          {elapsed && (
            <span className="font-normal text-muted-foreground" data-testid="pane-chat-state-for">
              {elapsed}
            </span>
          )}
        </span>
        {pane.pollError && (
          <span
            role="status"
            data-testid="pane-chat-poll-error"
            title={pane.pollError}
            className="max-w-[18rem] truncate text-xs text-destructive"
          >
            {t("agentic_grid.pane_chat.load_failed")}
          </span>
        )}
        <button
          type="button"
          onClick={reload}
          title={t("agentic_grid.pane_chat.reload")}
          aria-label={t("agentic_grid.pane_chat.reload")}
          data-testid="pane-chat-reload"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <RefreshCw className={cn("h-4 w-4", pane.loading && "animate-spin")} aria-hidden />
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

      {pane.readable === false ? (
        <div
          className="flex flex-1 flex-col items-center justify-center px-6 text-center"
          data-testid="pane-chat-not-readable"
        >
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-muted/30">
            <SquareTerminal className="h-5 w-5 text-muted-foreground" aria-hidden />
          </div>
          <p className="text-sm font-medium text-foreground">
            {t("agentic_grid.pane_chat.not_readable_title")}
          </p>
          <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">
            {t("agentic_grid.pane_chat.not_readable_detail")}
          </p>
          <button
            type="button"
            onClick={onShowTerminal}
            className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg border border-border px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted"
          >
            <SquareTerminal className="h-3.5 w-3.5" aria-hidden />
            {t("agentic_grid.pane_chat.show_terminal")}
          </button>
        </div>
      ) : pane.loading ? (
        <div
          className="mx-auto w-full max-w-[760px] flex-1 space-y-4 overflow-hidden px-6 py-8"
          data-testid="pane-chat-loading"
        >
          {["w-2/3", "w-full", "w-5/6", "w-1/2", "w-full", "w-4/5"].map((width, row) => (
            <div key={row} className={cn("h-3 animate-pulse rounded bg-muted/70", width)} />
          ))}
        </div>
      ) : (
        // The front page's chat, on the pane's store: the same stage, the same
        // timeline, the same composer with its pills. An empty transcript is
        // the same empty page a fresh chat shows — the folder's name and the
        // composer in the middle — which is what "nothing said yet" looks like.
        <AgentChatStoreProvider store={store}>
          <ChatStage />
        </AgentChatStoreProvider>
      )}
    </div>
  );
}
