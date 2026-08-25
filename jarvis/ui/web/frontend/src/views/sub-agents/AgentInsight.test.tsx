import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SubAgentNode } from "@/store/jarvisAgents";
import { AgentInsight, outcomeLines, reasonLabel, requestTitle } from "./AgentInsight";
import { deriveOutcome } from "./outcome";

const MISSION_ID = "019fecaa-5a92-7360-a784-829281639cf6";
const WID = "019fecaa-5a92::019fecaa-5aa5::iter0";

function envelope(payload: Record<string, unknown>, n: number, workerId: string | null = null) {
  return {
    event_id: `e${n}`,
    seq: n,
    mission_id: MISSION_ID,
    parent_event_id: null,
    worker_id: workerId,
    source_actor: "kontrollierer",
    ts_ms: 1_786_382_015_000 + n * 1_000,
    schema_version: 1,
    payload,
  };
}

const DETAIL = {
  mission: {
    id: MISSION_ID,
    prompt: "creates me an HTML file about the newest model releases",
    state: "FAILED",
    language: "en",
    iteration: 0,
    cost_usd: 0,
    created_ms: 1_786_382_015_000,
    updated_ms: 1_786_382_027_000,
  },
  events: [
    envelope({ event_type: "MissionDispatched", prompt: "x", parent_mission_id: null, priority: 0, language: "en" }, 1),
    envelope({ event_type: "MissionPlanReady", plan: [{}], n_workers: 1, expected_output: "Single-step plan" }, 2),
    envelope(
      { event_type: "WorkerSpawned", worker_id: WID, step: {}, pid: 0, cli: "claude", model: "", worktree: "C:/wt", session_id: "s1" },
      3,
      WID,
    ),
    envelope(
      { event_type: "WorkerProgress", worker_id: WID, pct: null, note: "Grep: jarvis/ui", stalled: false, tokens_so_far: 0, cost_so_far: 0 },
      4,
      WID,
    ),
    envelope(
      { event_type: "WorkerKilled", worker_id: WID, reason: "budget", error_class: "provider_quota", error_detail: "You've reached your limit." },
      5,
      WID,
    ),
    envelope(
      {
        event_type: "MissionFailed",
        reason: "task_error",
        error_class: "provider_quota",
        last_state: "CRITIQUING",
        partial_artifacts: [],
        error_detail: "You've reached your limit.",
        failed_provider: "claude",
      },
      6,
    ),
  ],
  verdicts: [],
  worker_snapshots: [],
};

const RESULT = {
  mission_id: MISSION_ID,
  state: "FAILED",
  language: "en",
  prompt: "creates me an HTML file about the newest model releases\n\nSupporting context: none",
  terminal_event: "MissionFailed",
  summary: null,
  result_uri: null,
  reason: "task_error",
  artifacts: [],
  artifact_count: 0,
  truncated: false,
};

const OUTPUTS = {
  sessions: [{ slug: "mission_019fecaa-5a92", status: "error", mission_id: MISSION_ID, terminal_reason: "task_error" }],
};

