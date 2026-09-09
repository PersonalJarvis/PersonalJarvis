import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
let extraProviders: AgentChatProvider[];
let liveModels: Record<string, { id: string; label: string }[]>;
let providerRows: unknown[];
let catalogCalls: number;
let saving: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  await loadLocaleChunk("society");
  row = {
    agent_id: "scout", name: "Scout", title: "Research", description: "Keep my instructions",
    tier: "specialist", state: "active", provider: "openai", model: "openai-large", effort: "high", account_id: "saved-seat",
    avatar: {}, grants: [], focus: [], denies: [], approval_rules: {}, stats: { runs: 3, total_cost_usd: 1, last_active_ms: null },
  } as unknown as SocietyAgentRow;
  posts = []; failSave = false; saving = vi.fn();
  extraProviders = []; liveModels = {}; providerRows = []; catalogCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const response = (data: unknown, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => data }) as Response;
    if (url.endsWith("/model")) {
      const choice = JSON.parse(String(init?.body));
      posts.push(choice);
      if (failSave) return response({}, false);
      row = { ...row, ...choice };
      return response({ agent: row, reseated: "society:scout" });
    }
    if (url.includes("/catalog")) { catalogCalls++; return response({ providers: [provider("openai"), provider("gemini"), provider("offline"), provider("ollama", { keyless: true, models_source: "live" }), ...extraProviders] }); }
    if (url.endsWith("/status")) return response({ mapping: [{ jarvis: "openai", key_set: true }, { jarvis: "gemini", key_set: true }, { jarvis: "offline", key_set: false }] });
    if (url.endsWith("/models")) return response({ models: liveModels[url.split("/").at(-2)!] ?? [] });
    if (url.endsWith("/providers")) return response({ providers: providerRows });
    if (url.endsWith("/agents")) return response({ agents: [row] });
    throw new Error(`Unexpected request: ${url}`);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mount(busy = false, inDialog = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const initial: SocietyAgent = { ...SAMPLE_ROSTER[1], ...rowToAgent(row) };
  client.setQueryData(["society", "roster"], { agents: [initial], sample: false });
  function Subject() {
    const { agent } = useSocietyAgent("scout");
    return <AgentModelPicker agent={agent ?? initial} busy={busy} onSavingChange={saving} />;
  }
  return render(<QueryClientProvider client={client}>{inDialog ? <Dialog.Root defaultOpen>
    <Dialog.Content><Dialog.Title>Agent</Dialog.Title><Dialog.Description>Model controls</Dialog.Description><Subject /></Dialog.Content>
  </Dialog.Root> : <Subject />}</QueryClientProvider>);
}

async function open() {
  fireEvent.click(screen.getByRole("button", { name: "Model" }));
  await screen.findByTitle("openai-large");
  return screen.getByRole("textbox", { name: "Search models" });
}

test("groups connected models and hides disconnected or empty local endpoints", async () => {
  mount(); await open();
  expect(screen.getByRole("group", { name: "openai · API key" })).toBeTruthy();
  expect(screen.getByTitle("gemini-small")).toBeTruthy();
  expect(screen.queryByTitle("offline-small")).toBeNull();
  expect(screen.queryByTitle("ollama-small")).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Apply model" })).toBeNull();
});

test("clicking a model persists immediately, preserves the account and updates the roster", async () => {
  mount(); await open();
  fireEvent.click(screen.getByTitle("openai-small"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Model" }).textContent).toContain("openai-small"));
  expect(posts).toEqual([{ provider: "openai", model: "openai-small", effort: "low", account_id: "saved-seat" }]);
  expect(row.description).toBe("Keep my instructions");
  expect(saving.mock.calls.map((call) => call[0])).toEqual([true, false]);
  await open();
  expect(screen.getByTitle("openai-small").getAttribute("aria-checked")).toBe("true");
});

test("changing provider resets the subscription account and uses a supported effort", async () => {
  mount(); await open();
  fireEvent.click(screen.getByTitle("gemini-small"));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ provider: "gemini", model: "gemini-small", effort: "low", account_id: "" });
});

