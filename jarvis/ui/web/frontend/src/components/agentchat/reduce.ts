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
  /** Empty when the vendor redacts its thinking — the block still says it happened. */
  text: string;
  durationMs: number | null;
  /** Still streaming — the finished block carries the duration. */
  live: boolean;
  /** When the model began to think (drives the live elapsed counter). */
  startedMs: number;
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
  /** When the call was made; a result without its own duration is timed from here. */
  startedMs: number;
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
  /** Tokens so far while the turn runs (``usage_delta``); the finished usage replaces it. */
  liveUsage: Record<string, number> | null;
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

/** A live reasoning block that text or a tool call is about to follow is over. */
function closeLiveReasoning(turn: TurnItem, nowMs: number): TurnItem {
  const i = turn.blocks.findIndex((b) => b.kind === "reasoning" && b.live);
  if (i < 0) return turn;
  const block = turn.blocks[i] as ReasoningBlock;
  return {
    ...turn,
    blocks: replaceAt(turn.blocks, i, {
      ...block,
      live: false,
      durationMs: block.durationMs ?? Math.max(0, nowMs - block.startedMs),
    }),
  };
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
            liveUsage: null,
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
          closeLiveReasoning(turn, ev.ts_ms),
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
          closeLiveReasoning(turn, ev.ts_ms),
          (b) => b.kind === "text" && b.id === id,
          (ex) => (ex && ex.text === text ? ex : { kind: "text", id, text }),
        ),
      );
    }

    case "reasoning_started":
      // The model began to think. Its thinking may never stream (Claude Code
      // redacts it), so this is what the person sees meanwhile: one live
      // row, "Thinking…", counting the seconds until the finished block.
      return updateTurn(base, turnId, (turn) => {
        const last = turn.blocks[turn.blocks.length - 1];
        if (last && last.kind === "reasoning" && last.live) return turn;
        return {
          ...turn,
          blocks: [
            ...turn.blocks,
            {
              kind: "reasoning",
              id: `r-${turn.blocks.length}`,
              text: "",
              durationMs: null,
              live: true,
              startedMs: ev.ts_ms,
            },
          ],
        };
      });

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
            {
              kind: "reasoning",
              id: `r-${turn.blocks.length}`,
              text: delta,
              durationMs: null,
              live: true,
              startedMs: ev.ts_ms,
            },
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
              durationMs: durationMs ?? Math.max(0, ev.ts_ms - last.startedMs),
              live: false,
            }),
          };
        }
        // A finished block with no text is still a fact worth a row when it
        // took time ("Thought for 8s") — redacted thinking is thinking too.
        if (!text && !(durationMs && durationMs > 0)) return turn;
        return {
          ...turn,
          blocks: [
            ...turn.blocks,
            {
              kind: "reasoning",
              id: `r-${turn.blocks.length}`,
              text,
              durationMs,
              live: false,
              startedMs: ev.ts_ms - (durationMs ?? 0),
            },
          ],
        };
      });
    }

    case "tool_call": {
      const callId = str(p.call_id);
      return updateTurn(base, turnId, (turn) =>
        upsertBlock<ToolBlock>(
          closeLiveReasoning(turn, ev.ts_ms),
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
              startedMs: ev.ts_ms,
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
            // The runner rarely knows how long a call took; the log does —
            // the result's timestamp minus the call's.
            durationMs:
              num(p.duration_ms) ?? (ex ? Math.max(0, ev.ts_ms - ex.startedMs) : null),
            approval: ex?.approval ?? null,
            startedMs: ex?.startedMs ?? ev.ts_ms,
          }),
        ),
      );
    }

    case "usage_delta": {
      const usage = p.usage;
      if (!usage || typeof usage !== "object") return base;
      const counts: Record<string, number> = {};
      for (const [k, v] of Object.entries(usage as Record<string, unknown>)) {
        const n = num(v);
        if (n !== null) counts[k] = n;
      }
      if (Object.keys(counts).length === 0) return base;
      return updateTurn(base, turnId, (turn) => ({ ...turn, liveUsage: counts }));
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
            startedMs: ex?.startedMs ?? ev.ts_ms,
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
        blocks: turn.blocks.map((b) =>
          b.kind === "reasoning" && b.live
            ? { ...b, live: false, durationMs: b.durationMs ?? Math.max(0, ev.ts_ms - b.startedMs) }
            : b,
        ),
        durationMs: num(p.duration_ms) ?? Math.max(0, ev.ts_ms - turn.startedMs),
        usage:
          p.usage && typeof p.usage === "object" && Object.keys(p.usage as object).length > 0
            ? (p.usage as Record<string, unknown>)
            : turn.liveUsage,
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
