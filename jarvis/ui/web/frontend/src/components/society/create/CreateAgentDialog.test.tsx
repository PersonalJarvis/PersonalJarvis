import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { AgentRuntimesResponse } from "@/lib/agentRuntimesApi";
import type { ProviderOption } from "@/store/agentChat";
import { CreateAgentDialogHost } from "./CreateAgentDialog";
import { isCreateCancelled, useCreateAgentDialog } from "./createAgentStore";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("../companion/CompanionEditor", () => ({
  CompanionEditor: ({ value }: { value: { shape: string } }) => <div data-testid="companion-editor">{value.shape}</div>,
}));
const { createAgent, ensureRuntime, runtimes, menu } = vi.hoisted(() => ({
  createAgent: vi.fn(),
  ensureRuntime: vi.fn(),
  runtimes: { value: null as unknown },
  menu: { options: [] as unknown[], live: {} as Record<string, unknown[]> },
}));
vi.mock("../data", async () => {
  class AgentNameTaken extends Error {}
  return { AgentNameTaken, useCreateSocietyAgent: () => createAgent };
});
vi.mock("../chat/useModelMenuData", () => ({
  useModelMenuData: () => ({
    options: menu.options, providers: [], live: menu.live,
    loading: false, refreshing: false, failed: false, refresh: () => Promise.resolve(),
  }),
}));
vi.mock("@/lib/agentRuntimesApi", () => ({
  fetchAgentRuntimes: () => Promise.resolve(runtimes.value),
  ensureAgentRuntime: ensureRuntime,
}));

const READY: AgentRuntimesResponse = {
  runtimes: [
    { runtime: "hermes", label: "Hermes", installed: true, version: "0.21.0", minimum_version: "0.20.6",
      ready: true, problem: "", problem_kind: "", install_hint: "", job: null },
    { runtime: "openclaw", label: "OpenClaw", installed: false, version: "", minimum_version: "2026.9.8",
      ready: false, problem: "", problem_kind: "not_installed", install_hint: "", job: null },
  ],
  supported_providers: ["openai", "ollama"],
};

function provider(over: Partial<ProviderOption>): ProviderOption {
  return {
    id: "openai", label: "OpenAI", family: "openai", runner: "brain", models_source: "live",
    curated_models: [{ id: "gpt-5.2", label: "GPT-5.2" }, { id: "gpt-5.5", label: "GPT-5.5" }],
    default_model: "", keyless: false, native_resume: false, effort_levels: [], default_effort: "",
    permission_modes: [], default_permission_mode: "", cli_installed: null, connected: true, active: false,
    ...over,
  };
}

const OPENAI = provider({});
const OLLAMA = provider({ id: "ollama", label: "Ollama", family: "ollama", keyless: true, curated_models: [] });
const CLAUDE = provider({
  id: "claude-api", label: "Anthropic Claude", family: "claude", runner: "claude-cli", models_source: "curated",
  cli_installed: true, curated_models: [{ id: "opusplan", label: "Opus Plan" }, { id: "claude-opus-5", label: "Claude Opus 5" }],
});

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <CreateAgentDialogHost />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  runtimes.value = READY;
  menu.options = [OPENAI, OLLAMA];
  menu.live = { ollama: [{ id: "qwen3:8b", label: "qwen3:8b" }] };
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  // Settle anything a test left open, so the next test's request starts clean.
  act(() => useCreateAgentDialog.getState().cancel());
});

