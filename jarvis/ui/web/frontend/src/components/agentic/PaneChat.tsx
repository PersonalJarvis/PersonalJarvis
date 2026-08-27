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
import { useEffect, useMemo } from "react";
import { CircleAlert, MessageSquare, RefreshCw, SquareTerminal } from "lucide-react";

import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { ChatStage } from "@/components/home/ChatStage";
import { createPaneChatStore, type PaneChatStoreHook } from "@/store/paneChat";
import type { PaneActivity } from "@/lib/agenticIdeApi";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

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
  /** The workspace folder — the composer's chip and the empty page's headline. */
  folder: string;
  /** The grid's own reading of the pane: working, waiting, asking… */
  activity: PaneActivity;
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
  folder,
  activity,
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
        {/* The pane's title where the sidebar row showed it too, so the header
            and the row that opened it agree on what this conversation is; the
            CLI's name steps aside to the right, the logo-sized fact it is. */}
        {title.trim() ? (
          <>
            <span
              className="min-w-0 flex-1 truncate text-sm text-foreground"
              data-testid="pane-chat-title"
              title={title}
            >
              {title}
            </span>
            <span className="max-w-[10rem] shrink-0 truncate text-xs text-muted-foreground">
              {agentLabel}
            </span>
          </>
        ) : (
          <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">{agentLabel}</span>
        )}
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
