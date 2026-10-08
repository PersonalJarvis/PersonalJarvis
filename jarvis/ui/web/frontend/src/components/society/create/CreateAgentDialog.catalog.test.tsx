import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import type { AgentChatProvider } from "@/lib/agentChatApi";
import { useSavedHiddenModels } from "@/lib/agentProviderPrefs";
import { MODEL_ACCESS_KEY } from "@/lib/modelAccess";
import { clearModelMenuSnapshot, writeModelMenuSnapshot } from "../chat/modelMenuSnapshot";
import { CreateAgentDialogHost } from "./CreateAgentDialog";
import { useCreateAgentDialog } from "./createAgentStore";

vi.mock("../companion/CompanionEditor", () => ({ CompanionEditor: () => <div>Companion preview</div> }));
const { createAgent } = vi.hoisted(() => ({ createAgent: vi.fn() }));
vi.mock("../data", () => ({ useCreateSocietyAgent: () => createAgent, AgentNameTaken: class extends Error {} }));

const fallback: AgentChatProvider = {
  id: "openai-codex", label: "OpenAI Codex", family: "openai", runner: "codex-cli", cli_installed: true,
  models_source: "curated", default_model: "", keyless: false, native_resume: true,
  effort_levels: ["medium"], default_effort: "medium", permission_modes: [], default_permission_mode: "ask",
  hidden_models: ["gpt-5.6-sol"], enabled: true,
  curated_models: [{ id: "gpt-5.6-sol", label: "GPT-5.6-Sol" }, { id: "gpt-5.2", label: "GPT-5.2" }],
};
const current = { ...fallback, curated_models: [
  { id: "gpt-6.1-sol", label: "GPT-6.1-Sol" }, { id: "gpt-6-astra", label: "GPT-6-Astra" },
  { id: "gpt-6-luna", label: "GPT-6-Luna" }, { id: "gpt-5.6-sol", label: "GPT-5.6-Sol" },
] };
const connections = [{ jarvis: "openai-codex", key_set: true, is_active_brain: false }];
const providers = [{ id: "openai-codex", label: "Codex", family: "openai", runner: "codex-cli", subscription: true,
  keyless: false, platform: null, accounts: [] }];
const catalog = (row: AgentChatProvider) => ({ providers: [row], default_cwd: "", shell: "" });
let client: QueryClient;

beforeEach(async () => {
  createAgent.mockReset(); createAgent.mockResolvedValue({ agentId: "created" });
  clearModelMenuSnapshot();
  localStorage.removeItem(MODEL_ACCESS_KEY);
  useSavedHiddenModels.setState({ hidden: null });
  await loadLocaleChunk("society");
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  cleanup(); client.clear(); clearModelMenuSnapshot(); vi.unstubAllGlobals();
  useSavedHiddenModels.setState({ hidden: null });
  act(() => useCreateAgentDialog.getState().cancel());
});

function openDialog() {
  render(<QueryClientProvider client={client}><CreateAgentDialogHost /></QueryClientProvider>);
  act(() => { void useCreateAgentDialog.getState().request().catch(() => undefined); });
}

test("opening replaces a fresh cached GPT-5.2 fallback with the enabled live subscription models", async () => {
  writeModelMenuSnapshot({ version: 1, savedAt: Date.now(), catalog: catalog(fallback), connections, providers, live: {} });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    requests.push(url);
    if (url.includes("/catalog")) { await pending; return new Response(JSON.stringify(catalog(current))); }
    if (url.includes("/agent-runtimes")) return new Response(JSON.stringify({ runtimes: [], supported_providers: [] }));
    if (url.includes("/status")) return new Response(JSON.stringify({ mapping: connections }));
    if (url.endsWith("/providers")) return new Response(JSON.stringify({ providers }));
    throw new Error(`Unexpected request: ${url}`);
  }));
  openDialog();
  expect(screen.getByTestId("create-agent-model").textContent).toContain("GPT-5.2");
  expect(screen.getByRole("status").textContent).toContain("Loading");
  await act(async () => { release(); await pending; });
  await waitFor(() => expect(screen.getByTestId("create-agent-model").textContent).toContain("GPT-6.1-Sol"));
  fireEvent.click(screen.getByTestId("create-agent-model"));
  expect(screen.getByRole("option", { name: "GPT-6-Astra" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "GPT-6-Luna" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "GPT-5.2" })).toBeNull();
  expect(screen.queryByRole("option", { name: "GPT-5.6-Sol" })).toBeNull();
  expect(requests.filter((url) => url.includes("/catalog"))).toHaveLength(1);
  // A just-saved visibility change wins even if a cached catalog still has the old list.
  act(() => useSavedHiddenModels.setState({ hidden: { "openai-codex": ["gpt-6-astra", "gpt-5.6-sol"] } }));
  expect(screen.queryByRole("option", { name: "GPT-6-Astra" })).toBeNull();
  expect(screen.getByRole("option", { name: "GPT-6-Luna" })).toBeTruthy();
});

