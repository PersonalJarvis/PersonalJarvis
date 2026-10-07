import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { AgentRuntimesResponse } from "@/lib/agentRuntimesApi";
import { CreateAgentDialogHost } from "./CreateAgentDialog";
import { isCreateCancelled, useCreateAgentDialog } from "./createAgentStore";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("../companion/CompanionEditor", () => ({
  CompanionEditor: ({ value }: { value: { shape: string } }) => <div data-testid="companion-editor">{value.shape}</div>,
}));
const { createAgent, ensureRuntime, runtimes } = vi.hoisted(() => ({
  createAgent: vi.fn(),
  ensureRuntime: vi.fn(),
  runtimes: { value: null as unknown },
}));
vi.mock("../data", async () => {
  class AgentNameTaken extends Error {}
  return { AgentNameTaken, useCreateSocietyAgent: () => createAgent };
});
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

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <CreateAgentDialogHost />
    </QueryClientProvider>,
  );
}

beforeEach(() => { runtimes.value = READY; });
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
    expect(choice).toMatchObject({ name: "Scout", runtime: "hermes", provider: "openai" });
    expect(choice.companion.shape).toBeTruthy();
    expect(useCreateAgentDialog.getState().open).toBe(false);
  });

  test("a Jarvis agent needs nothing but the button", async () => {
    createAgent.mockResolvedValue({ agentId: "agent-2" });
    mount();
    act(() => { useCreateAgentDialog.getState().request().catch(() => undefined); });
    fireEvent.click(await screen.findByTestId("create-agent-submit"));
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
