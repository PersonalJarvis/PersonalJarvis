import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import type { AgentChatProvider, AgentConnectionRow } from "@/lib/agentChatApi";
import { SAMPLE_ROSTER } from "../mockRoster";
import { rowToAgent, useSocietyAgent, type SocietyAgent } from "../data";
import { AgentModelPicker } from "./AgentModelPicker";
import type { SocietyAgentRow } from "@/lib/societyApi";
import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { createAgentChatStore, type AgentChatStoreHook } from "@/store/agentChat";

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
let catalogGate: Promise<void> | undefined;
let connectionsGate: Promise<void> | undefined;
let accountsGate: Promise<void> | undefined;

beforeEach(async () => {
  await loadLocaleChunk("society");
  row = {
    agent_id: "scout", name: "Scout", title: "Research", description: "Keep my instructions",
    tier: "specialist", state: "active", provider: "openai", model: "openai-large", effort: "high", account_id: "saved-seat",
    avatar: {}, grants: [], focus: [], denies: [], approval_rules: {}, stats: { runs: 3, total_cost_usd: 1, last_active_ms: null },
  } as unknown as SocietyAgentRow;
  posts = []; failSave = false; saving = vi.fn();
  extraProviders = []; liveModels = {}; providerRows = []; catalogCalls = 0;
  catalogGate = undefined; connectionsGate = undefined; accountsGate = undefined;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/catalog")) await catalogGate;
    if (url.endsWith("/status")) await connectionsGate;
    if (url.endsWith("/providers")) await accountsGate;
    const response = (data: unknown, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => data }) as Response;
    if (url.endsWith("/model")) {
      const choice = JSON.parse(String(init?.body));
      posts.push(choice);
      if (failSave) return response({}, false);
      row = { ...row, ...choice };
      return response({ agent: row, reseated: "society:scout" });
    }
    if (url.includes("/catalog")) { catalogCalls++; return response({ providers: [provider("openai"), provider("gemini"), provider("offline"), provider("ollama", { keyless: true, models_source: "live" }), ...extraProviders] }); }
    if (url.endsWith("/status")) return response({ mapping: [{ jarvis: "openai", key_set: true }, { jarvis: "gemini", key_set: true }, { jarvis: "openrouter", key_set: true }, { jarvis: "offline", key_set: false }] });
    if (url.endsWith("/models")) return response({ models: liveModels[url.split("/").at(-2)!] ?? [] });
    if (url.endsWith("/providers")) return response({ providers: providerRows });
    if (url.endsWith("/agents")) return response({ agents: [row] });
    throw new Error(`Unexpected request: ${url}`);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mount(busy = false, inDialog = false, store?: AgentChatStoreHook) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const initial: SocietyAgent = { ...SAMPLE_ROSTER[1], ...rowToAgent(row) };
  client.setQueryData(["society", "roster"], { agents: [initial], sample: false });
  function Subject() {
    const { agent } = useSocietyAgent("scout");
    const picker = <AgentModelPicker agent={agent ?? initial} busy={busy} onSavingChange={saving} />;
    return store ? <AgentChatStoreProvider store={store}>{picker}</AgentChatStoreProvider> : picker;
  }
  return render(<QueryClientProvider client={client}>{inDialog ? <Dialog.Root defaultOpen>
    <Dialog.Content><Dialog.Title>Agent</Dialog.Title><Dialog.Description>Model controls</Dialog.Description><Subject /></Dialog.Content>
  </Dialog.Root> : <Subject />}</QueryClientProvider>);
}

test("already loaded chat models are immediately selectable while every refresh is stalled", async () => {
  let release!: () => void;
  catalogGate = connectionsGate = accountsGate = new Promise<void>((resolve) => { release = resolve; });
  const store = createAgentChatStore("society");
  store.setState({
    catalog: { providers: [provider("openai")], default_cwd: "", shell: "" },
    connections: [{ jarvis: "openai", key_set: true }] as AgentConnectionRow[],
  });
  try {
    mount(false, false, store);
    fireEvent.click(screen.getByRole("button", { name: "Model" }));
    // Synchronous: no await, no timers and none of the requests has resolved.
    expect(screen.getByTitle("openai-small")).toBeTruthy();
    expect(screen.queryByText("Loading the provider catalog…")).toBeNull();
    fireEvent.click(screen.getByTitle("openai-small"));
    await waitFor(() => expect(posts[0].model).toBe("openai-small"));
  } finally {
    await act(async () => { release(); await catalogGate; });
  }
});

