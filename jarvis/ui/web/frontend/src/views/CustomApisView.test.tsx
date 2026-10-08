import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CustomApisView } from "./CustomApisView";
import { actionWithPath, newApiAction } from "@/lib/customApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><CustomApisView onBack={() => {}} /></QueryClientProvider>);
}
const reply = (data: unknown, ok = true) => ({ ok, status: ok ? 200 : 422, json: async () => data }) as Response;

describe("Custom APIs", () => {
  it("saves native actions and keeps the key out of the definition", async () => {
    let saved: { definition: Record<string, unknown>; credential: string } | null = null;
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init?.method === "PUT") { saved = JSON.parse(String(init.body)); return reply({}); }
      return reply([]);
    }));
    show();
    await screen.findByText("Your services, connected to Jarvis");
    fireEvent.click(screen.getByRole("button", { name: "Add API" }));
    fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "My service" } });
    fireEvent.change(screen.getByLabelText("Service URL"), { target: { value: "https://api.example.com" } });
    fireEvent.change(screen.getByLabelText("API key", { exact: true }), { target: { value: "test-secret-key" } });
    fireEvent.change(screen.getByLabelText("What should Jarvis use this action for?"), { target: { value: "List items" } });
    fireEvent.click(screen.getByRole("button", { name: "Save API" }));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.credential).toBe("test-secret-key");
    expect(JSON.stringify(saved!.definition)).not.toContain("test-secret-key");
    await screen.findByRole("button", { name: "Add API" });
    expect(screen.queryByDisplayValue("test-secret-key")).toBeNull();
  });

  it("keeps failed saves open and displays the backend error", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => init?.method === "PUT"
      ? reply({ detail: "Enter an API key before enabling this service" }, false) : reply([])));
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Add API" }));
    fireEvent.change(screen.getByLabelText("Service name"), { target: { value: "My service" } });
    fireEvent.change(screen.getByLabelText("Service URL"), { target: { value: "https://api.example.com" } });
    fireEvent.change(screen.getByLabelText("What should Jarvis use this action for?"), { target: { value: "List items" } });
    fireEvent.click(screen.getByRole("button", { name: "Save API" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Enter an API key");
    expect(screen.getByDisplayValue("My service")).toBeDefined();
  });

  it("derives required path arguments while keeping query parameters", () => {
    const action = newApiAction();
    action.parameters = [{ name: "page", location: "query", type: "integer", required: false, description: "" }];
    const changed = actionWithPath(action, "/items/{item_id}");
    expect(changed.parameters.map((p) => [p.name, p.location, p.required])).toEqual([
      ["page", "query", false], ["item_id", "path", true],
    ]);
    expect(actionWithPath(changed, "/items").parameters).toHaveLength(1);
  });

  it("shows a loading failure without pretending the list is empty", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("Offline"); }));
    show();
    expect((await screen.findByRole("alert")).textContent).toContain("Offline");
    expect(screen.queryByText("Your services, connected to Jarvis")).toBeNull();
  });
});
