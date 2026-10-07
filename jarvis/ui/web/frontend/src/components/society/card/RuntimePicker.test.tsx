import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { AgentRuntimeJob, AgentRuntimesResponse } from "@/lib/agentRuntimesApi";
import { RuntimeChoice, RuntimeStatusRow } from "./RuntimePicker";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
const { runtimes, ensure } = vi.hoisted(() => ({
  ensure: vi.fn(),
  runtimes: { value: null as unknown },
}));
vi.mock("@/lib/agentRuntimesApi", () => ({
  fetchAgentRuntimes: () => Promise.resolve(runtimes.value),
  ensureAgentRuntime: ensure,
}));

function job(runtime: "hermes" | "openclaw", state: AgentRuntimeJob["state"]): AgentRuntimeJob {
  return {
    runtime, kind: "install", state, started_ms: 0, finished_ms: null, exit_code: null,
    message: state === "failed" ? "installer exited with 1" : "", log_tail: [],
  };
}

function status(
  runtime: "hermes" | "openclaw",
  ready: boolean,
  running: AgentRuntimeJob | null = null,
) {
  return {
    runtime, label: runtime, installed: ready, version: ready ? "1.2.3" : "", minimum_version: "1.0.0",
    ready, problem: "", problem_kind: ready ? "" : "not_installed", install_hint: "", job: running,
  };
}

function withData(data: AgentRuntimesResponse, node: ReactNode) {
  runtimes.value = data;
  return render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
}

function data(...rows: ReturnType<typeof status>[]): AgentRuntimesResponse {
  return { runtimes: rows as AgentRuntimesResponse["runtimes"], supported_providers: ["openai"] };
}

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("RuntimeChoice", () => {
  test("offers the three runtimes and reports the pick", async () => {
    const onChange = vi.fn();
    withData(data(status("hermes", true), status("openclaw", false)), <RuntimeChoice value="jarvis" onChange={onChange} />);
    fireEvent.click(await screen.findByRole("radio", { name: "society.runtime.hermes" }));
    expect(onChange).toHaveBeenCalledWith("hermes");
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(ensure).not.toHaveBeenCalled();
  });

  test("a screen reader hears each runtime's setup state, and arrow keys move the pick", async () => {
    const onChange = vi.fn();
    withData(
      data(status("hermes", true), status("openclaw", false, job("openclaw", "running"))),
      <RuntimeChoice value="hermes" onChange={onChange} />,
    );
    const hermes = await screen.findByRole("radio", { name: "society.runtime.hermes" });
    const openclaw = screen.getByRole("radio", { name: "society.runtime.openclaw" });
    await waitFor(() => expect(
      document.getElementById(openclaw.getAttribute("aria-describedby") ?? "")?.textContent,
    ).toBe("society.runtime.setting_up"));
    expect(screen.getAllByRole("radio").map((radio) => radio.tabIndex)).toEqual([-1, 0, -1]);
    fireEvent.keyDown(hermes, { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("openclaw");
    expect(document.activeElement).toBe(openclaw);
    fireEvent.keyDown(hermes, { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("jarvis");
  });

  test("never shows a version number", async () => {
    withData(data(status("hermes", true), status("openclaw", true)), <RuntimeChoice value="hermes" onChange={vi.fn()} />);
    expect(await screen.findAllByText("society.runtime.ready")).toHaveLength(2);
    expect(screen.queryByText(/1\.2\.3/)).toBeNull();
  });

  test("picking a runtime that is not set up starts its setup by itself", async () => {
    ensure.mockResolvedValue(job("openclaw", "running"));
    withData(data(status("hermes", true), status("openclaw", false)), <RuntimeChoice value="openclaw" onChange={vi.fn()} />);
    await waitFor(() => expect(ensure).toHaveBeenCalledWith("openclaw"));
    expect(ensure).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /install|update/i })).toBeNull();
  });

  test("a running setup says so", async () => {
    withData(
      data(status("hermes", true), status("openclaw", false, job("openclaw", "running"))),
      <RuntimeChoice value="openclaw" onChange={vi.fn()} />,
    );
    expect(await screen.findByText("society.runtime.setting_up")).toBeTruthy();
    expect(screen.getByText("society.runtime.setting_up_hint")).toBeTruthy();
    expect(ensure).not.toHaveBeenCalled();
  });

  test("a failed setup can be tried again", async () => {
    ensure.mockResolvedValue(job("openclaw", "running"));
    withData(
      data(status("hermes", true), status("openclaw", false, job("openclaw", "failed"))),
      <RuntimeChoice value="openclaw" onChange={vi.fn()} />,
    );
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "society.runtime.retry" }));
    await waitFor(() => expect(ensure).toHaveBeenCalledWith("openclaw"));
  });
});

describe("RuntimeStatusRow", () => {
  test("names the runtime without a version", async () => {
    withData(data(status("hermes", true), status("openclaw", true)), <RuntimeStatusRow runtime="hermes" />);
    expect(await screen.findByText(/society\.runtime\.runs_on/)).toBeTruthy();
    expect(screen.queryByText(/1\.2\.3/)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  test("a Jarvis agent needs no setup", async () => {
    withData(data(status("hermes", true), status("openclaw", false)), <RuntimeStatusRow runtime="jarvis" />);
    expect(await screen.findByText(/society\.runtime\.runs_on/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
