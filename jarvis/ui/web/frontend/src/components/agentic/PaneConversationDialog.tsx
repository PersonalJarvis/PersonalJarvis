/**
 * The pane's conversation history with a REAL scrollbar.
 *
 * A coding TUI owns its screen, and 2.1.226-era Claude Code even switches
 * buffer modes mid-session, so no rail over the live terminal can honestly
 * show "where am I in the history". This dialog can: it renders the
 * conversation the CLI itself recorded on disk (see terminal_conversation in
 * agentic_ide_routes.py), which Jarvis owns end-to-end — so the scrollbar
 * here is a normal, accurate browser scrollbar over real content, identical
 * for every provider. Opened from the pane scroll rail.
 */
import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertCircle, BookOpenText, RefreshCw, X } from "lucide-react";

import { useT } from "@/i18n";
import {
  fetchTerminalConversation,
  type ConversationResponse,
  type ConversationTurn,
} from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";

function fill(template: string, ...values: Array<string | number>): string {
  return values.reduce<string>(
    (text, value, index) => text.replace(`{${index}}`, String(value)),
    template,
  );
}

export function PaneConversationDialog({
  terminal,
  open,
  onOpenChange,
}: {
  terminal: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): JSX.Element {
  const t = useT();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [conversation, setConversation] = useState<ConversationResponse | null>(
    null,
  );
  const [reloadToken, setReloadToken] = useState(0);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    let current = true;
    setLoading(true);
    setError("");
    fetchTerminalConversation(terminal)
      .then((result) => {
        if (current) setConversation(result);
      })
      .catch((reason: unknown) => {
        if (current) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [open, reloadToken, terminal]);

  useEffect(() => {
    // The newest exchange is what the user scrolled up FROM, so the view
    // starts at the bottom — exactly like the terminal it explains.
    if (loading || !conversation) return;
    const scroller = scrollerRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [loading, conversation]);

  const turns = conversation?.turns ?? [];
  const empty =
    !loading && !error && (!conversation || !conversation.available || turns.length === 0);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid={`pane-conversation-dialog-${terminal}`}
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex h-[min(84dvh,52rem)] w-[min(880px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card shadow-[0_28px_90px_-24px_rgba(0,0,0,0.75)] outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-2">
                <BookOpenText className="h-4 w-4 text-primary" aria-hidden="true" />
                <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                  {fill(t("agentic_grid.conversation.title"), terminal)}
                </Dialog.Title>
              </div>
              <Dialog.Description className="text-xs leading-relaxed text-muted-foreground">
                {t("agentic_grid.conversation.description")}
              </Dialog.Description>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                aria-label={t("agentic_grid.conversation.refresh")}
                title={t("agentic_grid.conversation.refresh")}
                data-testid="pane-conversation-refresh"
                onClick={() => setReloadToken((token) => token + 1)}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:translate-y-px"
              >
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              </button>
              <Dialog.Close asChild>
                <button
                  type="button"
                  aria-label={t("agentic_grid.conversation.close")}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:translate-y-px"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </Dialog.Close>
            </div>
          </header>

          {loading ? (
            <div className="flex-1 space-y-4 overflow-hidden p-6">
              {["w-3/4", "w-full", "w-5/6", "w-2/3", "w-full", "w-4/5"].map(
                (width, row) => (
                  <div
                    key={row}
                    className={cn("h-3 animate-pulse rounded bg-muted/70", width)}
                  />
                ),
              )}
            </div>
          ) : error ? (
            <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
              <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
              <p className="text-sm font-medium text-foreground">
                {t("agentic_grid.conversation.load_failed")}
              </p>
              <p className="mt-1 max-w-md text-xs text-muted-foreground" role="alert">
                {error}
              </p>
              <button
                type="button"
                onClick={() => setReloadToken((token) => token + 1)}
                className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg border border-border px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted active:translate-y-px"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                {t("agentic_grid.conversation.retry")}
              </button>
            </div>
          ) : empty ? (
            <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
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
          ) : (
            <div
              ref={scrollerRef}
              data-testid="pane-conversation-scroller"
              className="min-h-0 flex-1 overflow-y-auto scroll-pt-4 px-5 py-4 scrollbar-jarvis"
            >
              {turns.map((turn, index) => (
                <TurnBlock key={index} turn={turn} />
              ))}
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function TurnBlock({ turn }: { turn: ConversationTurn }): JSX.Element {
  const t = useT();
  const user = turn.role === "user";
  const tools = [...new Set(turn.steps.map((step) => step.tool))];
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
          {fill(
            t(
              turn.steps.length === 1
                ? "agentic_grid.conversation.steps_one"
                : "agentic_grid.conversation.steps_many",
            ),
            turn.steps.length,
          )}
          {tools.length > 0 && <> · {tools.slice(0, 6).join(", ")}</>}
        </div>
      )}
    </div>
  );
}
