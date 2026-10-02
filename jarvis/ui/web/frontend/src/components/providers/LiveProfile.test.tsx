import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LiveProfile, type LiveProfileValue } from "./LiveProfile";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("requires an explicit thinking model and sends one coherent selection", async () => {
  const requests: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        requests.push({ path, body: JSON.parse(String(init.body)) });
        return { ok: true, json: async () => ({}) };
      }
      return {
        ok: true,
        json: async () =>
          path.endsWith("options")
            ? {
                models: [{ id: "chosen-model", label: "Chosen" }],
                voices: ["gleam"],
                efforts: ["medium"],
              }
            : {
                key_ready: true,
                agent_configured: true,
                profile: {
                  model: "gpt-live-1",
                  voice: "gleam",
                  backend_model: "",
                  reasoning_effort: "medium",
                  web_search: true,
                  instructions: "",
                  backend_instructions: "",
                  configured: false,
                },
              },
      };
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(["unrelated-background-work"], { ready: true });
  const onSaved = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <LiveProfile onSaved={onSaved} />
    </QueryClientProvider>,
  );
  const button = await screen.findByRole("button", { name: "live.save" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("combobox", { name: "live.thinking_model" }));
  fireEvent.click(await screen.findByRole("option", { name: /Chosen/ }));
  fireEvent.click(button);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({
    path: "/api/live/profile",
    body: {
      model: "gpt-live-1",
      backend_model: "chosen-model",
      configured: true,
      reasoning_effort: "medium",
      web_search: true,
    },
  });
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(client.getQueryState(["unrelated-background-work"])?.isInvalidated).toBe(false);
  client.clear();
});

it("recognizes a newly connected key without losing the user's model choice", async () => {
  let keyReady = false;
  const fetcher = vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path.endsWith("options") ? {
      models: [{ id: "chosen-model", label: "Chosen" }], voices: ["gleam"], efforts: ["medium"],
    } : {
      key_ready: keyReady, active: false, agent_configured: true,
      profile: { model: "gpt-live-1", voice: "gleam", backend_model: "", reasoning_effort: "medium", web_search: true, instructions: "", backend_instructions: "", configured: false },
    },
  }));
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><LiveProfile /></QueryClientProvider>);
  const button = await screen.findByRole("button", { name: "live.save" });
  fireEvent.click(screen.getByRole("combobox", { name: "live.thinking_model" }));
  fireEvent.click(await screen.findByRole("option", { name: /Chosen/ }));
  expect((button as HTMLButtonElement).disabled).toBe(true);
  keyReady = true;
  fireEvent(window, new CustomEvent("jarvis:secret-configured"));
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  expect(screen.getByRole("combobox", { name: "live.thinking_model" }).textContent).toContain("Chosen");
  client.clear();
});

it("applies a new thinking model at once when Live is already set up", async () => {
  const requests: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        requests.push(JSON.parse(String(init.body)));
        return { ok: true, json: async () => ({}) };
      }
      return {
        ok: true,
        json: async () => path.endsWith("options") ? {
          models: [{ id: "old-model", label: "Old" }, { id: "cheap-model", label: "Cheap" }],
          voices: ["gleam"], efforts: ["medium"],
        } : {
          key_ready: true, active: true, agent_configured: true,
          profile: { model: "gpt-live-1", voice: "gleam", backend_model: "old-model", reasoning_effort: "medium", web_search: true, instructions: "", backend_instructions: "", configured: true },
        },
      };
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><LiveProfile /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("combobox", { name: "live.thinking_model" }));
  fireEvent.click(await screen.findByRole("option", { name: /Cheap/ }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({ backend_model: "cheap-model", configured: true });
  client.clear();
});

function subscriptionSetup({ accountMode = "subscription", catalogFails = false, modelEfforts }: {
  accountMode?: string; catalogFails?: boolean; modelEfforts?: string[];
} = {}) {
  let saved: LiveProfileValue = {
    auth_mode: "api_key", model: "gpt-live-1", voice: "gleam", backend_model: "api-model",
    reasoning_effort: "high", subscription_voice: "cove", subscription_backend_model: "",
    subscription_reasoning_effort: "medium", subscription_account_id: "",
    web_search: true, instructions: "", backend_instructions: "", configured: true,
  };
  const writes: LiveProfileValue[] = [];
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      saved = JSON.parse(String(init.body)) as LiveProfileValue;
      writes.push(saved);
      return { ok: true, json: async () => ({ profile: saved }) };
    }
    if (path === "/api/agent-accounts") return { ok: true, json: async () => ({ platforms: [{
      platform: "codex", active_account: "codex:default", accounts: [{
        id: "codex:default", label: "My ChatGPT", connected: true, mode: accountMode,
      }],
    }] }) };
    if (path.includes("auth_mode=chatgpt_subscription")) return {
      ok: !catalogFails, status: catalogFails ? 503 : 200,
      json: async () => ({ models: [{ id: "subscription-model", label: "Subscription model", efforts: modelEfforts }], voices: ["cove"], efforts: ["medium"] }),
    };
    if (path.endsWith("options")) return { ok: true, json: async () => ({
      models: [{ id: "api-model", label: "API model" }], voices: ["gleam"], efforts: ["high"],
    }) };
    return { ok: true, json: async () => ({
      profile: saved, active: true, key_ready: false, agent_configured: false,
      subscription: { account_id: "codex:default", account_connected: true, voice_status: "unverified" },
    }) };
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onAuthModeChange = vi.fn();
  render(<QueryClientProvider client={client}><LiveProfile onAuthModeChange={onAuthModeChange} /></QueryClientProvider>);
  return { client, fetcher, writes, onAuthModeChange };
}

