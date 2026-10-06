import type { AgentChatEvent, InternalMessage } from "@/lib/agentChatApi";
import type { ChatControlState } from "@/lib/chatControlApi";
import { hideAskBlocks } from "./askFence";
import { hideMeetingPass } from "./meetingPass";
import { readToolChoices, type ToolChoice } from "./toolChoices";

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
  /** What the reply shows: the agent's text without an end-of-turn question block. */
  text: string;
  /** The text as the agent wrote it, kept only when a question block was hidden from it. */
  raw?: string;
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
  /** The model message the thought belongs to, when the runner names one. */
  messageId?: string;
}

export interface ApprovalState {
  approvalId: string;
  summary: string;
  /** Set once the person (or a cancel) decided. */
  decision: string | null;
}

/** One prepared answer on an agent's question card. */
export interface QuestionOption {
  label: string;
  description: string;
}

/** One question of a card. Option 0 is always the agent's recommendation. */
export interface QuestionItem {
  question: string;
  options: QuestionOption[];
  recommendationReason: string;
}

/**
 * What one question resolved to. `source`: `person`, `timeout` (nobody
 * answered for five minutes), `skipped` (the card was closed), `cancelled`,
 * or `closed` (the turn ended without an answer).
 */
export interface QuestionAnswerState {
  text: string;
  optionIndex: number | null;
  source: string;
}

/**
 * An agent's question card (jarvis/agent_chat/questions.py): a short series
 * the person answers one by one. Open questions take their recommendation
 * when nobody answers before `expiresMs`, which every answer pushes back.
 */
export interface QuestionState {
  questionId: string;
  /** The agent asking, for the card's title; empty on older events. */
  asker: string;
  questions: QuestionItem[];
  /** One slot per question; `null` while unanswered. */
  answers: (QuestionAnswerState | null)[];
  expiresMs: number | null;
  /** The card no longer takes answers (resolved, or the turn ended). */
  closed: boolean;
  /**
   * An end-of-turn card (jarvis/agent_chat/turn_prompts.py): the agent already
   * stopped, and the answers go to it as the next message. It stays open after
   * its turn ended and closes when the person starts another turn instead.
   */
  deferred?: boolean;
}

/** The tool an agent asks its question with — bare or behind an MCP prefix. */
export const QUESTION_TOOL = "society_ask_user";
/** Claude Code's own question tool, answered on the same card. */
export const CLAUDE_QUESTION_TOOL = "AskUserQuestion";

export function isQuestionTool(name: string): boolean {
  return name === QUESTION_TOOL || name.endsWith(`__${QUESTION_TOOL}`) || name === CLAUDE_QUESTION_TOOL;
}

/**
 * A coding agent's plan card (jarvis/agent_chat/turn_prompts.py): the turn
 * finished in plan mode. `decision` is `build`, `keep`, `superseded` (the
 * person started another turn instead) or `null` while it waits.
 */
export interface PlanState {
  buildMode: string;
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
  /** The question card this call shows, when the call is an agent's question. */
  question?: QuestionState;
  /** When the call was made; a result without its own duration is timed from here. */
  startedMs: number;
  /** The sub-agent this call spawned, when the runner reported one (`subagent_*` events). */
  subagent?: SubagentState;
}

/** How a sub-agent stands: still working, or how it ended. */
export type SubagentStatus = "running" | "done" | "failed" | "stopped";

/**
 * A sub-agent a coding agent spawned, filed on the call that spawned it. Its
 * own conversation — text, thoughts, calls, its own sub-agents — folds into
 * `blocks` from the events that name it (`agent_id`), never into the turn.
 */
export interface SubagentState {
  /** The short task title ("Explore the auth code"). */
  description: string;
  /** The vendor's agent kind ("general-purpose", "Explore"); "" when it names none. */
  agentType: string;
  /** The whole task the main agent gave it. */
  prompt: string;
  /** It runs beside the main agent; the spawn call's own result is only a receipt. */
  background: boolean;
  /** The vendor's thread id for the agent, when it has one (Codex). */
  threadId: string;
  status: SubagentStatus;
  /** Its answer, as the vendor reported it at the end. */
  summary: string;
  /** What it is doing now ("Running the tests"). */
  activity: string;
  lastTool: string;
  tokens: number | null;
  toolUses: number | null;
  durationMs: number | null;
  startedMs: number;
  finishedMs: number | null;
  blocks: TurnBlock[];
}

