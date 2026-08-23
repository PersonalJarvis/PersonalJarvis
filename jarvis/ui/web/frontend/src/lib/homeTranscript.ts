/**
 * The live transcript the voice stage shows: what was said to the assistant,
 * what it did on the way, and what it answered — as lines, newest last.
 *
 * Why its own reducer and not `messages`: a SPOKEN turn does not travel as
 * `MessageSent`. The pipeline publishes the heard words as `TranscriptFinal`
 * (and the last `TranscriptionUpdate` with `is_final`), and the answer as
 * `SpeechSpoken` — the authoritative "this was actually said out loud" track,
 * usually in sentence-sized pieces. Typed turns DO come as `MessageSent`.
 * The lane merges the two so it reads the same whether you spoke or typed.
 *
 * Steps (2026-08-23): the event store collects reasoning steps only for TYPED
 * turns (`chatThinking`), so a spoken "search my mail" showed the answer and
 * nothing of the tool call that produced it. Here every turn carries its own
 * steps as a third line kind, `who: "steps"`, sitting between the user's
 * words and the answer. The step model itself is `lib/thinkingSteps.ts` —
 * the same reducer the chat uses, so both stages read the same events the
 * same way; this module only decides WHICH turn an event belongs to and when
 * a turn is over.
 *
 * Turn boundaries, in order of evidence:
 *   - open on `BrainTurnStarted` or on the first tool / worker / computer-use
 *     event (the classic pipeline publishes BrainTurnStarted+Completed
 *     together AFTER the tools ran, so a tool event is often the first sign);
 *   - close on `BrainTurnCompleted`, `VoiceTurnCompleted`, a turn-ending
 *     `SpeechSpoken` (a reply, not a preamble or progress nudge), the next
 *     user utterance, the session going idle or back to listening after
 *     speech, or staleness — a turn with no event for STALE_TURN_MS is over
 *     whatever the backend forgot to say.
 * Steps that arrive shortly after a close (a late ActionExecuted, the
 * pipeline's BrainTurnStarted after its tools) still join that turn.
 *
 * Pure: (lines, event) → lines, and the same array back for every event it
 * does not read, so the store can skip the update.
 */

import {
  finalizeThinkingSteps,
  reduceThinkingSteps,
  type ThinkingStep,
} from "@/lib/thinkingSteps";

export type TranscriptWho = "user" | "assistant" | "steps";

interface TranscriptLineBase {
  id: string;
  ts: number;
  who: TranscriptWho;
  text: string;
}

/** Heard words (user) or a spoken / typed answer (assistant). */
export interface TranscriptTextLine extends TranscriptLineBase {
  who: "user" | "assistant";
  /**
   * The spoken kind (`SpeechSpoken.spoken_kind`) when the line was said out
   * loud — "reply", "preamble", … Pieces of one answer join only when their
   * kinds agree, so a "One moment" preamble never melts into the reply that
   * follows the tool call.
   */
  kind?: string;
}

/** The reasoning steps of one turn — tools, workers, the brain call. */
export interface TranscriptStepsLine extends TranscriptLineBase {
  who: "steps";
  /** Always empty; kept so every line has the same shape. */
  text: "";
  steps: ThinkingStep[];
  /** The turn is still running — steps may still arrive. */
  live: boolean;
  /** When the turn began (the user's words, or the first step). */
  startedTs: number;
  /** Last event that touched this turn; drives the late-attach window. */
  lastTs: number;
  /** Thinking time once the turn is closed. */
  durationMs?: number;
}

export type TranscriptLine = TranscriptTextLine | TranscriptStepsLine;

/** How many lines are kept. The lane shows the tail; this bounds memory. */
export const TRANSCRIPT_MAX = 40;
/** Consecutive spoken pieces of one answer are joined when this close. */
const JOIN_WITHIN_MS = 6_000;
/** A repeat of the same words within this window is the same utterance. */
const DUPLICATE_WITHIN_MS = 12_000;
/**
 * A step event arriving this soon after a turn closed still belongs to it.
 * The classic pipeline publishes BrainTurnStarted+Completed back to back
 * AFTER its tools, and a late ActionExecuted can trail the reply by a
 * moment; anything later than this is a new (tool-only) turn.
 */
