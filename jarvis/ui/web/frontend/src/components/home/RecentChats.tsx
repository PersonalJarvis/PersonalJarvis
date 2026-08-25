import { useEffect, useState } from "react";
import { Archive, MessageSquare, Mic, Trash2 } from "lucide-react";

import { useAgentChatStore } from "@/store/agentChat";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { SidebarGroup } from "@/components/home/SidebarGroup";
import { AllChatsDialog } from "@/components/home/AllChatsDialog";
import { formatChatWhen, useChatRows, type ChatRow } from "@/components/home/chatRows";
import { CONVERSATIONS_REFRESH_MS } from "@/hooks/useConversations";

export const RECENT_CHATS_FOLDED = 3;
export const RECENT_CHATS_UNFOLDED = 15;

/**
 * The last conversations — agent chats and voice sessions in one list —
 * from the sidebar. This block is the one long-lived poller of both
 * histories (`useConversations({ poll: true })` for the voice sessions,
 * the agent-chat store's session list for the typed ones).
 *
 * It is a shortcut, not the archive: it shows the handful you touched last
 * and hands everything else to "All chats" (components/home/AllChatsDialog),
 * where the whole history is searchable. Both open a row through the same
 * `useChatRows().open`, so a click behaves identically in either place.
 *
 * Opening a row resumes it and lands on the surface that fits (maintainer,
 * 2026-08-23): a VOICE session opened while the voice stage is up stays
 * there, its words loaded into the transcript lane, so you just keep
 * talking. Opened from the chat surface it is read there as an archive.
 * An agent chat goes to the chat surface, where the session is read and
 * continued by typing — on the provider and model it was using.
 *
 * The typed rows are the front page's Jarvis chats (agent-chat sessions on
 * the `jarvis` surface) — the same assistant as the voice rows, reached by
 * keyboard. The Agentic IDE's coding sessions live on their own surface and
 * are listed there, never here. The classic brain's text threads are no
 * longer listed either: since the chat surface became the agent chat there
 * is nowhere to open them; the data stays on disk.
 */
export function RecentChats() {
  const t = useT();
  const { rows, isActive, open: openRow, remove } = useChatRows({ poll: true });
  const loadSessions = useAgentChatStore((s) => s.loadSessions);
  const [open, setOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);

  useEffect(() => {
    void loadSessions();
    const id = window.setInterval(() => void loadSessions(), CONVERSATIONS_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [loadSessions]);

  const shown = rows.slice(0, open ? RECENT_CHATS_UNFOLDED : RECENT_CHATS_FOLDED);
  const canExpand = rows.length > RECENT_CHATS_FOLDED;
  // The archive earns its place only once the sidebar cannot show everything.
  const hidden = rows.length - shown.length;

  return (
    <>
      <SidebarGroup
        title={t("sidebar.recent_chats")}
        action={
          canExpand
            ? {
                label: open ? t("sidebar.show_less") : t("sidebar.show_all"),
                onClick: () => setOpen((v) => !v),
                expanded: open,
              }
            : undefined
        }
        testId="recent-chats"
      >
        {shown.length === 0 ? (
          <p className="px-2 py-1 text-[11px] text-muted-foreground/70">{t("sidebar.no_chats")}</p>
        ) : (
          <ul className="space-y-px">
            {shown.map((row) => (
              <ChatRowItem
                key={`${row.kind}-${row.id}`}
                row={row}
                active={isActive(row)}
                onOpen={() => openRow(row)}
                onDelete={row.kind === "agent" ? () => remove(row) : undefined}
              />
            ))}
          </ul>
        )}
        {rows.length > 0 && (
          <button
            type="button"
            onClick={() => setArchiveOpen(true)}
            data-testid="see-all-chats"
            className={cn(
              "mt-0.5 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors",
              "text-muted-foreground hover:bg-background/60 hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            )}
          >
            <Archive aria-hidden className="h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 flex-1 truncate text-xs">{t("sidebar.see_all_chats")}</span>
            {hidden > 0 && (
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/70">
                +{hidden}
              </span>
            )}
          </button>
        )}
      </SidebarGroup>
      <AllChatsDialog open={archiveOpen} onOpenChange={setArchiveOpen} />
    </>
  );
}

function ChatRowItem({
  row,
  active,
  onOpen,
  onDelete,
}: {
  row: ChatRow;
  active: boolean;
  onOpen: () => void;
  onDelete?: () => void;
}) {
  const t = useT();
  const isVoice = row.kind === "voice";
  const Icon = isVoice ? Mic : MessageSquare;
  const title = row.title || t("chats_view.new_chat");
  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        title={title}
        data-testid="recent-chat-row"
        data-kind={row.kind}
        className={cn(
          "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          active ? "bg-card text-foreground shadow-sm" : "hover:bg-background/60",
        )}
      >
        <Icon
          aria-hidden
          className={cn("h-3.5 w-3.5 shrink-0", active ? "text-primary" : "text-muted-foreground")}
        />
        <span className="min-w-0 flex-1 truncate text-xs text-foreground">{title}</span>
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
          {formatChatWhen(row.updatedMs)}
        </span>
      </button>
      {onDelete && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title={t("chats_view.delete")}
          aria-label={t("chats_view.delete")}
          className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded-md bg-card p-1 text-muted-foreground transition-colors hover:bg-destructive/15 hover:text-destructive group-hover:block"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      )}
    </li>
  );
}
