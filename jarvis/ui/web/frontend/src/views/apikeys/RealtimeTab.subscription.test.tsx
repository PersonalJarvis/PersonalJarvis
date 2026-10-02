import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { RealtimeTab } from "./RealtimeTab";
import { liveProfileReady, type LiveAuthMode, type LiveProfileState } from "@/components/providers/LiveProfile";
import type { ProviderDescriptor } from "@/hooks/useProviders";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
const voiceRuntime = vi.hoisted(() => ({ statusKnown: true, mode: "realtime", activeModel: "",
  activeProvider: null as string | null, sessionActive: false, activeSessionProvider: "" }));
vi.mock("@/hooks/useVoiceMode", () => ({ useVoiceMode: () => voiceRuntime }));
vi.mock("@/components/RealtimeOptionsControl", () => ({ RealtimeOptionsControl: () => <div>Provider model options</div> }));
vi.mock("@/components/providers/ProviderTierSection", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/components/providers/ProviderTierSection")>(),
  AuthWidget: () => <p>API credential editor</p>,
  ProviderTestControl: () => <button>Paid API probe</button>,
  Tag: () => null,
}));

const scrollIntoViewDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();
  if (scrollIntoViewDescriptor) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", scrollIntoViewDescriptor);
  else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  voiceRuntime.activeProvider = null;
  voiceRuntime.sessionActive = false;
  voiceRuntime.activeSessionProvider = "";
});

function state(mode: LiveAuthMode): LiveProfileState {
  return {
    key_ready: mode === "api_key", active: true, agent_configured: true,
    subscription: { account_id: "codex:default", account_connected: true, voice_status: "unverified" },
    profile: {
      auth_mode: mode, model: "gpt-live-1", voice: "gleam", backend_model: "api-model",
      subscription_account_id: "", subscription_voice: "cove", subscription_backend_model: "subscription-model",
      subscription_reasoning_effort: "medium", reasoning_effort: "high", web_search: true,
      configured: true, instructions: "", backend_instructions: "",
    },
  };
}

function setup(mode: LiveAuthMode, options: {
  keyReady?: boolean; subscriptionModel?: string; accountConnected?: boolean;
  legacy?: boolean; saveFails?: boolean; includeGemini?: boolean;
} = {}) {
  let current = state(mode);
  current.key_ready = options.keyReady ?? current.key_ready;
  current.profile.subscription_backend_model = options.subscriptionModel ?? current.profile.subscription_backend_model;
  if (options.accountConnected === false) current.subscription!.account_connected = false;
  if (options.legacy) {
    delete current.profile.auth_mode;
    delete current.profile.subscription_backend_model;
    delete current.profile.subscription_account_id;
    delete current.profile.subscription_voice;
    delete current.profile.subscription_reasoning_effort;
    delete current.subscription;
  }
  const writes: LiveProfileState["profile"][] = [];
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      const chosen = JSON.parse(String(init.body)) as LiveProfileState["profile"];
      writes.push(chosen);
      if (options.saveFails) return { ok: false, status: 409, json: async () => ({ detail: "Selection could not be saved" }) };
      current = { ...current, profile: chosen };
    }
    const data = url === "/api/agent-accounts"
      ? { platforms: [{ platform: "codex", active_account: "codex:default", accounts: [
        { id: "codex:default", label: "My ChatGPT", connected: options.accountConnected !== false, mode: "subscription" },
      ] }] }
      : url.startsWith("/api/live/options")
        ? { models: url.includes("chatgpt_subscription")
          ? [{ id: "subscription-model", label: "Subscription model" }]
          : [{ id: "api-model", label: "API model" }, { id: "cheap-api", label: "Cheaper API model" }],
          voices: [url.includes("chatgpt_subscription") ? "cove" : "gleam"], efforts: ["", "medium", "high"] }
        : url.includes("/realtime-options")
          ? { models: [], voices: [], current_model: "", current_voice: "", preview_available: false }
          : current;
    return { ok: true, json: async () => data };
  });
  vi.stubGlobal("fetch", fetcher);
  const provider: ProviderDescriptor = {
    id: "openai-live", label: "OpenAI GPT-Live", tier: "realtime", auth_mode: "api_key", billing: "api",
    active: true, configured: true, configuration_surface: "live", secret_keys: ["openai_api_key"],
    secrets_set: {}, dashboard_url: null, login_cli: null, install_hint: null, credential_path_hint: null,
    cli_installed: null, credential_help: null, signup_url: null, alt_credential: null,
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let suppliedProviders = [provider, ...(options.includeGemini ? [{ ...provider,
    id: "gemini-live", label: "Gemini Live", configuration_surface: "provider" as const, active: false,
  }] : [])];
  let rendered: ReturnType<typeof render>;
  const onActivateOptimistic = vi.fn((_tier: string, id: string) => {
    suppliedProviders = suppliedProviders.map((entry) => ({ ...entry, active: entry.id === id }));
    rendered.rerender(tree());
  });
  const tree = () => <QueryClientProvider client={client}><RealtimeTab providers={suppliedProviders} loading={false} error={null}
    onChanged={vi.fn()} onActivateOptimistic={onActivateOptimistic} /></QueryClientProvider>;
  rendered = render(tree());
  return { client, writes, fetcher, onActivateOptimistic };
}