export const LATE_ATTACH_MS = 15_000;
/**
 * A turn that saw no event for this long is closed the next time anything
 * arrives. Generous on purpose — the brain timeout is 60 s in the composer
 * and a desktop action can run longer — but finite, so a turn whose closing
 * event never came does not spin forever.
 */
export const STALE_TURN_MS = 120_000;
/**
 * The user's words are the honest start of a turn: the brain starts working
 * the moment they end. Only when they were said this recently, though —
 * a tool-only turn minutes later is not a reaction to them.
 */
const USER_TURN_WITHIN_MS = 60_000;

/**
 * Spoken kinds that happen INSIDE a turn and must not close it. Everything
 * else (reply, clarify, timeout, unavailable, action_done, …) is the
 * assistant's last word on the matter. Mirrors SPOKEN_KIND_* in
 * jarvis/sessions/constants.py.
 */
const MID_TURN_SPOKEN_KINDS: ReadonlySet<string> = new Set([
  "preamble",
  "progress",
  "backchannel",
  "announcement",
  "completion",
  "subagent",
]);

let counter = 0;
function nextId(ts: number): string {
  counter = (counter + 1) % 1_000_000;
  return `t${ts}-${counter}`;
}

function clean(text: unknown): string {
  return typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function isDuplicate(
  lines: TranscriptLine[],
  who: "user" | "assistant",
  text: string,
  ts: number,
): boolean {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    if (ts - line.ts > DUPLICATE_WITHIN_MS) return false;
    if (line.who === who && (line.text === text || line.text.endsWith(text))) return true;
  }
  return false;
}

function cap(lines: TranscriptLine[]): TranscriptLine[] {
  return lines.length > TRANSCRIPT_MAX ? lines.slice(lines.length - TRANSCRIPT_MAX) : lines;
}

function push(
  lines: TranscriptLine[],
  who: "user" | "assistant",
  text: string,
  ts: number,
  kind?: string,
): TranscriptLine[] {
  const line: TranscriptTextLine = { id: nextId(ts), ts, who, text };
  if (kind) line.kind = kind;
  return cap([...lines, line]);
}

/** Index of the last steps line, or -1. */
function lastStepsIndex(lines: TranscriptLine[]): number {
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].who === "steps") return i;
  }
  return -1;
}

/** True when no user line sits after index `i` — the turn is still current. */
function isCurrentTurn(lines: TranscriptLine[], i: number): boolean {
  for (let j = i + 1; j < lines.length; j++) {
    if (lines[j].who === "user") return false;
  }
  return true;
}

function replaceAt(lines: TranscriptLine[], i: number, line: TranscriptLine): TranscriptLine[] {
  const next = [...lines];
  next[i] = line;
  return next;
}

function closeTurn(line: TranscriptStepsLine, tsMs: number): TranscriptStepsLine {
  return {
    ...line,
    live: false,
    steps: finalizeThinkingSteps(line.steps, tsMs),
    lastTs: Math.max(line.lastTs, tsMs),
    durationMs: Math.max(0, tsMs - line.startedTs),
  };
}

/** Close the open turn, if any. Same array back when there is none. */
function closeOpenTurn(lines: TranscriptLine[], tsMs: number): TranscriptLine[] {
  const i = lastStepsIndex(lines);
  if (i === -1) return lines;
  const line = lines[i] as TranscriptStepsLine;
  if (!line.live) return lines;
  return replaceAt(lines, i, closeTurn(line, tsMs));
}

/**
 * Close a turn nobody closed: the open turn is older than STALE_TURN_MS.
 * Cheap — one look at the last steps line — so it runs on every event.
 */
function closeStaleTurn(lines: TranscriptLine[], tsMs: number): TranscriptLine[] {
  const i = lastStepsIndex(lines);
  if (i === -1) return lines;
  const line = lines[i] as TranscriptStepsLine;
  if (!line.live || tsMs - line.lastTs < STALE_TURN_MS) return lines;
  // Closed at the moment it went quiet, not now: the duration must not
  // count the two minutes nobody was working.
  return replaceAt(lines, i, closeTurn(line, line.lastTs));
}

/**
 * Where a step event goes: the open turn, or — within the late-attach
 * window and before any newer user utterance — the turn that just closed.
 */
