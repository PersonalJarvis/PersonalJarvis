import type { AgentChatEvent } from "@/lib/agentChatApi";

/**
 * Fold the agent-chat event log into the timeline the column renders.
 *
 * The backend speaks ONE vocabulary for the persisted log and the live
 * stream (jarvis/agent_chat/events.py), so this reducer serves both: a
 * reopened session replays its stored events through it, and the socket
 * keeps feeding it live ones. The result is immutable per step — an event
 * that changes nothing returns the same object, so the store can skip the
 * re-render.
 *
 * Shape: a list of items, each either the person's message or one assistant
 * turn. A turn holds ordered blocks — text, reasoning, tool calls (with
 * their result and, when the runner asked, the approval card) — plus the
 * turn's status and the usage the runner reported at the end.
 */

export interface TextBlock {
  kind: "text";
  id: string;
  text: string;
}

export interface ReasoningBlock {
  kind: "reasoning";
  id: string;
  text: string;
  durationMs: number | null;
  /** Still streaming — the finished block carries the duration. */
  live: boolean;
}

export interface ApprovalState {
  approvalId: string;
  summary: string;
  /** Set once the person (or a cancel) decided. */
  decision: string | null;
}

export interface ToolBlock {
  kind: "tool";
  callId: string;
  name: string;
  input: unknown;
  output: string | null;
  isError: boolean;
  durationMs: number | null;
  approval: ApprovalState | null;
}

export type TurnBlock = TextBlock | ReasoningBlock | ToolBlock;

export type TurnStatus = "running" | "done" | "cancelled" | "error";

export interface UserItem {
  type: "user";
  id: string;
  text: string;
  tsMs: number;
}

export interface TurnItem {
  type: "turn";
  id: string;
  provider: string;
  model: string;
  effort: string;
  runner: string;
  status: TurnStatus;
  blocks: TurnBlock[];
  startedMs: number;
  durationMs: number | null;
  usage: Record<string, unknown> | null;
  costUsd: number | null;
  error: string | null;
}

export interface ErrorItem {
  type: "error";
  id: string;
  text: string;
  tsMs: number;
}

export type TimelineItem = UserItem | TurnItem | ErrorItem;

export interface PendingApproval {
  approvalId: string;
  turnId: string;
  callId: string;
  name: string;
  input: unknown;
  summary: string;
}

export interface Timeline {
  items: TimelineItem[];
  pendingApprovals: PendingApproval[];
  /** Highest persisted seq folded so far — the `?after=` for a reconnect. */
  lastSeq: number;
  /** Fields a `session_updated` event changed, applied by the store. */
  sessionPatch: Record<string, unknown> | null;
}

export const EMPTY_TIMELINE: Timeline = {
  items: [],
  pendingApprovals: [],
  lastSeq: 0,
  sessionPatch: null,
};

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function findTurn(items: TimelineItem[], turnId: string): number {
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const it = items[i];
    if (it.type === "turn" && it.id === turnId) return i;
  }
  return -1;
}

function replaceAt<T>(arr: T[], index: number, value: T): T[] {
  const next = arr.slice();
  next[index] = value;
  return next;
}

function updateTurn(
  tl: Timeline,
  turnId: string,
  fn: (turn: TurnItem) => TurnItem,
): Timeline {
  const idx = findTurn(tl.items, turnId);
  if (idx < 0) return tl;
  const turn = tl.items[idx] as TurnItem;
  const next = fn(turn);
  if (next === turn) return tl;
  return { ...tl, items: replaceAt(tl.items, idx, next) };
}

function upsertBlock<B extends TurnBlock>(
  turn: TurnItem,
  match: (b: TurnBlock) => boolean,
  make: (existing: B | null) => B,
): TurnItem {
  const i = turn.blocks.findIndex(match);
  if (i < 0) return { ...turn, blocks: [...turn.blocks, make(null)] };
  const existing = turn.blocks[i] as B;
  const next = make(existing);
  if (next === existing) return turn;
  return { ...turn, blocks: replaceAt(turn.blocks, i, next) };
}

