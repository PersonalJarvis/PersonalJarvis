import { useEffect, useMemo, useRef } from "react";
import type { NavigationRecord } from "./navigationApi";
import type { Vec3 } from "./world";

export interface AgentFollowTarget {
  agentId: string;
  name: string;
  position: Vec3;
}

export interface AgentFollowSnapshot {
  records: NavigationRecord[];
  names: ReadonlyMap<string, string>;
  navigationFresh: boolean;
  rosterFresh: boolean;
  offline: boolean;
}

/** A failed request is not evidence of deletion; cached data cannot restore follow. */
export function useAgentFollowTarget(agentId: string | null, snapshot: AgentFollowSnapshot) {
  const confirmed = useRef<AgentFollowTarget | null>(null);
  const { records, names, navigationFresh, rosterFresh, offline } = snapshot;
  const record = records.find((item) => item.agent_id === agentId);
  const name = agentId ? names.get(agentId) : undefined;
  const missing = Boolean(agentId && ((navigationFresh && record?.presence !== "placed")
    || (rosterFresh && name === undefined)));
  const live = Boolean(agentId && !missing && navigationFresh && rosterFresh && !offline);
  const target = useMemo<AgentFollowTarget | null>(() => {
    if (live && agentId && record && name !== undefined) return { agentId, name, position: record.position };
    if (agentId && !missing && confirmed.current?.agentId === agentId) return confirmed.current;
    return null;
  }, [agentId, live, missing, name, record]);

  useEffect(() => {
    if (!agentId || missing) confirmed.current = null;
    else if (live) confirmed.current = target;
  }, [agentId, missing, live, target]);

  const status = !agentId ? "idle" : missing ? "missing" : offline ? "offline" : live ? "live" : "waiting";
  return { target, status };
}
