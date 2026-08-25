import { describe, expect, it } from "vitest";

import type { SubAgentNode } from "@/store/jarvisAgents";
import type { CriticVerdictReady, WorkerKilled } from "@/types/missions";
import { composeNarrative } from "./narrative";
import type { AgentOutcome } from "./outcome";

const identity = (key: string) => key;

function agent(over: Partial<SubAgentNode> = {}): SubAgentNode {
  return {
    trace_id: "t",
    kind: "jarvis_agent",
    name: "",
    status: "failed",
    parent_trace_id: null,
    started_ns: 1,
    cost_usd: 0,
    tokens_in: 0,
    tokens_out: 0,
    context_hints: [],
    prompts: [],
    tool_calls: [],
    children_trace_ids: [],
    review_iterations: 0,
    depth: 0,
    ui_appeared_at: 1,
    ...over,
  };
}

function outcome(over: Partial<AgentOutcome> = {}): AgentOutcome {
  return {
    terminal: "failed",
    reason: "task_error",
    error_class: null,
    error_detail: null,
    failed_provider: null,
    last_state: null,
    partial_artifacts: [],
    summary: null,
    result_uri: null,
    cascade: false,
    deadline_ms: null,
    last_progress_ms: null,
    wall_ms: null,
    tokens_used: 0,
    cost_usd: 0,
    kills: [],
    verdicts: [],
    lastVerdict: null,
    lastCorrection: null,
    workers: [],
    revisions: 0,
    planWorkers: null,
    expectedOutput: null,
    ...over,
  };
}

const ctx = { agentName: "Agent", runtime: "12s", artifactCount: 0, notes: 0 };
const kill = (reason: WorkerKilled["reason"]): WorkerKilled => ({
  event_type: "WorkerKilled",
  worker_id: "m::t::iter0",
  reason,
});
const verdict = (v: CriticVerdictReady["verdict"], iteration = 1): CriticVerdictReady => ({
  event_type: "CriticVerdictReady",
  worker_id: "m::t::iter1",
  verdict: v,
  summary: "The document is missing.",
  confidence: 0.8,
  axes: {},
  iteration,
});

describe("composeNarrative — failures pick the most specific cause", () => {
  it("names a provider quota failure and quotes the provider", () => {
    const n = composeNarrative(
      agent(),
      outcome({ error_class: "provider_quota", error_detail: "limit reached", failed_provider: "claude", kills: [kill("budget")] }),
      ctx,
      identity,
    );
    expect(n.state).toBe("failed");
    expect(n.paragraph).toBe("subagents_view.narrative.failed_provider_quota");
    expect(n.quote).toEqual({ label: "subagents_view.narrative.quote_provider", text: "limit reached", mono: true });
  });

  it("explains an injection kill without a quote", () => {
    const n = composeNarrative(agent(), outcome({ kills: [kill("injection_detected")] }), ctx, identity);
    expect(n.paragraph).toBe("subagents_view.narrative.failed_injection");
    expect(n.quote).toBeNull();
  });

  it("quotes the reviewer when the review ran out of time", () => {
    const n = composeNarrative(
      agent(),
      outcome({ reason: "review_time_budget_exhausted", verdicts: [verdict("revise")], lastVerdict: verdict("revise"), revisions: 1 }),
      ctx,
      identity,
    );
    expect(n.paragraph).toBe("subagents_view.narrative.failed_review_time");
    expect(n.quote?.label).toBe("subagents_view.narrative.quote_reviewer");
    expect(n.quote?.text).toBe("The document is missing.");
  });

  it("mentions kept partial files as the closing note", () => {
    const n = composeNarrative(agent(), outcome({ partial_artifacts: ["a", "b"] }), ctx, identity);
    expect(n.paragraph).toBe("subagents_view.narrative.failed_generic");
    expect(n.note).toBe("subagents_view.narrative.kept_partial");
  });
});

describe("composeNarrative — the other outcomes", () => {
  it("uses the run's own summary when it delivered, plus the review tail", () => {
    const n = composeNarrative(
      agent({ status: "completed" }),
      outcome({ terminal: "approved", reason: null, summary: "Done. 3 files.", verdicts: [verdict("approve", 0)], lastVerdict: verdict("approve", 0) }),
      { ...ctx, artifactCount: 3 },
      identity,
    );
    expect(n.state).toBe("approved");
    expect(n.paragraph).toBe("Done. 3 files. subagents_view.narrative.approved_first_round");
    expect(n.note).toBe("subagents_view.narrative.delivered_files");
  });

  it("says you stopped it for a UI cancel", () => {
    const n = composeNarrative(agent({ status: "cancelled" }), outcome({ terminal: "cancelled", reason: "ui_cancel" }), ctx, identity);
    expect(n.paragraph).toBe("subagents_view.narrative.cancelled_by_you");
  });

  it("reports progress while still running", () => {
    const n = composeNarrative(agent({ status: "running" }), outcome({ terminal: null, reason: null }), { ...ctx, notes: 4 }, identity);
    expect(n.state).toBe("running");
    expect(n.paragraph).toBe("subagents_view.narrative.running_notes");
  });
});
