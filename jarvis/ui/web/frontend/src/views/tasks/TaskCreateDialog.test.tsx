import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskCreateDialog } from "./TaskCreateDialog";

const PLUGINS = {
  connected: 1,
  total: 1,
  plugins: [{ id: "gmail", name: "Gmail", status: "connected", live_callable: true }],
};

function installFetch(onPost?: (body: Record<string, unknown>) => void) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/marketplace/plugins") {
      return { ok: true, status: 200, json: async () => PLUGINS } as Response;
    }
    if (url === "/api/tasks" && init?.method === "POST") {
      onPost?.(JSON.parse(String(init.body)));
      return { ok: true, status: 201, json: async () => ({ id: "new1" }) } as Response;
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
}

function renderDialog(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TaskCreateDialog onClose={onClose} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("TaskCreateDialog", () => {
  it("renders the form with name + prompt fields and the connected plugin", async () => {
    installFetch();
    renderDialog();
    expect(await screen.findByText("Gmail")).toBeTruthy();
    // name input + prompt textarea
    expect(screen.getAllByRole("textbox").length).toBeGreaterThanOrEqual(2);
    // recurring is the default schedule mode
    expect(screen.getByText("Recurring")).toBeTruthy();
  });

  it("toggling a plugin reveals its read/write/full scope picker", async () => {
    installFetch();
    renderDialog();
    await screen.findByText("Gmail");
    expect(screen.queryByText("Read")).toBeNull();
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.getByText("Read")).toBeTruthy();
    expect(screen.getByText("Write")).toBeTruthy();
    expect(screen.getByText("Full")).toBeTruthy();
  });

  it("submits an agent task spec with the granted plugin and a recurring trigger", async () => {
    const posted: Record<string, unknown>[] = [];
    installFetch((b) => posted.push(b));
    renderDialog();
    await screen.findByText("Gmail");

    const boxes = screen.getAllByRole("textbox");
    fireEvent.change(boxes[0], { target: { value: "Morning Briefing" } });
    fireEvent.change(boxes[1], { target: { value: "Summarize inbox" } });
    fireEvent.click(screen.getByRole("switch")); // enable gmail (default scope read)
    fireEvent.click(screen.getByText("Create task"));

    await waitFor(() => expect(posted.length).toBe(1));
    const spec = posted[0] as {
      title: string;
      trigger: { type: string };
      action: { kind: string; plugin_grants: unknown[] };
    };
    expect(spec.title).toBe("Morning Briefing");
    expect(spec.trigger.type).toBe("every");
    expect(spec.action.kind).toBe("agent");
    expect(spec.action.plugin_grants).toEqual([{ plugin_id: "gmail", scope: "read" }]);
  });

  it("shows the unattended warning only at write/full scope", async () => {
    installFetch();
    renderDialog();
    await screen.findByText("Gmail");
    // no warning before any plugin is enabled
    expect(screen.queryByText(/unattended/i)).toBeNull();
    fireEvent.click(screen.getByRole("switch")); // enable gmail (default: read)
    expect(screen.queryByText(/unattended/i)).toBeNull();
    fireEvent.click(screen.getByText("Write")); // elevate to write
    expect(screen.getByText(/unattended/i)).toBeTruthy();
  });

  it("disables submit until name and prompt are filled", async () => {
    installFetch();
    renderDialog();
    await screen.findByText("Gmail");
    const saveBtn = screen.getByText("Create task").closest("button") as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(true);
    const boxes = screen.getAllByRole("textbox");
    fireEvent.change(boxes[0], { target: { value: "X" } });
    fireEvent.change(boxes[1], { target: { value: "Y" } });
    expect(saveBtn.disabled).toBe(false);
  });
});