const subscriptionRow = () => screen.getByTestId("realtime-provider-openai-live-subscription");
const apiRow = () => screen.getByTestId("realtime-provider-openai-live");

it("places the exact GPT Subscription row above the independent API row", async () => {
  const { client } = setup("api_key");
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  const rows = within(screen.getByTestId("realtime-provider-list")).getAllByRole("button");
  expect(rows.map((row) => row.dataset.testid)).toEqual([
    "realtime-provider-openai-live-subscription", "realtime-provider-openai-live",
  ]);
  expect(within(subscriptionRow()).getByText("GPT Subscription")).toBeTruthy();
  expect(within(subscriptionRow()).getByText("provider_billing.subscription")).toBeTruthy();
  expect(within(apiRow()).getByText("provider_billing.api")).toBeTruthy();
  expect(apiRow().dataset.active).toBe("true");
  expect(subscriptionRow().dataset.active).toBe("false");
  client.clear();
});

it("activates a complete subscription on its row click and switches back with the API row", async () => {
  const { client, writes, onActivateOptimistic } = setup("api_key", { keyReady: true });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  fireEvent.click(subscriptionRow());
  await waitFor(() => expect(subscriptionRow().dataset.active).toBe("true"));
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ auth_mode: "chatgpt_subscription", subscription_backend_model: "subscription-model",
    voice: "gleam", backend_model: "api-model", reasoning_effort: "high" });
  expect(screen.queryByText("API credential editor")).toBeNull();
  expect(screen.queryByRole("button", { name: "Paid API probe" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "live.billing_method" })).toBeNull();
  expect(apiRow().dataset.active).toBe("false");
  expect(within(apiRow()).getByText("provider_billing.api")).toBeTruthy();
  expect(within(screen.getByTestId("voice-now")).getByText("GPT Subscription")).toBeTruthy();
  expect(onActivateOptimistic).not.toHaveBeenCalled();
  fireEvent.click(apiRow());
  await waitFor(() => expect(apiRow().dataset.active).toBe("true"));
  expect(writes).toHaveLength(2);
  expect(writes[1]).toMatchObject({ auth_mode: "api_key", backend_model: "api-model", voice: "gleam",
    subscription_backend_model: "subscription-model", subscription_voice: "cove" });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  client.clear();
});