/** Fold one event. Pure; returns `tl` itself when nothing changed. */
export function reduceEvent(tl: Timeline, ev: AgentChatEvent): Timeline {
  const p = ev.payload ?? {};
  const seq = typeof ev.seq === "number" ? ev.seq : 0;
  const base: Timeline =
    seq > tl.lastSeq ? { ...tl, lastSeq: seq, sessionPatch: null } : tl.sessionPatch ? { ...tl, sessionPatch: null } : tl;
  const turnId = str(p.turn_id);

  switch (ev.kind) {
    case "user_message":
      return {
        ...base,
        items: [
          ...base.items,
          { type: "user", id: `u-${seq || ev.ts_ms}`, text: str(p.text), tsMs: ev.ts_ms },
        ],
      };

    case "turn_started":
      return {
        ...base,
        items: [
          ...base.items,
          {
            type: "turn",
            id: turnId,
            provider: str(p.provider),
            model: str(p.model),
            effort: str(p.effort),
            runner: str(p.runner),
            status: "running",
            blocks: [],
            startedMs: ev.ts_ms,
            durationMs: null,
            usage: null,
            costUsd: null,
            error: null,
          },
        ],
      };

    case "text_delta": {
      const id = str(p.message_id, "live");
      const delta = str(p.text);
      if (!delta) return base;
      return updateTurn(base, turnId, (turn) =>
        upsertBlock<TextBlock>(
          turn,
          (b) => b.kind === "text" && b.id === id,
          (ex) => ({ kind: "text", id, text: (ex?.text ?? "") + delta }),
        ),
      );
    }

    case "assistant_text": {
      const id = str(p.message_id, "live");
      const text = str(p.text);
      return updateTurn(base, turnId, (turn) =>
        upsertBlock<TextBlock>(
          turn,
          (b) => b.kind === "text" && b.id === id,
          (ex) => (ex && ex.text === text ? ex : { kind: "text", id, text }),
        ),
      );
    }

    case "reasoning_delta": {
      const delta = str(p.text);
      if (!delta) return base;
      return updateTurn(base, turnId, (turn) => {
        // Deltas grow the newest live reasoning block; a finished one starts a new block.
        const last = turn.blocks[turn.blocks.length - 1];
        if (last && last.kind === "reasoning" && last.live) {
          return {
            ...turn,
            blocks: replaceAt(turn.blocks, turn.blocks.length - 1, {
              ...last,
              text: last.text + delta,
            }),
          };
        }
        return {
          ...turn,
          blocks: [
            ...turn.blocks,
            { kind: "reasoning", id: `r-${turn.blocks.length}`, text: delta, durationMs: null, live: true },
          ],
        };
      });
    }

    case "reasoning": {
      const text = str(p.text);
      const durationMs = num(p.duration_ms);
      return updateTurn(base, turnId, (turn) => {
        const last = turn.blocks[turn.blocks.length - 1];
        if (last && last.kind === "reasoning" && last.live) {
          return {
            ...turn,
            blocks: replaceAt(turn.blocks, turn.blocks.length - 1, {
              ...last,
              text: text || last.text,
              durationMs,
              live: false,
            }),
          };
        }
        if (!text) return turn;
        return {
          ...turn,
          blocks: [
            ...turn.blocks,
            { kind: "reasoning", id: `r-${turn.blocks.length}`, text, durationMs, live: false },
          ],
        };
      });
    }

    case "tool_call": {
      const callId = str(p.call_id);
      return updateTurn(base, turnId, (turn) =>
        upsertBlock<ToolBlock>(
          turn,
          (b) => b.kind === "tool" && b.callId === callId,
          (ex) =>
            ex ?? {
              kind: "tool",
              callId,
              name: str(p.name),
              input: p.input,
              output: null,
              isError: false,
              durationMs: null,
              approval: null,
            },
        ),
      );
    }

    case "tool_result": {
      const callId = str(p.call_id);
      return updateTurn(base, turnId, (turn) =>
        upsertBlock<ToolBlock>(
          turn,
          (b) => b.kind === "tool" && b.callId === callId,
          (ex) => ({
            kind: "tool",
            callId,
            name: ex?.name ?? str(p.name),
            input: ex?.input,
            output: str(p.output, ""),
            isError: Boolean(p.is_error),
            durationMs: num(p.duration_ms),
            approval: ex?.approval ?? null,
          }),
        ),
      );
    }

    case "approval_required": {
      const approvalId = str(p.approval_id);
      const callId = str(p.call_id);
      const pending: PendingApproval = {
        approvalId,
        turnId,
        callId,
        name: str(p.name),
        input: p.input,
        summary: str(p.summary),
      };
      const withTurn = updateTurn(base, turnId, (turn) =>
        upsertBlock<ToolBlock>(
          turn,
          (b) => b.kind === "tool" && b.callId === callId,
          (ex) => ({
            kind: "tool",
            callId,
            name: ex?.name ?? pending.name,
            input: ex?.input ?? pending.input,
            output: ex?.output ?? null,
            isError: ex?.isError ?? false,
            durationMs: ex?.durationMs ?? null,
            approval: { approvalId, summary: pending.summary, decision: null },
          }),
        ),
      );
      return {
        ...withTurn,
        pendingApprovals: [
          ...withTurn.pendingApprovals.filter((a) => a.approvalId !== approvalId),
          pending,
        ],
      };
    }

    case "approval_resolved": {
      const approvalId = str(p.approval_id);
      const decision = str(p.decision);
      const withTurn = updateTurn(base, turnId, (turn) => {
        const i = turn.blocks.findIndex(
          (b) => b.kind === "tool" && b.approval?.approvalId === approvalId,
        );
        if (i < 0) return turn;
        const block = turn.blocks[i] as ToolBlock;
        return {
          ...turn,
          blocks: replaceAt(turn.blocks, i, {
            ...block,
            approval: block.approval ? { ...block.approval, decision } : null,
          }),
        };
      });
      return {
        ...withTurn,
        pendingApprovals: withTurn.pendingApprovals.filter((a) => a.approvalId !== approvalId),
      };
    }

    case "turn_finished": {
      const status = str(p.status, "done") as TurnStatus;
      const finished = updateTurn(base, turnId, (turn) => ({
        ...turn,
        status: status === "running" ? "done" : status,
        // A turn that ended mid-stream closes its live reasoning block.
        blocks: turn.blocks.map((b) => (b.kind === "reasoning" && b.live ? { ...b, live: false } : b)),
        durationMs: num(p.duration_ms),
        usage: p.usage && typeof p.usage === "object" ? (p.usage as Record<string, unknown>) : null,
        costUsd: num(p.cost_usd),
        error: str(p.error, "") || null,
      }));
      return {
        ...finished,
        pendingApprovals: finished.pendingApprovals.filter((a) => a.turnId !== turnId),
      };
    }

    case "session_updated":
      return { ...base, sessionPatch: { ...p } };

    case "error": {
      const text = str(p.message);
      if (!text) return base;
      if (turnId && findTurn(base.items, turnId) >= 0) {
        return updateTurn(base, turnId, (turn) => ({ ...turn, error: text }));
      }
      return {
        ...base,
        items: [...base.items, { type: "error", id: `e-${seq || ev.ts_ms}`, text, tsMs: ev.ts_ms }],
      };
    }

    default:
      return base;
  }
}

export function reduceEvents(tl: Timeline, events: AgentChatEvent[]): Timeline {
  let cur = tl;
  for (const ev of events) cur = reduceEvent(cur, ev);
  return cur;
}

/** The turn still running, if any. */
export function runningTurn(tl: Timeline): TurnItem | null {
  for (let i = tl.items.length - 1; i >= 0; i -= 1) {
    const it = tl.items[i];
    if (it.type === "turn") return it.status === "running" ? it : null;
  }
  return null;
}
