import { useEffect, useState } from "react";
import { Archive, ChevronDown, MessageSquare, Mic, Trash2 } from "lucide-react";

import { useAgentChatStore } from "@/store/agentChat";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { AllChatsDialog } from "@/components/home/AllChatsDialog";
import { formatChatWhen, useChatRows, type ChatRow } from "@/components/home/chatRows";
import { CONVERSATIONS_REFRESH_MS } from "@/hooks/useConversations";

export const RECENT_CHATS_FOLDED = 3;
export const RECENT_CHATS_UNFOLDED = 15;

/**
 * The last conversations — agent chats and voice sessions in one list —
 * folded under the sidebar's "Chat" row. This block is the one long-lived
 * poller of both histories (`useConversations({ poll: true })` for the voice
 * sessions, the agent-chat store's session list for the typed ones).
 *
 * It hangs off the nav row rather than standing above the navigation as its
 * own titled group. Standing free it was one more list in a column that, in
 * the Agentic IDE's chat mode, already opened with the workspaces and their
 * sessions — three lists of rows in a row, no edge between them (maintainer
 * report 2026-08-27). Under the row it is what it is: the Chat section's own
 * history, shown when the chevron on that row is opened and folded away
 * otherwise. The row itself still opens the chat; only the chevron opens the
 * list, so reaching the section never unfolds thirty rows nobody asked for.
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
      <div data-testid="recent-chats" className="pb-1 pt-0.5">
        {shown.length === 0 ? (
          <p className="py-1 pl-9 pr-2 text-[11px] text-muted-foreground/70">
            {t("sidebar.no_chats")}
          </p>
        ) : (
          <ul className={cn("relative space-y-px", TREE_GUIDE)}>
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
        {canExpand && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            data-testid="recent-chats-more"
            className={TAIL_ROW}
          >
            <ChevronDown
              aria-hidden
              className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-180")}
            />
            <span className="min-w-0 flex-1 truncate text-xs">
              {open ? t("sidebar.show_less") : t("sidebar.show_all")}
            </span>
            {!open && hidden > 0 && <Count n={hidden} />}
          </button>
        )}
        {rows.length > 0 && (
          <button
            type="button"
            onClick={() => setArchiveOpen(true)}
            data-testid="see-all-chats"
            className={TAIL_ROW}
          >
            <Archive aria-hidden className="h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 flex-1 truncate text-xs">{t("sidebar.see_all_chats")}</span>
            {open && hidden > 0 && <Count n={hidden} />}
          </button>
        )}
      </div>
      <AllChatsDialog open={archiveOpen} onOpenChange={setArchiveOpen} />
    </>
  );
}

/**
 * The thin line that ties the rows to the row they hang under — drawn at the
 * centre of the nav row's icon (12 px padding + half of 16 px), so the list
 * reads as the Chat row's branch and not as a second list that happens to
 * follow it.
 */
const TREE_GUIDE =
  "before:pointer-events-none before:absolute before:bottom-1 before:left-5 before:top-1 before:w-px before:bg-border/70 before:content-['']";

/** "Show all" and "See all chats": the two quiet rows that close the list. */
const TAIL_ROW = cn(
  "flex w-full items-center gap-2 rounded-lg py-1.5 pl-9 pr-2 text-left transition-colors",
  "text-muted-foreground hover:bg-background/60 hover:text-foreground",
  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
);

function Count({ n }: { n: number }) {
  return (
    <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/70">
      +{n}
    </span>
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
          "flex w-full items-center gap-2 rounded-lg py-1.5 pl-9 pr-2 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          // The open one wears the same yellow edge as the active nav row, so
          // "where am I" is said in one voice all the way down the column.
          active
            ? "bg-card text-foreground shadow-[inset_2px_0_0_hsl(var(--primary))]"
            : "hover:bg-background/60",
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
