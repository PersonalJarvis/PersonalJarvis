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
 *
 * The fetch and the turns are shared with the session PAGE the chat sidebar
 * opens (./PaneConversation); this file is the dialog frame around them.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { BookOpenText, RefreshCw, X } from "lucide-react";

import { useT } from "@/i18n";
import {
  PaneConversationBody,
  usePaneConversation,
} from "@/components/agentic/PaneConversation";
import { cn } from "@/lib/utils";

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
  // Mounted while closed on purpose (the rail keeps it), so the fetch waits
  // for the dialog to actually be opened.
  const state = usePaneConversation(terminal, undefined, open);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid={`pane-conversation-dialog-${terminal}`}
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex h-[min(84dvh,52rem)] w-[min(880px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-2">
                <BookOpenText className="h-4 w-4 text-primary" aria-hidden="true" />
                <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                  {t("agentic_grid.conversation.title").replace("{0}", terminal)}
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
                onClick={state.reload}
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

          <PaneConversationBody state={state} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