it("opens missing subscription model setup on click without changing the active API provider", async () => {
  const scroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scroll });
  const { client, writes } = setup("api_key", { subscriptionModel: "" });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  expect(within(subscriptionRow()).getByText("live.subscription_model_required")).toBeTruthy();
  expect(within(subscriptionRow()).queryByText("apikeys_view.state_missing")).toBeNull();
  fireEvent.click(subscriptionRow());
  await within(await screen.findByTestId("live-profile")).findByText("live.subscription_connected");
  expect(document.activeElement).toBe(screen.getByTestId("realtime-provider-setup"));
  expect(scroll).toHaveBeenCalledWith({ block: "start" });
  fireEvent.click(subscriptionRow());
  expect(scroll).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "live.subscription_sign_in" })).toBeNull();
  expect(writes).toHaveLength(0);
  expect(subscriptionRow().getAttribute("aria-pressed")).toBe("true");
  expect(subscriptionRow().dataset.active).toBe("false");
  expect(apiRow().dataset.active).toBe("true");
  expect((screen.getByRole("button", { name: "live.save" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("live.billing_pending")).toBeTruthy();
  await waitFor(() => expect((screen.getByRole("combobox", { name: "live.thinking_model" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("combobox", { name: "live.thinking_model" }));
  fireEvent.click(await screen.findByRole("option", { name: /Subscription model/ }));
  expect(writes).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "live.save" }));
  await screen.findByText("live.saved");
  expect(writes[0]).toMatchObject({ auth_mode: "chatgpt_subscription", subscription_backend_model: "subscription-model" });
  expect(subscriptionRow().dataset.active).toBe("true");
  client.clear();
});

it("shows account sign-in for an incomplete subscription without falling back to the saved API key", async () => {
  const { client, writes } = setup("api_key", { accountConnected: false });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  expect(within(subscriptionRow()).getByText("live.subscription_sign_in")).toBeTruthy();
  expect(within(subscriptionRow()).queryByText("apikeys_view.state_missing")).toBeNull();
  fireEvent.click(subscriptionRow());
  await screen.findByRole("button", { name: "live.subscription_sign_in" });
  expect(writes).toHaveLength(0);
  expect(apiRow().dataset.active).toBe("true");
  expect(screen.queryByRole("button", { name: "Paid API probe" })).toBeNull();
  client.clear();
});

it("activates a saved subscription without any API key", async () => {
  const { client, writes } = setup("api_key", { keyReady: false });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  fireEvent.click(subscriptionRow());
  await waitFor(() => expect(subscriptionRow().dataset.active).toBe("true"));
  expect(writes[0].auth_mode).toBe("chatgpt_subscription");
  expect(screen.queryByText("live.key_required")).toBeNull();
  client.clear();
});

it("preserves the active API row when a subscription activation fails", async () => {
  const { client, writes, onActivateOptimistic } = setup("api_key", { saveFails: true });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  fireEvent.click(subscriptionRow());
  await waitFor(() => expect(writes).toHaveLength(1));
  await within(await screen.findByTestId("live-profile")).findByText("live.subscription_connected");
  expect(apiRow().dataset.active).toBe("true");
  expect(subscriptionRow().dataset.active).toBe("false");
  expect(onActivateOptimistic).not.toHaveBeenCalled();
  client.clear();
});

it("offers an explicit restart on an older backend and never submits an unsupported subscription profile", async () => {
  const { client, writes, fetcher } = setup("api_key", { legacy: true });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  fireEvent.click(subscriptionRow());
  await within(await screen.findByTestId("live-profile")).findByText("settings_view.wake_word.restart_required");
  expect((screen.getByRole("button", { name: "live.save" }) as HTMLButtonElement).disabled).toBe(true);
  expect(apiRow().dataset.active).toBe("true");
  expect(writes).toHaveLength(0);
  expect(fetcher.mock.calls.some(([url]) => url.includes("auth_mode=chatgpt_subscription"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "taskbar_view.restart_now" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url, init]) => url === "/api/settings/restart-app" && init?.method === "POST")).toBe(true));
  client.clear();
});

