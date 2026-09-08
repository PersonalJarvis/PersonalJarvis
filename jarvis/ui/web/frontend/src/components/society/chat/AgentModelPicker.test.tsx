import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import type { AgentChatProvider } from "@/lib/agentChatApi";
import { SAMPLE_ROSTER } from "../mockRoster";
import { rowToAgent, useSocietyAgent, type SocietyAgent } from "../data";
import { AgentModelPicker } from "./AgentModelPicker";
import type { SocietyAgentRow } from "@/lib/societyApi";

const provider = (id: string, overrides: Partial<AgentChatProvider> = {}): AgentChatProvider => ({
  id, label: id, family: id, runner: "brain", models_source: "curated",
  curated_models: [{ id: `${id}-small`, label: "Small", efforts: ["low"] }, { id: `${id}-large`, label: "Large", efforts: ["medium", "high"] }],
  default_model: `${id}-small`, keyless: false, native_resume: false,
  effort_levels: ["low", "medium", "high"], default_effort: "high", permission_modes: [], default_permission_mode: "ask", cli_installed: null,
  ...overrides,
});
let row: SocietyAgentRow;
let posts: Record<string, string>[];
let failSave: boolean;
let saving: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  await loadLocaleChunk("society");
  row = {
    agent_id: "scout", name: "Scout", title: "Research", description: "Keep my instructions",
    tier: "specialist", state: "active", provider: "openai", model: "openai-large", effort: "high", account_id: "saved-seat",
    avatar: {}, grants: [], focus: [], denies: [], approval_rules: {}, stats: { runs: 3, total_cost_usd: 1, last_active_ms: null },
  } as unknown as SocietyAgentRow;
  posts = []; failSave = false; saving = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const response = (data: unknown, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => data }) as Response;
    if (url.endsWith("/model")) {
      const choice = JSON.parse(String(init?.body));
      posts.push(choice);
      if (failSave) return response({}, false);
      row = { ...row, ...choice };
      return response({ agent: row, reseated: "society:scout" });
    }
    if (url.includes("/catalog")) return response({ providers: [provider("openai"), provider("gemini"), provider("offline"), provider("ollama", { keyless: true, models_source: "live" })] });
    if (url.endsWith("/status")) return response({ mapping: [{ jarvis: "openai", key_set: true }, { jarvis: "gemini", key_set: true }, { jarvis: "offline", key_set: false }] });
    if (url.endsWith("/models")) return response({ models: [] });
    if (url.endsWith("/providers")) return response({ providers: [] });
    if (url.endsWith("/agents")) return response({ agents: [row] });
    throw new Error(`Unexpected request: ${url}`);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mount(busy = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const initial: SocietyAgent = { ...SAMPLE_ROSTER[1], ...rowToAgent(row) };
  client.setQueryData(["society", "roster"], { agents: [initial], sample: false });
  function Subject() {
    const { agent } = useSocietyAgent("scout");
    return <AgentModelPicker agent={agent ?? initial} busy={busy} onSavingChange={saving} />;
  }
  return render(<QueryClientProvider client={client}><Subject /></QueryClientProvider>);
}

async function open() {
  fireEvent.click(screen.getByRole("button", { name: "Model" }));
  return screen.findByRole("combobox", { name: "Provider" });
}

test("offers only connected providers and local endpoints with installed models", async () => {
  mount();
  const pick = await open();
  expect(pick.textContent).toContain("openai");
  expect(pick.textContent).toContain("gemini");
  expect(pick.textContent).not.toContain("offline");
  expect(pick.textContent).not.toContain("ollama");
});

test("persists a model change, keeps the account and updates the roster immediately", async () => {
  mount(); await open();
  fireEvent.change(screen.getByRole("combobox", { name: "Model" }), { target: { value: "openai-small" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply model" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Model" }).textContent).toContain("openai-small"));
  expect(posts).toEqual([{ provider: "openai", model: "openai-small", effort: "low", account_id: "saved-seat" }]);
  expect(row.description).toBe("Keep my instructions");
  expect(saving.mock.calls.map((call) => call[0])).toEqual([true, false]);
  await open();
  expect((screen.getByRole("combobox", { name: "Model" }) as HTMLSelectElement).value).toBe("openai-small");
});

test("changing provider resets the subscription account and selects a supported effort", async () => {
  mount(); const pick = await open();
  fireEvent.change(pick, { target: { value: "gemini" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply model" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ provider: "gemini", model: "gemini-small", effort: "low", account_id: "" });
});

test("a failed save keeps the current model and allows retry", async () => {
  failSave = true; mount(); await open();
  fireEvent.change(screen.getByRole("combobox", { name: "Model" }), { target: { value: "openai-small" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply model" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Could not save the model");
  expect(screen.getByRole("button", { name: "Model" }).textContent).toContain("openai-large");
  failSave = false;
  fireEvent.click(screen.getByRole("button", { name: "Apply model" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Model" }).textContent).toContain("openai-small"));
});

test("does not offer a switch during a running response", () => {
  mount(true);
  expect((screen.getByRole("button", { name: "Model" }) as HTMLButtonElement).disabled).toBe(true);
});

test("Escape discards an unsaved choice without saving", async () => {
  mount(); await open();
  fireEvent.change(screen.getByRole("combobox", { name: "Model" }), { target: { value: "openai-small" } });
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Model" }), { key: "Escape" });
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(posts).toEqual([]);
  await open();
  expect((screen.getByRole("combobox", { name: "Model" }) as HTMLSelectElement).value).toBe("openai-large");
});
