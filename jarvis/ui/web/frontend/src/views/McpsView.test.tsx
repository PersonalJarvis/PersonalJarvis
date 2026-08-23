/**
 * Component tests for the MCPs area: a table of servers with an on/off switch
 * per row, and a detail page (opened from a row) that lists the server's tools
 * and offers the connection check.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { McpsView } from "@/views/McpsView";
import { setUiLanguage } from "@/i18n";

interface Call {
  url: string;
  method: string;
}

const SERVERS = {
  servers: [
    {
      name: "notebooklm-mcp",
      display: "NotebookLM",
      description: "Notebooks, sources and overviews.",
      transport: "stdio",
      mandatory: false,
      required_auth: [],
      credentials_status: {},
      credentials_complete: true,
      platform_notes: "",
      install_command: ["python", "-m", "notebooklm_mcp.server"],
      is_bootstrap: false,
      enabled: true,
      status: "running",
      tools: [
        { name: "notebook_list", description: "List all notebooks." },
        { name: "notebook_get", description: "Get one notebook." },
      ],
      error: null,
    },
    {
      name: "context7",
      display: "Context7",
      description: "Library docs.",
      transport: "stdio",
      mandatory: false,
      required_auth: [],
      credentials_status: {},
      credentials_complete: true,
      platform_notes: "",
      install_command: ["npx", "context7"],
      is_bootstrap: true,
      enabled: false,
      status: "stopped",
      tools: [],
      error: null,
    },
  ],
  total: 2,
  running: 1,
  registry_ready: true,
};

function installFetchMock(): Call[] {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method });
    const respond = (body: unknown) =>
      ({
        ok: true,
        status: 200,
        json: async () => body,
        text: async () => JSON.stringify(body),
      }) as Response;
    if (method === "GET" && url === "/api/mcps") return respond(SERVERS);
    if (method === "POST" && url.endsWith("/disable")) return respond({ ok: true, enabled: false });
    if (method === "POST" && url.endsWith("/enable")) return respond({ ok: true, enabled: true });
    if (method === "POST" && url.endsWith("/check")) {
      return respond({ ok: true, tools_count: 2, error: null });
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return calls;
}

function renderView() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <McpsView />
    </QueryClientProvider>,
  );
}

beforeEach(() => setUiLanguage("en"));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("McpsView — table", () => {
  it("lists every server as a row with its switch state", async () => {
    installFetchMock();
    renderView();

    await screen.findByRole("row", { name: "notebooklm-mcp" });
    expect(screen.getByRole("row", { name: "context7" })).toBeTruthy();

    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(2);
    expect(switches[0].getAttribute("aria-checked")).toBe("true");
    expect(switches[1].getAttribute("aria-checked")).toBe("false");
    // The running server states its tool count right in the row.
    expect(screen.getByText("2 tools")).toBeTruthy();
  });

  it("flipping a switch calls the enable/disable endpoint", async () => {
    const calls = installFetchMock();
    renderView();

    await screen.findByRole("row", { name: "notebooklm-mcp" });
    fireEvent.click(screen.getAllByRole("switch")[0]);

    await waitFor(() => {
      expect(
        calls.some((c) => c.method === "POST" && c.url.endsWith("/api/mcps/notebooklm-mcp/disable")),
      ).toBe(true);
    });
  });
});

describe("McpsView — detail page", () => {
  it("opens from a row, lists the tools and runs the connection check", async () => {
    const calls = installFetchMock();
    renderView();

    fireEvent.click(await screen.findByRole("row", { name: "notebooklm-mcp" }));

    // Back link + the tool list from the wire payload.
    await screen.findByRole("button", { name: "MCPs" });
    expect(screen.getByText("notebook_list")).toBeTruthy();
    expect(screen.getByText("notebook_get")).toBeTruthy();
    expect(screen.getByText("python -m notebooklm_mcp.server")).toBeTruthy();

    // The overflow menu carries the connection check.
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Check connection" }));
    await waitFor(() => {
      expect(
        calls.some((c) => c.method === "POST" && c.url.endsWith("/api/mcps/notebooklm-mcp/check")),
      ).toBe(true);
    });

    // Back returns to the table.
    fireEvent.click(screen.getByRole("button", { name: "MCPs" }));
    await screen.findByRole("row", { name: "context7" });
  });
});
