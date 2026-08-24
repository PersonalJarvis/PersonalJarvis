/**
 * Give the agent board a memory.
 *
 * The board used to render only `JarvisAgentRegistry`, which is an in-memory
 * bus subscriber that drops a finished node after a 60 second TTL and starts
 * empty on every app start. So unless you happened to be looking at the screen
 * within a minute of a run, every number read 0 and the table read "no agents
 * are running right now" — even with hundreds of completed runs on disk. The
 * live view was working exactly as built; it just had nothing durable behind
 * it.
 *
 * The durable record already exists: the missions table behind `/api/missions`,
 * which is the SAME thing the board calls an agent — `rows.ts` describes a
 * mission as the root node carrying the task text. So the board now shows the
 * union: live registry nodes (which carry tool calls and tick in real time)
 * plus the mission history underneath them.
 *
 * A run present in both wins as its LIVE node, because only that one has the
 * tool calls and the running clock.
 */
import type { MissionSummary } from "@/types/missions";
import type { SubAgentNode } from "@/store/jarvisAgents";

/** How many past runs the board carries. The table is a board, not an archive. */
export const HISTORY_LIMIT = 50;

/**
 * Mission lifecycle → board status.
 *
 * The four pre-terminal states all mean "this run has not landed yet", which
 * is what the board's `running` row shows. TIMED_OUT joins `failed` (it did
 * not deliver); CANCELLED stays its own status, because stopping a run on
 * purpose is a decision, not a fault.
 */
const STATE_STATUS: Record<string, SubAgentNode["status"]> = {
  PENDING: "running",
  RUNNING: "running",
  CRITIQUING: "running",
  LOOPING: "running",
  APPROVED: "completed",
  FAILED: "failed",
  TIMED_OUT: "failed",
  CANCELLED: "cancelled",
};

/**
 * Registry trace ids have their dashes stripped (`sub_agents_routes.get_agent`
 * does the same before a lookup), mission ids keep theirs. Compare on the
 * stripped form or the same run shows up twice.
 */
export function normalizeTraceId(id: string): string {
  return id.replace(/-/g, "");
}

export function missionToNode(mission: MissionSummary): SubAgentNode {
  const status = STATE_STATUS[mission.state] ?? "completed";
  const createdMs = mission.created_ms;
  // `updated_ms` is when the row last changed state, so for a landed run it is
  // its end. A still-running one has no duration yet — the board then counts
  // up from `started_ns` itself.
  const updatedMs = mission.updated_ms;
  const settled = status !== "running";
  const durationMs =
    settled && typeof updatedMs === "number" && updatedMs > createdMs
      ? updatedMs - createdMs
      : null;

  return {
    trace_id: normalizeTraceId(mission.id),
    kind: "jarvis_agent",
    name: "",
    status,
    parent_trace_id: mission.parent_mission_id
      ? normalizeTraceId(mission.parent_mission_id)
      : null,
    started_ns: createdMs * 1_000_000,
    completed_ns: settled && updatedMs ? updatedMs * 1_000_000 : null,
    duration_ms: durationMs,
    cost_usd: mission.cost_usd ?? 0,
    tokens_in: 0,
    tokens_out: 0,
    utterance: mission.prompt,
    context_hints: [],
    prompts: [],
    // The history carries no tool calls — the registry is the only source for
    // those, and it forgets. An empty list keeps the row un-expandable rather
    // than opening onto nothing.
    tool_calls: [],
    children_trace_ids: [],
    error: null,
    error_class: null,
    review_iterations: mission.iteration ?? 0,
    depth: 0,
    ui_appeared_at: createdMs,
  };
}

/**
 * Live nodes first, then any past run the registry no longer holds.
 *
 * Sorted newest-first across both sources, so a run that just finished sits
 * above yesterday's regardless of which source it came from.
 */
export function mergeBoardRows(
  live: SubAgentNode[],
  missions: MissionSummary[],
  limit: number = HISTORY_LIMIT,
): SubAgentNode[] {
  const seen = new Set(live.map((n) => normalizeTraceId(n.trace_id)));
  const history: SubAgentNode[] = [];

  for (const mission of missions) {
    if (history.length >= limit) break;
    if (seen.has(normalizeTraceId(mission.id))) continue;
    seen.add(normalizeTraceId(mission.id));
    history.push(missionToNode(mission));
  }

  return [...live, ...history].sort((a, b) => b.started_ns - a.started_ns);
}
