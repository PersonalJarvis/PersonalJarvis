import { useEffect, useState } from "react";
import { Archive, ChevronDown, MessageSquare, Pin, PinOff, Trash2 } from "lucide-react";

import { useAgentChatStore } from "@/store/agentChat";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { AllChatsDialog } from "@/components/home/AllChatsDialog";
import { useChatRows, type ChatRow } from "@/components/home/chatRows";
import { CONVERSATIONS_REFRESH_MS } from "@/hooks/useConversations";

export const RECENT_CHATS_FOLDED = 15;
export const RECENT_CHATS_UNFOLDED = 50;

/** Flat sidebar history: pinned conversations first, then the latest chats. */
const PINNED_KEY = "jarvis.sidebar.pinned-chats.v1";
function readPins(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(PINNED_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    // Unavailable or invalid storage leaves the history usable without pins.
    return [];
  }
}
const rowKey = (row: ChatRow) => `${row.kind}:${row.id}`;
export function RecentChats() {
  const t = useT();
  const { rows, isActive, open: openRow, remove } = useChatRows({ poll: true });
  const loadSessions = useAgentChatStore((s) => s.loadSessions);
  const [pins, setPins] = useState(readPins);
  const togglePin = (row: ChatRow) => {
    const key = rowKey(row);
    const next = pins.includes(key) ? pins.filter((id) => id !== key) : [...pins, key];
    setPins(next);
    try { localStorage.setItem(PINNED_KEY, JSON.stringify(next)); }
    catch { /* Storage denied: keep the pin for this visit. */ }
  };
  const pinnedRows = rows.filter((row) => pins.includes(rowKey(row)));
  const recentRows = rows.filter((row) => !pins.includes(rowKey(row)));
  const [open, setOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);

  useEffect(() => {
    void loadSessions();
    const id = window.setInterval(() => void loadSessions(), CONVERSATIONS_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [loadSessions]);

  const shown = recentRows.slice(0, open ? RECENT_CHATS_UNFOLDED : RECENT_CHATS_FOLDED);
  const canExpand = recentRows.length > RECENT_CHATS_FOLDED;
  // The archive earns its place only once the sidebar cannot show everything.
  const hidden = recentRows.length - shown.length;

  return (
    <>
      <div data-testid="recent-chats" className="pb-1 pt-0.5">
        {pinnedRows.length > 0 && <section data-testid="pinned-chats" className="mb-6">
          <h2 className="px-3 pb-2 text-sm font-medium text-muted-foreground">{t("sidebar.pinned")}</h2>
          <ul className="space-y-1">{pinnedRows.map((row) => <ChatRowItem key={rowKey(row)} row={row}
            active={isActive(row)} pinned onPin={() => togglePin(row)} onOpen={() => openRow(row)}
            onDelete={row.kind === "agent" ? () => remove(row) : undefined} />)}</ul>
        </section>}
        <h2 className="px-3 pb-2 text-sm font-medium text-muted-foreground">{t("sidebar.recent")}</h2>
        {shown.length === 0 ? (
          <p className="py-1 px-3 text-sm text-foreground-faint">
            {t("sidebar.no_chats")}
          </p>
        ) : (
          <ul className="space-y-1">
            {shown.map((row) => (
              <ChatRowItem
                key={`${row.kind}-${row.id}`}
                row={row}
                active={isActive(row)}
                onPin={() => togglePin(row)}
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
            <span className="min-w-0 flex-1 truncate text-sm">
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
            <span className="min-w-0 flex-1 truncate text-sm">{t("sidebar.see_all_chats")}</span>
            {open && hidden > 0 && <Count n={hidden} />}
          </button>
        )}
      </div>
      <AllChatsDialog open={archiveOpen} onOpenChange={setArchiveOpen} />
    </>
  );
}

/** "Show all" and "See all chats": the two quiet rows that close the list. */
const TAIL_ROW = cn(
  "flex h-10 w-full items-center gap-2 rounded-md px-3 text-left transition-colors",
  "text-muted-foreground hover:bg-secondary hover:text-foreground",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

function Count({ n }: { n: number }) {
  return (
    <span className="shrink-0 text-sm tabular-nums text-foreground-faint">
      +{n}
    </span>
  );
}

function ChatRowItem({
  row,
  active,
  onOpen,
  onDelete,
  pinned = false,
  onPin,
}: {
  row: ChatRow;
  active: boolean;
  onOpen: () => void;
  onDelete?: () => void;
  pinned?: boolean;
  onPin: () => void;
}) {
  const t = useT();
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
          "flex h-10 w-full items-center gap-2 rounded-md px-3 text-left transition-colors group-hover:pr-16 group-focus-within:pr-16",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          // The open one wears the same accent edge as the active nav row, so
          // "where am I" is said in one voice all the way down the column.
          active ? "jarvis-nav-active bg-secondary text-foreground" : "hover:bg-secondary",
        )}
      >
        {pinned && <MessageSquare aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate text-base text-foreground">{title}</span>

      </button>
      <button type="button" onClick={onPin} title={t(pinned ? "sidebar.unpin_chat" : "sidebar.pin_chat")}
        aria-label={`${t(pinned ? "sidebar.unpin_chat" : "sidebar.pin_chat")}: ${title}`}
        className="absolute right-7 top-1/2 -translate-y-1/2 rounded-md bg-card p-1.5 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100">
        {pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
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
          className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 rounded-md bg-card p-1 text-muted-foreground transition-colors hover:bg-destructive/15 hover:text-destructive focus:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      )}
    </li>
  );
}
