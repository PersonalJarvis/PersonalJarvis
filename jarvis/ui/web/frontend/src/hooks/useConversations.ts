import { useCallback, useEffect } from "react";

import { useEventStore, type ConversationKind } from "@/store/events";
import {
  deleteTextConversation,
  detailToMessages,
  fetchConversations,
  resumeConversation,
} from "@/lib/chatsApi";

/** How often the history list is re-read while a poller is mounted. */
export const CONVERSATIONS_REFRESH_MS = 5000;

/**
 * The unified chat history (text threads + voice sessions) and the three
 * things anyone does with it: open one, start a new one, delete one.
 *
 * Lifted out of the old two-pane chat view so the sidebar's "recent chats"
 * block and the chat stage read ONE list and act on it the same way. The
 * list itself lives in the event store; `poll` says whether this mount keeps
 * it fresh (GET /api/chats is a fast local query). Exactly one long-lived
 * mount should poll — the sidebar — so a second surface showing the same
 * list does not double the traffic.
 */
export function useConversations({ poll = false }: { poll?: boolean } = {}) {
  const conversations = useEventStore((s) => s.conversations);
  const activeThreadId = useEventStore((s) => s.activeThreadId);
  const activeKind = useEventStore((s) => s.activeKind);
  const setConversations = useEventStore((s) => s.setConversations);
  const setActiveConversation = useEventStore((s) => s.setActiveConversation);
  const setMessages = useEventStore((s) => s.setMessages);

  const refresh = useCallback(async () => {
    try {
      setConversations(await fetchConversations());
    } catch {
      /* offline / headless — leave the list as-is */
    }
  }, [setConversations]);

  useEffect(() => {
    if (!poll) return;
    void refresh();
    const id = window.setInterval(() => void refresh(), CONVERSATIONS_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [poll, refresh]);

  const openConversation = useCallback(
    async (kind: ConversationKind, id: string) => {
      setActiveConversation(kind, id);
      try {
        const detail = await resumeConversation(kind, id);
        setMessages(detailToMessages(detail));
      } catch {
        setMessages([]);
      }
    },
    [setActiveConversation, setMessages],
  );

  const newChat = useCallback(() => {
    setActiveConversation("text", null);
    setMessages([]);
  }, [setActiveConversation, setMessages]);

  const removeConversation = useCallback(
    async (id: string) => {
      try {
        await deleteTextConversation(id);
      } catch {
        /* the list refresh below shows what is really there */
      }
      if (useEventStore.getState().activeThreadId === id) newChat();
      void refresh();
    },
    [newChat, refresh],
  );

  return {
    conversations,
    activeThreadId,
    activeKind,
    refresh,
    openConversation,
    newChat,
    removeConversation,
  };
}
