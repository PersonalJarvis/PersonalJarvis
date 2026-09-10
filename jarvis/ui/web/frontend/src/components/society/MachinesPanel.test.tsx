import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MachinesContent } from "./MachinesPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup() {
  const requests: { path: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (path: string, init?: RequestInit) => {
    requests.push({ path, body: init?.body ? JSON.parse(String(init.body)) : null });
    let body: unknown = {};
    if (path === "/api/machines") body = { machines: [{ id: "node", name: "Test computer", transport: "connector", online: true, capabilities: { os: "linux", shell: true, files: true, desktop: false, agent_runtime: true, workspace_isolation: false, isolated_desktop: false, shell_name: "sh" } }] };
    else if (path.startsWith("/api/machines/jobs")) body = { jobs: [] };
    else if (path === "/api/society/agents") body = { agents: [{ agent_id: "writer", name: "Writer", tier: "specialist" }] };
    else if (path === "/api/machines/placements") body = { placements: [], transfers: [] };
    else if (path === "/api/machines/grants/writer") body = { grants: [{ agent_id: "writer", machine_id: "node", scope: "workspace", workspace: "/work", shell: false, files: true, desktop: "none" }] };
    else if (path.endsWith("/move")) body = { id: "transfer", state: "queued" };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={query}><MachinesContent agentId="writer" /></QueryClientProvider>);
  return requests;
}

describe("connected computers", () => {
  it("loads the actual grant and refuses workspace-only shell access", async () => {
    setup();
    await screen.findByRole("heading", { name: "Test computer" });
    fireEvent.click(screen.getByRole("button", { name: "Access" }));
    const folder = await screen.findByLabelText("Workspace on this computer");
    await waitFor(() => expect((folder as HTMLInputElement).value).toBe("/work"));
    expect((screen.getByLabelText("Run shell commands") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("option", { name: "Own isolated desktop" }) as HTMLOptionElement).disabled).toBe(true);
  });

  it("reports a queued move without claiming that the handoff already completed", async () => {
    const requests = setup();
    await screen.findByRole("checkbox", { name: "Writer Hub computer" });
    fireEvent.change(screen.getByRole("combobox", { name: "Move to" }), { target: { value: "node" } });
    fireEvent.click(screen.getByRole("button", { name: "Move selected agents" }));
    await screen.findByText(/Writer: move queued/);
    expect(screen.queryByText(/Writer: moved to/)).toBeNull();
    expect(requests.find(row => row.path.endsWith("/writer/move"))?.body).toEqual({ host_id: "node" });
  });
});
