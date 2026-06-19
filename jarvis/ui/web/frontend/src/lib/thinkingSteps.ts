/**
 * Reasoning-trace step model for the chat "thinking" indicator.
 *
 * The backend forwards EVERY EventBus event over the WebSocket (wildcard
 * subscriber in server.py). While the optimistic `chatThinking` flag is on,
 * the store feeds those events through `reduceThinkingSteps` to build a live
 * list of human-readable thinking steps ("Using tool: wiki-recall",
 * "Looking at the screen", "Delegating to background worker", ...).
 *
 * Pure module on purpose: no React, no zustand, no side effects — the whole
 * event→step mapping is unit-testable in isolation (thinkingSteps.test.ts).
 * Labels are i18n keys (resolved at render time so a live language switch
 * re-labels past steps too); `detail` carries raw runtime text (tool names,
 * window titles) that is not translated.
 */

export type ThinkingStepKind =
  | "brain"
  | "tool"
  | "computer"
  | "worker"
  | "note";

export type ThinkingStepStatus = "active" | "done" | "error";

export interface ThinkingStep {
  id: string;
  kind: ThinkingStepKind;
  /** i18n key under "thinking.*" — resolved at render time. */
  labelKey: string;
  /** Raw runtime detail (tool name, window title, ...). Never translated. */
  detail?: string;
  status: ThinkingStepStatus;
  /** Wall-clock ms when the step appeared (drives live duration). */
  startedTs: number;
  /** Filled when the step completes. */
  durationMs?: number;
}

/** Finished trace attached to the assistant message that ended the turn. */
export interface ThinkingTraceSnapshot {
  steps: ThinkingStep[];
  durationMs: number;
}

/** Hard cap — a runaway turn must not grow the array unbounded. */
export const MAX_THINKING_STEPS = 40;

let seq = 0;
function nextId(): string {
  seq += 1;
  return `ts-${seq}`;
}

const CU_PHASE_LABEL: Record<string, string> = {
  observe: "thinking.step_cu_observe",
  uia: "thinking.step_cu_uia",
  plan: "thinking.step_cu_plan",
  think: "thinking.step_cu_think",
  act: "thinking.step_cu_act",
  verify: "thinking.step_cu_verify",
  settle: "thinking.step_cu_settle",
};

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function clip(text: string, max = 60): string {
  const clean = text.trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

function push(steps: ThinkingStep[], step: ThinkingStep): ThinkingStep[] {
  const next = [...steps, step];
  // Drop the oldest *finished* entries first so active spinners survive the cap.
  if (next.length > MAX_THINKING_STEPS) {
    const doneIdx = next.findIndex((s) => s.status !== "active");
    next.splice(doneIdx === -1 ? 0 : doneIdx, 1);
  }
  return next;
}

/** Complete the most recent active step of `kind`. Returns null when none. */
function complete(
  steps: ThinkingStep[],
  kind: ThinkingStepKind,
  tsMs: number,
  opts: { durationMs?: number; error?: boolean } = {},
): ThinkingStep[] | null {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s.kind === kind && s.status === "active") {
      const next = [...steps];
      next[i] = {
        ...s,
        status: opts.error ? "error" : "done",
        durationMs: opts.durationMs ?? Math.max(0, tsMs - s.startedTs),
      };
      return next;
    }
  }
  return null;
}

function hasActiveTool(steps: ThinkingStep[], name: string): boolean {
  return steps.some(
    (s) => s.kind === "tool" && s.status === "active" && s.detail === name,
  );
}

/**
 * Complete a tool step: prefer the active row carrying the same tool name
 * (ToolExecutor runs can interleave), fall back to the most recent active
 * tool row (ToolCallCompleted carries no name).
 */
function completeTool(
  steps: ThinkingStep[],
  name: string,
  tsMs: number,
  opts: { durationMs?: number; error?: boolean },
): ThinkingStep[] | null {
  let idx = -1;
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s.kind !== "tool" || s.status !== "active") continue;
    if (name && s.detail === name) {
      idx = i;
      break;
    }
    if (idx === -1) idx = i;
  }
  if (idx === -1) return null;
  const next = [...steps];
  next[idx] = {
    ...next[idx],
    status: opts.error ? "error" : "done",
    durationMs: opts.durationMs ?? Math.max(0, tsMs - next[idx].startedTs),
  };
  return next;
}

/**
 * Computer-Use phases fire many times per turn (observe→plan→act→...). One
 * mutating row reads far calmer than 30 appended rows, so the active
 * "computer" step is updated in place; a new row only appears when none is
 * active yet.
 */