describe("CreateAgentDialog", () => {
  test("creates a named Hermes agent with a provider and a companion", async () => {
    const agent = { agentId: "agent-1", name: "Scout" };
    createAgent.mockResolvedValue(agent);
    mount();
    let pending!: Promise<unknown>;
    act(() => { pending = useCreateAgentDialog.getState().request(); });
    fireEvent.change(await screen.findByTestId("create-agent-name"), { target: { value: "Scout" } });
    fireEvent.click(await screen.findByRole("radio", { name: "society.runtime.hermes" }));
    await screen.findByTestId("create-agent-provider");
    fireEvent.click(screen.getByTestId("create-agent-submit"));
    await expect(pending).resolves.toBe(agent);
    const choice = createAgent.mock.calls[0][0];
    expect(choice).toMatchObject({ name: "Scout", runtime: "hermes", provider: "openai", model: "gpt-5.5", accountId: "" });
    expect(choice.companion.shape).toBeTruthy();
    expect(useCreateAgentDialog.getState().open).toBe(false);
  });

  test("a Jarvis agent picks a provider, its access and a model too", async () => {
    createAgent.mockResolvedValue({ agentId: "agent-2" });
    mount();
    act(() => { useCreateAgentDialog.getState().request().catch(() => undefined); });
    await screen.findByTestId("create-agent-provider");
    expect(screen.getByTestId("create-agent-model")).toBeTruthy();
    expect(screen.getByRole("radio", { name: "society.create.kind_api" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByTestId("create-agent-submit"));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(createAgent.mock.calls[0][0]).toMatchObject({ runtime: "jarvis", provider: "openai", model: "gpt-5.5" });
  });

  test("Claude can run on the subscription or on the API key", async () => {
    runtimes.value = { ...READY, access: { "claude-api": ["api", "subscription"] } };
    menu.options = [CLAUDE, OPENAI];
    createAgent.mockResolvedValue({ agentId: "agent-4" });
    mount();
    act(() => { useCreateAgentDialog.getState().request().catch(() => undefined); });
    const subscription = await screen.findByRole("radio", { name: "society.create.kind_subscription" });
    await waitFor(() => expect(subscription.getAttribute("aria-checked")).toBe("true"));
    fireEvent.click(screen.getByRole("radio", { name: "society.create.kind_api" }));
    fireEvent.click(screen.getByTestId("create-agent-submit"));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(createAgent.mock.calls[0][0]).toMatchObject({
      runtime: "jarvis", provider: "claude-api", model: "claude-opus-5", accountId: "api-key",
    });
  });

  test("a Claude login Anthropic refuses is shown with its reason and the API key is used", async () => {
    runtimes.value = {
      ...READY,
      supported_providers: ["claude-api", "openai"],
      login_providers: ["claude-api"],
      access: { "claude-api": ["api", "subscription"] },
      access_blocked: { "claude-api": { subscription: "extra_usage_off" } },
    };
    menu.options = [CLAUDE];
    createAgent.mockResolvedValue({ agentId: "agent-6" });
    mount();
    act(() => { useCreateAgentDialog.getState().request().catch(() => undefined); });
    fireEvent.click(await screen.findByRole("radio", { name: "society.runtime.hermes" }));
    const subscription = await screen.findByRole("radio", { name: "society.create.kind_subscription" });
    await waitFor(() => expect((subscription as HTMLButtonElement).disabled).toBe(true));
    expect(subscription.getAttribute("aria-checked")).toBe("false");
    const reason = screen.getByTestId("create-agent-access-blocked-subscription");
    expect(reason.textContent).toBe("society.create_agent.access_blocked_extra_usage_off");
    expect(subscription.getAttribute("aria-describedby")).toBe(reason.id);
    expect(screen.getByRole("radio", { name: "society.create.kind_api" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByTestId("create-agent-submit"));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(createAgent.mock.calls[0][0]).toMatchObject({ runtime: "hermes", provider: "claude-api", accountId: "api-key" });
  });

  test("with only a refused Claude login the agent cannot be created, and the reason is shown", async () => {
    runtimes.value = {
      ...READY,
      supported_providers: ["claude-api"],
      login_providers: ["claude-api"],
      access: { "claude-api": ["subscription"] },
      access_blocked: { "claude-api": { subscription: "extra_usage_spent" } },
    };
    menu.options = [CLAUDE];
    mount();
    act(() => { useCreateAgentDialog.getState().request().catch(() => undefined); });
    fireEvent.click(await screen.findByRole("radio", { name: "society.runtime.hermes" }));
    expect((await screen.findByTestId("create-agent-access-blocked-subscription")).textContent)
      .toBe("society.create_agent.access_blocked_extra_usage_spent");
    expect((screen.getByTestId("create-agent-submit") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId("create-agent-model")).toBeNull();
  });

  test("arrow keys move the access choice and only one radio is a tab stop", async () => {
    runtimes.value = { ...READY, access: { "claude-api": ["api", "subscription"] } };
    menu.options = [CLAUDE, OPENAI];
    mount();
    act(() => { useCreateAgentDialog.getState().request().catch(() => undefined); });
    const subscription = await screen.findByRole("radio", { name: "society.create.kind_subscription" });
    await waitFor(() => expect(subscription.getAttribute("aria-checked")).toBe("true"));
    const api = await screen.findByRole("radio", { name: "society.create.kind_api" });
    await waitFor(() => expect(subscription.getAttribute("aria-checked")).toBe("true"));
    expect([subscription.tabIndex, api.tabIndex]).toEqual([0, -1]);
    fireEvent.keyDown(subscription, { key: "ArrowRight" });
    await waitFor(() => expect(api.getAttribute("aria-checked")).toBe("true"));
    expect(document.activeElement).toBe(api);
    expect([subscription.tabIndex, api.tabIndex]).toEqual([-1, 0]);
    expect(screen.getByTestId("create-agent-access").getAttribute("aria-describedby")).toBeTruthy();
  });

  test("a Jarvis agent with nothing connected still starts on the chat's seat", async () => {
    menu.options = [];
    createAgent.mockResolvedValue({ agentId: "agent-5" });
    mount();
    act(() => { useCreateAgentDialog.getState().request().catch(() => undefined); });
    await screen.findByText("society.create_agent.no_provider_jarvis");
    fireEvent.click(screen.getByTestId("create-agent-submit"));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(createAgent.mock.calls[0][0]).toMatchObject({ runtime: "jarvis", provider: undefined });
    expect(screen.queryByTestId("create-agent-provider")).toBeNull();
  });

  test("a runtime that is not installed yet sets itself up and can be created right away", async () => {
    ensureRuntime.mockResolvedValue(null);
    createAgent.mockResolvedValue({ agentId: "agent-3" });
    mount();
    act(() => { useCreateAgentDialog.getState().request().catch(() => undefined); });
    fireEvent.click(await screen.findByRole("radio", { name: "society.runtime.openclaw" }));
    await waitFor(() => expect(ensureRuntime).toHaveBeenCalledWith("openclaw"));
    const submit = screen.getByTestId("create-agent-submit") as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(createAgent.mock.calls[0][0]).toMatchObject({ runtime: "openclaw", provider: "openai" });
  });

  test("closing the dialog cancels the request quietly", async () => {
    mount();
    let pending!: Promise<unknown>;
    // Catch at once: the rejection must never be unhandled, even for a tick.
    act(() => { pending = useCreateAgentDialog.getState().request().catch((exc: unknown) => exc); });
    fireEvent.click(await screen.findByRole("button", { name: "society.create_agent.cancel" }));
    const error = await pending;
    expect(isCreateCancelled(error)).toBe(true);
    expect(createAgent).not.toHaveBeenCalled();
  });
});
