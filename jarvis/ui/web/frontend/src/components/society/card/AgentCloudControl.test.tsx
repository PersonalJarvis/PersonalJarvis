import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentCloudControl } from "./AgentCloudControl";
import en from "@/i18n/locales/agent_cloud/en.json";

vi.mock("@/i18n", () => ({
  useLocaleChunk: () => true,
  useT: () => (key: string) => en.agent_cloud[key.replace("agent_cloud.", "") as keyof typeof en.agent_cloud] ?? key,
}));
vi.mock("@/hooks/useComputers", () => ({ useComputers: () => ({
  data: [{ id: "vps-1", name: "My VPS", facts: { os_id: "ubuntu" }, health: { status: "online" } }],
  isPending: false, isError: false, refetch: vi.fn(),
}) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><AgentCloudControl agentId="helper" /></QueryClientProvider>);
}

it("moves only after a destination and the explicit handoff action", async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init) requests.push(init);
    return new Response(JSON.stringify({ placement: init ? { agent_id: "helper", computer_id: "vps-1", state: "active" } : null }));
  }));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Move agent to the cloud" }));
  const button = await screen.findByRole("button", { name: "Check server and move" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "vps-1" } });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  expect(requests).toHaveLength(0);
  fireEvent.click(button);
  await screen.findByText(/The VPS owns this agent/);
  expect(requests).toHaveLength(1);
  expect(JSON.parse(String(requests[0].body))).toEqual({ computer_id: "vps-1" });
});

it("keeps a lost handoff response fenced when ownership is uncertain", async () => {
  let sent = false;
  let mutations = 0;
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init) { mutations++; sent = true; throw new Error("Connection interrupted"); }
    return new Response(JSON.stringify({ placement: sent ? { agent_id: "helper", computer_id: "vps-1", state: "uncertain" } : null }));
  }));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Move agent to the cloud" }));
  fireEvent.change(await screen.findByRole("combobox"), { target: { value: "vps-1" } });
  const button = screen.getByRole("button", { name: "Check server and move" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  await screen.findByText(/Handoff is not yet confirmed/);
  expect(screen.queryByRole("button", { name: "Check server and move" })).toBeNull();
  expect(mutations).toBe(1);
});

it("does not offer a handoff when ownership cannot be read", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Move agent to the cloud" }));
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Check server and move" })).toBeNull();
});
