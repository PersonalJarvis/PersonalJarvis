import { useCallback, useEffect } from "react";

import { useEventStore, type ChatMessage, type ConversationKind } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { requestVoiceHangup } from "@/lib/voiceApi";
import { HISTORY_REFRESH_MS, useHistoryPolling } from "@/hooks/useHistoryPolling";
import {
  deleteTextConversation,
  detailToMessages,
  detailToTraces,
  fetchConversations,
  invalidateConversations,
  resumeConversation,
  startNewVoiceRun,
} from "@/lib/chatsApi";

/** How often the history list is re-read while a poller is mounted. */
export const CONVERSATIONS_REFRESH_MS = HISTORY_REFRESH_MS;

let selectionGeneration = 0;
let observedVoiceEvent = "";

/** Stable across sidebar/rail mounts: one polling owner writes the shared list. */
async function refreshConversations(): Promise<void> {
  try {
    useEventStore.getState().setConversations(await fetchConversations());
  } catch {
    // Offline / restarting: retain the last list until the next bounded read.
  }
}

/** Wait for the real session boundary rather than treating an accepted stop as completion. */
function waitForVoiceIdle(): Promise<void> {
  if (useEventStore.getState().voiceState === "idle") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      unsubscribe();
      reject(new Error("The voice call has not ended yet. Please try again."));
    }, 10_000);
    const unsubscribe = useEventStore.subscribe((state) => {
      if (state.voiceState !== "idle") return;
      window.clearTimeout(timer);
      unsubscribe();
      resolve();
    });
  });
}

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
  const setActiveConversation = useEventStore((s) => s.setActiveConversation);
  const setMessages = useEventStore((s) => s.setMessages);
  const seedThinkingTraces = useEventStore((s) => s.seedThinkingTraces);

  const refresh = refreshConversations;
  const voiceEvent = useEventStore((s) => s.events.find((event) =>
    event.name === "VoiceSessionStarted" || event.name === "VoiceSessionEnded")?.id ?? "");
  useEffect(() => {
    if (!poll || !voiceEvent || voiceEvent === observedVoiceEvent) return;
    observedVoiceEvent = voiceEvent;
    invalidateConversations();
    // The shared visible poll catches up within one interval. Invalidating an
    // in-flight read also prevents its pre-event snapshot replacing fresh data.
  }, [poll, voiceEvent]);
  useHistoryPolling(refresh, poll);

  /**
   * Make a conversation the active one and resume it on the backend (the
   * brain is seeded with it, so the next typed OR spoken turn continues it).
   * Resolves to the stored messages — empty when the backend had none or
   * could not be reached — so a caller can show them elsewhere too.
   */
  const openConversation = useCallback(
    async (kind: ConversationKind, id: string): Promise<ChatMessage[]> => {
      const generation = ++selectionGeneration;
      useHomeStore.setState({ voiceSelectionPending: kind === "voice", freshVoicePending: false });
      setActiveConversation(kind, id);
      let messages: ChatMessage[] = [];
      let traces = {};
      try {
        if (kind === "voice" && ["listening", "thinking", "speaking", "paused", "connecting"].includes(useEventStore.getState().voiceState)) {
          useHomeStore.setState({ voiceSwitchStopping: true });
          try {
            await requestVoiceHangup();
            await waitForVoiceIdle();
          } finally {
            useHomeStore.setState({ voiceSwitchStopping: false });
          }
        }
        const selected = useEventStore.getState();
        if (generation !== selectionGeneration || selected.activeKind !== kind || selected.activeThreadId !== id) return [];
        const detail = await resumeConversation(kind, id);
        messages = detailToMessages(detail);
        traces = detailToTraces(detail);
      } catch (error) {
        if (generation === selectionGeneration) {
          useEventStore.getState().pushToast("error", error instanceof Error ? error.message : "Could not open conversation");
        }
      } finally {
        if (generation === selectionGeneration) useHomeStore.setState({ voiceSelectionPending: false });
      }
      // The stored traces replace the previous conversation's, so a reply
      // in the new thread never wears the steps of an old one.
      const active = useEventStore.getState();
      if (generation !== selectionGeneration || active.activeKind !== kind || active.activeThreadId !== id) return [];
      seedThinkingTraces(traces);
      setMessages(messages);
      return messages;
    },
    [seedThinkingTraces, setActiveConversation, setMessages],
  );

  const newChat = useCallback(() => {
    setActiveConversation("text", null);
    seedThinkingTraces({});
    setMessages([]);
  }, [seedThinkingTraces, setActiveConversation, setMessages]);

  /**
   * Start a fresh voice run: drop the open voice thread here and tell the
   * backend to forget the one it was seeded with (and to end a session that
   * is still live). The lane on the voice stage is cleared by the caller —
   * it belongs to the home store, not to the history.
   *
   * Separate from `newChat` on purpose: that one lands on the chat surface,
   * which is exactly what someone standing on the voice stage did not ask for.
   */
  const newVoiceRun = useCallback(async () => {
    setActiveConversation("voice", null);
    seedThinkingTraces({});
    setMessages([]);
    try {
      await startNewVoiceRun();
    } catch {
      /* headless / offline: the local reset above already happened, and the
         backend has no seeded thread to forget when it is not running */
    }
  }, [seedThinkingTraces, setActiveConversation, setMessages]);

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
    newVoiceRun,
    removeConversation,
  };
}
