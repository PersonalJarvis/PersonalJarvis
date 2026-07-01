import { useEffect, useRef, useState, type FormEvent } from "react";
import { Loader2, Send } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import {
  useFriendMessages,
  useSendFriendMessage,
} from "@/hooks/useFriendMessages";
import type { FriendDetail, FriendMessage } from "@/hooks/useFriends";
import { SourceBadge } from "./SourceBadge";
import { cn } from "@/lib/utils";

/**
 * Chat thread for a friend (right side of the chat tab).
 *
 * Layout (like ChatsView):
 *   - Header (friend name + source badge + permission profile)
 *   - ScrollArea with message bubbles (timestamp-sorted)
 *   - Input footer (send button + Enter-to-send)
 *
 * If the friend has no linked channel, the send path is disabled.
 */
export function ChatThread({ friend }: { friend: FriendDetail }) {
  const t = useT();
  const messages = useFriendMessages(friend.id);
  const send = useSendFriendMessage();
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const list = messages.data ?? [];
  const hasChannel = friend.channels.length > 0;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [list.length]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !hasChannel || send.isPending) return;
    send.mutate(
      { friend_id: friend.id, text },
      { onSuccess: () => setDraft("") }
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-5 py-3">
        <div className="flex items-center gap-2">
          <span className="font-display text-sm font-semibold text-foreground">
            {friend.display_name}
          </span>
          <SourceBadge channels={friend.channels} />
        </div>
        <span className="rounded-md border border-border/60 bg-muted/40 px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("chat_thread.profile")}: {friend.permission_profile}
        </span>
      </header>

      <div className="flex-1 min-h-0">
        {list.length === 0 ? (
          <EmptyThread hasChannel={hasChannel} />
        ) : (
          <ScrollArea className="h-full">
            <div ref={scrollRef} className="space-y-3 px-5 py-4">
              {list.map((m, idx) => (
                <FriendMessageBubble key={`${m.timestamp_ns}-${idx}`} message={m} />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 border-t border-border bg-card/30 px-5 py-3"
      >
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            hasChannel
              ? `${t("chat_thread.message_to")} ${friend.display_name} ...`
              : t("chat_thread.no_channel_placeholder")
          }
          disabled={!hasChannel || send.isPending}
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm focus:border-primary/40 focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={!hasChannel || send.isPending || draft.trim().length === 0}
          className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-xs uppercase tracking-wider text-primary hover:bg-primary/20 disabled:opacity-50"
        >
          {send.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Send className="h-3 w-3" />
          )}
          {t("chat_thread.send")}
        </button>
      </form>

      {send.isError && (
        <div className="border-t border-destructive/40 bg-destructive/10 px-5 py-2 text-xs text-destructive">
          {t("chat_thread.send_failed")}: {(send.error as Error).message}
        </div>
      )}
    </div>
  );
}

function FriendMessageBubble({ message }: { message: FriendMessage }) {
  const isOutbound = message.direction === "outbound";
  return (
    <div className={cn("flex", isOutbound ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
          isOutbound
            ? "bg-primary text-primary-foreground"
            : "border border-border bg-card/80 text-foreground backdrop-blur"
        )}
      >
        <div className="whitespace-pre-wrap">{message.text}</div>
        <div
          className={cn(
            "mt-1 text-[10px]",
            isOutbound ? "text-primary-foreground/70" : "text-muted-foreground"
          )}
        >
          {formatTimestamp(message.timestamp_ns)}
          {message.channel ? " - " + message.channel : ""}
        </div>
      </div>
    </div>
  );
}

function EmptyThread({ hasChannel }: { hasChannel: boolean }) {
  const t = useT();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center text-sm text-muted-foreground">
      <span>{t("chat_thread.no_messages")}</span>
      {!hasChannel && (
        <span className="rounded-md border border-dashed border-border/60 px-3 py-1 text-[11px]">
          {t("chat_thread.link_channel_hint")}
        </span>
      )}
    </div>
  );
}

function formatTimestamp(ns: number): string {
  try {
    const date = new Date(ns / 1_000_000);
    return date.toLocaleTimeString("de-DE", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}