test("a cold menu renders models without waiting for subscription account discovery", async () => {
  let release!: () => void;
  accountsGate = new Promise<void>((resolve) => { release = resolve; });
  try {
    mount(); await open();
    expect(screen.getByTitle("gemini-small")).toBeTruthy();
    expect(screen.queryByText("Loading the provider catalog…")).toBeNull();
  } finally {
    await act(async () => { release(); await accountsGate; });
  }
});

test("catalog loading starts when the card mounts, before the picker is clicked", async () => {
  mount();
  await waitFor(() => expect(catalogCalls).toBe(1));
  expect(screen.queryByRole("menu")).toBeNull();
  await open();
  expect(catalogCalls).toBe(1);
});

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
    curated_models: [{ id: "opencode/test-free", label: "Free model" }] })];
  mount(); await open();
  fireEvent.click(screen.getByTitle("opencode/test-free"));
  await waitFor(() => expect(posts[0].provider).toBe("opencode"));
  expect(posts[0].model).toBe("opencode/test-free");
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
  fireEvent.click(await screen.findByRole("button", { name: /Show more models/ }));
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

test("subscriptions precede OpenCode, which initially shows only explicit free models", async () => {
  extraProviders = [provider("opencode", { runner: "opencode-cli", cli_installed: true, curated_models: [
    { id: "opencode/paid", label: "Paid" }, { id: "opencode/test-free", label: "Free" },
    { id: "opencode/big-pickle", label: "Big Pickle" },
  ] }), provider("z-plan", { runner: "grok-cli", cli_installed: true })];
  mount(); await open();
  const groups = screen.getAllByRole("group");
  expect(groups[0].getAttribute("aria-label")).toBe("Grok subscription");
  expect(groups[1].getAttribute("aria-label")).toBe("opencode");
  expect(screen.getByTitle("opencode/test-free")).toBeTruthy();
  expect(screen.getByTitle("opencode/big-pickle")).toBeTruthy();
  expect(screen.queryByTitle("opencode/paid")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Show more models/ }));
  expect(screen.getByTitle("opencode/paid")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Show fewer models" }));
  expect(screen.queryByTitle("opencode/paid")).toBeNull();
  expect(posts).toEqual([]);
});

test("OpenRouter folds independently, searching finds hidden models, and clearing restores the fold", async () => {
  extraProviders = [provider("openrouter")];
  mount(); const input = await open();
  const toggle = screen.getByRole("button", { name: "openrouter · API key" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByTitle("openrouter-small")).toBeNull();
  fireEvent.click(toggle);
  expect(screen.getByTitle("openrouter-small")).toBeTruthy();
  fireEvent.click(toggle);
  fireEvent.change(input, { target: { value: "openrouter large" } });
  expect(screen.getByTitle("openrouter-large")).toBeTruthy();
  expect(screen.queryByTitle("openrouter-small")).toBeNull();
  fireEvent.change(input, { target: { value: "" } });
  expect(screen.queryByTitle("openrouter-large")).toBeNull();
  expect(posts).toEqual([]);
});

test("hidden OpenCode models remain selectable through search and a reopened menu is compact", async () => {
  extraProviders = [provider("opencode", { runner: "opencode-cli", cli_installed: true })];
  mount(); const input = await open();
  fireEvent.click(screen.getByRole("button", { name: /Show more models/ }));
  fireEvent.keyDown(input, { key: "Escape" });
  const reopened = await open();
  expect(screen.queryByTitle("opencode-large")).toBeNull();
  fireEvent.change(reopened, { target: { value: "opencode large" } });
  fireEvent.click(screen.getByTitle("opencode-large"));
  await waitFor(() => expect(posts[0]).toEqual({ provider: "opencode", model: "opencode-large", effort: "high", account_id: "" }));
});