it("keeps the original API save contract on an older backend", async () => {
  const { client, writes } = setup("api_key", { legacy: true });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  fireEvent.click(screen.getByRole("combobox", { name: "live.thinking_model" }));
  fireEvent.click(await screen.findByRole("option", { name: /Cheaper API model/ }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ backend_model: "cheap-api", voice: "gleam" });
  expect(writes[0]).not.toHaveProperty("auth_mode");
  await screen.findByText("live.saved");
  client.clear();
});

it("preserves grouped settings for the saved subscription while the API row remains an API choice", async () => {
  const { client } = setup("chatgpt_subscription");
  await within(await screen.findByTestId("live-profile")).findByText("live.subscription_connected");
  expect(screen.queryByText("API credential editor")).toBeNull();
  expect(screen.queryByRole("button", { name: "Paid API probe" })).toBeNull();
  expect(within(apiRow()).getByText("provider_billing.api")).toBeTruthy();
  expect(within(screen.getByTestId("voice-now")).getByText("GPT Subscription")).toBeTruthy();
  expect(within(screen.getByTestId("voice-now")).getByText("provider_billing.subscription")).toBeTruthy();
  expect(screen.getByRole("region", { name: "live.conversation_heading" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "live.thinking_heading" })).toBeTruthy();
  expect(screen.getByText("live.prompts").closest("details")).toBeTruthy();
  expect(screen.getByTestId("live-profile-saved")).toBeTruthy();
  client.clear();
});

it("checks both saved credential paths independently", () => {
  const subscription = state("chatgpt_subscription");
  expect(liveProfileReady(subscription)).toBe(true);
  expect(liveProfileReady({ ...subscription, key_ready: true, subscription: undefined })).toBe(false);
  expect(liveProfileReady({ ...subscription, profile: { ...subscription.profile, subscription_backend_model: "" } })).toBe(false);
  const api = state("api_key");
  expect(liveProfileReady(api)).toBe(true);
  expect(liveProfileReady({ ...api, key_ready: false })).toBe(false);
  expect(liveProfileReady(api, "chatgpt_subscription")).toBe(true);
  expect(liveProfileReady({ ...subscription, key_ready: true }, "api_key")).toBe(true);
});

it.each([false, true])("keeps the actual API runtime in the header despite the saved subscription (running call=%s)", async (running) => {
  voiceRuntime.activeProvider = running ? "openai-live-subscription" : "openai-live";
  voiceRuntime.sessionActive = running;
  voiceRuntime.activeSessionProvider = running ? "openai-live" : "";
  const { client } = setup("chatgpt_subscription");
  await within(await screen.findByTestId("live-profile")).findByText("live.subscription_connected");
  expect(within(screen.getByTestId("voice-now")).getByText("OpenAI GPT-Live")).toBeTruthy();
  expect(within(screen.getByTestId("voice-now")).getByText("provider_billing.api")).toBeTruthy();
  expect(within(subscriptionRow()).getByText("provider_billing.subscription")).toBeTruthy();
  client.clear();
});


it.each(["api_key", "chatgpt_subscription"] as const)("clears the %s Live row when Gemini becomes active before cached Live queries update", async (mode) => {
  voiceRuntime.activeProvider = mode === "api_key" ? "openai-live" : "openai-live-subscription";
  const { client, onActivateOptimistic } = setup(mode, { includeGemini: true });
  await screen.findByRole("combobox", { name: "live.thinking_model" });
  fireEvent.click(screen.getByTestId("realtime-provider-gemini-live"));
  await waitFor(() => expect(onActivateOptimistic).toHaveBeenCalledWith("realtime", "gemini-live"));
  expect(screen.getByTestId("realtime-provider-gemini-live").dataset.active).toBe("true");
  expect(apiRow().dataset.active).toBe("false");
  expect(subscriptionRow().dataset.active).toBe("false");
  expect(screen.getByTestId("realtime-provider-list").querySelectorAll('[data-active="true"]')).toHaveLength(1);
  expect(within(screen.getByTestId("voice-now")).getByText("Gemini Live")).toBeTruthy();
  client.clear();
});