export type TurnBlock = TextBlock | ReasoningBlock | ToolBlock;

export type TurnStatus = "running" | "done" | "cancelled" | "error";

export interface UserItem {
  origin?: "control";
  type: "user";
  id: string;
  /**
   * What the person WROTE.
   *
   * Not always what the turn received: a message with attached files carries
   * their contents too (the backend composes it — jarvis/agent_chat/
   * attachments.py), and showing a page of extracted PDF back to the person
   * who typed one sentence would bury their own words. The full prompt stays
   * in the event log, which is what the API runner rebuilds history from.
   */
  text: string;
  /** Files that went in with this message; empty on an ordinary one. */
  attachments: UserAttachment[];
  toolChoices?: ToolChoice[];
  /** A Jarvis agent wrote this message into a coding thread on the person's behalf. */
  author?: { agentId: string; name: string };
  tsMs: number;
}

/** One file's receipt in the timeline — enough to say what was sent, no more. */
export interface UserAttachment {
  name: string;
  kind: string;
  /** `vision` / `extraction` / `none` — whether the model could read it. */
  describedBy: string;
  /**
   * Where the file itself can be fetched from, same-origin — set for an image
   * that lives inside an open workspace (a drop the Agentic IDE stored; the
   * backend folds it in, `jarvis/agentic_ide/prompt_receipts.py`). The
   * timeline draws the picture when it has this and a chip when it does not.
   */
  url?: string;
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
  /** The plan card of a turn that finished in plan mode. */
  plan?: PlanState;
}

export interface ErrorItem {
  type: "error";
  id: string;
  text: string;
  tsMs: number;
}

/**
 * A system line that is not a turn: the agent society posts a delegated
 * task's result here ("Gmail agent is done: …"), a learned skill, a login
 * request. Stored server-side as a `notice` event, so a reopened chat still
 * shows it.
 */
export interface NoticeItem {
  type: "notice";
  id: string;
  /** The server's notice kind — "society_result", "learned_skill", … */
  kind: string;
  text: string;
  agentName: string;
  agentId: string;
  /** "done" | "blocked" | "" for notices that carry no outcome. */
  status: string;
  tsMs: number;
  /** The raw notice payload — a proposal card reads its kind, summary, reason, payload. */
  data: Record<string, unknown>;
  /** A proposal's outcome once the person decided: "applied" | "rejected" | "failed" | "". */
  resolved: string;
}

export interface InternalMessageItem {
  type: "internal";
  id: string;
  message: InternalMessage;
  tsMs: number;
  /** Board-backed outbound receipt; it is never input to the receiving model. */
  outgoing?: { recipientId: string; recipientName: string };
}

export type TimelineItem = UserItem | TurnItem | ErrorItem | NoticeItem | InternalMessageItem;

export interface PendingApproval {
  approvalId: string;
  turnId: string;
  callId: string;
  name: string;
  input: unknown;
  summary: string;
}

export interface Timeline {
  control?: ChatControlState;
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

/** The attachment receipts off one `user_message`, tolerant of any shape. */
/** Who wrote a message on the person's behalf (`author` on `user_message`), if anyone. */
function messageAuthor(raw: unknown): { agentId: string; name: string } | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const row = raw as Record<string, unknown>;
  const name = typeof row.name === "string" ? row.name : "";
  return name ? { agentId: typeof row.agent_id === "string" ? row.agent_id : "", name } : undefined;
}

function userAttachments(raw: unknown): UserAttachment[] {
  if (!Array.isArray(raw)) return [];
  const out: UserAttachment[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const row = entry as Record<string, unknown>;
    const name = str(row.name);
    if (name) {
      const url = str(row.url);
      out.push({
        name,
        kind: str(row.kind),
        describedBy: str(row.described_by),
        ...(url ? { url } : {}),
      });
    }
  }
  return out;
}

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

function updateTurn(tl: Timeline, turnId: string, fn: (turn: TurnItem) => TurnItem): Timeline {
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

function questionOptions(raw: unknown): QuestionOption[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const row = entry as Record<string, unknown>;
    const label = str(row.label);
    return label ? [{ label, description: str(row.description) }] : [];
  });
}

function questionAnswer(raw: unknown): QuestionAnswerState | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  return { text: str(row.answer), optionIndex: num(row.option_index), source: str(row.source, "person") };
}

