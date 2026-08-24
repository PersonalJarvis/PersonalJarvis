import { useCallback, useMemo } from "react";

import { useConversations } from "@/hooks/useConversations";
import { useEventStore, type ConversationSummary } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { transcriptFromMessages } from "@/lib/homeTranscript";

/**
 * The one chat history the front page has, and the one way to open a row.
 *
 * Both kinds in it are the SAME assistant: the voice sessions you spoke and
 * the chats you typed, from `/api/chats`, merged into one list sorted by when
 * each was last touched. That is the point of the front page — one Jarvis,
 * two ways to reach it — and it is why a typed thread can pick up where a
 * spoken one stopped. The sidebar block and the "All chats" dialog both read
 * THIS, so the two can never disagree about what exists or about what a click
 * does.
 *
 * The Agentic IDE's agent chats are deliberately NOT here (maintainer,
 * 2026-08-24). They belong to a workspace folder and are opened where that
 * folder is — components/agentic/WorkspaceChats. Listing them next to Jarvis'
 * own conversations was the visible half of the mix-up this file's history
 * records: for a day, opening the front page's chat opened a coding agent.
 *
 * The rule a click follows: the front page shows exactly ONE conversation, so
 * opening a row replaces whatever was on stage. A spoken session opened while
 * the voice stage is up stays there with its words in the lane; opened from
 * the chat surface it is read there as an archive
 * (components/home/VoiceThreadStage).
 */

export interface ChatRow {
  /** "voice" was spoken, "text" was typed — both are Jarvis. */
  kind: "voice" | "text";
  id: string;
  title: string;
  preview: string;
  updatedMs: number;
  messageCount: number;
  raw: ConversationSummary;
}

export interface ChatRowsApi {
  rows: ChatRow[];
  /** True for the row currently on the front page's stage. */
  isActive: (row: ChatRow) => boolean;
  open: (row: ChatRow) => void;
  /** A typed thread can be deleted; a voice session is a recording, not a draft. */
  remove: (row: ChatRow) => void;
}

export function useChatRows({ poll = false }: { poll?: boolean } = {}): ChatRowsApi {
  const { conversations, openConversation, removeConversation } = useConversations({ poll });
  const activeThreadId = useEventStore((s) => s.activeThreadId);
  const activeKind = useEventStore((s) => s.activeKind);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const seedTranscript = useHomeStore((s) => s.seedTranscript);
  const setSurface = useHomeStore((s) => s.setSurface);

  const rows = useMemo<ChatRow[]>(
    () =>
      conversations
        .map((c) => ({
          kind: c.kind === "voice" ? ("voice" as const) : ("text" as const),
          id: c.id,
          title: c.title || c.preview,
          preview: c.preview,
          updatedMs: c.updated_ms,
          messageCount: c.message_count,
          raw: c,
        }))
        .sort((a, b) => b.updatedMs - a.updatedMs),
    [conversations],
  );

  const isActive = useCallback(
    (row: ChatRow) => row.id === activeThreadId && row.kind === activeKind,
    [activeKind, activeThreadId],
  );

  const open = useCallback(
    (row: ChatRow) => {
      if (row.kind === "voice") {
        // Standing on the voice stage, a spoken session is resumed there with
        // its words in the lane — you just keep talking. From the chat
        // surface it is read as an archive instead.
        const stayOnVoice = useHomeStore.getState().surface === "voice";
        const opened = openConversation("voice", row.id);
        if (stayOnVoice) {
          void opened.then((messages) => seedTranscript(transcriptFromMessages(messages)));
        } else {
          void opened;
          setSurface("chat");
        }
      } else {
        void openConversation("text", row.id);
        setSurface("chat");
      }
      setActiveSection("chats");
    },
    [openConversation, seedTranscript, setActiveSection, setSurface],
  );

  const remove = useCallback(
    (row: ChatRow) => {
      if (row.kind !== "text") return;
      void removeConversation(row.id);
    },
    [removeConversation],
  );

  return { rows, isActive, open, remove };
}

/** Short time for a row: clock today, day + month before that. */
export function formatChatWhen(ms: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (d.getTime() >= today.getTime()) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
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
