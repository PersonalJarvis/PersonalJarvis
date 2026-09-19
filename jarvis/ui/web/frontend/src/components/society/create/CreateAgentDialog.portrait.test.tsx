import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { CreateAgentDialog } from "./CreateAgentDialog";

vi.mock("../figures/AgentFigureViewer", () => ({ AgentFigureViewer: () => <div /> }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("a new agent receives a customizable local portrait by default", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/society/agents" && init?.method === "POST") {
      return { ok: false, status: 503, json: async () => ({}) } as Response;
    }
    if (url === "/api/society/agents") return { ok: true, json: async () => ({ agents: [] }) } as Response;
    if (url.startsWith("/api/agent-chat/catalog")) return { ok: true, json: async () => ({ providers: [] }) } as Response;
    if (url === "/api/jarvis-agent/status") return { ok: true, json: async () => ({ mapping: [] }) } as Response;
    if (url === "/api/society/providers") return { ok: true, json: async () => ({ providers: [] }) } as Response;
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const created = vi.fn();
  render(<QueryClientProvider client={client}>
    <CreateAgentDialog open onClose={() => undefined} onCreated={created} />
  </QueryClientProvider>);
  expect(screen.getByTestId("portrait-mode-illustrated").getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByTestId("portrait-option-glasses"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Atlas" } });
  fireEvent.click(screen.getByTestId("society-create-submit"));
  await waitFor(() => expect(created).toHaveBeenCalled());
  const post = fetchMock.mock.calls.find(([url, init]) => url === "/api/society/agents" && init?.method === "POST");
  const body = JSON.parse((post?.[1]?.body ?? "{}") as string) as { avatar?: { portrait?: string; base?: string } };
  expect(body.avatar?.portrait).toMatch(/^illustrated:v1:/);
  expect(body.avatar?.base).toBeTruthy();
});
