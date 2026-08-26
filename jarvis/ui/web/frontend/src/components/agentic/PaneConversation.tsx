/**
 * A pane's conversation, read from the CLI's own record — the shared half.
 *
 * The dialog opened from a pane's scroll rail is what reads it: a look at the
 * conversation of the pane you are already watching, with a real scrollbar.
 * The fetch, the three empty states and the turn rendering live here rather
 * than inside that dialog so a second reader can be added without either of
 * them drifting from the other.
 *
 * Why the transcript comes from the CLI's file rather than the pane's screen is
 * argued in `jarvis/agentic_ide/agent_transcript.py`: a TUI is a picture, and
 * anything that reconstructs a conversation from it guesses.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, BookOpenText, RefreshCw } from "lucide-react";

import { useT } from "@/i18n";
import {
  fetchTerminalConversation,
  type ConversationResponse,
  type ConversationTurn,
} from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";

export interface PaneConversationState {
  loading: boolean;
  error: string;
  conversation: ConversationResponse | null;
  /** Fetch again — the surfaces put this behind their own refresh button. */
  reload: () => void;
}

/**
 * Load one pane's conversation.
 *
 * `enabled` exists for the dialog, which is mounted while closed: fetching a
 * megabyte-tail transcript for a dialog nobody opened is a cost with nothing on
 * screen to show for it.
 */
export function usePaneConversation(
  terminal: string,
  workspaceId?: string,
  enabled = true,
): PaneConversationState {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [conversation, setConversation] = useState<ConversationResponse | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let current = true;
    setLoading(true);
    setError("");
    fetchTerminalConversation(terminal, workspaceId)
      .then((result) => {
        if (current) setConversation(result);
      })
      .catch((reason: unknown) => {
        if (current) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [enabled, reloadToken, terminal, workspaceId]);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);
  return { loading, error, conversation, reload };
}

/**
 * The conversation itself: skeleton, error, empty, or the turns.
 *
 * One component for all four because they are one question — "what is there to
 * read?" — and a surface that hand-rolled three of them would be the surface
 * that forgets one.
 */
export function PaneConversationBody({
  state,
  className,
  testId,
}: {
  state: PaneConversationState;
  className?: string;
  testId?: string;
}): JSX.Element {
  const t = useT();
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const { loading, error, conversation, reload } = state;
  const turns = conversation?.turns ?? [];
  const empty = !loading && !error && (!conversation || !conversation.available || turns.length === 0);

  useEffect(() => {
    // The newest exchange is what the user scrolled up FROM, so the view starts
    // at the bottom — exactly like the terminal it explains.
    if (loading || !conversation) return;
    const scroller = scrollerRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [loading, conversation]);

  if (loading) {
    return (
      <div className={cn("flex-1 space-y-4 overflow-hidden p-6", className)}>
        {["w-3/4", "w-full", "w-5/6", "w-2/3", "w-full", "w-4/5"].map((width, row) => (
          <div key={row} className={cn("h-3 animate-pulse rounded bg-muted/70", width)} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className={cn("flex flex-1 flex-col items-center justify-center px-6 text-center", className)}>
        <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
        <p className="text-sm font-medium text-foreground">
          {t("agentic_grid.conversation.load_failed")}
        </p>
        <p className="mt-1 max-w-md text-xs text-muted-foreground" role="alert">
          {error}
        </p>
        <button
          type="button"
          onClick={reload}
          className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg border border-border px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted active:translate-y-px"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          {t("agentic_grid.conversation.retry")}
        </button>
      </div>
    );
  }

  if (empty) {
    return (
      <div className={cn("flex flex-1 flex-col items-center justify-center px-6 text-center", className)}>
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-muted/30">
          <BookOpenText className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium text-foreground">
          {t("agentic_grid.conversation.empty_title")}
        </p>
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">
          {t("agentic_grid.conversation.empty_detail")}
        </p>
      </div>
    );
  }

  return (
    <div
      ref={scrollerRef}
      data-testid={testId ?? "pane-conversation-scroller"}
      className={cn("min-h-0 flex-1 overflow-y-auto scroll-pt-4 px-5 py-4 scrollbar-jarvis", className)}
    >
      {turns.map((turn, index) => (
        <TurnBlock key={index} turn={turn} />
      ))}
    </div>
  );
}

function TurnBlock({ turn }: { turn: ConversationTurn }): JSX.Element {
  const t = useT();
  const user = turn.role === "user";
  const tools = [...new Set(turn.steps.map((step) => step.tool))];
  const steps = t(
    turn.steps.length === 1
      ? "agentic_grid.conversation.steps_one"
      : "agentic_grid.conversation.steps_many",
  ).replace("{0}", String(turn.steps.length));
  return (
    <div className="mb-4 last:mb-0">
      <div
        className={cn(
          "mb-1 text-[10px] font-semibold uppercase tracking-[0.14em]",
          user ? "text-primary" : "text-muted-foreground",
        )}
      >
        {t(user ? "agentic_grid.conversation.you" : "agentic_grid.conversation.agent")}
      </div>
      {turn.text && (
        <div
          className={cn(
            "whitespace-pre-wrap break-words rounded-xl border px-3.5 py-2.5 text-[12.5px] leading-relaxed",
            user
              ? "border-primary/25 bg-primary/[0.07] text-foreground"
              : "border-border/60 bg-background/35 text-foreground/90",
          )}
        >
          {turn.text}
        </div>
      )}
      {turn.steps.length > 0 && (
        <div className="mt-1 truncate text-[10px] text-muted-foreground/80">
          {steps}
          {tools.length > 0 && <> · {tools.slice(0, 6).join(", ")}</>}
        </div>
      )}
    </div>
  );
}