/** The answer slots off a progress/resolved payload; older events carried one flat answer. */
function questionAnswers(p: Record<string, unknown>, count: number): (QuestionAnswerState | null)[] {
  const raw = Array.isArray(p.answers) ? p.answers : "answer" in p ? [p] : [];
  return Array.from({ length: count }, (_, i) => questionAnswer(raw[i]));
}

function updateQuestion(
  tl: Timeline,
  turnId: string,
  questionId: string,
  fn: (q: QuestionState) => QuestionState,
): Timeline {
  return updateTurn(tl, turnId, (turn) => {
    const i = turn.blocks.findIndex((b) => b.kind === "tool" && b.question?.questionId === questionId);
    if (i < 0) return turn;
    const block = turn.blocks[i] as ToolBlock;
    return { ...turn, blocks: replaceAt(turn.blocks, i, { ...block, question: fn(block.question!) }) };
  });
}

/** Attach a question to its tool row: the open ask call, else a row of its own. */
/** A text block whose question block, if any, is hidden behind its card. */
function textBlock(id: string, raw: string): TextBlock {
  const text = hideMeetingPass(hideAskBlocks(raw));
  return text === raw ? { kind: "text", id, text } : { kind: "text", id, text, raw };
}

/**
 * A new turn settles the cards the last one left waiting: an end-of-turn
 * question nobody answered and a plan nobody approved. The person moved on —
 * the server no longer takes answers for them either.
 */
function settleWaitingCards(items: TimelineItem[]): TimelineItem[] {
  let changed = false;
  const next = items.map((item) => {
    if (item.type !== "turn") return item;
    const openPlan = item.plan && item.plan.decision === null;
    const openAsk = item.blocks.some((b) => b.kind === "tool" && b.question?.deferred && !b.question.closed);
    if (!openPlan && !openAsk) return item;
    changed = true;
    return {
      ...item,
      ...(openPlan && item.plan ? { plan: { ...item.plan, decision: "superseded" } } : {}),
      blocks: openAsk
        ? item.blocks.map((b) =>
          b.kind === "tool" && b.question?.deferred && !b.question.closed
            ? {
              ...b,
              question: {
                ...b.question,
                closed: true,
                answers: b.question.answers.map((a) => a ?? { text: "", optionIndex: null, source: "closed" }),
              },
            }
            : b)
        : item.blocks,
    };
  });
  return changed ? next : items;
}

