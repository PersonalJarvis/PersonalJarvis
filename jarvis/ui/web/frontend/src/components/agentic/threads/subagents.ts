import { EMPTY_TIMELINE, reduceEvents, type SubagentState, type SubagentStatus, type TextBlock, type TimelineItem, type ToolBlock, type TurnBlock, type TurnItem, type TurnStatus } from "@/components/agentchat/reduce";
import type { AgentChatEvent } from "@/lib/agentChatApi";

/**
 * The sub-agents of a thread, read the same way for every coding agent.
 *
 * Claude Code and Codex report their sub-agents (`subagent_*` events): those
 * carry a life — started, working, finished — and, for Claude Code, the
 * sub-agent's whole conversation. Every other CLI is read from its spawn
 * call alone: the task it was given (the call's input) and its answer (the
 * call's result). Either way a sub-agent is one entry here, keyed by the call
 * that spawned it, so the thread can count, list and open them.
 */

/** Tool names that spawn a sub-agent, across CLIs: Claude Code, Codex, OpenCode, Kimi and kin. */
const SPAWN_TOOLS = new Set([
  "task", "agent", "spawn_agent", "spawnagent", "subagent", "sub_agent", "spawn_subagent",
  "launch_agent", "dispatch_agent", "run_agent", "delegate_task",
]);

export function isSpawnTool(name: string): boolean {
  const bare = name.split("__").pop() ?? name;
  return SPAWN_TOOLS.has(bare.toLowerCase());
}

/** True when this call spawned a sub-agent — reported, or read off its tool name. */
export function isSubagentCall(block: ToolBlock): boolean {
  return Boolean(block.subagent) || isSpawnTool(block.name);
}