async function selectSubscription() {
  fireEvent.click(await screen.findByRole("combobox", { name: "live.billing_method" }));
  fireEvent.click(await screen.findByRole("option", { name: "live.subscription_mode" }));
}

it("saves subscription voice without an API key and preserves the complete API selection", async () => {
  const { client, writes, fetcher, onAuthModeChange } = subscriptionSetup();
  await selectSubscription();
  await screen.findByText("live.subscription_connected");
  expect(screen.getByText("live.subscription_voice_unverified")).toBeTruthy();
  expect(screen.queryByText("live.key_required")).toBeNull();
  expect(screen.queryByRole("button", { name: "live.use_for_agents" })).toBeNull();
  await waitFor(() => expect((screen.getByRole("combobox", { name: "live.thinking_model" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("combobox", { name: "live.thinking_model" }));
  expect(screen.queryByRole("option", { name: /API model/ })).toBeNull();
  fireEvent.click(await screen.findByRole("option", { name: /Subscription model/ }));
  expect(writes).toHaveLength(0);
  expect(onAuthModeChange).toHaveBeenLastCalledWith("chatgpt_subscription", true);
  fireEvent.click(screen.getByRole("button", { name: "live.save" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({
    auth_mode: "chatgpt_subscription", subscription_backend_model: "subscription-model",
    subscription_voice: "cove", subscription_reasoning_effort: "medium",
    model: "gpt-live-1", voice: "gleam", backend_model: "api-model", reasoning_effort: "high",
  });
  expect(fetcher.mock.calls.some(([path]) => path === "/api/live/options?auth_mode=chatgpt_subscription&account_id=codex%3Adefault")).toBe(true);
  await screen.findByText("live.saved");
  expect(onAuthModeChange).toHaveBeenLastCalledWith("chatgpt_subscription", false);
  fireEvent.click(screen.getByRole("combobox", { name: "live.billing_method" }));
  fireEvent.click(await screen.findByRole("option", { name: "live.api_key_mode" }));
  expect(screen.getByRole("combobox", { name: "live.voice" }).textContent).toContain("Gleam");
  expect(screen.getByRole("combobox", { name: "live.thinking_model" }).textContent).toContain("API model");
  expect((screen.getByRole("button", { name: "live.save" }) as HTMLButtonElement).disabled).toBe(true);
  expect(writes).toHaveLength(1);
  client.clear();
});

it("does not treat a Codex API-key login as a ChatGPT subscription", async () => {
  const { client, writes, fetcher } = subscriptionSetup({ accountMode: "api_key" });
  await selectSubscription();
  await screen.findByRole("button", { name: "live.subscription_sign_in" });
  expect(screen.queryByText("live.subscription_connected")).toBeNull();
  expect((screen.getByRole("button", { name: "live.save" }) as HTMLButtonElement).disabled).toBe(true);
  expect(fetcher.mock.calls.some(([path]) => path.includes("auth_mode=chatgpt_subscription"))).toBe(false);
  expect(writes).toHaveLength(0);
  client.clear();
});

it("offers only the selected subscription model's advertised reasoning levels", async () => {
  const { client, writes } = subscriptionSetup({ modelEfforts: ["low", "high", "max"] });
  await selectSubscription();
  await waitFor(() => expect((screen.getByRole("combobox", { name: "live.thinking_model" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("combobox", { name: "live.thinking_model" }));
  fireEvent.click(await screen.findByRole("option", { name: /Subscription model/ }));
  expect(screen.getByRole("combobox", { name: "live.reasoning" }).textContent).toContain("live.model_default");
  fireEvent.click(screen.getByRole("combobox", { name: "live.reasoning" }));
  expect(screen.queryByRole("option", { name: "Medium" })).toBeNull();
  expect(screen.getByRole("option", { name: "Low" })).toBeTruthy();
  fireEvent.click(screen.getByRole("option", { name: "Max" }));
  fireEvent.click(screen.getByRole("button", { name: "live.save" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ subscription_reasoning_effort: "max", reasoning_effort: "high" });
  await screen.findByText("live.saved");
  client.clear();
});

it("keeps subscription selection when its catalog fails instead of using API options", async () => {
  const { client, writes } = subscriptionSetup({ catalogFails: true });
  await selectSubscription();
  await screen.findByText("live.options_failed");
  expect((screen.getByRole("combobox", { name: "live.thinking_model" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "live.save" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("combobox", { name: "live.billing_method" }).textContent).toContain("live.subscription_mode");
  expect(writes).toHaveLength(0);
  client.clear();
});
