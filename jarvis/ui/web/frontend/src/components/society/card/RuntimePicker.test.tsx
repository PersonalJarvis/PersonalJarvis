import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { AgentRuntimesResponse } from "@/lib/agentRuntimesApi";
import type { SocietyAgent } from "../data";
import { RuntimePicker } from "./RuntimePicker";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
const { updateModel, runtimes, setup } = vi.hoisted(() => ({
  updateModel: vi.fn(),
  setup: vi.fn(),
  runtimes: { value: null as unknown },
}));
vi.mock("../data", () => ({ useUpdateAgentModel: () => updateModel }));
vi.mock("@/lib/agentRuntimesApi", () => ({
  fetchAgentRuntimes: () => Promise.resolve(runtimes.value),
  startAgentRuntimeSetup: setup,
}));

function status(runtime: "hermes" | "openclaw", ready: boolean, installed = ready) {
  return {
    runtime, label: runtime, installed, version: installed ? "1.0.0" : "", minimum_version: "1.0.0",
    ready, problem: "", problem_kind: installed ? (ready ? "" : "outdated") : "not_installed",
    install_hint: "", job: null,
  };
}

function mount(agent: Partial<SocietyAgent>, data: AgentRuntimesResponse) {
  runtimes.value = data;
  const full = {
    agentId: "scout", name: "Scout", provider: "openai", model: "gpt-5.2", effort: "",
    accountId: "", runtime: "jarvis", computerId: null, ...agent,
  } as SocietyAgent;
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimePicker agent={full} />
    </QueryClientProvider>,
  );
}

const READY: AgentRuntimesResponse = {
  runtimes: [status("hermes", true), status("openclaw", true)] as AgentRuntimesResponse["runtimes"],
  supported_providers: ["openai", "ollama"],
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("RuntimePicker", () => {
  test("switching keeps the agent's model and names the runtime", async () => {
    updateModel.mockResolvedValue(undefined);
    mount({}, READY);
    const hermes = await screen.findByRole("radio", { name: /society.runtime.hermes$/ });
    await waitFor(() => expect((hermes as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(hermes);
    await waitFor(() => expect(updateModel).toHaveBeenCalledWith("scout", {
      provider: "openai", model: "gpt-5.2", effort: "", account_id: "", runtime: "hermes",
    }));
  });

  test("a subscription model cannot move to an external runtime", async () => {
    mount({ provider: "openai-codex" }, READY);
    expect(await screen.findByText("society.runtime.model_hint")).toBeTruthy();
    const openclaw = screen.getByRole("radio", { name: /society.runtime.openclaw$/ });
    await waitFor(() => expect((openclaw as HTMLButtonElement).disabled).toBe(true));
  });

  test("a missing runtime offers its installer", async () => {
    setup.mockResolvedValue({});
    mount({}, {
      runtimes: [status("hermes", false), status("openclaw", true)] as AgentRuntimesResponse["runtimes"],
      supported_providers: ["openai"],
    });
    const install = await screen.findByRole("button", { name: "society.runtime.install" });
    expect(screen.getByText("society.runtime.not_installed")).toBeTruthy();
    fireEvent.click(install);
    await waitFor(() => expect(setup).toHaveBeenCalledWith("hermes", "install"));
  });

  test("an outdated runtime offers its updater", async () => {
    mount({}, {
      runtimes: [status("hermes", true), status("openclaw", false, true)] as AgentRuntimesResponse["runtimes"],
      supported_providers: ["openai"],
    });
    expect(await screen.findByRole("button", { name: "society.runtime.update" })).toBeTruthy();
  });

  test("an agent on another computer stays on Jarvis", async () => {
    mount({ computerId: "box-1" }, READY);
    expect(await screen.findByText("society.runtime.remote_hint")).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("radio", { name: /society.runtime.hermes$/ }) as HTMLButtonElement).disabled).toBe(true));
  });
});
