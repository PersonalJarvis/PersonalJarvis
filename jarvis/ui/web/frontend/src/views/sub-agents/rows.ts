import type { SubAgentNode } from "@/store/jarvisAgents";

/**
 * Collapse the flat node map to ONE row per task.
 *
 * The backend `SubAgentRegistry` holds two nodes for a single dispatched task,
 * and the store keeps both (the DetailPanel reads any node by id): the mission
 * ("Sub-Agent", a root, carries the task text in `utterance`) and the worker
 * that executes it ("Worker", a child linked via `parent_trace_id`, no task
 * text). The operations board is a per-task view, so a worker whose parent
 * mission is already on the board is an execution detail of that mission, not a
 * separate sub-agent — surfacing both made one task appear as two rows.
 *
 * This mirrors the backend `SubAgentRegistry.tree()` roots filter, applied
 * client-side so the header counts (derived from the returned array) stay
 * consistent with the rows actually shown. An orphaned worker whose mission
 * has already faded out is still kept, so nothing silently disappears.
 *
 * Sorted newest-first by `started_ns`. The full node map is untouched.
 */
export function selectTaskRows(
  all: Record<string, SubAgentNode> | null | undefined,
): SubAgentNode[] {
  const map = all ?? {};
  return Object.values(map)
    .filter((n) => !n.parent_trace_id || !(n.parent_trace_id in map))
    .sort((a, b) => (b.started_ns ?? 0) - (a.started_ns ?? 0));
}
