import { useCallback, useMemo } from "react";

import { useConversations } from "@/hooks/useConversations";
import { useEventStore, type ConversationSummary } from "@/store/events";
import { useAgentChatStore } from "@/store/agentChat";
import { useHomeStore } from "@/store/home";
import { transcriptFromMessages } from "@/lib/homeTranscript";
import { useI18nStore } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";

/**
 * The one chat history the app has, and the one way to open a row of it.
 *
 * Two backends feed it — the voice sessions (`/api/chats`) and the typed
 * Jarvis chats (`/api/agent-chat/sessions?surface=jarvis`, read through the
 * front page's agent-chat store) — merged into one list sorted by when each
 * was last touched. Both kinds are the same assistant, reached by voice or
 * by keyboard; the Agentic IDE's coding sessions are on another surface and
 * never enter this list. The sidebar block and the "All chats" dialog both
 * read THIS, so the two can never disagree about what exists or about what
 * a click does. A typed row's kind is still called `"agent"` — that is the
 * internal name of the agent-chat backend that carries it, not a claim
 * about who answers.
 *
 * The rule a click follows (the bug this file was extracted to fix): the
 * front page shows exactly ONE conversation, so opening a row must also
 * close the other kind. Opening an agent chat drops the voice thread from
 * the stage; opening a voice session ends the agent session's socket. Before
 * that, clicking a voice row while the chat surface was up loaded its words
 * into a store the chat stage does not read — the screen simply kept showing
 * the previous conversation.
 */

export interface ChatRow {
  kind: "voice" | "agent";
  id: string;
  title: string;
  preview: string;
  updatedMs: number;
  messageCount: number;
  /** The sub-agent an agent chat runs on; empty for a voice session. */
  provider: string;
  raw: ConversationSummary | null;
}

export interface ChatRowsApi {
  rows: ChatRow[];
  /** True for the row currently on the front page's stage. */
  isActive: (row: ChatRow) => boolean;
  open: (row: ChatRow) => void;
  /** Agent chats can be deleted; a voice session is a recording, not a draft. */
  remove: (row: ChatRow) => void;
}

export function useChatRows({ poll = false }: { poll?: boolean } = {}): ChatRowsApi {
  const { conversations, openConversation, reopenLiveConversation } = useConversations({ poll });
  const sessions = useAgentChatStore((s) => s.sessions);
  const activeSessionId = useAgentChatStore((s) => s.activeSessionId);
  const activeVoiceId = useEventStore((s) => (s.activeKind === "voice" ? s.activeThreadId : null));
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const setActiveConversation = useEventStore((s) => s.setActiveConversation);
  const setMessages = useEventStore((s) => s.setMessages);
  const seedTranscript = useHomeStore((s) => s.seedTranscript);
  const setSurface = useHomeStore((s) => s.setSurface);

  const rows = useMemo<ChatRow[]>(() => {
    const voice: ChatRow[] = conversations
      .filter((c) => c.kind === "voice")
      .map((c) => ({
        kind: "voice",
        id: c.id,
        // The backend names each chat by its topic; "" means it has none, and
        // the row then says what kind of chat it was (chatRowLabel) instead
        // of promoting its first words ("Hallo", "Kannst") to a headline.
        title: c.title,
        preview: c.preview,
        updatedMs: c.updated_ms,
        messageCount: c.message_count,
        provider: "",
        raw: c,
      }));
    const agent: ChatRow[] = sessions.map((s) => ({
      kind: "agent",
      id: s.session_id,
      title: s.title,
      preview: s.preview,
      updatedMs: s.updated_ms,
      messageCount: s.message_count,
      provider: s.provider,
      raw: null,
    }));
    return [...voice, ...agent].sort((a, b) => b.updatedMs - a.updatedMs);
  }, [conversations, sessions]);

  const isActive = useCallback(
    (row: ChatRow) => (row.kind === "agent" ? row.id === activeSessionId : row.id === activeVoiceId),
    [activeSessionId, activeVoiceId],
  );

  const open = useCallback(
    (row: ChatRow) => {
      // A history row is Jarvis' conversation: it takes the stage back from
      // an agent's chat opened from the sidebar.
      useHomeStore.getState().openAgentChat(null);
      if (row.kind === "voice") {
        if (reopenLiveConversation(row.id)) {
          setSurface("voice");
          setActiveSection("chats");
          return;
        }
        // End the agent session on stage first: the chat stage renders the
        // voice archive only while no agent chat is open, and its socket has
        // no business staying connected to a conversation nobody is looking at.
        // The voice chat goes on stage as itself: calls from here continue it
        // (one row in the history), not a new chat beside it.
        useAgentChatStore.getState().newChat({ voiceSessionId: row.id });
        useHomeStore.getState().setContinuedVoiceId(row.id);
        const stayOnVoice = useHomeStore.getState().surface === "voice";
        const opened = openConversation("voice", row.id);
        if (stayOnVoice) {
          void opened.then((messages) => {
            const active = useEventStore.getState();
            if (messages === null || active.activeKind !== "voice" || active.activeThreadId !== row.id) return;
            seedTranscript(transcriptFromMessages(messages));
          });
        } else {
          void opened;
          setSurface("chat");
        }
      } else {
        // Drop the voice thread so the stage shows the agent timeline, not a
        // stale archive underneath it.
        setActiveConversation("text", null);
        setMessages([]);
        useAgentChatStore.getState().openSession(row.id);
        setSurface("chat");
      }
      setActiveSection("chats");
    },
    [openConversation, reopenLiveConversation, seedTranscript, setActiveConversation, setActiveSection, setMessages, setSurface],
  );

  const remove = useCallback((row: ChatRow) => {
    if (row.kind !== "agent") return;
    void useAgentChatStore.getState().removeSession(row.id);
  }, []);

  return { rows, isActive, open, remove };
}

