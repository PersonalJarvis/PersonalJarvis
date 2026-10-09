import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CustomApisView } from "./CustomApisView";
import type { ApiConnection } from "@/lib/customApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const connection: ApiConnection = {
  id: "a".repeat(32), name: "ElevenLabs", website: "https://elevenlabs.io", brand_id: "elevenlabs",
  logo_data: "", categories: ["Speech"], action_count: 414, omitted_operations: 0,
  enabled: true, has_credential: true, tools_ready: true, status: "verified",
};
const reply = (data: unknown, ok = true) => ({ ok, status: ok ? 200 : 422, json: async () => data }) as Response;
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><CustomApisView onBack={() => {}} /></QueryClientProvider>);
}

describe("simple service connections", () => {
  it("adds a service with only its name and key, then shows its inherited capabilities", async () => {
    let rows: ApiConnection[] = [];
    let saved: Record<string, string> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
      if (String(url).endsWith("/connect") && init?.method === "POST") {
        saved = JSON.parse(String(init.body)); rows = [connection]; return reply(connection);
      }
      return reply(rows);
    }));
    const rendered = show();
    await screen.findByLabelText("Service name");
    expect(rendered.container.querySelectorAll("input")).toHaveLength(2);
    expect(screen.queryByLabelText("Service URL")).toBeNull();
    expect(screen.queryByText("JSON body schema (optional)")).toBeNull();
    fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "11labs" } });
    expect(screen.getByTestId("provider-logo-elevenlabs")).toBeDefined();
    fireEvent.change(screen.getByLabelText("API key", { exact: true }), { target: { value: "fixture-only-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Add plugin" }));
    await screen.findByText("ElevenLabs", { exact: true });
    expect(saved!.name).toBe("11labs");
    expect(saved!.credential).toBe("fixture-only-key");
    expect(Object.keys(saved!).sort()).toEqual(["credential", "id", "name"]);
    expect(screen.queryByDisplayValue("fixture-only-key")).toBeNull();
    expect(screen.getByText(/414 capabilities/)).toBeDefined();
    expect(screen.getByText(/New capabilities become available/)).toBeDefined();
  });

  it("keeps the form recoverable when a key is rejected", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => init?.method === "POST"
      ? reply({ detail: { code: "invalid_key" } }, false) : reply([])));
    show();
    fireEvent.change(await screen.findByLabelText("Service name"), { target: { value: "ElevenLabs" } });
    fireEvent.change(screen.getByLabelText("API key", { exact: true }), { target: { value: "fixture-only-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Add plugin" }));
    expect((await screen.findByRole("alert")).textContent).toContain("did not accept");
    expect(screen.getByDisplayValue("ElevenLabs")).toBeDefined();
    expect(screen.getByRole("button", { name: "Add plugin" }).hasAttribute("disabled")).toBe(false);
  });

  it("lets another connection be added without replacing the existing one", async () => {
    let rows = [connection];
    const requested: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)); requested.push(body.id);
        const second = { ...connection, id: body.id, name: "Sample Cloud", brand_id: "samplecloud" };
        rows = [...rows, second]; return reply(second);
      }
      return reply(rows);
    }));
    show();
    await screen.findByText("ElevenLabs", { exact: true });
    fireEvent.click(screen.getByRole("button", { name: "Add plugin" }));
    fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "Sample Cloud" } });
    fireEvent.change(screen.getByLabelText("API key", { exact: true }), { target: { value: "second-fixture-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Add plugin" }));
    await screen.findByText("Sample Cloud", { exact: true });
    expect(screen.getByText("ElevenLabs", { exact: true })).toBeDefined();
    expect(requested[0]).not.toBe(connection.id);
    expect(screen.getByTestId("generated-connection-mark")).toBeDefined();
  });

  it("pauses the selected connection through its menu", async () => {
    let row = connection;
    const requests: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        requests.push([String(url), JSON.parse(String(init.body))]); row = { ...row, enabled: false, tools_ready: false }; return reply(row);
      }
      return reply([row]);
    }));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Manage ElevenLabs" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Pause connection" }));
    await screen.findByText("Paused");
    expect(requests).toEqual([[`/api/custom-apis/connections/${connection.id}`, { enabled: false }]]);
  });

  it("offers name suggestions without requiring technical configuration", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => init?.method === "POST"
      ? reply({ detail: { code: "ambiguous_service", suggestions: ["Sample Calendar", "Sample Mail"] } }, false) : reply([])));
    show();
    fireEvent.change(await screen.findByLabelText("Service name"), { target: { value: "Sample" } });
    fireEvent.change(screen.getByLabelText("API key", { exact: true }), { target: { value: "fixture-only-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Add plugin" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sample Calendar" }));
    expect(screen.getByDisplayValue("Sample Calendar")).toBeDefined();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not render an empty setup form when the connection list failed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("Offline"); }));
    show();
    await screen.findByRole("alert");
    expect(screen.queryByLabelText("API key", { exact: true })).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeDefined());
  });
});
