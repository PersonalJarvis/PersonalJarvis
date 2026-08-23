import { useState } from "react";
import { MessageSquare, Mic, Trash2 } from "lucide-react";

import { useConversations } from "@/hooks/useConversations";
import { useEventStore, type ConversationSummary } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { SidebarGroup } from "@/components/home/SidebarGroup";

export const RECENT_CHATS_FOLDED = 3;
export const RECENT_CHATS_UNFOLDED = 15;

/**
 * The last conversations — text threads and voice sessions in one list —
 * from the sidebar. This block is the one long-lived poller of the history
 * (`useConversations({ poll: true })`); the chat stage reads the same store.
 *
 * Opening a row resumes it on the CHAT surface: that is where a stored
 * thread can be read and continued by typing. The voice surface shows the
 * live turn, not an archive.
 */
export function RecentChats() {
  const t = useT();
  const { conversations, activeThreadId, openConversation, removeConversation } =
    useConversations({ poll: true });
  const [open, setOpen] = useState(false);
  const setActive = useEventStore((s) => s.setActiveSection);
  const setSurface = useHomeStore((s) => s.setSurface);

  const shown = conversations.slice(0, open ? RECENT_CHATS_UNFOLDED : RECENT_CHATS_FOLDED);
  const canExpand = conversations.length > RECENT_CHATS_FOLDED;

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
          {shown.map((c) => (
            <ChatRow
              key={`${c.kind}-${c.id}`}
              conversation={c}
              active={c.id === activeThreadId}
              onOpen={() => {
                void openConversation(c.kind, c.id);
                setSurface("chat");
                setActive("chats");
              }}
              onDelete={() => void removeConversation(c.id)}
            />
          ))}
        </ul>
      )}
    </SidebarGroup>
  );
}

function ChatRow({
  conversation,
  active,
  onOpen,
  onDelete,
}: {
  conversation: ConversationSummary;
  active: boolean;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const isVoice = conversation.kind === "voice";
  const Icon = isVoice ? Mic : MessageSquare;
  const title = conversation.title || conversation.preview || t("chats_view.new_chat");
  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        title={title}
        data-testid="recent-chat-row"
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
          {formatWhen(conversation.updated_ms)}
        </span>
      </button>
      {!isVoice && (
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
