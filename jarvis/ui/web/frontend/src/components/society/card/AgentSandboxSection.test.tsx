import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { SocietyAgent } from "../data";
import { AgentSandboxSection } from "./AgentSandboxSection";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const agent = { agentId: "sandbox-user", runtime: "jarvis", executionEnvironment: "sandbox" } as SocietyAgent;
function mount(value = agent) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AgentSandboxSection agent={value} />
  </QueryClientProvider>);
}

test("ready sandbox browses and downloads results from its own agent", async () => {
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(
    url === "/api/society/sandbox" ? { available: true }
      : { files: [{ name: "result.txt", directory: false }, { name: "sub", directory: true }] },
  )));
  vi.stubGlobal("fetch", fetcher);
  mount();
  const link = await screen.findByRole("link", { name: "result.txt" });
  expect(link.getAttribute("href")).toBe("/api/society/agents/sandbox-user/sandbox/file?path=result.txt");
  fireEvent.click(screen.getByRole("button", { name: "sub/" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    "/api/society/agents/sandbox-user/sandbox/files?path=sub", undefined,
  ));
});

test("unavailable runtime displays the error without requesting files", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ available: false, message: "Docker is stopped" })));
  vi.stubGlobal("fetch", fetcher);
  mount();
  await screen.findByText("Docker is stopped");
  expect(fetcher.mock.calls.length).toBe(1);
});

test("rejected environment selection keeps the saved environment", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: { detail: "Choose an API model" } }), { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  mount({ ...agent, executionEnvironment: "local" });
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "sandbox" } });
  await screen.findByRole("alert");
  expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("local");
  expect(fetcher).toHaveBeenCalledWith("/api/society/agents/sandbox-user", expect.objectContaining({
    method: "PATCH", body: JSON.stringify({ execution_environment: "sandbox" }),
  }));
});
