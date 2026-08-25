import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SubAgentNode } from "@/store/jarvisAgents";
import { AgentInsight, requestTitle } from "./AgentInsight";

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

const note = (n: number, text: string) =>
  envelope(
    { event_type: "WorkerProgress", worker_id: WID, pct: null, note: text, stalled: false, tokens_so_far: 0, cost_so_far: 0 },
    n,
    WID,
  );

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
    note(4, "I will look at the workspace first."),
    note(5, "Grep: jarvis/ui"),
    note(6, "Read: C:/x.py"),
    note(7, "Grep: jarvis/core"),
    note(8, "You've reached your limit."),
    envelope(
      { event_type: "WorkerKilled", worker_id: WID, reason: "budget", error_class: "provider_quota", error_detail: "You've reached your limit." },
      9,
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
      10,
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
  artifacts: [
    { path: "tasks/t/artifacts/files/index.html", deliverable_path: "index.html", size: 2048, is_text: true, content: "<html>", truncated: false },
  ],
  artifact_count: 1,
  truncated: false,
};

const CHANGES = {
  mission_id: MISSION_ID,
  tasks: [],
  files: [
    { path: "index.html", previous_path: null, status: "added", additions: 120, deletions: 0, binary: false },
    { path: "README.md", previous_path: null, status: "modified", additions: 4, deletions: 2, binary: false },
  ],
  additions: 124,
  deletions: 2,
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
  final_answer: "## Done\n\nI built the **page** and wrote it to `index.html`.",
  truncated: false,
  dropped_steps: 0,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
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

function stubFetch(opts: { changes: "ok" | "missing" } = { changes: "ok" }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/api/missions/${MISSION_ID}/result`)) return jsonResponse(RESULT);
      if (url.endsWith(`/api/missions/${MISSION_ID}/changes`)) {
        return opts.changes === "ok" ? jsonResponse(CHANGES) : jsonResponse({ detail: "Not Found" }, 404);
      }
      if (url.endsWith(`/api/missions/${MISSION_ID}`)) return jsonResponse(DETAIL);
      if (url.endsWith("/api/outputs")) return jsonResponse(OUTPUTS);
      if (url.endsWith("/api/outputs/mission_019fecaa-5a92/plan")) return jsonResponse(PLAN);
      return new Response("not found", { status: 404 });
    }),
  );
}

describe("AgentInsight", () => {
  beforeEach(() => stubFetch());
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("explains a quota failure in one plain sentence and quotes the provider once", async () => {
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={() => {}} />);

    expect(await screen.findByText("Did not deliver")).toBeTruthy();
    expect(await screen.findByText(/stopped because the provider \(claude\) ran out of quota/i)).toBeTruthy();
    // Quoted in register 01; the timeline's kill and failure rows do NOT
    // repeat it (the note before them already said it).
    expect(screen.getByText("Provider said")).toBeTruthy();
    expect(screen.getAllByText("You've reached your limit.")).toHaveLength(2);
    // Raw tokens live behind "Details", not in the verdict.
    expect(screen.getByText(/task_error · provider_quota · CRITIQUING/)).toBeTruthy();
  });

  it("numbers the registers in order and folds the report past a screenful", async () => {
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={() => {}} />);
    await screen.findByText(/ran out of quota/i);

    expect(screen.getByRole("heading", { name: "What happened" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: /'s report$/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Timeline" })).toBeTruthy();
    // "Files" is also a fact label in register 01 — the heading is the register.
    expect(await screen.findByRole("heading", { name: "Files" })).toBeTruthy();
    // The report is rendered Markdown, not raw text.
    expect(screen.getByRole("heading", { name: "Done" })).toBeTruthy();
    expect(screen.getByText("page", { selector: "strong" })).toBeTruthy();
  });

  it("folds consecutive tool calls into one ledger line", async () => {
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={() => {}} />);
    await screen.findByText(/ran out of quota/i);

    const fold = await screen.findByRole("button", { name: /Ran 3 actions/ });
    expect(screen.getByText("×2")).toBeTruthy(); // Grep ran twice
    expect(screen.queryByText("jarvis/core")).toBeNull();
    fireEvent.click(fold);
    expect(screen.getByText("jarvis/core")).toBeTruthy();
    // The worker's own words stand alone, labelled by who said them.
    expect(screen.getByText("I will look at the workspace first.")).toBeTruthy();
    expect(screen.getAllByText("Said").length).toBeGreaterThan(0);
  });

  it("lists the changed files with their line counts and opens Artifacts from a row", async () => {
    const opened: string[] = [];
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={(s) => opened.push(s)} />);

    expect(await screen.findByText("README.md")).toBeTruthy();
    expect(screen.getByText("+120")).toBeTruthy();
    expect(screen.getByText("−2")).toBeTruthy();
    expect(screen.getByText("2 files · +124 −2 lines")).toBeTruthy();
    fireEvent.click(screen.getByText("README.md"));
    expect(opened).toEqual(["mission_019fecaa-5a92"]);
  });

  it("falls back to the deliverable list when the backend has no /changes route", async () => {
    vi.unstubAllGlobals();
    stubFetch({ changes: "missing" });
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={() => {}} />);

    expect(await screen.findByText("index.html")).toBeTruthy();
    expect(screen.getByText("2.0 KB")).toBeTruthy();
    expect(screen.queryByText("README.md")).toBeNull();
    expect(screen.getByText("1 deliverable file(s)")).toBeTruthy();
  });

  it("hands the archived output slug to the Artifacts section from the masthead", async () => {
    const opened: string[] = [];
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={(s) => opened.push(s)} />);

    const buttons = await screen.findAllByRole("button", { name: /open in artifacts/i });
    await waitFor(() => expect((buttons[0] as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(buttons[0]);
    expect(opened).toEqual(["mission_019fecaa-5a92"]);
  });

  it("keeps the worker transcript folded away under the timeline", async () => {
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => {}} onOpenOutput={() => {}} />);
    await screen.findByText(/ran out of quota/i);

    const fold = await screen.findByRole("button", { name: /transcript · 2 steps/i });
    expect(screen.queryByText("quota")).toBeNull();
    fireEvent.click(fold);
    expect(screen.getByText("quota")).toBeTruthy();
    expect(screen.getByText("Thought")).toBeTruthy();
  });

  it("goes back to the board", async () => {
    let back = 0;
    renderWithQuery(<AgentInsight agent={agentNode()} onBack={() => { back += 1; }} onOpenOutput={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Agents/ }));
    expect(back).toBe(1);
  });
});

describe("requestTitle", () => {
  it("takes the first paragraph and clamps it", () => {
    expect(requestTitle("Do this\n\nSupporting context: …", "none")).toBe("Do this");
    expect(requestTitle("   ", "none")).toBe("none");
    expect(requestTitle("x".repeat(300), "none")).toHaveLength(178);
  });
});