/**
 * What a row is called: its topic, or — when it has none — what it was.
 * ``untitled`` lets a list set those rows in a quieter tone.
 */
export function chatRowLabel(row: ChatRow, t: (key: string) => string): { text: string; untitled: boolean } {
  const title = row.title.trim();
  // "New Chat" is the placeholder title `ensureActiveThread` stores for a fresh
  // text thread; it is not a topic, so it reads in the UI language like any
  // other untitled row.
  if (title && title !== "New Chat") return { text: title, untitled: false };
  if (row.kind === "voice") {
    const when = formatChatWhen(row.updatedMs);
    const kind = t("sidebar.untitled_voice_chat");
    return { text: when ? `${kind} · ${when}` : kind, untitled: true };
  }
  return { text: t("chats_view.new_chat"), untitled: true };
}

/** Short time for a row: clock today, day + month before that. */
export function formatChatWhen(ms: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (d.getTime() >= today.getTime()) {
    return d.toLocaleTimeString(localeForUiLanguage(useI18nStore.getState().ui), { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString(localeForUiLanguage(useI18nStore.getState().ui), { month: "short", day: "numeric" });
}

export type ChatDayBucket = "today" | "yesterday" | "week" | "month" | "older";

const BUCKET_ORDER: ChatDayBucket[] = ["today", "yesterday", "week", "month", "older"];

/**
 * Group rows the way a desktop mail client does — today, yesterday, the last
 * week, the last month, then everything older. Empty buckets are dropped, so
 * a fresh install shows one heading rather than five.
 */
export function groupChatRows(rows: ChatRow[], now = Date.now()): Array<{ bucket: ChatDayBucket; rows: ChatRow[] }> {
  const startOfDay = (ms: number) => {
    const d = new Date(ms);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  };
  const today = startOfDay(now);
  const yesterday = today - 86_400_000;
  const week = today - 7 * 86_400_000;
  const month = today - 30 * 86_400_000;

  const buckets = new Map<ChatDayBucket, ChatRow[]>();
  for (const row of rows) {
    const day = startOfDay(row.updatedMs);
    const bucket: ChatDayBucket =
      day >= today ? "today" : day >= yesterday ? "yesterday" : day >= week ? "week" : day >= month ? "month" : "older";
    const list = buckets.get(bucket);
    if (list) list.push(row);
    else buckets.set(bucket, [row]);
  }
  return BUCKET_ORDER.filter((b) => (buckets.get(b)?.length ?? 0) > 0).map((bucket) => ({
    bucket,
    rows: buckets.get(bucket) ?? [],
  }));
}

/** Case-insensitive match over title and preview. An empty query keeps everything. */
export function filterChatRows(rows: ChatRow[], query: string): ChatRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter(
    (r) => r.title.toLowerCase().includes(q) || r.preview.toLowerCase().includes(q),
  );
}