test("a failed refresh is visible and the refresh button recovers the current catalog", async () => {
  client.setQueryData(["agent-chat", "catalog", "society"], catalog(fallback));
  client.setQueryData(["agent-chat", "connections"], connections);
  client.setQueryData(["society", "providers"], providers);
  client.setQueryData(["agent-runtimes"], { runtimes: [], supported_providers: [] });
  let fail = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/catalog")) return new Response(JSON.stringify(fail ? {} : catalog(current)), { status: fail ? 503 : 200 });
    if (url.includes("/status")) return new Response(JSON.stringify({ mapping: connections }));
    if (url.endsWith("/providers")) return new Response(JSON.stringify({ providers }));
    throw new Error(`Unexpected request: ${url}`);
  }));
  openDialog();
  expect(await screen.findByRole("alert")).toBeTruthy();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Refresh models" }));
  await waitFor(() => expect(screen.getByTestId("create-agent-model").textContent).toContain("GPT-6.1-Sol"));
  expect(screen.queryByRole("alert")).toBeNull();
});

test.each([
  ["claude-api", "claude", "claude-cli"], ["grok-build", "xai", "grok-cli"], ["openai-codex", "openai", "codex-cli"],
])("%s loads and submits the selected account's models, never the active account's list", async (id, family, runner) => {
  const row = { ...fallback, id, family, runner, hidden_models: [], curated_models: [{ id: "active-model", label: "Active model" }] };
  const accounts = ["first", "second"].map((accountId) => ({ id: accountId, label: accountId, connected: true, mode: "subscription" }));
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/catalog")) {
      const account = new URL(url, "http://localhost").searchParams.get("account_id");
      if (account === "second") await pending;
      return new Response(JSON.stringify(catalog(account ? { ...row, curated_models: [{ id: `${account}-only`, label: `${account} only` }] } : row)));
    }
    if (url.includes("/agent-runtimes")) return new Response(JSON.stringify({ runtimes: [], supported_providers: [] }));
    if (url.includes("/status")) return new Response(JSON.stringify({ mapping: [{ jarvis: id, key_set: true }] }));
    if (url.endsWith("/providers")) return new Response(JSON.stringify({ providers: [{ ...providers[0], id, family, runner, accounts }] }));
    throw new Error(`Unexpected request: ${url}`);
  }));
  openDialog();
  await screen.findByTestId("create-agent-account");
  await waitFor(() => expect(screen.getByTestId("create-agent-model").textContent).toContain("Active model"));
  fireEvent.click(screen.getByTestId("create-agent-account"));
  fireEvent.click(screen.getByRole("option", { name: "second" }));
  expect((screen.getByTestId("create-agent-submit") as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByTestId("create-agent-model").textContent).not.toContain("Active model");
  await act(async () => { release(); await pending; });
  await waitFor(() => expect(screen.getByTestId("create-agent-model").textContent).toContain("second only"));
  fireEvent.click(screen.getByTestId("create-agent-submit"));
  await waitFor(() => expect(createAgent).toHaveBeenCalledWith(expect.objectContaining({ provider: id, model: "second-only", accountId: "second" })));
});

test.each(["claude-api", "gemini", "grok", "openrouter", "nvidia"])("%s distinguishes a failed model catalog from an empty one and recovers without inventing a default", async (id) => {
  const row = { ...fallback, id, family: id, runner: "brain", cli_installed: null, models_source: "live" as const,
    hidden_models: [], curated_models: [{ id: "stale", label: "Stale fallback" }] };
  let phase: "error" | "empty" | "ready" = "error";
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/catalog")) return new Response(JSON.stringify(catalog(row)));
    if (url.includes("/agent-runtimes")) return new Response(JSON.stringify({ runtimes: [], supported_providers: [] }));
    if (url.includes("/status")) return new Response(JSON.stringify({ mapping: [{ jarvis: id, key_set: true, api_key_set: true }] }));
    if (url.endsWith("/providers")) return new Response(JSON.stringify({ providers: [{ ...providers[0], id, subscription: false, accounts: [] }] }));
    if (url.endsWith("/models")) return new Response(JSON.stringify({ models: phase === "ready" ? [{ id: "live-model", label: "Live model" }] : [] }), { status: phase === "error" ? 401 : 200 });
    throw new Error(`Unexpected request: ${url}`);
  }));
  openDialog();
  await screen.findByRole("alert");
  expect((screen.getByTestId("create-agent-submit") as HTMLButtonElement).disabled).toBe(true);
  phase = "empty";
  fireEvent.click(screen.getByRole("button", { name: "Refresh models" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(screen.getByTestId("create-agent-model").textContent).not.toContain("Stale fallback");
  expect((screen.getByTestId("create-agent-submit") as HTMLButtonElement).disabled).toBe(true);
  phase = "ready";
  fireEvent.click(screen.getByRole("button", { name: "Refresh models" }));
  await waitFor(() => expect(screen.getByTestId("create-agent-model").textContent).toContain("Live model"));
  expect((screen.getByTestId("create-agent-submit") as HTMLButtonElement).disabled).toBe(false);
  expect(createAgent).not.toHaveBeenCalled();
});
