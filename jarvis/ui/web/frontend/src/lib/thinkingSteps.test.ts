/**
 * Unit tests for the reasoning-trace reducer: WS events in, human-readable
 * thinking steps out. Pure function — no store, no React.
 */
import { describe, expect, it } from "vitest";
import {
  finalizeThinkingSteps,
  MAX_THINKING_STEPS,
  reduceThinkingSteps,
  type ThinkingStep,
} from "@/lib/thinkingSteps";

const T0 = 1_000_000;

function run(
  events: Array<[string, unknown, number?]>,
  initial: ThinkingStep[] = [],
): ThinkingStep[] {
  let steps = initial;
  for (const [name, payload, ts] of events) {
    steps = reduceThinkingSteps(steps, name, payload, ts ?? T0) ?? steps;
  }
  return steps;
}

describe("reduceThinkingSteps", () => {
  it("ignores irrelevant events (returns null, not a new array)", () => {
    expect(reduceThinkingSteps([], "SystemStateChanged", {}, T0)).toBeNull();
    expect(reduceThinkingSteps([], "MessageSent", { role: "user" }, T0)).toBeNull();
  });

  it("opens a brain step with provider · model detail", () => {
    const steps = run([
      ["BrainTurnStarted", { provider: "gemini", model: "gemini-3-flash" }],
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({
      kind: "brain",
      labelKey: "thinking.step_brain",
      detail: "gemini · gemini-3-flash",
      status: "active",
    });
  });

  it("closes the previous brain attempt when the fallback chain restarts", () => {
    const steps = run([
      ["BrainTurnStarted", { provider: "openai" }, T0],
      ["BrainTurnStarted", { provider: "claude-api" }, T0 + 500],
    ]);
    expect(steps).toHaveLength(2);
    expect(steps[0].status).toBe("done");
    expect(steps[0].durationMs).toBe(500);
    expect(steps[1].status).toBe("active");
  });

  it("completes the brain step on BrainTurnCompleted with wall-clock duration", () => {
    const steps = run([
      ["BrainTurnStarted", { provider: "gemini" }, T0],
      ["BrainTurnCompleted", { tokens_out: 42 }, T0 + 1200],
    ]);
    expect(steps[0].status).toBe("done");
    expect(steps[0].durationMs).toBe(1200);
  });

  it("pairs ToolCallCompleted with the most recent active tool step", () => {
    const steps = run([
      ["ToolCallStarted", { tool_name: "wiki-recall" }],
      ["ToolCallCompleted", { success: true, duration_ms: 340 }],
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({
      kind: "tool",
      detail: "wiki-recall",
      status: "done",
      durationMs: 340,
    });
  });

  it("marks a failed tool call as error", () => {
    const steps = run([
      ["ToolCallStarted", { tool_name: "open-app" }],
      ["ToolCallCompleted", { success: false, duration_ms: 90, error: "boom" }],
    ]);
    expect(steps[0].status).toBe("error");
  });

  it("maps the ToolExecutor's ActionProposed/ActionExecuted pair by tool name", () => {
    const steps = run([
      ["ActionProposed", { tool_name: "wiki-recall", risk_tier: "safe" }],
      ["ActionProposed", { tool_name: "open-app", risk_tier: "monitor" }],
      ["ActionExecuted", { tool_name: "wiki-recall", success: true, duration_ms: 210 }],
    ]);
    expect(steps).toHaveLength(2);
    expect(steps[0]).toMatchObject({ detail: "wiki-recall", status: "done", durationMs: 210 });
    expect(steps[1]).toMatchObject({ detail: "open-app", status: "active" });
  });

  it("dedupes when both event families fire for the same call", () => {
    const steps = run([
      ["ToolCallStarted", { tool_name: "wiki-recall" }],
      ["ActionProposed", { tool_name: "wiki-recall" }],
    ]);
    expect(steps).toHaveLength(1);
  });

  it("surfaces an ActionExecuted without a Proposed as a finished row", () => {
    const steps = run([
      ["ActionExecuted", { tool_name: "open-app", success: false, duration_ms: 5000 }],
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ detail: "open-app", status: "error" });
  });

  it("marks a denied tool call as error", () => {
    const steps = run([
      ["ActionProposed", { tool_name: "delete-files" }],
      ["ActionDenied", { tool_name: "delete-files", reason: "blacklist" }],
    ]);
    expect(steps[0].status).toBe("error");
  });

  it("upserts computer-use phases into ONE row instead of appending", () => {
    const steps = run([
      ["ObservationCaptured", { window_title: "Chrome" }],
      ["CUStepProfiled", { phase: "plan", step_idx: 0 }],
      ["ActionPlanned", { action_kind: "click", target_hint: "{Button}" }],
      ["CUStepProfiled", { phase: "verify", step_idx: 1 }],
    ]);
    const cu = steps.filter((s) => s.kind === "computer");
    expect(cu).toHaveLength(1);
    expect(cu[0].labelKey).toBe("thinking.step_cu_verify");
    expect(cu[0].status).toBe("active");
  });

  it("pairs Jarvis-Agent worker start/completion and converts duration_s to ms", () => {
    const steps = run([
      ["JarvisAgentTaskStarted", { utterance: "build a flask app" }],
      ["JarvisAgentTaskCompleted", { success: true, duration_s: 44.8 }],
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({
      kind: "worker",
      status: "done",
      durationMs: 44_800,
    });
  });

  it("adds progress announcements as instantly-done notes, skips other kinds", () => {
    const steps = run([
      ["AnnouncementRequested", { kind: "progress", text: "Halfway there" }],
      ["AnnouncementRequested", { kind: "preamble", text: "On it" }],
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ kind: "note", detail: "Halfway there", status: "done" });
  });

  it("caps the list and evicts finished steps before active ones", () => {
    let steps: ThinkingStep[] = [];
    for (let i = 0; i < MAX_THINKING_STEPS + 5; i++) {
      steps = run(
        [
          ["ToolCallStarted", { tool_name: `tool-${i}` }],
          ["ToolCallCompleted", { success: true, duration_ms: 1 }],
        ],
        steps,
      );
    }
    steps = run([["ToolCallStarted", { tool_name: "still-running" }]], steps);
    expect(steps.length).toBeLessThanOrEqual(MAX_THINKING_STEPS);
    expect(steps.some((s) => s.detail === "still-running")).toBe(true);
  });
});

describe("finalizeThinkingSteps", () => {
  it("closes active steps with a computed duration and keeps finished ones", () => {
    const steps = run([
      ["ToolCallStarted", { tool_name: "a" }, T0],
      ["ToolCallCompleted", { success: true, duration_ms: 10 }, T0 + 10],
      ["ToolCallStarted", { tool_name: "b" }, T0 + 20],
    ]);
    const finalized = finalizeThinkingSteps(steps, T0 + 100);
    expect(finalized[0]).toMatchObject({ status: "done", durationMs: 10 });
    expect(finalized[1]).toMatchObject({ status: "done", durationMs: 80 });
  });
});
