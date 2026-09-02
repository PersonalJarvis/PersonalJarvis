/**
 * The Quest Board's data seam: the list from `/api/society/quests`, kept
 * live by the `SocietyQuestChanged` push the WebSocket forwards (every
 * change re-reads within a second) with a slow poll as the safety net, and
 * the three mutations — post, cancel, retry — each of which invalidates the
 * list AND the roster, because a quest may have forged a new agent.
 */
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelSocietyQuest,
  fetchSocietyQuests,
  postSocietyQuest,
  retrySocietyQuest,
  type SocietyQuestRow,
} from "@/lib/societyApi";
import { useEventStore } from "@/store/events";

export const QUESTS_QUERY_KEY = ["society", "quests"] as const;
/** The roster's key as `components/society/data.ts` spells it. */
const ROSTER_QUERY_KEY = ["society", "roster"] as const;

/** Bus event names that mean "the board changed" (jarvis/core/events.py). */
const QUEST_EVENTS = new Set(["SocietyQuestChanged"]);

export function useSocietyQuests(enabled = true) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: QUESTS_QUERY_KEY,
    queryFn: fetchSocietyQuests,
    enabled,
    staleTime: 2_000,
    refetchInterval: 10_000,
  });
  useEffect(() => {
    if (!enabled) return;
    let lastId: string | null = useEventStore.getState().events[0]?.id ?? null;
    return useEventStore.subscribe((state) => {
      const top = state.events[0];
      if (!top || top.id === lastId) return;
      lastId = top.id;
      if (!QUEST_EVENTS.has(top.name)) return;
      void client.invalidateQueries({ queryKey: QUESTS_QUERY_KEY });
      const payload = top.payload as { previous?: string } | undefined;
      // A quest that just left "open" may have forged its taker: refresh the roster.
      if (payload?.previous === "open") void client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
    });
  }, [client, enabled]);
  return query;
}

function useQuestMutation<TArgs extends unknown[]>(fn: (...args: TArgs) => Promise<SocietyQuestRow>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: TArgs) => fn(...args),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUESTS_QUERY_KEY }),
        client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY }),
      ]);
    },
  });
}

export function usePostQuest() {
  return useQuestMutation<[text: string, title: string]>((text, title) => postSocietyQuest(text, title));
}

export function useCancelQuest() {
  return useQuestMutation((questId: string) => cancelSocietyQuest(questId));
}

export function useRetryQuest() {
  return useQuestMutation((questId: string) => retrySocietyQuest(questId));
}
