/**
 * The Agentic IDE's new chat — an empty chat window with no agent behind it.
 *
 * Chat mode used to start a coding session the way the grid does: a menu asked
 * WHICH CLI, a pane opened, and the conversation began on whatever that binary
 * defaults to. From where the user sits that is the wrong order (maintainer
 * report, 2026-08-27): a new chat should be an empty chat window, and the
 * coding agent, its model, how hard it thinks and how often it asks should be
 * chosen in the interface — in the composer's own picks, where every other
 * chat in this app chooses them.
 *
 * So this is that window: the same `ChatStage` the front page and a running
 * pane use, on a store that holds the picks instead of a conversation
 * (`store/newPaneChat`). Its picker lists the coding CLIs the Agentic IDE has
 * connected on this machine, each with its own models and ladders. Nothing is
 * spawned while it is open — the first message is what opens the pane, WITH
 * those picks on the CLI's command line, and from then on the pane's own chat
 * takes over.
 *
 * The grid keeps its menu. There a pane is a pane: you are placing a terminal
 * in a layout, not starting a conversation, and asking four questions before
 * a split would be four questions too many.
 */
import { useEffect, useMemo } from "react";
import { MessageSquarePlus } from "lucide-react";

import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import type { SplitAgentChoice } from "@/components/agentic/AgentPicker";
import { ChatStage } from "@/components/home/ChatStage";
import {
  createNewPaneChatStore,
  type NewPaneChatStoreHook,
  type NewPaneRequest,
} from "@/store/newPaneChat";
import { useT } from "@/i18n";

export interface NewPaneChatProps {
  /** The workspace folder the agent will work in. */
  folder: string;
  /** Every entry the backend registered, as the split menus receive them. */
  agents: readonly SplitAgentChoice[];
  /** Open the pane on these picks and deliver the first message. */
  onOpen: (request: NewPaneRequest) => Promise<void>;
  /** Leave the draft without starting anything. */
  onDismiss: () => void;
}

export function NewPaneChat({ folder, agents, onOpen, onDismiss }: NewPaneChatProps) {
  const t = useT();
  // One store per draft. Keyed by folder in the caller, so opening a new chat
  // in another workspace starts from that workspace's own picks rather than
  // inheriting the last one's.
  const store: NewPaneChatStoreHook = useMemo(
    () => createNewPaneChatStore({ folder, agents, open: onOpen }),
    // `agents` is rebuilt by the IDE's poll on every tick; rebuilding the store
    // with it would throw away a half-typed sentence every few seconds. The
    // list only changes when a CLI is installed, which is rare and worth a
    // remount — so it is compared by what it says, not by identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [folder, agentsFingerprint(agents), onOpen],
  );

  // Escape leaves an empty draft, the way it closes any other overlay. A draft
  // holds nothing but picks, so there is nothing to lose and nothing to
  // confirm — and a window with no way out is a trap.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onDismiss]);

  return (
    <div
      className="absolute inset-0 z-20 flex min-h-0 flex-col rounded-lg bg-background"
      data-testid="new-pane-chat"
    >
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-4">
        <MessageSquarePlus className="h-4 w-4 shrink-0 text-primary" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
          {t("agentic_grid.new_chat.title")}
        </span>
        <button
          type="button"
          onClick={onDismiss}
          data-testid="new-pane-chat-dismiss"
          className="inline-flex h-8 items-center rounded-lg border border-border px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          {t("agentic_grid.new_chat.cancel")}
        </button>
      </header>

      {/* The empty page every chat in this app opens on: the folder's name and
          the composer in the middle, with the picks under the text box. */}
      <AgentChatStoreProvider store={store}>
        <ChatStage />
      </AgentChatStoreProvider>
    </div>
  );
}

/** What the CLI list SAYS, so a poll that changes nothing remounts nothing. */
function agentsFingerprint(agents: readonly SplitAgentChoice[]): string {
  return agents
    .map((agent) => `${agent.name}:${agent.installed ? 1 : 0}:${agent.picks?.models.length ?? 0}`)
    .join("|");
}
