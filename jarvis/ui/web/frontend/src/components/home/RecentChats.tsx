import { useEffect, useMemo, useState } from "react";
import { MessageSquare, Mic, Trash2 } from "lucide-react";

import { useConversations } from "@/hooks/useConversations";
import { useEventStore, type ConversationSummary } from "@/store/events";
import { useAgentChatStore } from "@/store/agentChat";
import { useHomeStore } from "@/store/home";
import { useT } from "@/i18n";
import { transcriptFromMessages } from "@/lib/homeTranscript";
import { cn } from "@/lib/utils";
import { SidebarGroup } from "@/components/home/SidebarGroup";
import { CONVERSATIONS_REFRESH_MS } from "@/hooks/useConversations";

export const RECENT_CHATS_FOLDED = 3;
export const RECENT_CHATS_UNFOLDED = 15;

/**
 * The last conversations — agent chats and voice sessions in one list —
 * from the sidebar. This block is the one long-lived poller of both
 * histories (`useConversations({ poll: true })` for the voice sessions,
 * the agent-chat store's session list for the typed ones).
 *
 * Opening a row resumes it and lands on the surface that fits (maintainer,
 * 2026-08-23): a VOICE session opened while the voice stage is up stays
 * there, its words loaded into the transcript lane, so you just keep
 * talking. An agent chat goes to the chat surface, where the session is
 * read and continued by typing — on the provider and model it was using,
 * which the composer shows. Reading the surface at click time, not via a
 * subscription, keeps the list from re-rendering on every switch.
 *
 * The classic brain's text threads are no longer listed: since the chat
 * surface became the agent chat there is nowhere to open them; the data
 * stays on disk.
 */
export function RecentChats() {
  const t = useT();
  const { conversations, openConversation } = useConversations({ poll: true });
  const sessions = useAgentChatStore((s) => s.sessions);
  const activeSessionId = useAgentChatStore((s) => s.activeSessionId);
  const loadSessions = useAgentChatStore((s) => s.loadSessions);
  const openSession = useAgentChatStore((s) => s.openSession);
  const removeSession = useAgentChatStore((s) => s.removeSession);
  const [open, setOpen] = useState(false);
  const setActive = useEventStore((s) => s.setActiveSection);
  const activeVoiceId = useEventStore((s) => (s.activeKind === "voice" ? s.activeThreadId : null));
  const setSurface = useHomeStore((s) => s.setSurface);
  const seedTranscript = useHomeStore((s) => s.seedTranscript);

  useEffect(() => {
    void loadSessions();
    const id = window.setInterval(() => void loadSessions(), CONVERSATIONS_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [loadSessions]);

  const rows = useMemo<RecentRow[]>(() => {
    const voice: RecentRow[] = conversations
      .filter((c) => c.kind === "voice")
      .map((c) => ({ kind: "voice", id: c.id, title: c.title || c.preview, updatedMs: c.updated_ms, raw: c }));
    const agent: RecentRow[] = sessions.map((s) => ({
      kind: "agent",
      id: s.session_id,
      title: s.title || s.preview,
      updatedMs: s.updated_ms,
      raw: null,
    }));
    return [...voice, ...agent].sort((a, b) => b.updatedMs - a.updatedMs);
  }, [conversations, sessions]);

  const openRow = (row: RecentRow) => {
    if (row.kind === "voice" && row.raw) {
      const stayOnVoice = useHomeStore.getState().surface === "voice";
      const opened = openConversation("voice", row.id);
      if (stayOnVoice) {
        void opened.then((messages) => seedTranscript(transcriptFromMessages(messages)));
      } else {
        void opened;
        setSurface("chat");
      }
    } else {
      openSession(row.id);
      setSurface("chat");
    }
    setActive("chats");
  };

  const shown = rows.slice(0, open ? RECENT_CHATS_UNFOLDED : RECENT_CHATS_FOLDED);
  const canExpand = rows.length > RECENT_CHATS_FOLDED;

  return (
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
            <ChatRow
              key={`${row.kind}-${row.id}`}
              row={row}
              active={row.kind === "agent" ? row.id === activeSessionId : row.id === activeVoiceId}
              onOpen={() => openRow(row)}
              onDelete={row.kind === "agent" ? () => void removeSession(row.id) : undefined}
            />
          ))}
        </ul>
      )}
    </SidebarGroup>
  );
}

interface RecentRow {
  kind: "voice" | "agent";
  id: string;
  title: string;
  updatedMs: number;
  raw: ConversationSummary | null;
}

function ChatRow({
  row,
  active,
  onOpen,
  onDelete,
}: {
  row: RecentRow;
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
          {formatWhen(row.updatedMs)}
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

function formatWhen(ms: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (d.getTime() >= today.getTime()) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
