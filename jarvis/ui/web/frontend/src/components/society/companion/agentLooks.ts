/**
 * Which wearable looks an agent has unlocked. The rulebook's LOOK_UNLOCKS
 * arrive with the progression snapshot; the agent's level from its subject.
 * Unknown data never locks anything: an unreachable level system leaves
 * every look open, and a look an agent already wears always stays wearable.
 */
import { useQuery } from "@tanstack/react-query";
import { agentSubject, fetchProgression } from "../progression/progressionApi";

export interface AgentLooks {
  level: number;
  /** look id -> agent level it opens at; null while unknown (nothing locked). */
  unlocks: Readonly<Record<string, number>> | null;
}

export function lookOpen(looks: AgentLooks, id: string): boolean {
  const at = looks.unlocks?.[id];
  return at === undefined || looks.level >= at;
}

/** The next locked look and its level, lowest first; undefined once all are open. */
export function nextLook(looks: AgentLooks): { id: string; level: number } | undefined {
  if (!looks.unlocks) return undefined;
  return Object.entries(looks.unlocks)
    .filter(([, at]) => at > looks.level)
    .sort((a, b) => a[1] - b[1])
    .map(([id, level]) => ({ id, level }))[0];
}

export function useAgentLooks(agentId: string | undefined): AgentLooks {
  const snapshot = useQuery({
    queryKey: ["progression", "looks"],
    enabled: !!agentId,
    staleTime: 60_000,
    retry: false,
    queryFn: () => fetchProgression(),
  });
  if (!agentId || !snapshot.data || Object.keys(snapshot.data.looks).length === 0) return { level: 1, unlocks: null };
  const subject = snapshot.data.subjects.find((row) => row.subjectId === agentSubject(agentId));
  return { level: subject?.level ?? 1, unlocks: snapshot.data.looks };
}