function stepsTarget(lines: TranscriptLine[], tsMs: number): number {
  const i = lastStepsIndex(lines);
  if (i === -1) return -1;
  const line = lines[i] as TranscriptStepsLine;
  if (line.live) return i;
  if (isCurrentTurn(lines, i) && tsMs - line.lastTs <= LATE_ATTACH_MS) return i;
  return -1;
}

/** The start of a fresh turn: the user's recent words, else right now. */
function turnStart(lines: TranscriptLine[], tsMs: number): number {
  const last = lines[lines.length - 1];
  if (last && last.who === "user" && tsMs - last.ts <= USER_TURN_WITHIN_MS) return last.ts;
  return tsMs;
}

/**
 * The classic pipeline publishes BrainTurnStarted only once the brain call
 * has SUCCEEDED, immediately followed by BrainTurnCompleted — so the brain
 * step would always read "0 ms". Backdate it to the last thing we know the
 * turn was doing (the previous step, or the user's words): that span is the
 * time the brain was demonstrably busy. A realtime BrainTurnStarted arriving
 * mid-turn gets the same treatment, which is equally honest there.
 */
function backdateBrainStep(
  steps: ThinkingStep[],
  previous: ThinkingStep[],
  sinceTs: number,
): ThinkingStep[] {
  const last = steps[steps.length - 1];
  if (!last || last.kind !== "brain" || last.status !== "active") return steps;
  if (previous.length && previous[previous.length - 1] === last) return steps;
  if (last.startedTs <= sinceTs) return steps;
  const next = [...steps];
  next[next.length - 1] = { ...last, startedTs: sinceTs };
  return next;
}

function reduceSteps(
  lines: TranscriptLine[],
  name: string,
  payload: unknown,
  tsMs: number,
): TranscriptLine[] {
  const target = stepsTarget(lines, tsMs);
  if (target !== -1) {
    const line = lines[target] as TranscriptStepsLine;
    const reduced = reduceThinkingSteps(line.steps, name, payload, tsMs);
    if (!reduced) {
      // The event meant nothing to the steps — but a completion still ends
      // the turn (a realtime turn has no BrainTurnStarted, so its
      // BrainTurnCompleted finds no brain step to complete).
      if (name === "BrainTurnCompleted" && line.live) {
        return replaceAt(lines, target, closeTurn(line, tsMs));
      }
      return lines;
    }
    const sinceTs = line.steps.length
      ? Math.max(line.startedTs, line.lastTs)
      : line.startedTs;
    const steps =
      name === "BrainTurnStarted" ? backdateBrainStep(reduced, line.steps, sinceTs) : reduced;
    // A late ACTIVE step (a tool starting after the reply) reopens the turn;
    // the next closing event ends it again. A late finished step is just
    // one more row on a closed turn.
    const reopened = !line.live && steps.some((s) => s.status === "active");
    const updated: TranscriptStepsLine = {
      ...line,
      steps,
      lastTs: tsMs,
      live: line.live || reopened,
    };
    if (name === "BrainTurnCompleted") {
      return replaceAt(lines, target, closeTurn(updated, tsMs));
    }
    // No duration while it runs again — the old one would lie.
    if (reopened) delete updated.durationMs;
    return replaceAt(lines, target, updated);
  }

  // No turn to join: does this event open one?
  const opened = reduceThinkingSteps([], name, payload, tsMs);
  if (!opened || !opened.length) return lines;
  const startedTs = turnStart(lines, tsMs);
  const steps =
    name === "BrainTurnStarted" ? backdateBrainStep(opened, [], startedTs) : opened;
  const line: TranscriptStepsLine = {
    id: nextId(tsMs),
    ts: tsMs,
    who: "steps",
    text: "",
    steps,
    // A turn born from an already-finished step (ActionExecuted with no
    // Proposed before it) is not running — it is a record.
    live: steps.some((s) => s.status === "active"),
    startedTs,
    lastTs: tsMs,
  };
  if (!line.live) line.durationMs = Math.max(0, tsMs - startedTs);
  return cap([...lines, line]);
}

