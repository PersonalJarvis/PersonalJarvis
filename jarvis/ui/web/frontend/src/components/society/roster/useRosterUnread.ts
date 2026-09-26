import { useEffect, useRef, useState } from "react";

import type { SocietyAgent } from "../data";

const STORAGE_KEY = "society.rosterUnread.v1";

function readStored(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((entry): entry is string => typeof entry === "string"));
  } catch {
    return new Set();
  }
}

/**
 * Unseen-results tracking for the roster rail.
 *
 * Backend `agent.state` only knows idle/working/waiting/paused. The rail
 * needs one more fact: did this agent finish something the person has not
 * opened yet. That fact lives here, in the window:
 *
 * - `working` is rendered by the caller as a loading spinner.
 * - a `working -> idle|waiting` transition marks the agent unread (green dot),
 *   unless that agent is the one currently open.
 * - opening an agent (`activeAgentId`) clears its unread mark (grey dot).
 *
 * Persisted to localStorage so a fresh result survives a reload until opened.
 */
export function useRosterUnread(
  agents: SocietyAgent[],
  activeAgentId: string | null,
): Set<string> {
  const [unread, setUnread] = useState<Set<string>>(() => readStored());
  const prevStates = useRef(new Map<string, string>());
  const initialized = useRef(false);

  useEffect(() => {
    const ids = new Set(agents.map((agent) => agent.agentId));
    const current = new Map(agents.map((agent) => [agent.agentId, agent.state]));

    if (!initialized.current) {
      prevStates.current = current;
      initialized.current = true;
      setUnread((prev) => {
        if ([...prev].every((id) => ids.has(id) && id !== activeAgentId)) return prev;
        const copy = new Set(prev);
        for (const id of [...copy]) {
          if (!ids.has(id) || id === activeAgentId) copy.delete(id);
        }
        return copy;
      });
      return;
    }

    const finished: string[] = [];
    for (const [id, state] of current) {
      const prev = prevStates.current.get(id);
      if (prev === "working" && state !== "working" && state !== "paused") {
        if (id !== activeAgentId) finished.push(id);
      }
    }
    prevStates.current = current;

    setUnread((prev) => {
      if (finished.length === 0) {
        const needsPrune =
          (activeAgentId !== null && prev.has(activeAgentId)) ||
          [...prev].some((id) => !ids.has(id));
        if (!needsPrune) return prev;
        const copy = new Set(prev);
        if (activeAgentId) copy.delete(activeAgentId);
        for (const id of [...copy]) {
          if (!ids.has(id)) copy.delete(id);
        }
        return copy;
      }
      const copy = new Set(prev);
      for (const id of finished) copy.add(id);
      if (activeAgentId) copy.delete(activeAgentId);
      for (const id of [...copy]) {
        if (!ids.has(id)) copy.delete(id);
      }
      return copy;
    });
  }, [agents, activeAgentId]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...unread]));
    } catch {
      /* storage full / private mode — the dots still work for this window */
    }
  }, [unread]);

  return unread;
}