function withQuestion(turn: TurnItem, question: QuestionState, tsMs: number): TurnItem {
  let index = -1;
  for (let i = turn.blocks.length - 1; i >= 0; i -= 1) {
    const b = turn.blocks[i];
    if (b.kind !== "tool") continue;
    if (b.question?.questionId === question.questionId) return turn;
    if (isQuestionTool(b.name) && !b.question) {
      index = i;
      break;
    }
  }
  if (index >= 0) {
    const block = turn.blocks[index] as ToolBlock;
    return { ...turn, blocks: replaceAt(turn.blocks, index, { ...block, question }) };
  }
  const closed = closeLiveReasoning(turn, tsMs);
  return {
    ...closed,
    blocks: [
      ...closed.blocks,
      {
        kind: "tool",
        callId: `q-${question.questionId}`,
        name: QUESTION_TOOL,
        input: null,
        output: null,
        isError: false,
        durationMs: null,
        approval: null,
        question,
        startedMs: tsMs,
      },
    ],
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

function emptySubagent(startedMs: number): SubagentState {
  return {
    description: "",
    agentType: "",
    prompt: "",
    background: false,
    threadId: "",
    status: "running",
    summary: "",
    activity: "",
    lastTool: "",
    tokens: null,
    toolUses: null,
    durationMs: null,
    startedMs,
    finishedMs: null,
    blocks: [],
  };
}

/**
 * `blocks` with the first tool block `match` picks — at the top or inside
 * any sub-agent's conversation — replaced by `fn(block)`. Null when none matches.
 */
function mapToolDeep(
  blocks: TurnBlock[],
  match: (b: ToolBlock) => boolean,
  fn: (b: ToolBlock) => ToolBlock,
): TurnBlock[] | null {
  for (let i = 0; i < blocks.length; i += 1) {
    const b = blocks[i];
    if (b.kind !== "tool") continue;
    if (match(b)) {
      const next = fn(b);
      return next === b ? blocks : replaceAt(blocks, i, next);
    }
    if (b.subagent) {
      const inner = mapToolDeep(b.subagent.blocks, match, fn);
      if (inner) {
        return inner === b.subagent.blocks ? blocks : replaceAt(blocks, i, { ...b, subagent: { ...b.subagent, blocks: inner } });
      }
    }
  }
  return null;
}

/** Update the sub-agent `agentId` spawned; a spawn call the stream never announced gets a row of its own. */
function updateSubagent(
  turn: TurnItem,
  agentId: string,
  tsMs: number,
  fn: (sub: SubagentState) => SubagentState,
): TurnItem {
  const apply = (b: ToolBlock): ToolBlock => {
    const sub = b.subagent ?? emptySubagent(b.startedMs);
    const next = fn(sub);
    return next === sub && b.subagent ? b : { ...b, subagent: next };
  };
  const blocks = mapToolDeep(turn.blocks, (b) => b.callId === agentId, apply);
  if (blocks) return blocks === turn.blocks ? turn : { ...turn, blocks };
  const spawn: ToolBlock = {
    kind: "tool", callId: agentId, name: "Agent", input: null, output: null,
    isError: false, durationMs: null, approval: null, startedMs: tsMs,
  };
  return { ...turn, blocks: [...turn.blocks, apply(spawn)] };
}

/**
 * Fold a block-level step into a sub-agent's own conversation: the same step
 * the turn would take, taken on the sub-agent's blocks.
 */
function updateHost(tl: Timeline, turnId: string, agentId: string, tsMs: number, fn: (turn: TurnItem) => TurnItem): Timeline {
  if (!agentId) return updateTurn(tl, turnId, fn);
  return updateTurn(tl, turnId, (turn) =>
    updateSubagent(turn, agentId, tsMs, (sub) => {
      const host: TurnItem = { ...turn, blocks: sub.blocks };
      const next = fn(host);
      return next === host || next.blocks === sub.blocks ? sub : { ...sub, blocks: next.blocks };
    }),
  );
}

function closeLiveBlocks(blocks: TurnBlock[], nowMs: number): TurnBlock[] {
  return blocks.some((b) => b.kind === "reasoning" && b.live)
    ? blocks.map((b) => b.kind === "reasoning" && b.live ? { ...b, live: false, durationMs: b.durationMs ?? Math.max(0, nowMs - b.startedMs) } : b)
    : blocks;
}

function finishSubagent(sub: SubagentState, status: SubagentStatus, nowMs: number, summary = ""): SubagentState {
  return {
    ...sub,
    status,
    summary: summary || sub.summary,
    activity: "",
    finishedMs: sub.finishedMs ?? nowMs,
    durationMs: sub.durationMs ?? Math.max(0, nowMs - sub.startedMs),
    blocks: closeLiveBlocks(sub.blocks, nowMs),
  };
}

/**
 * A turn's end settles every sub-agent still marked working, however deep: a
 * foreground one by its call's result, a background one that never reported
 * its end as stopped — its process is gone.
 */
function settleSubagents(blocks: TurnBlock[], nowMs: number): TurnBlock[] {
  let changed = false;
  const next = blocks.map((b) => {
    if (b.kind !== "tool" || !b.subagent) return b;
    const inner = settleSubagents(b.subagent.blocks, nowMs);
    let sub = inner === b.subagent.blocks ? b.subagent : { ...b.subagent, blocks: inner };
    if (sub.status === "running") {
      const answered = !sub.background && b.output !== null;
      sub = finishSubagent(sub, answered ? (b.isError ? "failed" : "done") : "stopped", nowMs, answered ? b.output ?? "" : "");
    }
    if (sub === b.subagent) return b;
    changed = true;
    return { ...b, subagent: sub };
  });
  return changed ? next : blocks;
}

const SUBAGENT_STATUSES: readonly SubagentStatus[] = ["running", "done", "failed", "stopped"];

/** Fold one event. Pure; returns `tl` itself when nothing changed. */
export function reduceEvent(tl: Timeline, ev: AgentChatEvent): Timeline {
  const p = ev.payload ?? {};
  const seq = typeof ev.seq === "number" ? ev.seq : 0;
  const base: Timeline =
    seq > tl.lastSeq ? { ...tl, lastSeq: seq, sessionPatch: null } : tl.sessionPatch ? { ...tl, sessionPatch: null } : tl;
  const turnId = str(p.turn_id);
  // A sub-agent's own step names the agent; it folds into that agent's conversation.
  const agentId = str(p.agent_id);

  switch (ev.kind) {
    case "agent_message": {
      const id = str(p.message_id);
      if (!id || base.items.some((item) => item.type === "internal" && item.id === id)) return base;
      return { ...base, items: [...base.items, {
        type: "internal", id, tsMs: ev.ts_ms,
        message: {
          message_id: id, sender_id: str(p.sender_id), sender_name: str(p.sender_name),
          sender_kind: p.sender_kind === "jarvis" ? "jarvis" : p.sender_kind === "user" ? "user" : "agent",
          text: str(p.text), prompt: str(p.prompt), trace_id: str(p.trace_id),
          status: p.status === "delivered" || p.status === "failed" ? p.status : "queued",
          turn_id: str(p.turn_id), error: str(p.error),
        },
      }] };
    }
    case "agent_message_status":
      return { ...base, items: base.items.map((item) =>
        item.type === "internal" && item.id === str(p.message_id)
          ? { ...item, message: { ...item.message,
              status: p.status === "delivered" || p.status === "failed" ? p.status : "queued",
              turn_id: str(p.turn_id), error: str(p.error),
            } }
          : item,
      ) };

    case "user_message":
      return {
        ...base,
        items: [
          ...base.items,
          {
            type: "user",
            id: `u-${seq || ev.ts_ms}`,
            ...(p.origin === "control" ? { origin: "control" as const } : {}),
            ...(messageAuthor(p.author) ? { author: messageAuthor(p.author) } : {}),
            // `typed` is present only when the message carried files, and it
            // is the person's own sentence; `text` is the composed prompt.
            text: str(p.typed) || str(p.text),
            attachments: userAttachments(p.attachments),
            toolChoices: readToolChoices(p.tool_choices),
            tsMs: ev.ts_ms,
          },
        ],
      };

    case "turn_started":
      return {
        ...base,
        items: [
          ...settleWaitingCards(base.items),
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
      return updateHost(base, turnId, agentId, ev.ts_ms, (turn) =>
        upsertBlock<TextBlock>(
          closeLiveReasoning(turn, ev.ts_ms),
          (b) => b.kind === "text" && b.id === id,
          (ex) => textBlock(id, (ex?.raw ?? ex?.text ?? "") + delta),
        ),
      );
    }

    case "assistant_text": {
      const id = str(p.message_id, "live");
      const text = str(p.text);
      return updateHost(base, turnId, agentId, ev.ts_ms, (turn) =>
        upsertBlock<TextBlock>(
          closeLiveReasoning(turn, ev.ts_ms),
          (b) => b.kind === "text" && b.id === id,
          (ex) => (ex && (ex.raw ?? ex.text) === text ? ex : textBlock(id, text)),
        ),
      );
    }

    case "reasoning_started":
      // The model began to think. Its thinking may never stream (Claude Code
      // redacts it), so this is what the person sees meanwhile: one live
      // row, "Thinking…", counting the seconds until the finished block.
      return updateHost(base, turnId, agentId, ev.ts_ms, (turn) => {
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
      return updateHost(base, turnId, agentId, ev.ts_ms, (turn) => {
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
      const messageId = str(p.message_id) || undefined;
      return updateHost(base, turnId, agentId, ev.ts_ms, (turn) => {
        const last = turn.blocks[turn.blocks.length - 1];
        if (last && last.kind === "reasoning" && last.live) {
          return {
            ...turn,
            blocks: replaceAt(turn.blocks, turn.blocks.length - 1, {
              ...last,
              text: text || last.text,
              durationMs: durationMs ?? Math.max(0, ev.ts_ms - last.startedMs),
              live: false,
              ...(messageId ? { messageId } : {}),
            }),
          };
        }
        // The CLI sends a message's thinking blocks one by one, each event
        // carrying everything that message thought so far. A later block of
        // the same message replaces the finished one instead of repeating it.
        if (messageId && last && last.kind === "reasoning" && last.messageId === messageId) {
          return {
            ...turn,
            blocks: replaceAt(turn.blocks, turn.blocks.length - 1, {
              ...last,
              text: text || last.text,
              durationMs: durationMs ?? last.durationMs,
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
              ...(messageId ? { messageId } : {}),
            },
          ],
        };
      });
    }

    case "tool_call": {
      const callId = str(p.call_id);
      return updateHost(base, turnId, agentId, ev.ts_ms, (turn) =>
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
      const answered = updateHost(base, turnId, agentId, ev.ts_ms, (turn) =>
        upsertBlock<ToolBlock>(
          turn,
          (b) => b.kind === "tool" && b.callId === callId,
          (ex) => ({
            ...(ex?.question ? { question: ex.question } : {}),
            ...(ex?.subagent ? { subagent: ex.subagent } : {}),
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
      // A foreground sub-agent's answer IS its spawn call's result.
      return updateTurn(answered, turnId, (turn) => {
        const blocks = mapToolDeep(
          turn.blocks,
          (b) => b.callId === callId && b.subagent?.status === "running" && !b.subagent.background,
          (b) => ({ ...b, subagent: finishSubagent(b.subagent!, b.isError ? "failed" : "done", ev.ts_ms, b.output ?? "") }),
        );
        return blocks && blocks !== turn.blocks ? { ...turn, blocks } : turn;
      });
    }

    case "subagent_started": {
      if (!agentId) return base;
      return updateTurn(base, turnId, (turn) =>
        updateSubagent(turn, agentId, ev.ts_ms, (sub) => ({
          ...sub,
          description: str(p.description) || sub.description,
          agentType: str(p.agent_type) || sub.agentType,
          prompt: str(p.prompt) || sub.prompt,
          background: Boolean(p.background),
          threadId: str(p.thread_id) || sub.threadId,
        })),
      );
    }

    case "subagent_progress": {
      if (!agentId) return base;
      return updateTurn(base, turnId, (turn) =>
        updateSubagent(turn, agentId, ev.ts_ms, (sub) => sub.status !== "running" ? sub : {
          ...sub,
          activity: str(p.activity) || sub.activity,
          lastTool: str(p.last_tool) || sub.lastTool,
          tokens: num(p.tokens) ?? sub.tokens,
          toolUses: num(p.tool_uses) ?? sub.toolUses,
          durationMs: num(p.duration_ms) ?? sub.durationMs,
        }),
      );
    }

    case "subagent_finished": {
      if (!agentId) return base;
      const raw = str(p.status, "done") as SubagentStatus;
      const status = SUBAGENT_STATUSES.includes(raw) && raw !== "running" ? raw : "done";
      return updateTurn(base, turnId, (turn) =>
        updateSubagent(turn, agentId, ev.ts_ms, (sub) => {
          const ended = finishSubagent({ ...sub, durationMs: num(p.duration_ms) ?? sub.durationMs }, status, ev.ts_ms, str(p.summary));
          return {
            ...ended,
            // A later report (the notification after a status patch) may only add to the first.
            status: sub.status === "running" ? status : sub.status,
            tokens: num(p.tokens) ?? sub.tokens,
            toolUses: num(p.tool_uses) ?? sub.toolUses,
          };
        }),
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
      return updateTurn(base, turnId, (turn) => ({
        ...turn,
        liveUsage: counts,
      }));
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
      const withTurn = updateTurn(base, turnId, (turn) => {
        const approval = { approvalId, summary: pending.summary, decision: null };
        const nested = mapToolDeep(turn.blocks, (b) => b.callId === callId, (b) => ({ ...b, approval }));
        if (nested) return nested === turn.blocks ? turn : { ...turn, blocks: nested };
        return upsertBlock<ToolBlock>(
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
            approval,
            startedMs: ex?.startedMs ?? ev.ts_ms,
          }),
        );
      });
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
        const blocks = mapToolDeep(
          turn.blocks,
          (b) => b.approval?.approvalId === approvalId,
          (b) => ({ ...b, approval: b.approval ? { ...b.approval, decision } : null }),
        );
        return blocks && blocks !== turn.blocks ? { ...turn, blocks } : turn;
      });
      return {
        ...withTurn,
        pendingApprovals: withTurn.pendingApprovals.filter((a) => a.approvalId !== approvalId),
      };
    }

    case "question_required": {
      const questionId = str(p.question_id);
      if (!questionId) return base;
      // Older events carried a single question's fields at the top level.
      const raw = Array.isArray(p.questions) ? p.questions : [p];
      const questions: QuestionItem[] = raw.flatMap((entry) => {
        if (!entry || typeof entry !== "object") return [];
        const row = entry as Record<string, unknown>;
        const options = questionOptions(row.options);
        const text = str(row.question);
        return text && options.length ? [{ question: text, options, recommendationReason: str(row.recommendation_reason) }] : [];
      });
      if (!questions.length) return base;
      const question: QuestionState = {
        questionId,
        asker: str(p.asker),
        questions,
        answers: questions.map(() => null),
        expiresMs: num(p.expires_ms),
        closed: false,
        ...(p.deferred ? { deferred: true } : {}),
      };
      return updateTurn(base, turnId, (turn) => withQuestion(turn, question, ev.ts_ms));
    }

    case "question_progress": {
      return updateQuestion(base, turnId, str(p.question_id), (q) => ({
        ...q,
        answers: questionAnswers(p, q.questions.length).map((a, i) => a ?? q.answers[i]),
        expiresMs: num(p.expires_ms) ?? q.expiresMs,
      }));
    }

    case "question_resolved": {
      return updateQuestion(base, turnId, str(p.question_id), (q) => ({
        ...q,
        answers: questionAnswers(p, q.questions.length).map((a, i) => a ?? q.answers[i]),
        closed: true,
      }));
    }

    case "turn_finished": {
      const status = str(p.status, "done") as TurnStatus;
      const finished = updateTurn(base, turnId, (turn) => ({
        ...turn,
        status: status === "running" ? "done" : status,
        // A turn that ended mid-stream closes its live reasoning block, and
        // a question nobody can answer any more stops asking.
        blocks: settleSubagents(turn.blocks, ev.ts_ms).map((b) =>
          b.kind === "reasoning" && b.live
            ? {
                ...b,
                live: false,
                durationMs: b.durationMs ?? Math.max(0, ev.ts_ms - b.startedMs),
              }
            : b.kind === "tool" && b.question && !b.question.closed
              ? {
                  ...b,
                  question: {
                    ...b.question,
                    closed: true,
                    answers: b.question.answers.map((a) => a ?? { text: "", optionIndex: null, source: "closed" }),
                  },
                }
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

    case "plan_ready":
      return updateTurn(base, turnId, (turn) => ({
        ...turn,
        plan: { buildMode: str(p.build_mode), decision: null },
      }));

    case "plan_resolved":
      return updateTurn(base, turnId, (turn) =>
        turn.plan ? { ...turn, plan: { ...turn.plan, decision: str(p.decision) || "keep" } } : turn,
      );

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

    case "notice": {
      if (p.kind === "chat_control" && p.state && typeof p.state === "object") {
        return { ...base, control: p.state as ChatControlState };
      }
      const text = str(p.text);
      const kind = str(p.kind);
      if (!text && !kind) return base;
      if (kind === "message_dequeued") {
        // A waiting message started (or could not): its waiting line goes.
        const queueId = str(p.queue_id);
        const items = base.items.filter(
          (item) => !(item.type === "notice" && item.kind === "message_queued" && str(item.data.queue_id) === queueId),
        );
        if (str(p.status) !== "failed") return { ...base, items };
        const failed: NoticeItem = {
          type: "notice", id: `n-${seq || ev.ts_ms}`, kind, text, agentName: str(p.agent_name),
          agentId: str(p.agent_id), status: "failed", tsMs: ev.ts_ms,
          data: p as Record<string, unknown>, resolved: "",
        };
        return { ...base, items: [...items, failed] };
      }
      if (kind === "proposal_resolved") {
        // The outcome of a proposal patches the card it answers, never a second row.
        const proposalId = str(p.proposal_id);
        const index = base.items.findIndex(
          (item) => item.type === "notice" && item.kind === "proposal" && str(item.data.proposal_id) === proposalId,
        );
        if (index >= 0) {
          const card = base.items[index] as NoticeItem;
          return {
            ...base,
            items: replaceAt(base.items, index, {
              ...card,
              resolved: str(p.status) || "applied",
              status: str(p.status),
              text: text ? `${card.text}\n${text}` : card.text,
              // An applied identity carries what an undo restores.
              data: p.previous ? { ...card.data, previous: p.previous } : card.data,
            }),
          };
        }
      }
      return {
        ...base,
        items: [
          ...base.items,
          {
            type: "notice",
            id: `n-${seq || ev.ts_ms}`,
            kind,
            text,
            agentName: str(p.agent_name),
            agentId: str(p.agent_id),
            status: str(p.status),
            tsMs: ev.ts_ms,
            data: p as Record<string, unknown>,
            resolved: "",
          },
        ],
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
