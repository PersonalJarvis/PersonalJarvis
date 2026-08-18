/**
 * Mission-deck state model — the pure event→state mapping behind the deck's
 * cards (MissionDeckView.tsx).
 *
 * The backend forwards EVERY EventBus event over the WebSocket. This module
 * folds the handful the deck cares about into a small, plain state object:
 * what the brain has cost this session, what Computer-Use is doing right now,
 * whether a screen capture just happened (and the ones before it), the last
 * shell/CLI lines, recent wiki changes, how many words were spoken — and two
 * things built for the front page alone: the session LOG (one line per thing
 * the assistant heard, thought, did or said, with timings) and the current
 * TURN (which phase it is in and how long each stage took).
 *
 * Every figure here is REAL and sourced from a payload the backend already
 * publishes. Nothing is estimated: `BrainTurnCompleted` carries token counts
 * and cost, `CUStepProfiled` the phase, `ObservationCaptured` the frame hash,
 * `TranscriptFinal` the words. A card whose number cannot be sourced this way
 * does not get one.
 *
 * Pure module on purpose: no React, no zustand, no timers — covered by
 * deckState.test.ts (same pattern as thinkingSteps.ts / commandActivity.ts).
 */

export interface BrainUsageByModel {
  turns: number;
  tokensIn: number;
  tokensOut: number;
  costUsd: number;
}

export interface BrainUsage {
  turns: number;
  tokensIn: number;
  tokensOut: number;
  costUsd: number;
  lastProvider: string;
  lastModel: string;
  lastTurnTs: number | null;
  /** True when the last turn was answered from a prompt cache. */
  lastCacheHit: boolean;
  byModel: Record<string, BrainUsageByModel>;
}

export type CuPhase = "observe" | "uia" | "plan" | "think" | "act" | "verify" | "idle";

export interface CuState {
  active: boolean;
  missionId: string;
  phase: CuPhase;
  stepIdx: number;
  /** "click" | "type" | "hotkey" | "wait" | "verify" — raw from ActionPlanned. */
  lastActionKind: string;
  /** Raw target description ("{role:Button,name:Save}"), never translated. */
  lastTargetHint: string;
  lastActionOk: boolean | null;
  /** sha256 of the newest observation frame — served by /api/deck/cu-frame. */
  lastFrameSha: string | null;
  windowTitle: string;
  frames: number;
  startedTs: number | null;
  endedReason: string;
}

export interface CaptureState {
  /** Bumps on every ScreenCaptureCompleted — the card refetches on change. */
  seq: number;
  ts: number;
  targetKind: string;
  targetLabel: string;
  width: number;
  height: number;
  redactions: number;
}

export type TermLineKind = "cmd" | "out" | "err" | "cli" | "note";

export interface TermLine {
  id: string;
  ts: number;
  kind: TermLineKind;
  /** Raw runtime text (a command, one output line). Never translated. */
  text: string;
  /** terminal_id / cli name — lets the card group by source. */
  source: string;
}

export interface WikiChange {
  slug: string;
  kind: string;
  ts: number;
}

/**
 * One line of the session log — the deck's terminal.
 *
 * `kind` is the tag at the front of the line; `labelKey` the standing phrase
 * (an i18n key under "deck.log_*", resolved at render time so a language
 * switch re-labels the past too); `text` the raw runtime detail — a
 * transcript, a tool name, a model id — which is never translated. `ms` is a
 * measured duration and `ok === false` marks a failure. `open` is true while
 * the span the line stands for (a tool call, a brain turn, a worker) is still
 * running; the same line is closed in place when it finishes.
 */
export type JournalKind =
  | "boot"
  | "wake"
  | "hear"
  | "think"
  | "done"
  | "tool"
  | "say"
  | "worker"
  | "control"
  | "look"
  | "memory"
  | "error"
  | "note";

export interface JournalLine {
  id: string;
  ts: number;
  kind: JournalKind;
  labelKey?: string;
  /** Placeholders for `labelKey` ("{in}", "{out}", …), raw runtime values. */
  args?: Record<string, string>;
  text?: string;
  ms?: number;
  ok?: boolean;
  open?: boolean;
}

/**
 * The turn the assistant is in — or the last one it finished.
 *
 * Phases follow the voice loop: `hear` (mic open, transcript pending),
 * `think` (a brain turn is running), `act` (a tool, a worker or Computer-Use
 * is doing something on the brain's behalf), `speak` (the answer is being
 * said). `idle` means no turn is open; the figures then describe the LAST
 * turn, so the card always has something real to show after the first one.
 *
 * Every millisecond figure is measured from `anchorTs`, the moment the
 * request was complete (the utterance ended / the message was sent) — the
 * same anchor the backend's latency tracker uses, so "first audio 1.9 s"
 * here is the number the latency report would print.
 */
export type TurnPhase = "idle" | "hear" | "think" | "act" | "speak";

