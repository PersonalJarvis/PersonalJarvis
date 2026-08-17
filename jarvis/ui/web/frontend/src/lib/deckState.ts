/**
 * Mission-deck state model — the pure event→state mapping behind the deck's
 * cards (MissionDeckView.tsx).
 *
 * The backend forwards EVERY EventBus event over the WebSocket. This module
 * folds the handful the deck cares about into a small, plain state object:
 * what the brain has cost this session, what Computer-Use is doing right now,
 * whether a screen capture just happened, the last shell/CLI lines, recent
 * wiki changes, and how many words were spoken.
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

export interface DeckState {
  usage: BrainUsage;
  cu: CuState;
  capture: CaptureState | null;
  termLines: TermLine[];
  wikiChanges: WikiChange[];
  /** Words in every final transcript this session. */
  wordsSession: number;
  /** Words in the most recent final transcript. */
  wordsLast: number;
  /** How many utterances were finalised. */
  utterances: number;
}

/** Hard caps — a long session must not grow any list unbounded. */
export const MAX_TERM_LINES = 80;
export const MAX_WIKI_CHANGES = 12;

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
    termLines: [],
    wikiChanges: [],
    wordsSession: 0,
    wordsLast: 0,
    utterances: 0,
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
 * every event, and most of them are not for the deck.
 */
export function reduceDeck(
  state: DeckState,
  name: string,
  payload: unknown,
  tsMs: number,
): DeckState {
  const p = obj(payload);

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
    case "ScreenCaptureCompleted":
      return {
        ...state,
        capture: {
          seq: (state.capture?.seq ?? 0) + 1,
          ts: tsMs,
          targetKind: str(p.target_kind),
          targetLabel: str(p.target_label),
          width: num(p.width),
          height: num(p.height),
          redactions: num(p.redaction_count),
        },
      };

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
    case "TranscriptFinal": {
      const transcript = obj(p.transcript);
      const words = countWords(str(transcript.text));
      if (words === 0) return state;
      return {
        ...state,
        wordsSession: state.wordsSession + words,
        wordsLast: words,
        utterances: state.utterances + 1,
      };
    }

    default:
      return state;
  }
}
