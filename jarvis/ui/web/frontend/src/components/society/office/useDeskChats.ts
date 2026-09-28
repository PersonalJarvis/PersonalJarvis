/**
 * Live chat tails for the agents who sit at their desk right now.
 *
 * Polls the existing session endpoint only for seated agents, one request per
 * agent per interval, jittered so windows and agents never fire in lockstep
 * (AP-33). A failed poll keeps the last good screen; it is logged, not shown.
 */
import { useEffect, useRef, useState } from "react";
import { fetchAgentChatSession } from "@/lib/agentChatApi";
import { chatLines, chatVersion, type ChatLine } from "./deskChat";

/** Events fetched per poll: enough for a full screen after tool noise is filtered out. */
const TAIL_EVENTS = 60;
import { seatedAtDesk } from "./walkerRegistry";

const POLL_MS = 2500;
const JITTER_MS = 1200;
/** Never more sessions in flight per tick than this, however many agents sit. */
const MAX_PER_TICK = 6;

export interface DeskChat { lines: ChatLine[]; version: string }

export function useDeskChats(sessions: ReadonlyMap<string, string>, awake: boolean): ReadonlyMap<string, DeskChat> {
  const [chats, setChats] = useState<ReadonlyMap<string, DeskChat>>(new Map());
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  useEffect(() => {
    if (!awake) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const due = [...seatedAtDesk].filter((id) => sessionsRef.current.has(id)).slice(0, MAX_PER_TICK);
      const results = await Promise.all(due.map(async (agentId) => {
        try {
          const detail = await fetchAgentChatSession(sessionsRef.current.get(agentId)!, { tail: TAIL_EVENTS });
          return [agentId, { lines: chatLines(detail.events), version: chatVersion(detail.events) }] as const;
        } catch (error) {
          // The monitor keeps its last good frame; the chat view itself reports real failures.
          console.debug("Desk chat poll failed", agentId, error);
          return null;
        }
      }));
      if (cancelled) return;
      setChats((prev) => {
        let next: Map<string, DeskChat> | null = null;
        for (const result of results) {
          if (!result) continue;
          const [agentId, chat] = result;
          if (prev.get(agentId)?.version === chat.version) continue;
          next ??= new Map(prev);
          next.set(agentId, chat);
        }
        return next ?? prev;
      });
      timer = setTimeout(() => void tick(), POLL_MS + Math.random() * JITTER_MS);
    };
    timer = setTimeout(() => void tick(), Math.random() * JITTER_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [awake]);
  return chats;
}
