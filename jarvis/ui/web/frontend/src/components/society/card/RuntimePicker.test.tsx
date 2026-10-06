import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { AgentRuntimesResponse } from "@/lib/agentRuntimesApi";
import { RuntimeChoice, RuntimeStatusRow } from "./RuntimePicker";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
const { runtimes, setup } = vi.hoisted(() => ({
  setup: vi.fn(),
  runtimes: { value: null as unknown },
}));
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

function withData(data: AgentRuntimesResponse, node: ReactNode) {
  runtimes.value = data;
  return render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
}

const MIXED: AgentRuntimesResponse = {
  runtimes: [status("hermes", true), status("openclaw", false)] as AgentRuntimesResponse["runtimes"],
  supported_providers: ["openai"],
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("RuntimeChoice", () => {
  test("offers the three runtimes and reports the pick", async () => {
    const onChange = vi.fn();
    withData(MIXED, <RuntimeChoice value="jarvis" onChange={onChange} />);
    fireEvent.click(await screen.findByRole("radio", { name: "society.runtime.hermes" }));
    expect(onChange).toHaveBeenCalledWith("hermes");
    expect(screen.getAllByRole("radio")).toHaveLength(3);
  });

  test("a chosen runtime that is missing offers its installer", async () => {
    setup.mockResolvedValue({});
    withData(MIXED, <RuntimeChoice value="openclaw" onChange={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "society.runtime.install" }));
    await waitFor(() => expect(setup).toHaveBeenCalledWith("openclaw", "install"));
  });
});

describe("RuntimeStatusRow", () => {
  test("names the runtime and offers an update when it is outdated", async () => {
    withData(
      {
        runtimes: [status("hermes", false, true), status("openclaw", true)] as AgentRuntimesResponse["runtimes"],
        supported_providers: [],
      },
      <RuntimeStatusRow runtime="hermes" />,
    );
    expect(await screen.findByRole("button", { name: "society.runtime.update" })).toBeTruthy();
    expect(screen.getByText(/society\.runtime\.runs_on/)).toBeTruthy();
  });

  test("a Jarvis agent needs no setup", async () => {
    withData(MIXED, <RuntimeStatusRow runtime="jarvis" />);
    expect(await screen.findByText(/society\.runtime\.runs_on/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
