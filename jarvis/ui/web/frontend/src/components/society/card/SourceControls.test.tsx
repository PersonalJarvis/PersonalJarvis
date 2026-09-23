import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SourceControls } from "./SourceControls";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("required choices cannot be submitted empty through a themed dropdown", async () => {
  const requests: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") requests.push(JSON.parse(String(init.body)));
    return { ok: true, json: async () => ({ status: "queued" }) } as Response;
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SourceControls taskId="choice" source={{ kind: "form",
    form_fields: { priority: { label: "Priority", kind: "choice", required: true, choices: ["High", "Low"] } },
  }} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Trigger input" }));
  const run = screen.getByRole("button", { name: "Submit" });
  expect(run).toHaveProperty("disabled", true);
  fireEvent.submit(run.closest("form")!);
  expect(requests).toEqual([]);
  fireEvent.click(screen.getByRole("combobox", { name: "Priority" }));
  fireEvent.click(screen.getByRole("option", { name: "High" }));
  expect(run).toHaveProperty("disabled", false);
  fireEvent.click(run);
  await waitFor(() => expect(requests).toEqual([{ payload: { priority: "High" } }]));
});

test("a form updated through chat discards input from its previous schema", async () => {
  const requests: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") requests.push(JSON.parse(String(init.body)));
    return { ok: true, json: async () => ({ status: "queued" }) } as Response;
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (name: string) => <QueryClientProvider client={client}><SourceControls taskId="form-1" source={{ kind: "form", form_fields: { [name]: { label: name, kind: "text", required: true, choices: [] } } }} /></QueryClientProvider>;
  const mounted = render(view("old_field"));
  fireEvent.click(await screen.findByRole("button", { name: "Trigger input" }));
  fireEvent.change(screen.getByLabelText("old_field"), { target: { value: "stale" } });
  mounted.rerender(view("new_field"));
  fireEvent.click(await screen.findByRole("button", { name: "Trigger input" }));
  fireEvent.change(screen.getByLabelText("new_field"), { target: { value: "current" } });
  fireEvent.submit(screen.getByLabelText("new_field").closest("form")!);
  await waitFor(() => expect(requests).toEqual([{ payload: { "new_field": "current" } }]));
});
