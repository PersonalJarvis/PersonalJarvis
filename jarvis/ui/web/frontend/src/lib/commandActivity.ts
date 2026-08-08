/**
 * Live command activity model — the pure event→entry mapping behind the
 * shell-visibility layer (CommandActivityLayer.tsx).
 *
 * Until now a running shell command was invisible: no window opens (all
 * subprocesses spawn with CREATE_NO_WINDOW), output goes to the brain, and
 * the only trace sat in data/sessions.db. This module turns the ToolExecutor
 * events the backend already broadcasts over the WS wildcard forwarder into
 * a small live list the activity cards render.
 *
 * The impact classification (read / modify / destructive) arrives ON the
 * ActionProposed payload — computed server-side by
 * `jarvis/safety/command_impact.py` at the WS boundary, so there is no
 * TypeScript twin of the classifier to drift out of sync.
 *
 * Pure module on purpose: no React, no zustand, no timers — fully covered by
 * commandActivity.test.ts (same pattern as thinkingSteps.ts).
 */

export type CommandImpactLevel = "read" | "modify" | "destructive" | "unknown";

export type CommandActivityStatus = "running" | "done" | "failed" | "blocked";

export interface CommandActivityEntry {
  /** trace_id of the ToolExecutor run — pairs Proposed with Executed/Denied. */
  id: string;
  /** The full command string (render-clipped, full text in the tooltip). */
  command: string;
  level: CommandImpactLevel;
  /** Classified leading words ("rm", "ls, grep") for the badge tooltip. */
  words: string;
  status: CommandActivityStatus;
  startedTs: number;
  durationMs?: number;
  /** Denial reason / execution error — raw runtime text, never translated. */
  detail?: string;
  /** Wall-clock ms when the entry left "running" (drives auto-expiry). */
  settledTs?: number;
}

/** Hard cap — a runaway turn must not grow the array unbounded. */
export const MAX_COMMAND_ENTRIES = 24;

/** How long a settled card lingers before the layer prunes it. */
export const SETTLED_TTL_MS = 6_000;
/** Failures and blocks stay longer — they are the ones worth reading. */
export const FAILED_TTL_MS = 12_000;
/** A "running" entry whose Executed event never arrived (dropped WS frame,
 * client reconnect) must not spin forever. */
export const RUNNING_TTL_MS = 5 * 60_000;

const LEVELS: readonly CommandImpactLevel[] = [
  "read",
  "modify",
  "destructive",
  "unknown",
];

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function impactOf(payload: Record<string, unknown>): {
  level: CommandImpactLevel;
  words: string;
} {
  const impact = (payload.impact ?? {}) as Record<string, unknown>;
  const raw = str(impact.level);
  const level = (LEVELS as readonly string[]).includes(raw)
    ? (raw as CommandImpactLevel)
    : "unknown";
  return { level, words: str(impact.commands) };
}

function commandOf(payload: Record<string, unknown>): string {
  const args = (payload.args ?? {}) as Record<string, unknown>;
  return str(args.command).trim();
}

function push(
  entries: CommandActivityEntry[],
  entry: CommandActivityEntry,
): CommandActivityEntry[] {
  const next = [...entries, entry];
  if (next.length > MAX_COMMAND_ENTRIES) {
    // Drop the oldest *settled* entry first so running cards survive the cap.
    const idx = next.findIndex((e) => e.status !== "running");
    next.splice(idx === -1 ? 0 : idx, 1);
  }
  return next;
}

function settle(
  entries: CommandActivityEntry[],
  id: string,
  tsMs: number,
  patch: Pick<CommandActivityEntry, "status"> &
    Partial<Pick<CommandActivityEntry, "durationMs" | "detail">>,
): CommandActivityEntry[] | null {
  // Prefer the exact trace pair; fall back to the most recent running entry
  // (an Executed forwarded without its Proposed, e.g. after a reconnect).
  let idx = entries.findIndex((e) => e.id === id && e.status === "running");
  if (idx === -1) {
    for (let i = entries.length - 1; i >= 0; i--) {
      if (entries[i].status === "running") {
        idx = i;
        break;
      }
    }
  }
  if (idx === -1) return null;
  const next = [...entries];
  next[idx] = { ...next[idx], ...patch, settledTs: tsMs };
  return next;
}

/**
 * Apply one WS event. Returns the new list, or null when the event is
 * irrelevant (the common case — callers skip the store update entirely).
 */
export function reduceCommandActivity(
  entries: CommandActivityEntry[],
  eventName: string,
  traceId: string,
  payload: unknown,
  tsMs: number,
): CommandActivityEntry[] | null {
  const p = (payload ?? {}) as Record<string, unknown>;
  if (str(p.tool_name) !== "run_shell") return null;

  switch (eventName) {
    case "ActionProposed": {
      if (entries.some((e) => e.id === traceId)) return null;
      const { level, words } = impactOf(p);
      return push(entries, {
        id: traceId,
        command: commandOf(p),
        level,
        words,
        status: "running",
        startedTs: tsMs,
      });
    }

    case "ActionExecuted": {
      const failed = p.success === false;
      const settled = settle(entries, traceId, tsMs, {
        status: failed ? "failed" : "done",
        durationMs: num(p.duration_ms) || undefined,
        detail: failed ? str(p.error) || undefined : undefined,
      });
      if (settled) return settled;
      // Executed without a visible Proposed: still surface it, already done.
      return push(entries, {
        id: traceId,
        command: "",
        level: "unknown",
        words: "",
        status: failed ? "failed" : "done",
        startedTs: tsMs,
        durationMs: num(p.duration_ms) || undefined,
        detail: failed ? str(p.error) || undefined : undefined,
        settledTs: tsMs,
      });
    }

    case "ActionDenied": {
      const detail = str(p.reason) || undefined;
      const settled = settle(entries, traceId, tsMs, {
        status: "blocked",
        detail,
      });
      if (settled) return settled;
      // A blacklist match raises BEFORE ActionProposed — the denial is the
      // only event of that run. Showing it is the whole point: the user
      // SEES the safety layer step in.
      return push(entries, {
        id: traceId,
        command: "",
        level: "destructive",
        words: "",
        status: "blocked",
        startedTs: tsMs,
        detail,
        settledTs: tsMs,
      });
    }

    default:
      return null;
  }
}

/** Drop expired entries. Returns null when nothing changed (skip re-render). */
export function pruneCommandActivity(
  entries: CommandActivityEntry[],
  nowMs: number,
): CommandActivityEntry[] | null {
  const keep = entries.filter((e) => {
    if (e.status === "running") return nowMs - e.startedTs < RUNNING_TTL_MS;
    const ttl = e.status === "done" ? SETTLED_TTL_MS : FAILED_TTL_MS;
    return nowMs - (e.settledTs ?? e.startedTs) < ttl;
  });
  return keep.length === entries.length ? null : keep;
}
