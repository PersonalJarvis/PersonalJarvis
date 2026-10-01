import type { AgentRecord, TeamRecord, TeamUnavailable, WorldSnapshot } from "./types";
import type { PreparationView } from "./preparationTypes";

export function preparationFixture(id = "alpha", ready = false): PreparationView {
  return {
    team: { ...teamFixture(id), state: "created", checkpoint: { preparation: { required: true, revision: ready ? 2 : 1, state: ready ? "ready" : "clarifying" } } },
    revision: ready ? 2 : 1, state: ready ? "ready" : "clarifying", busy: false,
    questions: [{ id: "deliverable", prompt: "Which deliverable should be saved?", choices: ["JSON file", "Readable report"], hint: "Choose the output you want to inspect." }],
    answers: ready ? { deliverable: "JSON file" } : {},
    plan: ready ? { goal: "Compute the requested total", acceptance: "A saved JSON file with independently verified arithmetic", summary: "Calculate, verify and save the result.", assumptions: ["Use the provided numbers"], exclusions: ["No external publishing"], tasks: [{ id: "calculate", title: "Calculate the total", description: "Use executable arithmetic on the provided numbers.", acceptance: "The arithmetic agrees with an independent check.", dependencies: [], domain: "math", milestone: "delivery", difficulty: 3, priority: 5, verification: "javascript", verification_script: "function main(x) { return {accepted: x.result === 42}; }", required_tools: ["run_javascript"], independent_verification: true }], remaining_decomposition: false } : null,
    digest: ready ? "a".repeat(64) : "", error: "",
  };
}

export function unavailableTeamFixture(id = "beta"): TeamUnavailable {
  return { id, name: `Team ${id}`, created_at: 1, available: false, error: "Team storage could not be read. Recovery is required." };
}

export function teamFixture(id = "alpha"): TeamRecord {
  return { id, name: `Team ${id}`, goal: `Goal ${id}`, acceptance: `Verify ${id}`, lead_id: `${id}-lead`, state: "running", version: 1,
    created_at: 1, updated_at: 1, started_at: 1, reason: "", mode: "local", checkpoint: {},
    tokens_used: "10000000000", tokens_reserved: "1234567", cost_microusd: "1250000", cost_reserved_microusd: "1000", network_bytes: "5",
    limits: { token_budget: "100000000000", monetary_limit_microusd: null, concurrency: 3, worker_limit: "10", runtime_seconds: 1800, max_attempts: 3, max_output_tokens: 8192, max_tool_calls: 32 },
    policy: { tools: [], internet: true, allowed_domains: [], dependency_registries: [], max_network_bytes: "20000000", max_artifact_bytes: "50000000", allow_dependencies: true } };
}
export function agentFixture(teamId = "alpha", id = `${teamId}-lead`): AgentRecord {
  return { id, team_id: teamId, name: id, role: "lead", state: "running", domain: "general", group_id: "general", task_id: null, generation: 1, level: 1, reliability: .5, verified_tasks: "0", tool_activity: null };
}
export function snapshotFixture(id = "alpha"): WorldSnapshot {
  return { team: teamFixture(id), revision: "1", agents: [agentFixture(id)], tasks: [], groups: [], activity: [], counts: { agents: "1", tasks: "0" }, aggregated: false, has_more: false };
}

/** A real event-driven transport fake; no Swarm hook or API is replaced. */
export class SwarmSocketFake {
  static all: SwarmSocketFake[] = [];
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(readonly url: URL) { SwarmSocketFake.all.push(this); }
  close() { if (this.closed) return; this.closed = true; this.onclose?.(); }
  emit(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }); }
}
