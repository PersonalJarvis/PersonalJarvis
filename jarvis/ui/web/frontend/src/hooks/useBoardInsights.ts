import { useQuery } from "@tanstack/react-query";

import type { BoardCategories } from "@/hooks/useBoard";

// ----------------------------------------------------------------------
// Types (mirror the InsightsResponse schema in jarvis/ui/web/board_routes.py)
// ----------------------------------------------------------------------

export interface InsightsDay {
  date: string; // local YYYY-MM-DD
  dictations: number;
  dictation_words: number;
  voice_sessions: number;
  voice_words: number;
  chat_messages: number;
  agent_sessions: number;
  agent_turns: number;
}

export interface InsightsAgent {
  agent: string;
  sessions: number;
  sessions_30d: number;
  turns: number;
  turns_30d: number;
  tokens: number;
  last_ms: number;
}

export interface InsightsChatProvider {
  provider: string;
  sessions: number;
  messages: number;
  jarvis_sessions: number;
}

export interface InsightsRecord {
  date: string;
  value: number;
}

export interface BoardInsights {
  generated_at: string;
  reference: { typing_wpm: number; novel_words: number };
  dictation: { available: boolean; words: number; dictations: number; seconds: number };
  voice: {
    available: boolean;
    sessions: number;
    turns: number;
    user_words: number;
    jarvis_words: number;
    /** Time voice sessions were OPEN (wake to hang-up), not time spent speaking. */
    seconds: number;
  };
  chats: {
    available: boolean;
    sessions: number;
    messages: number;
    providers: InsightsChatProvider[];
  };
  agents: {
    available: boolean;
    sessions: number;
    turns: number;
    tokens: number;
    items: InsightsAgent[];
  };
  days: InsightsDay[];
  /** 7 rows, Monday first, x 24 local hours. */
  punch_card: number[][];
  trend: { words_30d: number; words_prev_30d: number };
  streak: {
    current_days: number;
    longest_days: number;
    active_days: number;
    first_day: string | null;
  };
  records: {
    best_words_day: InsightsRecord | null;
    best_agent_day: InsightsRecord | null;
  };
  categories: BoardCategories;
}

async function fetchInsights(): Promise<BoardInsights> {
  const res = await fetch("/api/board/insights");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export const boardInsightsQuery = {
  queryKey: ["board", "insights"] as const,
  queryFn: fetchInsights,
  // The backend reuses one picture for 30 s; polling faster only re-reads it.
  refetchInterval: 30_000,
  staleTime: 15_000,
};

export function useBoardInsights() {
  return useQuery(boardInsightsQuery);
}
