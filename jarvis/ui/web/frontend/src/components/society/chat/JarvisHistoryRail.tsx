import { useEffect, useRef, useState } from "react";
import { MessageSquare, Mic, Plus, Trash2 } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { startNewVoiceRun } from "@/lib/chatsApi";
import { CONVERSATIONS_REFRESH_MS, useConversations } from "@/hooks/useConversations";
import { formatChatWhen } from "@/components/home/chatRows";
import { setJarvisCardMode } from "./AgentChatPanel";

/**
 * The lead's own history, in the Options column: typed chats and spoken
 * sessions as two lists. Specialists keep the rail they have — only Jarvis
 * is reached by voice, so only his card lists voice sessions.
 *
 * A click never leaves the card: a chat opens in the middle column's
 * timeline, a voice session is read there as an archive (continued by
 * speaking, like the front page). Opening either kind closes the other, so
 * the two can never claim the column at once. "+" starts a blank page.
 */
export function JarvisHistoryRail() {
  const t = useT();
  const [startingVoice, setStartingVoice] = useState(false);
  const startingVoiceRef = useRef(false);
  const [voiceError, setVoiceError] = useState(false);
  const sessions = useAgentChatStore((s) => s.sessions);
  const activeSessionId = useAgentChatStore((s) => s.activeSessionId);
  const loadSessions = useAgentChatStore((s) => s.loadSessions);
  const { conversations, openConversation, refresh } = useConversations();
  const setActiveConversation = useEventStore((s) => s.setActiveConversation);
  const setMessages = useEventStore((s) => s.setMessages);
  const voiceThreadId = useEventStore((s) => (s.activeKind === "voice" ? s.activeThreadId : null));

  // The dialog is transient, so it polls while open: a session that ends
  // with a hang-up appears without reopening the card.
  useEffect(() => {
    void loadSessions();
    void refresh();
    const id = window.setInterval(() => {
      void loadSessions();
      void refresh();
    }, CONVERSATIONS_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [loadSessions, refresh]);

  const voiceRows = conversations.filter((c) => c.kind === "voice");

  const openChat = (sessionId: string) => {
    setActiveConversation("text", null);
    setMessages([]);
    useAgentChatStore.getState().openSession(sessionId);
    setJarvisCardMode("chat");
  };

  const openVoice = (voiceId: string) => {
    useAgentChatStore.getState().newChat();
    setJarvisCardMode("chat");
    void openConversation("voice", voiceId);
  };

  const startNew = () => {
    setActiveConversation("text", null);
    setMessages([]);
    useAgentChatStore.getState().newChat();
    setJarvisCardMode("chat");
  };

  const removeChat = (sessionId: string) => {
    void useAgentChatStore.getState().removeSession(sessionId);
  };

  const startVoice = async () => {
    if (startingVoiceRef.current) return;
    startingVoiceRef.current = true;
    setStartingVoice(true);
    setVoiceError(false);
    try {
      await startNewVoiceRun();
      useAgentChatStore.getState().newChat();
      setActiveConversation("voice", null);
      setMessages([]);
      useEventStore.getState().seedThinkingTraces({});
      useEventStore.getState().setTranscription("", true);
      useHomeStore.getState().resetTranscript();
      setJarvisCardMode("voice");
      void refresh();
    } catch {
      // Keep the current conversation visible if the backend could not reset it.
      setVoiceError(true);
    } finally {
      startingVoiceRef.current = false;
      setStartingVoice(false);
    }
  };

  return (
    <section className="flex min-h-0 shrink-0 flex-col gap-3" data-testid="jarvis-history">
      <div className="flex min-h-0 flex-col">
        <div className="mb-1 flex shrink-0 items-center justify-between gap-2">
          <h3 className="font-display text-[13px] font-semibold tracking-tight text-foreground">
            {t("society.chat.history_chats")}
            {sessions.length > 0 ? (
              <span className="ml-1.5 tabular-nums text-muted-foreground">{sessions.length}</span>
            ) : null}
          </h3>
          <button
            type="button"
            onClick={startNew}
            title={t("society.chat.new_chat")}
            aria-label={t("society.chat.new_chat")}
            data-testid="jarvis-history-new"
            className="rounded p-0.5 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus size={14} aria-hidden />
          </button>
        </div>
        {sessions.length === 0 ? (
          <p className="px-1 py-0.5 text-xs text-muted-foreground">{t("society.chat.history_empty")}</p>
        ) : (
          <ul className="max-h-36 space-y-px overflow-y-auto">
            {sessions.map((s) => (
              <HistoryRow
                key={s.session_id}
                icon={<MessageSquare aria-hidden className="h-3 w-3 shrink-0" />}
                title={s.title || s.preview || t("society.chat.new_chat")}
                when={formatChatWhen(s.updated_ms)}
                active={s.session_id === activeSessionId && voiceThreadId === null}
                onOpen={() => openChat(s.session_id)}
                onDelete={() => removeChat(s.session_id)}
                deleteLabel={t("society.chat.history_delete")}
                testId="jarvis-history-chat-row"
              />
            ))}
          </ul>
        )}
      </div>
      <div className="flex min-h-0 flex-col">
        <div className="mb-1 flex shrink-0 items-center justify-between gap-2">
          <h3 className="font-display text-[13px] font-semibold tracking-tight text-foreground">
            {t("society.chat.history_voice")}
            {voiceRows.length > 0 ? (
              <span className="ml-1.5 tabular-nums text-muted-foreground">{voiceRows.length}</span>
            ) : null}
          </h3>
          <button
            type="button"
            onClick={() => void startVoice()}
            disabled={startingVoice}
            title={t("sidebar.new_voice_chat")}
            aria-label={t("sidebar.new_voice_chat")}
            data-testid="jarvis-history-new-voice"
            className="rounded p-0.5 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            <Plus size={14} aria-hidden />
          </button>
        </div>
        {voiceError ? (
          <p role="alert" className="px-1 py-0.5 text-xs text-destructive">
            {t("sidebar.new_voice_chat")}: {t("voice_state.error")}
          </p>
        ) : null}
        {voiceRows.length === 0 ? (
          <p className="px-1 py-0.5 text-xs text-muted-foreground">{t("society.chat.history_empty")}</p>
        ) : (
          <ul className="max-h-36 space-y-px overflow-y-auto">
            {voiceRows.map((c) => (
              <HistoryRow
                key={c.id}
                icon={<Mic aria-hidden className="h-3 w-3 shrink-0" />}
                title={c.title || c.preview || t("society.chat.new_chat")}
                when={formatChatWhen(c.updated_ms)}
                active={c.id === voiceThreadId}
                onOpen={() => openVoice(c.id)}
                testId="jarvis-history-voice-row"
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function HistoryRow({
  icon,
  title,
  when,
  active,
  onOpen,
  onDelete,
  deleteLabel,
  testId,
}: {
  icon: React.ReactNode;
  title: string;
  when: string;
  active: boolean;
  onOpen: () => void;
  onDelete?: () => void;
  deleteLabel?: string;
  testId: string;
}) {
  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        title={title}
        data-testid={testId}
        data-active={active ? "true" : undefined}
        className={cn(
          "flex h-7 w-full items-center gap-1.5 rounded-md px-1.5 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          active ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary hover:text-foreground",
        )}
      >
        <span className={cn("shrink-0", active ? "text-foreground" : "text-muted-foreground")}>{icon}</span>
        <span className="min-w-0 flex-1 truncate text-xs">{title}</span>
        {when ? <span className="shrink-0 text-micro tabular-nums text-foreground-faint">{when}</span> : null}
      </button>
      {onDelete ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title={deleteLabel}
          aria-label={deleteLabel}
          className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded-md bg-card p-1 text-muted-foreground transition-colors hover:bg-destructive/15 hover:text-destructive group-hover:block"
        >
          <Trash2 className="h-3 w-3" aria-hidden />
        </button>
      ) : null}
    </li>
  );
}