export interface TurnState {
  /** Turns opened this session; 0 before the first. */
  index: number;
  phase: TurnPhase;
  /** Opened by the voice loop (wake word, mic, transcript) — not typed. */
  voice: boolean;
  /** The last event that touched this turn — the card dims a turn gone quiet. */
  lastEventTs: number | null;
  /** When the turn became visible (wake word, mic opened, first event). */
  openedTs: number | null;
  /** When the request was complete — the clock and every offset run from here. */
  anchorTs: number | null;
  /** When the turn closed; null while it is open. */
  endedTs: number | null;
  provider: string;
  model: string;
  /** Words heard (final transcripts of this turn). */
  words: number;
  /** ms from anchor: the recogniser's final transcript. */
  sttMs: number | null;
  /** ms from anchor: the instant acknowledgment became audible. */
  ackMs: number | null;
  /** ms from anchor: the brain's first token. */
  ttftMs: number | null;
  /** ms from anchor: the answer's first audible audio. */
  firstAudioMs: number | null;
  /** How long the (last) brain attempt took, start to completed. */
  brainMs: number | null;
  /** Wall-clock start of the running brain attempt (internal). */
  brainStartedTs: number | null;
  /** BrainTurnStarted count — more than one means the fallback chain ran. */
  attempts: number;
  tools: number;
  toolsFailed: number;
  toolsOpen: number;
  workers: number;
  /** Computer-Use took the screen during this turn. */
  cu: boolean;
  tokensIn: number;
  tokensOut: number;
  costUsd: number;
  errors: number;
  /** True when the last brain attempt was answered from a prompt cache. */
  cacheHit: boolean;
}

export interface DeckState {
  usage: BrainUsage;
  cu: CuState;
  /** The newest capture — what the picture card shows. */
  capture: CaptureState | null;
  /** The captures of this session, newest first — the card's ledger. */
  captures: CaptureState[];
  termLines: TermLine[];
  wikiChanges: WikiChange[];
  /** The session log, oldest first. */
  journal: JournalLine[];
  turn: TurnState;
  /** Words in every final transcript this session. */
  wordsSession: number;
  /** Words in the most recent final transcript. */
  wordsLast: number;
  /** How many utterances were finalised. */
  utterances: number;
  /**
   * The most recent final transcript, verbatim, until the brain answered it.
   * A later final that carries it whole — a live snapshot that grew, or a
   * buffered fragment the pipeline merged — replaces its words instead of
   * adding to them.
   */
  heardText: string;
}

/** Hard caps — a long session must not grow any list unbounded. */
export const MAX_TERM_LINES = 80;
export const MAX_WIKI_CHANGES = 12;
export const MAX_JOURNAL_LINES = 160;
export const MAX_CAPTURES = 8;

/**
 * A soft signal (a brain start, a state change, a latency mark) arriving this
 * soon after a turn closed re-opens THAT turn rather than starting another:
 * the reply's speech and its chat message do not arrive in a fixed order, and
 * a turn must not be counted twice for that.
 */
export const TURN_REOPEN_GRACE_MS = 5_000;

/** How much of a transcript or reply the log keeps on one line. */
const LOG_TEXT_MAX = 120;

const CU_PHASES: readonly CuPhase[] = ["observe", "uia", "plan", "think", "act", "verify"];

export function emptyDeckState(): DeckState {
  return {
    usage: {
      turns: 0,
      tokensIn: 0,
      tokensOut: 0,
      costUsd: 0,
      lastProvider: "",
      lastModel: "",
      lastTurnTs: null,
      lastCacheHit: false,
      byModel: {},
    },
    cu: {
      active: false,
      missionId: "",
      phase: "idle",
      stepIdx: 0,
      lastActionKind: "",
      lastTargetHint: "",
      lastActionOk: null,
      lastFrameSha: null,
      windowTitle: "",
      frames: 0,
      startedTs: null,
      endedReason: "",
    },
    capture: null,
    captures: [],
    termLines: [],
    wikiChanges: [],
    journal: [],
    turn: emptyTurn(),
    wordsSession: 0,
    wordsLast: 0,
    utterances: 0,
    heardText: "",
  };
}

export function emptyTurn(): TurnState {
  return {
    index: 0,
    phase: "idle",
    voice: false,
    lastEventTs: null,
    openedTs: null,
    anchorTs: null,
    endedTs: null,
    provider: "",
    model: "",
    words: 0,
    sttMs: null,
    ackMs: null,
    ttftMs: null,
    firstAudioMs: null,
    brainMs: null,
    brainStartedTs: null,
    attempts: 0,
    tools: 0,
    toolsFailed: 0,
    toolsOpen: 0,
    workers: 0,
    cu: false,
    tokensIn: 0,
    tokensOut: 0,
    costUsd: 0,
    errors: 0,
    cacheHit: false,
  };
}

let seq = 0;
function nextId(): string {
  seq += 1;
  return `dk-${seq}`;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

/**
 * Word count the way a person counts: runs of non-space characters. Good
 * enough for every language the app speaks; a CJK-aware count would need a
 * segmenter and would only matter for a locale the recogniser does not have.
 */
export function countWords(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).length;
}

/**
 * The words of a final transcript, from either event that carries one.
 *
 * The classic pipeline publishes `TranscriptFinal` (and, right after it, a
 * `TranscriptionUpdate` flagged final with the same words). The live session
 * (Gemini / OpenAI realtime) publishes ONLY the latter — as a snapshot of the
 * whole utterance so far, flagged final per provider chunk. Reading just the
 * first left the live session's user with no line in the log and no words in
 * the counter (2026-08-18). Null for anything else, partials included.
 */
function finalTranscript(name: string, p: Record<string, unknown>): string | null {
  if (name === "TranscriptFinal") return str(obj(p.transcript).text);
  if (name === "TranscriptionUpdate" && p.is_final === true) return str(p.text);
  return null;
}

/**
 * True when `text` carries `earlier` whole: a live snapshot that grew ("Mir"
 * → "Mir geht es gut"), or the completion buffer's merge of a fragment with
 * the utterance that finished it. Such a final REPLACES the earlier one; it
 * is not a second thing the person said.
 */
function extendsHeard(text: string, earlier: string): boolean {
  return earlier.length > 0 && text !== earlier && (text.startsWith(earlier) || text.endsWith(earlier));
}