function upsertComputer(
  steps: ThinkingStep[],
  labelKey: string,
  detail: string,
  tsMs: number,
): ThinkingStep[] {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s.kind === "computer" && s.status === "active") {
      const next = [...steps];
      next[i] = { ...s, labelKey, detail: detail || s.detail };
      return next;
    }
  }
  return push(steps, {
    id: nextId(),
    kind: "computer",
    labelKey,
    detail: detail || undefined,
    status: "active",
    startedTs: tsMs,
  });
}

/**
 * Apply one WebSocket event to the step list. Returns the new list, or null
 * when the event is irrelevant (the common case — callers skip the store
 * update entirely then).
 */
export function reduceThinkingSteps(
  steps: ThinkingStep[],
  eventName: string,
  payload: unknown,
  tsMs: number,
): ThinkingStep[] | null {
  const p = (payload ?? {}) as Record<string, unknown>;

  switch (eventName) {
    case "BrainTurnStarted": {
      // The fallback chain may publish several BrainTurnStarted per turn —
      // close the previous attempt before opening the next one.
      const closed = complete(steps, "brain", tsMs) ?? steps;
      const provider = str(p.provider);
      const model = str(p.model);
      return push(closed, {
        id: nextId(),
        kind: "brain",
        labelKey: "thinking.step_brain",
        detail: [provider, model].filter(Boolean).join(" · ") || undefined,
        status: "active",
        startedTs: tsMs,
      });
    }

    case "BrainTurnCompleted":
      return complete(steps, "brain", tsMs);

    // ActionProposed/Executed/Denied are what the ToolExecutor actually
    // publishes today; ToolCallStarted/Completed is the (currently unwired)
    // sibling vocabulary — handled identically so either path lights up.
    case "ToolCallStarted":
    case "ActionProposed": {
      const name = str(p.tool_name);
      // Dedupe in case both event families ever fire for the same call.
      if (name && hasActiveTool(steps, name)) return null;
      return push(steps, {
        id: nextId(),
        kind: "tool",
        labelKey: "thinking.step_tool",
        detail: name || undefined,
        status: "active",
        startedTs: tsMs,
      });
    }

    case "ToolCallCompleted":
    case "ActionExecuted": {
      const name = str(p.tool_name);
      const opts = {
        durationMs: num(p.duration_ms) || undefined,
        error: p.success === false,
      };
      const completed = completeTool(steps, name, tsMs, opts);
      if (completed) return completed;
      // Executed without a visible Proposed (e.g. the manager's timeout
      // path): still surface the call as an already-finished row.
      if (eventName === "ActionExecuted" && name) {
        return push(steps, {
          id: nextId(),
          kind: "tool",
          labelKey: "thinking.step_tool",
          detail: name,
          status: opts.error ? "error" : "done",
          startedTs: tsMs,
          durationMs: opts.durationMs,
        });
      }
      return null;
    }

    case "ActionDenied": {
      const name = str(p.tool_name);
      return completeTool(steps, name, tsMs, { error: true });
    }

    case "ObservationCaptured":
      return upsertComputer(
        steps,
        "thinking.step_cu_observe",
        clip(str(p.window_title)),
        tsMs,
      );

    case "ActionPlanned": {
      const detail = [str(p.action_kind), clip(str(p.target_hint), 40)]
        .filter(Boolean)
        .join(" · ");
      return upsertComputer(steps, "thinking.step_cu_plan", detail, tsMs);
    }

    case "CUStepProfiled": {
      const labelKey = CU_PHASE_LABEL[str(p.phase)];
      if (!labelKey) return null;
      const idx = num(p.step_idx);
      return upsertComputer(steps, labelKey, `#${idx + 1}`, tsMs);
    }

    case "OpenClawTaskStarted":
      return push(steps, {
        id: nextId(),
        kind: "worker",
        labelKey: "thinking.step_worker",
        detail: clip(str(p.utterance)) || undefined,
        status: "active",
        startedTs: tsMs,
      });

    case "OpenClawTaskCompleted":
      return complete(steps, "worker", tsMs, {
        durationMs: num(p.duration_s) * 1000 || undefined,
        error: p.success === false,
      });

    case "AnnouncementRequested": {
      // Progress announcements only — preambles already render as their own
      // chat bubble and completions arrive as the final reply.
      if (str(p.kind) !== "progress") return null;
      const text = clip(str(p.text), 80);
      if (!text) return null;
      return push(steps, {
        id: nextId(),
        kind: "note",
        labelKey: "thinking.step_update",
        detail: text,
        status: "done",
        startedTs: tsMs,
        durationMs: 0,
      });
    }

    default:
      return null;
  }
}

/** Finalize a live step list for the stored trace: active → done. */
export function finalizeThinkingSteps(
  steps: ThinkingStep[],
  tsMs: number,
): ThinkingStep[] {
  return steps.map((s) =>
    s.status === "active"
      ? { ...s, status: "done", durationMs: Math.max(0, tsMs - s.startedTs) }
      : s,
  );
}