/**
 * A stored conversation as transcript lines — what the voice stage shows
 * when a past voice session is reopened WITHOUT leaving the voice surface
 * (sidebar recent chats, 2026-08-23): you pick up where you left off and
 * keep talking. The words survive (user / assistant) and, since the
 * backend keeps the reasoning trace next to a reply, so do the steps: a
 * reply that carries a replayed trace gets its steps line in front of it,
 * folded, exactly as the live turn showed them. UI-only roles (a preamble
 * bubble, a system note) are not part of the conversation. The reducer
 * appends live events after these lines as usual — their timestamps are
 * old, so no live piece ever joins or dedups against an archived line.
 */
export function transcriptFromMessages(
  messages: ReadonlyArray<{
    role: string;
    content: string;
    ts: number;
    trace?: { steps: ThinkingStep[]; durationMs: number };
  }>,
): TranscriptLine[] {
  const lines: TranscriptLine[] = [];
  for (const m of messages) {
    if (m.role !== "user" && m.role !== "assistant") continue;
    const text = clean(m.content);
    if (!text) continue;
    if (m.role === "assistant" && m.trace && m.trace.steps.length > 0) {
      const startedTs = Math.max(0, m.ts - m.trace.durationMs);
      lines.push({
        id: nextId(m.ts),
        ts: m.ts,
        who: "steps",
        text: "",
        steps: m.trace.steps,
        live: false,
        startedTs,
        lastTs: m.ts,
        durationMs: m.trace.durationMs,
      });
    }
    lines.push({ id: nextId(m.ts), ts: m.ts, who: m.role, text });
  }
  return cap(lines);
}

export function reduceTranscript(
  lines: TranscriptLine[],
  name: string,
  payload: unknown,
  tsMs: number,
): TranscriptLine[] {
  const p = (payload ?? {}) as Record<string, unknown>;
  lines = closeStaleTurn(lines, tsMs);

  switch (name) {
    case "TranscriptFinal": {
      const transcript = (p.transcript ?? {}) as Record<string, unknown>;
      const text = clean(transcript.text);
      if (!text || isDuplicate(lines, "user", text, tsMs)) return lines;
      return push(closeOpenTurn(lines, tsMs), "user", text, tsMs);
    }
    case "TranscriptionUpdate": {
      if (p.is_final !== true) return lines;
      const text = clean(p.text);
      if (!text || isDuplicate(lines, "user", text, tsMs)) return lines;
      return push(closeOpenTurn(lines, tsMs), "user", text, tsMs);
    }
    case "SpeechSpoken": {
      const text = clean(p.text);
      if (!text || isDuplicate(lines, "assistant", text, tsMs)) return lines;
      // The assistant's last word closes the turn; a preamble or a progress
      // nudge is said WHILE the turn runs and leaves it open.
      const kind = str(p.spoken_kind);
      const base = MID_TURN_SPOKEN_KINDS.has(kind) ? lines : closeOpenTurn(lines, tsMs);
      const last = base[base.length - 1];
      if (
        last &&
        last.who === "assistant" &&
        tsMs - last.ts < JOIN_WITHIN_MS &&
        (!last.kind || !kind || last.kind === kind)
      ) {
        // One answer, said in pieces: grow the line instead of stacking pieces.
        return replaceAt(base, base.length - 1, {
          ...last,
          ts: tsMs,
          text: `${last.text} ${text}`,
        });
      }
      return push(base, "assistant", text, tsMs, kind);
    }
    case "MessageSent": {
      const role = p.role;
      if (role !== "user" && role !== "assistant") return lines;
      const text = clean(p.text);
      if (!text || isDuplicate(lines, role, text, tsMs)) return lines;
      return push(role === "user" ? closeOpenTurn(lines, tsMs) : lines, role, text, tsMs);
    }
    case "VoiceTurnCompleted":
      return closeOpenTurn(lines, tsMs);
    case "SystemStateChanged": {
      // The session went idle, or the floor came back to the user after the
      // assistant spoke: whatever the turn was doing, it is over.
      const next = str(p.new_state).toLowerCase();
      const previous = str(p.previous).toLowerCase();
      if (next === "idle" || (next === "listening" && previous === "speaking")) {
        return closeOpenTurn(lines, tsMs);
      }
      return lines;
    }
    default:
      return reduceSteps(lines, name, payload, tsMs);
  }
}