/** One sub-agent as the thread shows it. */
export interface SubagentEntry {
  /** The spawning call's id — the key everything opens a sub-agent by. */
  id: string;
  turnId: string;
  /** 0 for the main agent's own sub-agents, 1 for theirs, … */
  depth: number;
  /** The sub-agent that spawned this one; null for the main agent's. */
  parentId: string | null;
  title: string;
  agentType: string;
  prompt: string;
  status: SubagentStatus;
  summary: string;
  activity: string;
  tokens: number | null;
  toolUses: number | null;
  durationMs: number | null;
  startedMs: number;
  finishedMs: number | null;
  background: boolean;
  /** An approval or a question inside it waits for the person. */
  waiting: boolean;
  /** Its conversation; empty when the CLI only reported its task and answer. */
  blocks: TurnBlock[];
  /** The CLI streamed its conversation (not only its task and answer). */
  streamed: boolean;
  block: ToolBlock;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** The receipt a background spawn returns at once — never the agent's answer. */
export function isLaunchReceipt(output: string | null): boolean {
  return Boolean(output && /^(Async agent launched|agentId:)/i.test(output.trim()));
}

function firstLine(value: string, max = 80): string {
  const line = value.split(/\r?\n/).find((part) => part.trim())?.trim() ?? "";
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
}

function waitsForPerson(blocks: TurnBlock[]): boolean {
  return blocks.some((b) => b.kind === "tool" && (
    (b.approval !== null && b.approval.decision === null)
    || (b.question !== undefined && !b.question.closed)
    || (b.credential !== undefined && b.credential.status === null)
  ));
}

/** A sub-agent read from a spawn call the CLI did not report on. */
function derived(block: ToolBlock, turnStatus: TurnStatus): SubagentState {
  const input = record(block.input);
  const prompt = text(input.prompt) || text(input.message) || text(input.task) || text(input.instructions);
  const output = block.output;
  const status: SubagentStatus = output !== null && !isLaunchReceipt(output)
    ? block.isError ? "failed" : "done"
    : turnStatus === "running" ? "running" : "stopped";
  return {
    description: text(input.description) || text(input.title) || text(input.name),
    agentType: text(input.subagent_type) || text(input.agent_type) || text(input.agent) || text(input.role),
    prompt,
    background: Boolean(input.run_in_background),
    threadId: "",
    status,
    summary: status === "running" || isLaunchReceipt(output) ? "" : output ?? "",
    activity: "",
    lastTool: "",
    tokens: null,
    toolUses: null,
    durationMs: block.durationMs,
    startedMs: block.startedMs,
    finishedMs: block.durationMs !== null ? block.startedMs + block.durationMs : null,
    blocks: [],
  };
}

function entryOf(block: ToolBlock, turn: TurnItem, depth: number, parentId: string | null): SubagentEntry {
  const sub = block.subagent ?? derived(block, turn.status);
  const input = record(block.input);
  const title = sub.description || text(input.description) || firstLine(sub.prompt) || sub.agentType || "Sub-agent";
  return {
    id: block.callId,
    turnId: turn.id,
    depth,
    parentId,
    title,
    agentType: sub.agentType || text(input.subagent_type),
    prompt: sub.prompt || text(input.prompt),
    status: sub.status,
    summary: sub.summary,
    activity: sub.activity,
    tokens: sub.tokens,
    toolUses: sub.toolUses ?? (sub.blocks.length ? sub.blocks.filter((b) => b.kind === "tool").length : null),
    durationMs: sub.durationMs,
    startedMs: sub.startedMs,
    finishedMs: sub.finishedMs,
    background: sub.background,
    waiting: block.approval?.decision === null || waitsForPerson(sub.blocks),
    blocks: sub.blocks,
    streamed: sub.blocks.length > 0,
    block,
  };
}

function walk(blocks: TurnBlock[], turn: TurnItem, depth: number, parentId: string | null, out: SubagentEntry[]): void {
  for (const block of blocks) {
    if (block.kind !== "tool" || !isSubagentCall(block)) continue;
    const entry = entryOf(block, turn, depth, parentId);
    out.push(entry);
    walk(entry.blocks, turn, depth + 1, entry.id, out);
  }
}

/** Every sub-agent of a thread, in the order they were spawned, nested ones after their parent. */
export function listSubagents(items: readonly TimelineItem[]): SubagentEntry[] {
  const out: SubagentEntry[] = [];
  for (const item of items) if (item.type === "turn") walk(item.blocks, item, 0, null, out);
  return out;
}

/** The sub-agent a call spawned, read for the turn it ran in. */
export function subagentEntry(block: ToolBlock, turn: TurnItem): SubagentEntry {
  return entryOf(block, turn, 0, null);
}

/** The chain from the main agent's sub-agent down to `id`; empty when it is unknown. */
export function subagentPath(entries: readonly SubagentEntry[], id: string): SubagentEntry[] {
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const path: SubagentEntry[] = [];
  let at = byId.get(id);
  while (at) {
    path.unshift(at);
    at = at.parentId ? byId.get(at.parentId) : undefined;
  }
  return path;
}

/** How many are still working, and how many in all. */
export function subagentCounts(entries: readonly SubagentEntry[]): { total: number; running: number; failed: number; waiting: number } {
  return {
    total: entries.length,
    running: entries.filter((entry) => entry.status === "running").length,
    failed: entries.filter((entry) => entry.status === "failed").length,
    waiting: entries.filter((entry) => entry.waiting).length,
  };
}

const TURN_STATUS: Record<SubagentStatus, TurnStatus> = { running: "running", done: "done", failed: "error", stopped: "cancelled" };

/**
 * A sub-agent's conversation as a turn, so the thread draws it exactly like
 * the main agent's work: its steps, thoughts and answer. A CLI that only
 * reported the answer shows that answer as the text.
 */
export function subagentTurn(entry: SubagentEntry): TurnItem {
  const answer: TextBlock[] = !entry.streamed && entry.summary.trim()
    ? [{ kind: "text", id: `answer:${entry.id}`, text: entry.summary }]
    : [];
  const running = entry.status === "running";
  return {
    type: "turn",
    id: `agent:${entry.id}`,
    provider: "",
    model: "",
    effort: "",
    runner: "",
    status: TURN_STATUS[entry.status],
    blocks: [...entry.blocks, ...answer],
    startedMs: entry.startedMs,
    durationMs: running ? null : entry.durationMs ?? (entry.finishedMs !== null ? Math.max(0, entry.finishedMs - entry.startedMs) : null),
    usage: null,
    liveUsage: null,
    costUsd: null,
    error: entry.status === "failed" ? firstLine(entry.summary, 300) || null : null,
  };
}

/** "23.4k tokens" — a count the way a dense line says it. */
export function shortCount(value: number): string {
  if (value < 1000) return String(value);
  if (value < 100_000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  if (value < 1_000_000) return `${Math.round(value / 1000)}k`;
  return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/** A sub-agent the CLI filed on its own (a Codex rollout), as `/sessions/{id}/subagents` returns it. */
export interface DiscoveredAgent {
  thread_id: string;
  parent_thread_id: string;
  nickname: string;
  role: string;
  path: string;
  started_ms: number;
  updated_ms: number;
  status: string;
  summary: string;
  events: AgentChatEvent[];
}

/** The sub-agents a thread's CLI filed on its own; empty for a CLI that streams them. */
export async function fetchDiscoveredAgents(sessionId: string): Promise<DiscoveredAgent[]> {
  const res = await fetch(`/api/agent-chat/sessions/${encodeURIComponent(sessionId)}/subagents`);
  if (!res.ok) throw new Error(`sub-agents unavailable (${res.status})`);
  const body = (await res.json()) as { agents?: DiscoveredAgent[] };
  return Array.isArray(body.agents) ? body.agents : [];
}

function discoveredStatus(raw: string): SubagentStatus {
  return raw === "done" || raw === "failed" || raw === "stopped" ? raw : "running";
}

/** "/root/grok_research" → "grok research": the task name Codex gave the agent. */
function pathTitle(path: string): string {
  const tail = path.split("/").filter(Boolean).pop() ?? "";
  return tail.replace(/[_-]+/g, " ").trim();
}

/** A discovered agent's own steps, folded by the same reducer as the thread. */
function discoveredBlocks(agent: DiscoveredAgent): TurnBlock[] {
  const scratch = "discovered";
  const events: AgentChatEvent[] = [
    { seq: 0, ts_ms: agent.started_ms, kind: "turn_started", payload: { turn_id: scratch } } as AgentChatEvent,
    ...agent.events.map((event) => ({ ...event, seq: 0, payload: { ...event.payload, turn_id: scratch } })),
  ];
  const turn = reduceEvents(EMPTY_TIMELINE, events).items.find((item): item is TurnItem => item.type === "turn");
  return turn?.blocks ?? [];
}

function findThread(blocks: TurnBlock[], threadId: string): boolean {
  return blocks.some((b) => b.kind === "tool" && (b.subagent?.threadId === threadId || (b.subagent ? findThread(b.subagent.blocks, threadId) : false)));
}

/** `blocks` with `fn` applied to the spawn block of `threadId`, wherever it sits. */
function patchThread(blocks: TurnBlock[], threadId: string, fn: (b: ToolBlock) => ToolBlock): TurnBlock[] {
  let changed = false;
  const next = blocks.map((b) => {
    if (b.kind !== "tool" || !b.subagent) return b;
    if (b.subagent.threadId === threadId) { changed = true; return fn(b); }
    const inner = patchThread(b.subagent.blocks, threadId, fn);
    if (inner === b.subagent.blocks) return b;
    changed = true;
    return { ...b, subagent: { ...b.subagent, blocks: inner } };
  });
  return changed ? next : blocks;
}

/** Where a new card goes: before the first call made after it started, else before the closing answer. */
function insertAt(blocks: TurnBlock[], startedMs: number, finished: boolean): number {
  const later = blocks.findIndex((b) => b.kind === "tool" && b.startedMs > startedMs);
  if (later >= 0) return later;
  if (!finished) return blocks.length;
  let at = blocks.length;
  while (at > 0 && blocks[at - 1].kind === "text") at -= 1;
  return at;
}

/**
 * The thread with the sub-agents its CLI filed on its own folded in: a known
 * one (its spawn call named its thread) gains its steps and its end; an
 * unknown one gets a card of its own in the turn it ran in, or inside the
 * sub-agent that spawned it.
 */
export function mergeDiscovered(items: TimelineItem[], agents: readonly DiscoveredAgent[], rootThread: string): TimelineItem[] {
  if (!agents.length) return items;
  let next = items;
  for (const agent of agents) {
    const blocks = discoveredBlocks(agent);
    const status = discoveredStatus(agent.status);
    const turnIndex = next.findIndex((item) => item.type === "turn" && findThread(item.blocks, agent.thread_id));
    if (turnIndex >= 0) {
      const turn = next[turnIndex] as TurnItem;
      const patched = patchThread(turn.blocks, agent.thread_id, (b) => {
        const sub = b.subagent!;
        return {
          ...b,
          subagent: {
            ...sub,
            blocks: sub.blocks.length ? sub.blocks : blocks,
            summary: sub.summary || agent.summary,
            status: sub.status === "running" && status !== "running" ? status : sub.status,
          },
        };
      });
      next = next.map((item, index) => (index === turnIndex ? { ...turn, blocks: patched } : item));
      continue;
    }
    const spawn: ToolBlock = {
      kind: "tool", callId: `codex:${agent.thread_id}`, name: "spawn_agent", input: null, output: null,
      isError: false, durationMs: null, approval: null, startedMs: agent.started_ms,
      subagent: {
        description: pathTitle(agent.path) || agent.nickname, agentType: agent.nickname || (agent.role !== "default" ? agent.role : ""),
        prompt: "", background: true, threadId: agent.thread_id, status, summary: agent.summary,
        activity: "", lastTool: "", tokens: null, toolUses: null, durationMs: null,
        startedMs: agent.started_ms, finishedMs: status === "running" ? null : agent.updated_ms || null, blocks,
      },
    };
    if (agent.parent_thread_id && agent.parent_thread_id !== rootThread) {
      const parentIndex = next.findIndex((item) => item.type === "turn" && findThread(item.blocks, agent.parent_thread_id));
      if (parentIndex < 0) continue;
      const turn = next[parentIndex] as TurnItem;
      const patched = patchThread(turn.blocks, agent.parent_thread_id, (b) => ({
        ...b,
        subagent: { ...b.subagent!, blocks: [...b.subagent!.blocks, spawn] },
      }));
      next = next.map((item, index) => (index === parentIndex ? { ...turn, blocks: patched } : item));
      continue;
    }
    let turnAt = -1;
    next.forEach((item, index) => { if (item.type === "turn" && item.startedMs <= agent.started_ms) turnAt = index; });
    if (turnAt < 0) continue;
    const turn = next[turnAt] as TurnItem;
    const running = turn.status === "running";
    // A turn that ended took its sub-agents with it.
    const placed = !running && status === "running"
      ? { ...spawn, subagent: { ...spawn.subagent!, status: "stopped" as const, finishedMs: agent.updated_ms || agent.started_ms } }
      : spawn;
    const at = insertAt(turn.blocks, agent.started_ms, !running);
    next = next.map((item, index) => (index === turnAt ? { ...turn, blocks: [...turn.blocks.slice(0, at), placed, ...turn.blocks.slice(at)] } : item));
  }
  return next;
}