test("a failed save keeps the current model and permits retry", async () => {
  failSave = true; mount(); await open();
  fireEvent.click(screen.getByTitle("openai-small"));
  expect((await screen.findByRole("alert")).textContent).toContain("Could not save the model");
  expect(screen.getByRole("button", { name: "Model" }).textContent).toContain("openai-large");
  failSave = false;
  fireEvent.click(screen.getByTitle("openai-small"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Model" }).textContent).toContain("openai-small"));
});

test("does not permit a switch during a running response", () => {
  mount(true);
  expect((screen.getByRole("button", { name: "Model" }) as HTMLButtonElement).disabled).toBe(true);
});

test("search filters by model and provider, and Escape closes without saving", async () => {
  mount(); const input = await open();
  fireEvent.change(input, { target: { value: "gemini small" } });
  expect(screen.getByTitle("gemini-small")).toBeTruthy();
  expect(screen.queryByTitle("gemini-large")).toBeNull();
  expect(screen.queryByTitle("openai-small")).toBeNull();
  fireEvent.keyDown(input, { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
  expect(posts).toEqual([]);
});

test("the effort submenu commits the model and chosen effort together", async () => {
  mount(); await open();
  fireEvent.click(within(screen.getByRole("group", { name: "gemini · API key" })).getByRole("button", { name: "Thinking effort: Large" }));
  fireEvent.click(within(screen.getByRole("menu", { name: "Thinking effort" })).getByRole("menuitemradio", { name: "Medium" }));
  await waitFor(() => expect(posts[0]).toEqual({ provider: "gemini", model: "gemini-large", effort: "medium", account_id: "" }));
});

test("OpenCode models are offered without a duplicate app-managed login", async () => {
  extraProviders = [provider("opencode", { runner: "opencode-cli", cli_installed: true,
    curated_models: [{ id: "opencode/free-model", label: "Free model" }] })];
  mount(); await open();
  fireEvent.click(screen.getByTitle("opencode/free-model"));
  await waitFor(() => expect(posts[0].provider).toBe("opencode"));
  expect(posts[0].model).toBe("opencode/free-model");
});

test("all connected subscription accounts are selectable for a model", async () => {
  extraProviders = [provider("openai-codex", { runner: "codex-cli", cli_installed: true })];
  providerRows = [{ id: "openai-codex", subscription: true, accounts: [
    { id: "work", label: "Work", connected: true }, { id: "personal", label: "Personal", connected: true },
    { id: "expired", label: "Expired", connected: false },
  ] }];
  mount(); await open();
  fireEvent.click(screen.getByRole("button", { name: "Account: ChatGPT / Codex subscription" }));
  expect(screen.queryByRole("menuitemradio", { name: "Expired" })).toBeNull();
  fireEvent.click(screen.getByRole("menuitemradio", { name: "Personal" }));
  fireEvent.click(screen.getByTitle("openai-codex-large"));
  await waitFor(() => expect(posts[0].account_id).toBe("personal"));
  expect(posts[0].provider).toBe("openai-codex");
});

test("refresh reloads catalogs and reveals newly available models", async () => {
  mount(); await open();
  extraProviders = [provider("opencode", { runner: "opencode-cli", cli_installed: true })];
  fireEvent.click(screen.getByRole("button", { name: "Refresh models" }));
  await screen.findByTitle("opencode-small");
  expect(catalogCalls).toBe(2);
});

test("local endpoints show only installed models, and search works by model id", async () => {
  liveModels.ollama = [{ id: "qwen3:8b", label: "Qwen 3" }];
  mount(); const input = await open();
  await screen.findByTitle("qwen3:8b");
  fireEvent.change(input, { target: { value: "qwen3:8b" } });
  fireEvent.click(screen.getByTitle("qwen3:8b"));
  await waitFor(() => expect(posts[0].provider).toBe("ollama"));
});

test("keyboard navigation moves through results and closes back to the trigger", async () => {
  mount(); const input = await open();
  fireEvent.keyDown(input, { key: "ArrowDown" });
  expect(document.activeElement).toBe(screen.getByTitle("gemini-small"));
  fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
  expect(document.activeElement).toBe(screen.getByTitle("gemini-large"));
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Model" }));
});

test("Escape closes a nested menu before the surrounding agent card", async () => {
  mount(false, true); const input = await open();
  fireEvent.keyDown(input, { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Model" }));
});