// Terminal output arrives as raw PTY data: ANSI colour, cursor moves, CR-only
// progress lines. The deck shows plain lines, so strip the escapes and split
// on newlines; a bare "\r" redraw is treated as its own line rather than
// dropped, which keeps progress bars visible instead of blank.
// CSI (colours, cursor moves), OSC (window titles, terminated by BEL or
// ESC \), and the charset selectors — each introduced by ESC (0x1b).
// eslint-disable-next-line no-control-regex
const ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]/g;

export function splitTerminalData(data: string): string[] {
  return data
    .replace(ANSI_RE, "")
    .split(/\r\n|\n|\r/)
    .map((l) => l.replace(/\t/g, "  ").trimEnd())
    .filter((l) => l.length > 0);
}

function pushLines(lines: TermLine[], added: TermLine[]): TermLine[] {
  const merged = lines.concat(added);
  return merged.length > MAX_TERM_LINES ? merged.slice(merged.length - MAX_TERM_LINES) : merged;
}

/**
 * Fold one WS event into the deck state.
 *
 * Returns the SAME object when the event is not one the deck reads, so a
 * store can bail out without a re-render — the wildcard forwarder delivers
 * every event, and most of them are not for the deck. Three folds in a row,
 * each pure and each returning its input untouched when the event is not
 * for it: the cards' figures, the session log, the turn.
 */
export function reduceDeck(
  state: DeckState,
  name: string,
  payload: unknown,
  tsMs: number,
): DeckState {
  const p = obj(payload);
  let next = reduceCards(state, name, p, tsMs);
  next = reduceJournal(next, name, p, tsMs);
  next = reduceTurn(next, name, p, tsMs);
  return next;
}

function reduceCards(
  state: DeckState,
  name: string,
  p: Record<string, unknown>,
  tsMs: number,
): DeckState {
  switch (name) {
    // ---- brain: what this session has cost ------------------------------
    case "BrainTurnCompleted": {
      const tokensIn = num(p.tokens_in);
      const tokensOut = num(p.tokens_out);
      const costUsd = num(p.cost_usd);
      const provider = str(p.provider);
      const model = str(p.model);
      const key = model || provider || "unknown";
      const prev = state.usage.byModel[key] ?? {
        turns: 0,
        tokensIn: 0,
        tokensOut: 0,
        costUsd: 0,
      };
      return {
        ...state,
        // Answered: the next final transcript is a new utterance, however it starts.
        heardText: "",
        usage: {
          ...state.usage,
          turns: state.usage.turns + 1,
          tokensIn: state.usage.tokensIn + tokensIn,
          tokensOut: state.usage.tokensOut + tokensOut,
          costUsd: state.usage.costUsd + costUsd,
          lastProvider: provider || state.usage.lastProvider,
          lastModel: model || state.usage.lastModel,
          lastTurnTs: tsMs,
          byModel: {
            ...state.usage.byModel,
            [key]: {
              turns: prev.turns + 1,
              tokensIn: prev.tokensIn + tokensIn,
              tokensOut: prev.tokensOut + tokensOut,
              costUsd: prev.costUsd + costUsd,
            },
          },
        },
      };
    }
    case "BrainTTFT":
      return {
        ...state,
        usage: {
          ...state.usage,
          lastCacheHit: Boolean(p.cache_hit),
          lastModel: str(p.model) || state.usage.lastModel,
        },
      };

    // ---- computer use: what it is doing right now ------------------------
    case "CUControlStarted":
      return {
        ...state,
        cu: {
          ...emptyDeckState().cu,
          active: true,
          missionId: str(p.mission_id),
          phase: "observe",
          startedTs: tsMs,
        },
      };
    case "CUControlEnded":
      return {
        ...state,
        cu: {
          ...state.cu,
          active: false,
          phase: "idle",
          endedReason: str(p.reason) || "finished",
        },
      };
    case "CUStepProfiled": {
      const phase = str(p.phase) as CuPhase;
      return {
        ...state,
        cu: {
          ...state.cu,
          // A step event while no control session is open means the deck
          // joined mid-mission (page reload); mark it active so the card
          // shows something rather than "idle" over a moving cursor.
          active: true,
          phase: CU_PHASES.includes(phase) ? phase : state.cu.phase,
          stepIdx: Math.max(state.cu.stepIdx, num(p.step_idx)),
        },
      };
    }
    case "ActionPlanned":
      return {
        ...state,
        cu: {
          ...state.cu,
          phase: "act",
          lastActionKind: str(p.action_kind),
          lastTargetHint: str(p.target_hint),
          lastActionOk: null,
        },
      };
    case "ActionVerified":
    case "ActionExecuted": {
      // Only meaningful while a CU mission runs — the same events fire for
      // ordinary tool calls, which the thinking trace already shows.
      if (!state.cu.active) return state;
      const ok = typeof p.success === "boolean" ? p.success : null;
      return { ...state, cu: { ...state.cu, lastActionOk: ok } };
    }
    case "ObservationCaptured": {
      const sha = str(p.screenshot_hash);
      return {
        ...state,
        cu: {
          ...state.cu,
          lastFrameSha: sha || state.cu.lastFrameSha,
          windowTitle: str(p.window_title) || state.cu.windowTitle,
          frames: state.cu.frames + 1,
        },
      };
    }

    // ---- screen capture: the one-shot look --------------------------------
    case "ScreenCaptureCompleted": {
      const capture: CaptureState = {
        seq: (state.capture?.seq ?? 0) + 1,
        ts: tsMs,
        targetKind: str(p.target_kind),
        targetLabel: str(p.target_label),
        width: num(p.width),
        height: num(p.height),
        redactions: num(p.redaction_count),
      };
      return {
        ...state,
        capture,
        captures: [capture, ...state.captures].slice(0, MAX_CAPTURES),
      };
    }

    // ---- terminals: shell + CLI ---------------------------------------------
    case "TerminalCommandExecuted": {
      const command = str(p.command);
      if (!command) return state;
      return {
        ...state,
        termLines: pushLines(state.termLines, [
          { id: nextId(), ts: tsMs, kind: "cmd", text: command, source: str(p.terminal_id) },
        ]),
      };
    }
    case "TerminalOutput": {
      const lines = splitTerminalData(str(p.data));
      if (lines.length === 0) return state;
      const source = str(p.terminal_id);
      return {
        ...state,
        termLines: pushLines(
          state.termLines,
          lines.map((text) => ({ id: nextId(), ts: tsMs, kind: "out" as const, text, source })),
        ),
      };
    }
    case "CliInvoked": {
      const cli = str(p.cli_name);
      const preview = str(p.command_preview);
      const text = preview ? `${cli} ${preview}`.trim() : cli;
      if (!text) return state;
      return {
        ...state,
        termLines: pushLines(state.termLines, [
          { id: nextId(), ts: tsMs, kind: "cli", text, source: cli },
        ]),
      };
    }
    case "CliInvocationFinished": {
      const cli = str(p.cli_name);
      const code = typeof p.exit_code === "number" ? p.exit_code : null;
      const ms = num(p.duration_ms);
      const text = code === null ? `${cli} · ${ms} ms` : `${cli} · exit ${code} · ${ms} ms`;
      return {
        ...state,
        termLines: pushLines(state.termLines, [
          { id: nextId(), ts: tsMs, kind: code === 0 || code === null ? "note" : "err", text, source: cli },
        ]),
      };
    }

    // ---- wiki: what memory just wrote ------------------------------------
    case "WikiPageChanged": {
      const slug = str(p.slug);
      if (!slug) return state;
      const next = [{ slug, kind: str(p.kind), ts: tsMs }]
        .concat(state.wikiChanges.filter((c) => c.slug !== slug))
        .slice(0, MAX_WIKI_CHANGES);
      return { ...state, wikiChanges: next };
    }

    // ---- words: the live counter's ground truth ---------------------------
    case "TranscriptFinal":
    case "TranscriptionUpdate": {
      const text = (finalTranscript(name, p) ?? "").trim();
      const words = countWords(text);
      // The same words on the second channel are not a second utterance.
      if (words === 0 || text === state.heardText) return state;
      if (extendsHeard(text, state.heardText)) {
        return {
          ...state,
          wordsSession: state.wordsSession - state.wordsLast + words,
          wordsLast: words,
          heardText: text,
        };
      }
      return {
        ...state,
        wordsSession: state.wordsSession + words,
        wordsLast: words,
        utterances: state.utterances + 1,
        heardText: text,
      };
    }

    default:
      return state;
  }
}

