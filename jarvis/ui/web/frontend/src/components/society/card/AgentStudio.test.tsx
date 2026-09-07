import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import type { SocietyAgentRow } from "@/lib/societyApi";
import { rowToAgent } from "../data";
import { AgentStudio } from "./AgentStudio";
import { AgentStudioModel } from "./AgentStudioModel";
import { AgentStudioRoutines } from "./AgentStudioRoutines";
import { studioDraft, studioPatch, validStudioLimits } from "./agentStudioData";

const ROW: SocietyAgentRow = {
  agent_id: "studio-test", name: "Test agent", title: "Specialist", description: "Keep notes.", tier: "specialist",
  parent_agent_id: null, state: "active", avatar: { contract: 1, archetype: "biped", base: "rogue", parts: {} },
  checkpoint: "idle", provider: "test-provider", model: "model-a", effort: "medium", account_id: "saved-seat",
  grant_mode: "all", grants: [], focus: ["plugin:notes"], denies: ["plugin:blocked"], skills: null,
  workspace_dir: "society/studio-test/workspace", wiki_namespace: "society/studio-test", knowledge_scope: "own",
  permission_ceiling: "monitor", approval_rules: { require_approval: ["plugin:notes:delete"], always_allow: [] },
  daily_budget_usd: 2, max_concurrent_runs: 1, browser_mode: "own", browser_allowed_domains: [], session_id: "society:studio-test",
  created_ms: 1000, updated_ms: 1000, stats: { runs: 2, total_cost_usd: 0.5, last_active_ms: null },
};
const AGENT = rowToAgent(ROW);
const ok = (body: object) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}
function props() { return { agent: AGENT, onOpenChat: vi.fn(), onRetired: vi.fn(), onPreview: vi.fn(), onGuardChange: vi.fn() }; }
beforeAll(async () => { await loadLocaleChunk("society"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("agent studio drafts", () => {
  test("a budget edit does not overwrite instructions, tool grants or approvals", () => {
    const base = studioDraft(AGENT);
    expect(studioPatch(base, { ...base, dailyBudget: "0", concurrentRuns: "3" })).toEqual({ daily_budget_usd: 0, max_concurrent_runs: 3 });
  });
  test("rejects empty, non-finite, negative and fractional limits", () => {
    const base = studioDraft(AGENT);
    for (const dailyBudget of ["", " ", "-1", "Infinity", "NaN"]) expect(validStudioLimits({ ...base, dailyBudget })).toBe(false);
    for (const concurrentRuns of ["", "0", "1.5", "Infinity"]) expect(validStudioLimits({ ...base, concurrentRuns })).toBe(false);
    expect(validStudioLimits({ ...base, dailyBudget: "0", concurrentRuns: "2" })).toBe(true);
  });
  test("saves only the edited role and reports persistence", async () => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => ok({ agent: { ...ROW, ...JSON.parse(String(init?.body ?? "{}")) } }));
    vi.stubGlobal("fetch", fetcher);
    render(wrap(<AgentStudio {...props()} />));
    fireEvent.change(screen.getByRole("textbox", { name: "Role" }), { target: { value: "Research partner" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Changes saved");
    const writes = fetcher.mock.calls.filter((call) => call[1]?.method === "PATCH");
    expect(writes).toHaveLength(1);
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ title: "Research partner" });
  });
  test("a failed save retains the draft and allows a retry", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Save unavailable" }), { status: 503 })));
    render(wrap(<AgentStudio {...props()} />));
    fireEvent.change(screen.getByRole("textbox", { name: "Role" }), { target: { value: "Keep this draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByRole("alert");
    expect((screen.getByRole("textbox", { name: "Role" }) as HTMLInputElement).value).toBe("Keep this draft");
    expect((screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement).disabled).toBe(false);
  });
  test("polling cannot erase a draft, and discard restores the initial values", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ok({})));
    const controls = props();
    const client = new QueryClient();
    const tree = (agent = AGENT) => <QueryClientProvider client={client}><AgentStudio {...controls} agent={agent} /></QueryClientProvider>;
    const view = render(tree());
    fireEvent.change(screen.getByRole("textbox", { name: "Role" }), { target: { value: "Unsaved role" } });
    view.rerender(tree({ ...AGENT, state: "working" }));
    expect((screen.getByRole("textbox", { name: "Role" }) as HTMLInputElement).value).toBe("Unsaved role");
    expect(controls.onGuardChange).toHaveBeenLastCalledWith(true, false);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect((screen.getByRole("textbox", { name: "Role" }) as HTMLInputElement).value).toBe("Specialist");
  });
  test("appearance edits preview immediately without a write; discard restores the figure", async () => {
    const fetcher = vi.fn(async () => ok({}));
    vi.stubGlobal("fetch", fetcher);
    const controls = props();
    render(wrap(<AgentStudio {...controls} />));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Appearance" }), { button: 0 });
    fireEvent.click(await screen.findByRole("button", { name: "Surprise me" }));
    await waitFor(() => expect(controls.onGuardChange).toHaveBeenLastCalledWith(true, false));
    expect(controls.onPreview.mock.lastCall?.[0]).not.toEqual(AGENT.figure);
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(controls.onPreview.mock.lastCall?.[0]).toEqual(AGENT.figure);
  });
  test("sample previews never claim to save", () => {
    render(wrap(<AgentStudio {...props()} sample />));
    expect((screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/sample agents cannot be saved/)).toBeTruthy();
  });
});

test("model changes use the reseating route and preserve the saved account", async () => {
  const provider = { id: ROW.provider, family: "test", label: "Test provider", runner: "api", models_source: "curated",
    curated_models: [{ id: "model-a", label: "A" }, { id: "model-b", label: "B" }], default_model: "model-a", keyless: false,
    native_resume: false, effort_levels: ["low", "medium"], default_effort: "medium", permission_modes: [], default_permission_mode: "", cli_installed: null };
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return ok({ agent: { ...ROW, ...JSON.parse(String(init.body)) } });
    if (url.includes("/catalog")) return ok({ providers: [provider] });
    if (url === "/api/jarvis-agent/status") return ok({ mapping: [{ jarvis: ROW.provider, key_set: true, api_key_set: true }] });
    if (url === "/api/society/providers") return ok({ providers: [{ ...provider, subscription: false, accounts: [] }] });
    return ok({ agent: ROW });
  });
  vi.stubGlobal("fetch", fetcher);
  render(wrap(<AgentStudioModel agent={AGENT} sample={false} onGuardChange={vi.fn()} />));
  await screen.findByRole("option", { name: "Test provider" });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Provider" }).closest("fieldset")?.disabled).toBe(false));
  fireEvent.change(screen.getByRole("combobox", { name: "Model" }), { target: { value: "model-b" } });
  await waitFor(() => expect((screen.getByRole("button", { name: "Apply model" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Apply model" }));
  await screen.findByText("Changes saved");
  const write = fetcher.mock.calls.find((call) => call[1]?.method === "POST");
  expect(write?.[0]).toBe("/api/society/agents/studio-test/model");
  expect(JSON.parse(String(write?.[1]?.body))).toEqual({ provider: ROW.provider, model: "model-b", effort: "medium", account_id: "saved-seat" });
});

test("routine creation sends the agent task and interval, and protects its draft", async () => {
  const fetcher = vi.fn(async () => ok({ routines: [] }));
  vi.stubGlobal("fetch", fetcher);
  const guard = vi.fn();
  render(wrap(<AgentStudioRoutines agentId={AGENT.agentId} sample={false} onGuardChange={guard} />));
  fireEvent.click(screen.getByRole("button", { name: "Add routine" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Routine name" }), { target: { value: "Daily summary" } });
  fireEvent.change(screen.getByRole("textbox", { name: "What should this agent do?" }), { target: { value: "Summarize the project notes." } });
  expect(guard).toHaveBeenLastCalledWith(true, false);
  const create = screen.getAllByRole("button", { name: "Add routine" }).find((button) => !(button as HTMLButtonElement).disabled)!;
  fireEvent.click(create);
  await waitFor(() => expect(screen.queryByRole("textbox", { name: "Routine name" })).toBeNull());
  const calls = fetcher.mock.calls as unknown as [string, RequestInit | undefined][];
  const write = calls.find((call) => call[1]?.method === "POST");
  expect(write?.[0]).toBe("/api/society/agents/studio-test/routines");
  expect(JSON.parse(String(write?.[1]?.body))).toEqual({ title: "Daily summary", prompt: "Summarize the project notes.", schedule: { kind: "every", interval_seconds: 86400 } });
  expect(guard).toHaveBeenLastCalledWith(false, false);
});
