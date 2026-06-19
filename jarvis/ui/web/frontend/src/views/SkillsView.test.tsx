/**
 * Component tests for the reworked SkillsView: a flat list where every healthy
 * skill has an On/Off switch (on by default), a draft skill is shown locked with
 * no switch, deletion is confirmed before it fires, and built-in skills cannot
 * be deleted. Drag-reorder is verified live (jsdom has no real pointer/layout).
 *
 * Driven through a mocked fetch (mirrors ContactsView.test.tsx) with the UI
 * language forced to English for deterministic labels.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SkillsView } from "@/views/SkillsView";
import { setUiLanguage } from "@/i18n";

interface RouteResult {
  status?: number;
  body: unknown;
}
interface Call {
  url: string;
  method: string;
}

function installFetchMock(routes: Record<string, () => RouteResult>): Call[] {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method });
    const keys = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const key of keys) {
      const [routeMethod, prefix] = key.split(" ");
      if (method === routeMethod && url.startsWith(prefix)) {
        const { status = 200, body } = routes[key]();
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status >= 200 && status < 300 ? "OK" : "ERR",
          json: async () => body,
          text: async () => JSON.stringify(body),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return calls;
}

function skill(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    name: "alpha",
    state: "validated",
    is_builtin: false,
    error: null,
    description: "Alpha skill",
    category: "general",
    version: "0.1.0",
    triggers: [],
    tags: [],
    resources: { references: [], scripts: [], assets: [], agents: [] },
    resource_count: 0,
    ...overrides,
  };
}

function renderView() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <SkillsView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  setUiLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SkillsView — On/Off switch", () => {
  it("shows a switch that is ON for a healthy (validated) skill", async () => {
    installFetchMock({
      "GET /api/skills": () => ({ body: { skills: [skill({})], total: 1 } }),
    });
    renderView();

    await screen.findByText("alpha");
    const sw = screen.getByRole("switch");
    expect(sw.getAttribute("aria-checked")).toBe("true");
  });

  it("toggling the switch off calls the disable endpoint", async () => {
    const calls = installFetchMock({
      "GET /api/skills": () => ({ body: { skills: [skill({})], total: 1 } }),
      "POST /api/skills/alpha/disable": () => ({
        body: skill({ state: "disabled" }),
      }),
    });
    renderView();

    await screen.findByText("alpha");
    fireEvent.click(screen.getByRole("switch"));

    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.method === "POST" &&
            c.url.endsWith("/api/skills/alpha/disable"),
        ),
      ).toBe(true);
    });
  });
});

describe("SkillsView — draft is locked", () => {
  it("a draft skill shows an error label and no switch", async () => {
    installFetchMock({
      "GET /api/skills": () => ({
        body: {
          skills: [skill({ name: "broken", state: "draft", error: "boom" })],
          total: 1,
        },
      }),
    });
    renderView();

    await screen.findByText("broken");
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByText("Error")).toBeTruthy();
  });
});

describe("SkillsView — delete", () => {
  it("asks for confirmation, then calls DELETE", async () => {
    const calls = installFetchMock({
      "GET /api/skills": () => ({ body: { skills: [skill({})], total: 1 } }),
      "DELETE /api/skills/alpha": () => ({ body: { ok: true, removed: true } }),
    });
    renderView();

    await screen.findByText("alpha");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    // A confirmation dialog appears before anything is deleted.
    const dialog = await screen.findByRole("dialog");
    expect(
      calls.some((c) => c.method === "DELETE"),
    ).toBe(false);

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      expect(
        calls.some(
          (c) => c.method === "DELETE" && c.url.endsWith("/api/skills/alpha"),
        ),
      ).toBe(true);
    });
  });

  it("a built-in skill has no delete button", async () => {
    installFetchMock({
      "GET /api/skills": () => ({
        body: {
          skills: [skill({ name: "brainstorming", is_builtin: true, state: "active" })],
          total: 1,
        },
      }),
    });
    renderView();

    await screen.findByText("brainstorming");
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});

describe("SkillsView — multi-select bulk delete", () => {
  it("selects multiple skills and deletes them in one confirmed batch", async () => {
    const calls = installFetchMock({
      "GET /api/skills": () => ({
        body: {
          skills: [
            skill({ name: "alpha" }),
            skill({ name: "beta" }),
            skill({ name: "gamma" }),
          ],
          total: 3,
        },
      }),
      "POST /api/skills/bulk-delete": () => ({
        body: { deleted: ["alpha", "beta"], failed: [] },
      }),
    });
    renderView();

    await screen.findByText("alpha");

    // Enter selection mode, then check two skills.
    fireEvent.click(screen.getByRole("button", { name: "Select" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /beta/i }));

    // The bulk action reflects the count and opens a confirm dialog first.
    fireEvent.click(screen.getByRole("button", { name: /Delete \(2\)/ }));
    const dialog = await screen.findByRole("dialog");
    expect(calls.some((c) => c.url.endsWith("/bulk-delete"))).toBe(false);

    // One confirmation deletes the whole batch in a single request.
    fireEvent.click(within(dialog).getByRole("button", { name: /Delete \(2\)/ }));
    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.method === "POST" && c.url.endsWith("/api/skills/bulk-delete"),
        ),
      ).toBe(true);
    });
  });

  it("select-all checks every deletable skill but never a built-in", async () => {
    installFetchMock({
      "GET /api/skills": () => ({
        body: {
          skills: [
            skill({ name: "alpha" }),
            skill({ name: "locked", is_builtin: true, state: "active" }),
            skill({ name: "beta" }),
          ],
          total: 3,
        },
      }),
    });
    renderView();

    await screen.findByText("alpha");
    fireEvent.click(screen.getByRole("button", { name: "Select" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select all/i }));

    // Two user skills selected, the built-in excluded.
    expect(
      screen.getByRole("button", { name: /Delete \(2\)/ }),
    ).toBeTruthy();
  });
});