const PLAN = {
  plan: { plan_id: "mission_019fecaa-5a92", vision: "", status: "failed", total_steps: 2 },
  steps: [
    { step_id: "t:0", name: "I will look at the workspace first.", kind: "reasoning", status: "done", output: "I will look at the workspace first.", task_key: "t" },
    { step_id: "t:1", name: "jarvis/ui", tool_name: "Grep", kind: "tool", status: "failed", error: "quota", task_key: "t" },
  ],
  final_answer: null,
  truncated: false,
  dropped_steps: 0,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function agentNode(over: Partial<SubAgentNode> = {}): SubAgentNode {
  return {
    trace_id: MISSION_ID.replace(/-/g, ""),
    mission_id: MISSION_ID,
    kind: "jarvis_agent",
    name: "",
    status: "failed",
    parent_trace_id: null,
    started_ns: 1_786_382_015_000 * 1_000_000,
    completed_ns: null,
    duration_ms: 12_000,
    cost_usd: 0,
    tokens_in: 0,
    tokens_out: 0,
    utterance: "creates me an HTML file about the newest model releases",
    context_hints: [],
    prompts: [],
    tool_calls: [],
    children_trace_ids: [],
    error: null,
    error_class: null,
    review_iterations: 0,
    depth: 0,
    ui_appeared_at: 1,
    ...over,
  };
}

function renderWithQuery(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const identity = (key: string) => key;

describe("AgentInsight", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith(`/api/missions/${MISSION_ID}/result`)) return jsonResponse(RESULT);
        if (url.endsWith(`/api/missions/${MISSION_ID}`)) return jsonResponse(DETAIL);
        if (url.endsWith("/api/outputs")) return jsonResponse(OUTPUTS);
        if (url.endsWith("/api/outputs/mission_019fecaa-5a92/plan")) return jsonResponse(PLAN);
        return new Response("not found", { status: 404 });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("explains a quota failure in plain words, with the provider's own text", async () => {
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={() => {}} />);

    // "Did not deliver" shows from the row's own status before the record
    // arrives; the reason line is the first thing that needs the fetch.
    expect(await screen.findByText("Did not deliver")).toBeTruthy();
    expect(await screen.findByText("The worker's task failed")).toBeTruthy();
    // The classified cause and the raw upstream message.
    expect(screen.getByText(/Provider quota exhausted/)).toBeTruthy();
    // Quoted in the verdict AND in the story's kill entry — both on purpose.
    expect(screen.getAllByText("You've reached your limit.").length).toBeGreaterThanOrEqual(2);
    // The kill and where in the pipeline it broke.
    // Rounds are 1-based for people; the worker id says iter0.
    expect(screen.getByText("Worker (round 1) stopped")).toBeTruthy();
    expect(screen.getByText("budget or provider quota")).toBeTruthy();
    // The story timeline lands on the failure by default for a failed run.
    expect(await screen.findByText("Worker started · claude · round 1")).toBeTruthy();
    expect(screen.getByText("Grep")).toBeTruthy();
  });

  it("hands the archived output slug to the Artifacts section", async () => {
    const opened: string[] = [];
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={(s) => opened.push(s)} />);

    const button = await screen.findByRole("button", { name: /open output/i });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    expect(opened).toEqual(["mission_019fecaa-5a92"]);
  });

  it("shows the reconstructed transcript with reasoning and a failed tool step", async () => {
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={() => {}} />);
    await screen.findByText("Did not deliver");

    fireEvent.click(await screen.findByRole("tab", { name: /transcript/i }));
    expect(await screen.findByText("I will look at the workspace first.")).toBeTruthy();
    expect(screen.getByText("quota")).toBeTruthy();
    // The header's status dot says "Failed" too; the step adds a second one.
    expect(screen.getAllByText("Failed", { selector: "span" }).length).toBeGreaterThanOrEqual(2);
  });

  it("goes back to the board", async () => {
    let back = 0;
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => { back += 1; }} onOpenOutput={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Agents/ }));
    expect(back).toBe(1);
  });
});

describe("outcomeLines", () => {
  it("lists nothing beyond progress for a run still working", () => {
    const outcome = deriveOutcome(DETAIL.events.slice(0, 4) as never, "en");
    const lines = outcomeLines(agentNode({ status: "running" }), outcome, identity);
    expect(lines.map((l) => l.id)).toEqual(["notes"]);
  });

  it("orders a failure as reason → cause → provider text → kills → state", () => {
    const outcome = deriveOutcome(DETAIL.events as never, "en");
    const lines = outcomeLines(agentNode(), outcome, identity);
    expect(lines.map((l) => l.id)).toEqual(["reason", "class", "detail", "provider", `kill-${WID}`, "state"]);
    expect(lines.find((l) => l.id === "detail")?.mono).toBe(true);
  });
});

describe("requestTitle / reasonLabel", () => {
  it("takes the first paragraph and clamps it", () => {
    expect(requestTitle("Do this\n\nSupporting context: …", "none")).toBe("Do this");
    expect(requestTitle("   ", "none")).toBe("none");
    expect(requestTitle("x".repeat(300), "none")).toHaveLength(178);
  });

  it("falls back to the raw token for an unknown reason and keeps the tail", () => {
    expect(reasonLabel("decompose_failed: no brain", identity)).toBe(
      "subagents_view.reason.decompose_failed (no brain)",
    );
    expect(reasonLabel("something_new", identity)).toBe("something_new");
    expect(reasonLabel(null, identity)).toBeNull();
  });
});