// ----------------------------------------------------------------------
// The session log — one line per thing heard, thought, done or said
// ----------------------------------------------------------------------

function clip(text: string, max = LOG_TEXT_MAX): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(2)}k`;
  return String(n);
}

function fmtUsd(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

function appendLine(journal: JournalLine[], line: Omit<JournalLine, "id">): JournalLine[] {
  const next = journal.concat({ id: nextId(), ...line });
  return next.length > MAX_JOURNAL_LINES ? next.slice(next.length - MAX_JOURNAL_LINES) : next;
}

/**
 * Index of the newest OPEN line of `kind` — preferring one that carries this
 * exact `text` (tool calls interleave), else the newest open one of the kind.
 */
function findOpen(journal: JournalLine[], kind: JournalKind, text?: string): number {
  let fallback = -1;
  for (let i = journal.length - 1; i >= 0; i--) {
    const line = journal[i];
    if (line.kind !== kind || !line.open) continue;
    if (text && line.text === text) return i;
    if (fallback === -1) fallback = i;
  }
  return fallback;
}

/** Close an open line in place: duration, outcome, optionally a new label. */
function closeLine(
  journal: JournalLine[],
  index: number,
  tsMs: number,
  patch: Partial<Pick<JournalLine, "ms" | "ok" | "labelKey" | "text">>,
): JournalLine[] {
  const line = journal[index];
  const next = [...journal];
  next[index] = {
    ...line,
    open: false,
    ms: patch.ms ?? Math.max(0, tsMs - line.ts),
    ok: patch.ok ?? true,
    ...(patch.labelKey ? { labelKey: patch.labelKey } : {}),
    ...(patch.text !== undefined ? { text: patch.text } : {}),
  };
  return next;
}

/** True when one of the last few lines already says this (a reply arrives on two channels). */
function recentlySaid(journal: JournalLine[], kind: JournalKind, text: string, within = 8): boolean {
  for (let i = journal.length - 1; i >= 0 && i >= journal.length - within; i--) {
    const line = journal[i];
    if (line.kind === kind && line.text === text) return true;
  }
  return false;
}

function withJournal(state: DeckState, journal: JournalLine[]): DeckState {
  return journal === state.journal ? state : { ...state, journal };
}

function reduceJournal(
  state: DeckState,
  name: string,
  p: Record<string, unknown>,
  tsMs: number,
): DeckState {
  const journal = state.journal;

  switch (name) {
    // ---- boot ---------------------------------------------------------------
    case "VoiceBootStatus": {
      if (p.ready !== true) return state;
      const last = [...journal].reverse().find((l) => l.kind === "boot");
      if (last && last.labelKey === "deck.log_voice_ready") return state;
      return withJournal(state, appendLine(journal, { ts: tsMs, kind: "boot", labelKey: "deck.log_voice_ready" }));
    }
    case "BrainProviderSwitched": {
      const provider = str(p.provider) || str(p.new_provider);
      return withJournal(
        state,
        appendLine(journal, { ts: tsMs, kind: "boot", labelKey: "deck.log_brain_switched", text: provider || undefined }),
      );
    }

    // ---- wake ---------------------------------------------------------------
    case "WakeWordDetected": {
      const keyword = str(p.keyword);
      const confidence = num(p.confidence);
      const text = [keyword, confidence > 0 ? confidence.toFixed(2) : ""].filter(Boolean).join(" · ");
      return withJournal(state, appendLine(journal, { ts: tsMs, kind: "wake", labelKey: "deck.log_wake", text: text || undefined }));
    }
    case "HotkeyPressed":
      return withJournal(
        state,
        appendLine(journal, { ts: tsMs, kind: "wake", labelKey: "deck.log_hotkey", text: str(p.combo) || undefined }),
      );
    case "VoiceSessionEnded": {
      const turns = num(p.turn_count);
      const reason = str(p.hangup_reason);
      return withJournal(
        state,
        appendLine(journal, {
          ts: tsMs,
          kind: "note",
          labelKey: "deck.log_session_ended",
          args: { turns: String(turns) },
          text: reason || undefined,
          ms: num(p.duration_s) * 1000 || undefined,
        }),
      );
    }

    // ---- hear ---------------------------------------------------------------
    case "TranscriptFinal":
    case "TranscriptionUpdate": {
      const raw = finalTranscript(name, p);
      if (raw === null) return state;
      const text = clip(raw);
      if (!text) return state;
      // The classic pipeline says every transcript twice (TranscriptFinal,
      // then the same words as a final TranscriptionUpdate): one line.
      if (name === "TranscriptionUpdate" && recentlySaid(journal, "hear", text)) return state;
      const labelKey = p.continues_previous === true ? "deck.log_hear_more" : undefined;
      // A final that carries the previous line whole — the live session's
      // growing snapshot, the completion buffer's merge — rewrites that line
      // in place: the sentence, at the moment it began. Only the LAST line:
      // anything logged in between means the earlier words were answered.
      const last = journal.length - 1;
      if (last >= 0 && journal[last].kind === "hear" && extendsHeard(text, journal[last].text ?? "")) {
        const next = [...journal];
        next[last] = { ...journal[last], text, ...(labelKey ? { labelKey } : {}) };
        return withJournal(state, next);
      }
      return withJournal(state, appendLine(journal, { ts: tsMs, kind: "hear", labelKey, text }));
    }

    // ---- think --------------------------------------------------------------
    case "BrainTurnStarted": {
      const text = [str(p.provider), str(p.model)].filter(Boolean).join(" · ");
      // A new attempt closes the one before it — the fallback chain moved
      // on, or a second brain call started alongside. Neither is a failure
      // this reducer can vouch for, so the line closes with its duration and
      // no verdict; a real error arrives as ErrorOccurred and gets its line.
      const idx = findOpen(journal, "think");
      const closed = idx === -1 ? journal : closeLine(journal, idx, tsMs, {});
      return withJournal(state, appendLine(closed, { ts: tsMs, kind: "think", text: text || undefined, open: true }));
    }
    case "BrainTTFT": {
      const idx = findOpen(journal, "think");
      return withJournal(
        state,
        appendLine(journal, {
          ts: tsMs,
          kind: "note",
          labelKey: "deck.log_first_token",
          ms: idx === -1 ? undefined : Math.max(0, tsMs - journal[idx].ts),
          text: p.cache_hit === true ? "cache" : undefined,
        }),
      );
    }
    case "BrainTurnCompleted": {
      const idx = findOpen(journal, "think");
      const closed = idx === -1 ? journal : closeLine(journal, idx, tsMs, {});
      return withJournal(
        state,
        appendLine(closed, {
          ts: tsMs,
          kind: "done",
          labelKey: "deck.log_done",
          args: {
            in: fmtTokens(num(p.tokens_in)),
            out: fmtTokens(num(p.tokens_out)),
            cost: fmtUsd(num(p.cost_usd)),
          },
          ms: idx === -1 ? undefined : Math.max(0, tsMs - journal[idx].ts),
        }),
      );
    }

    // ---- tools --------------------------------------------------------------
    case "ToolCallStarted":
    case "ActionProposed": {
      const tool = str(p.tool_name);
      if (!tool) return state;
      // The same call may be announced by both event families.
      const dup = findOpen(journal, "tool", tool);
      if (dup !== -1 && journal[dup].text === tool) return state;
      return withJournal(state, appendLine(journal, { ts: tsMs, kind: "tool", text: tool, open: true }));
    }
    case "ToolCallCompleted":
    case "ActionExecuted": {
      const tool = str(p.tool_name);
      const ok = p.success !== false;
      const ms = num(p.duration_ms) || undefined;
      const idx = findOpen(journal, "tool", tool);
      if (idx !== -1) return withJournal(state, closeLine(journal, idx, tsMs, { ms, ok }));
      if (name === "ActionExecuted" && tool) {
        return withJournal(state, appendLine(journal, { ts: tsMs, kind: "tool", text: tool, ms, ok }));
      }
      return state;
    }
    case "ActionDenied": {
      const idx = findOpen(journal, "tool", str(p.tool_name));
      if (idx === -1) return state;
      return withJournal(state, closeLine(journal, idx, tsMs, { ok: false, labelKey: "deck.log_denied" }));
    }

    // ---- say ----------------------------------------------------------------
    case "SpeechSpoken": {
      const text = clip(str(p.text));
      if (!text || recentlySaid(journal, "say", text)) return state;
      const kind = str(p.spoken_kind);
      return withJournal(
        state,
        appendLine(journal, {
          ts: tsMs,
          kind: "say",
          labelKey: kind === "preamble" ? "deck.log_ack" : undefined,
          text,
        }),
      );
    }
    case "MessageSent": {
      const role = str(p.role);
      const text = clip(str(p.text));
      if (!text) return state;
      if (role === "user") {
        // A voice turn's transcript reaches the log through TranscriptFinal;
        // this only adds a TYPED message.
        if (recentlySaid(journal, "hear", text)) return state;
        return withJournal(state, appendLine(journal, { ts: tsMs, kind: "hear", labelKey: "deck.log_typed", text }));
      }
      if (role === "assistant" || role === "preamble") {
        if (recentlySaid(journal, "say", text)) return state;
        return withJournal(
          state,
          appendLine(journal, {
            ts: tsMs,
            kind: "say",
            labelKey: role === "preamble" ? "deck.log_ack" : undefined,
            text,
          }),
        );
      }
      if (role === "system") {
        return withJournal(state, appendLine(journal, { ts: tsMs, kind: "note", text }));
      }
      return state;
    }
    case "LatencySpan": {
      const phase = str(p.phase);
      const ms = num(p.duration_ms);
      if (phase === "turn_to_first_audio" || phase === "realtime_first_audio") {
        return withJournal(state, appendLine(journal, { ts: tsMs, kind: "note", labelKey: "deck.log_first_audio", ms }));
      }
      if (phase === "ack_first_audio") {
        return withJournal(state, appendLine(journal, { ts: tsMs, kind: "note", labelKey: "deck.log_ack_audio", ms }));
      }
      return state;
    }
    case "AnnouncementRequested": {
      if (str(p.kind) !== "progress") return state;
      const text = clip(str(p.text), 80);
      if (!text) return state;
      return withJournal(state, appendLine(journal, { ts: tsMs, kind: "note", labelKey: "deck.log_progress", text }));
    }

    // ---- workers, control, look, memory ---------------------------------------
    case "JarvisAgentTaskStarted":
      return withJournal(
        state,
        appendLine(journal, {
          ts: tsMs,
          kind: "worker",
          labelKey: "deck.log_worker",
          text: clip(str(p.utterance), 80) || undefined,
          open: true,
        }),
      );
    case "JarvisAgentTaskCompleted": {
      const idx = findOpen(journal, "worker");
      if (idx === -1) return state;
      return withJournal(
        state,
        closeLine(journal, idx, tsMs, { ms: num(p.duration_s) * 1000 || undefined, ok: p.success !== false }),
      );
    }
    case "CUControlStarted":
      return withJournal(
        state,
        appendLine(journal, {
          ts: tsMs,
          kind: "control",
          labelKey: "deck.log_control",
          text: str(p.mission_id) || undefined,
          open: true,
        }),
      );
    case "CUControlEnded": {
      const idx = findOpen(journal, "control");
      const reason = str(p.reason);
      const ok = reason !== "error" && reason !== "failed";
      if (idx === -1) {
        return withJournal(
          state,
          appendLine(journal, { ts: tsMs, kind: "control", labelKey: "deck.log_control_ended", text: reason || undefined, ok }),
        );
      }
      return withJournal(state, closeLine(journal, idx, tsMs, { text: reason || journal[idx].text, ok }));
    }
    case "ScreenCaptureCompleted": {
      const size = num(p.width) && num(p.height) ? `${num(p.width)}×${num(p.height)}` : "";
      const text = [str(p.target_label), size].filter(Boolean).join(" · ");
      const redactions = num(p.redaction_count);
      return withJournal(
        state,
        appendLine(journal, {
          ts: tsMs,
          kind: "look",
          labelKey: redactions > 0 ? "deck.log_look_redacted" : "deck.log_look",
          args: redactions > 0 ? { n: String(redactions) } : undefined,
          text: text || undefined,
        }),
      );
    }
    case "WikiPageChanged": {
      const slug = str(p.slug);
      if (!slug) return state;
      const kind = str(p.kind);
      return withJournal(
        state,
        appendLine(journal, { ts: tsMs, kind: "memory", labelKey: "deck.log_memory", text: kind ? `${slug} · ${kind}` : slug }),
      );
    }
    case "ErrorOccurred": {
      const layer = str(p.layer) || str(p.source_layer);
      const message = clip(str(p.message), 100);
      const text = [layer, message].filter(Boolean).join(": ");
      if (!text) return state;
      return withJournal(state, appendLine(journal, { ts: tsMs, kind: "error", text, ok: false }));
    }
    default:
      return state;
  }
}

// ----------------------------------------------------------------------
// The turn — which phase, and how long each stage took
// ----------------------------------------------------------------------

function withTurn(state: DeckState, turn: TurnState): DeckState {
  return turn === state.turn ? state : { ...state, turn };
}

/** A fresh turn, closing whatever was open. */
function openTurn(state: DeckState, tsMs: number, init: Partial<TurnState>): TurnState {
  return {
    ...emptyTurn(),
    index: state.turn.index + 1,
    openedTs: tsMs,
    lastEventTs: tsMs,
    ...init,
  };
}

/** A turn that closed this recently is re-opened by a soft signal, not doubled. */
function justClosed(turn: TurnState, tsMs: number): boolean {
  return turn.phase === "idle" && turn.endedTs !== null && tsMs - turn.endedTs <= TURN_REOPEN_GRACE_MS;
}

/**
 * The turn a soft signal belongs to: the open one, the one that closed a
 * moment ago (re-opened), or — when neither — a new one anchored now.
 */
function joinTurn(state: DeckState, tsMs: number, phase: TurnPhase): TurnState {
  const turn = state.turn;
  if (turn.phase !== "idle") return { ...turn, phase, lastEventTs: tsMs };
  if (justClosed(turn, tsMs)) return { ...turn, phase, endedTs: null, lastEventTs: tsMs };
  return openTurn(state, tsMs, { phase, anchorTs: tsMs });
}

function endTurn(turn: TurnState, tsMs: number): TurnState {
  if (turn.phase === "idle") return turn;
  return { ...turn, phase: "idle", endedTs: tsMs, lastEventTs: tsMs, toolsOpen: 0 };
}

/** ms from the turn's anchor to now, or null before the request was complete. */
function sinceAnchor(turn: TurnState, tsMs: number): number | null {
  return turn.anchorTs === null ? null : Math.max(0, tsMs - turn.anchorTs);
}

function reduceTurn(
  state: DeckState,
  name: string,
  p: Record<string, unknown>,
  tsMs: number,
): DeckState {
  const turn = state.turn;

  switch (name) {
    // ---- hard starters: a person began a turn ------------------------------
    case "WakeWordDetected":
    case "HotkeyPressed":
    case "VoiceTurnStarted":
      return withTurn(state, openTurn(state, tsMs, { phase: "hear", voice: true }));

    case "TranscriptFinal": {
      const words = countWords(str(obj(p.transcript).text));
      if (words === 0) return state;
      if (p.continues_previous === true && turn.phase !== "idle") {
        return withTurn(state, { ...turn, words: turn.words + words, lastEventTs: tsMs });
      }
      // The mic was opened for this utterance (wake word / follow-up): the
      // transcript completes THAT turn's request rather than opening another.
      if (turn.phase === "hear" && turn.anchorTs === null) {
        return withTurn(state, { ...turn, phase: "think", anchorTs: tsMs, words, voice: true, lastEventTs: tsMs });
      }
      return withTurn(state, openTurn(state, tsMs, { phase: "think", anchorTs: tsMs, words, voice: true }));
    }

    case "TranscriptionUpdate": {
      // The live session's transcript (see `finalTranscript`): a snapshot that
      // contains the one before it, so the count is the larger, not a sum —
      // which also swallows the classic pipeline's repeat of TranscriptFinal.
      // The phase is left alone: TranscriptFinal, or the brain, moves it.
      const text = finalTranscript(name, p);
      if (text === null) return state;
      const words = countWords(text);
      if (words === 0) return state;
      if (turn.phase !== "idle") {
        return words > turn.words ? withTurn(state, { ...turn, words, lastEventTs: tsMs }) : state;
      }
      // Heard with no turn open (the live session's VoiceTurnStarted did not
      // reach us): a person began a turn — open it as its hear phase.
      return withTurn(state, openTurn(state, tsMs, { phase: "hear", words, voice: true }));
    }

    case "MessageSent": {
      const role = str(p.role);
      if (role === "user") {
        // Typed. A voice transcript already opened the turn — do not reopen.
        if (turn.phase !== "idle" || justClosed(turn, tsMs)) return state;
        return withTurn(
          state,
          openTurn(state, tsMs, { phase: "think", anchorTs: tsMs, words: countWords(str(p.text)), voice: false }),
        );
      }
      if (role === "assistant" && !turn.voice && turn.phase !== "idle") {
        return withTurn(state, endTurn(turn, tsMs));
      }
      return state;
    }

    // ---- the brain ----------------------------------------------------------
    case "BrainTurnStarted": {
      const joined = joinTurn(state, tsMs, "think");
      return withTurn(state, {
        ...joined,
        provider: str(p.provider) || joined.provider,
        model: str(p.model) || joined.model,
        attempts: joined.attempts + 1,
        brainStartedTs: tsMs,
        brainMs: null,
      });
    }
    case "BrainTTFT": {
      if (turn.phase === "idle") return state;
      const estimate = sinceAnchor(turn, tsMs) ?? (turn.brainStartedTs === null ? null : tsMs - turn.brainStartedTs);
      return withTurn(state, {
        ...turn,
        ttftMs: turn.ttftMs ?? estimate,
        cacheHit: p.cache_hit === true,
        model: str(p.model) || turn.model,
        lastEventTs: tsMs,
      });
    }
    case "BrainTurnCompleted": {
      if (turn.phase === "idle" && !justClosed(turn, tsMs)) return state;
      const joined = joinTurn(state, tsMs, turn.phase === "idle" ? "think" : turn.phase);
      const done: TurnState = {
        ...joined,
        brainMs: joined.brainStartedTs === null ? joined.brainMs : Math.max(0, tsMs - joined.brainStartedTs),
        brainStartedTs: null,
        provider: str(p.provider) || joined.provider,
        model: str(p.model) || joined.model,
        tokensIn: joined.tokensIn + num(p.tokens_in),
        tokensOut: joined.tokensOut + num(p.tokens_out),
        costUsd: joined.costUsd + num(p.cost_usd),
        lastEventTs: tsMs,
      };
      // A voice turn goes on to speak; anything else (a typed chat, a turn
      // the brain ran on its own) is over when the brain is.
      if (!done.voice) return withTurn(state, endTurn(done, tsMs));
      return withTurn(state, { ...done, phase: done.toolsOpen > 0 ? "act" : "speak" });
    }

    // ---- tools, computer use, workers -----------------------------------------
    case "ToolCallStarted":
    case "ActionProposed": {
      if (!str(p.tool_name)) return state;
      const joined = joinTurn(state, tsMs, "act");
      return withTurn(state, { ...joined, tools: joined.tools + 1, toolsOpen: joined.toolsOpen + 1 });
    }
    case "ToolCallCompleted":
    case "ActionExecuted":
    case "ActionDenied": {
      if (turn.phase === "idle") return state;
      const failed = name === "ActionDenied" || p.success === false;
      const toolsOpen = Math.max(0, turn.toolsOpen - 1);
      const backToThinking = toolsOpen === 0 && turn.phase === "act" && !turn.cu;
      return withTurn(state, {
        ...turn,
        toolsOpen,
        toolsFailed: turn.toolsFailed + (failed ? 1 : 0),
        phase: backToThinking ? "think" : turn.phase,
        lastEventTs: tsMs,
      });
    }
    case "CUControlStarted": {
      const joined = joinTurn(state, tsMs, "act");
      return withTurn(state, { ...joined, cu: true });
    }
    case "CUControlEnded": {
      if (turn.phase === "idle") return state;
      return withTurn(state, {
        ...turn,
        phase: turn.toolsOpen > 0 ? "act" : turn.brainStartedTs !== null ? "think" : turn.phase,
        lastEventTs: tsMs,
      });
    }
    case "JarvisAgentTaskStarted": {
      if (turn.phase === "idle") return state;
      return withTurn(state, { ...turn, workers: turn.workers + 1, lastEventTs: tsMs });
    }

    // ---- the measured marks ---------------------------------------------------
    case "LatencySpan": {
      if (turn.phase === "idle" && !justClosed(turn, tsMs)) return state;
      const patch = latencyPatch(turn, str(p.phase), num(p.duration_ms));
      if (!patch) return state;
      return withTurn(state, { ...turn, ...patch, lastEventTs: tsMs });
    }
    case "LatencyTurnComplete": {
      if (turn.phase === "idle" && !justClosed(turn, tsMs)) return state;
      const stages = obj(p.stages_ms);
      let next = turn;
      for (const [phase, value] of Object.entries(stages)) {
        const patch = latencyPatch(next, phase, num(value));
        if (patch) next = { ...next, ...patch };
      }
      return withTurn(state, next === turn ? turn : { ...next, lastEventTs: tsMs });
    }
    case "AudioOutFirst": {
      if (turn.phase === "idle") return state;
      return withTurn(state, {
        ...turn,
        phase: "speak",
        firstAudioMs: turn.firstAudioMs ?? sinceAnchor(turn, tsMs),
        lastEventTs: tsMs,
      });
    }

    // ---- the supervisor's state: the phase, and the end ------------------------
    case "SystemStateChanged": {
      const next = str(p.new_state).toLowerCase();
      if (next === "thinking") {
        const joined = joinTurn(state, tsMs, turn.phase === "act" ? "act" : "think");
        return withTurn(state, { ...joined, voice: true });
      }
      if (next === "speaking") {
        const joined = joinTurn(state, tsMs, "speak");
        return withTurn(state, { ...joined, voice: true });
      }
      if (next === "listening" || next === "idle") {
        // Back to the mic after speaking (or an interrupted answer): over.
        // LISTENING right after the wake word is the turn's own `hear` phase.
        if (turn.phase === "hear" || turn.phase === "idle") return state;
        return withTurn(state, endTurn(turn, tsMs));
      }
      return state;
    }
    case "VoiceTurnCompleted": {
      if (turn.phase === "idle" && !justClosed(turn, tsMs)) return state;
      const joined: TurnState = turn.phase === "idle" ? { ...turn, phase: "speak" } : turn;
      const filled: TurnState = {
        ...joined,
        provider: joined.provider || str(p.provider),
        model: joined.model || str(p.model),
        tokensIn: joined.attempts > 0 ? joined.tokensIn : num(p.tokens_in),
        tokensOut: joined.attempts > 0 ? joined.tokensOut : num(p.tokens_out),
        costUsd: joined.attempts > 0 ? joined.costUsd : num(p.cost_usd),
        tools: Math.max(joined.tools, Array.isArray(p.tool_calls) ? p.tool_calls.length : 0),
      };
      return withTurn(state, endTurn(filled, tsMs));
    }
    case "ErrorOccurred": {
      if (turn.phase === "idle") return state;
      const layer = str(p.layer) || str(p.source_layer);
      const counted = { ...turn, errors: turn.errors + 1, lastEventTs: tsMs };
      // The brain failing ends the thinking; anything else is noted.
      if (layer === "brain" && (turn.phase === "think" || turn.phase === "act")) {
        return withTurn(state, endTurn(counted, tsMs));
      }
      return withTurn(state, counted);
    }
    default:
      return state;
  }
}

/** Which turn figure a latency mark fills, if any — the first mark wins. */
function latencyPatch(turn: TurnState, phase: string, ms: number): Partial<TurnState> | null {
  if (!Number.isFinite(ms) || ms < 0) return null;
  switch (phase) {
    case "stt_finalize":
      return turn.sttMs === null ? { sttMs: ms } : null;
    case "ack_first_audio":
      return turn.ackMs === null ? { ackMs: ms } : null;
    case "brain_first_token":
    case "realtime_first_transcript":
      // Precise where BrainTTFT's wall-clock delta was an estimate.
      return { ttftMs: ms };
    case "turn_to_first_audio":
    case "realtime_first_audio":
      return { firstAudioMs: ms };
    case "brain_first_audio":
      return turn.firstAudioMs === null ? { firstAudioMs: ms } : null;
    default:
      return null;
  }
}
