import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ConnectIconButton,
  PkceConnectDialog,
  PluginsView,
} from "@/views/PluginsView";

const CATALOG = {
  version: 1,
  schema_version: "2026-05-09",
  total: 2,
  connected: 1,
  plugins: [
    {
      id: "github",
      display_name: "GitHub",
      description: "Repos, issues, pull requests and Actions runs",
      category: "Developer",
      logo_slug: "github",
      logo_color: "F4F4F5",
      featured: true,
      auth: { mode: "pat_paste" },
      status: "connected",
      live_callable: true,
    },
    {
      id: "vercel",
      display_name: "Vercel",
      description: "Deployments, runtime logs, domains and env-vars",
      category: "Developer",
      logo_slug: "vercel",
      logo_color: "F4F4F5",
      featured: true,
      auth: { mode: "pat_paste" },
      status: "not_connected",
      live_callable: false,
    },
  ],
};

function installCatalogFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/marketplace/plugins") {
      return {
        ok: true,
        status: 200,
        json: async () => CATALOG,
      } as Response;
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

function renderPluginsView() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <PluginsView />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PluginsView has no roadmap tab", () => {
  it("exposes only Browse and Installed tabs and drops the hardcoded Codex list", async () => {
    installCatalogFetchMock();

    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Browse\b/i })).toBeDefined();
    });

    // The roadmap tab carried a static "AVAILABLE" list of Codex plugins that
    // were never installable in Jarvis — it has been removed entirely.
    expect(screen.getByRole("button", { name: /^Installed\b/i })).toBeDefined();
    expect(screen.queryByRole("button", { name: /^Roadmap\b/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Vote on the roadmap/i })).toBeNull();
    expect(screen.queryByText("ChatGPT Codex plugins")).toBeNull();
    expect(screen.queryByText("Planned Jarvis integrations")).toBeNull();
    expect(screen.queryByText("OpenAI Developers")).toBeNull();
    expect(screen.queryByText("Documents")).toBeNull();
  });
});

describe("PluginsView live badge", () => {
  it("shows the Live badge for a connected plugin with live_callable: true", async () => {
    installCatalogFetchMock();

    renderPluginsView();

    // Wait until the catalog data has loaded: the header subtitle shows
    // "2 available · 1 connected" once the fetch resolves.
    await waitFor(() => {
      expect(screen.getByText(/available.*connected/i)).toBeDefined();
    });

    // GitHub is connected + live_callable: true → the Live badge must appear
    expect(screen.getByText("· Live")).toBeDefined();
  });
});

// Regression: `/connect/start` takes ~0.6s with no other feedback, so a user
// clicked the "+" several times and EACH click launched its own OAuth flow — a
// burst of browser tabs + multiple DCR client registrations. The button must
// lock itself (and show a spinner) while a connect is in flight, ignoring the
// extra clicks, then re-enable so a genuine retry still works.
describe("ConnectIconButton click-lock", () => {
  it("ignores extra clicks while a connect is in flight", async () => {
    let release: () => void = () => {};
    const onConnect = vi.fn(
      () => new Promise<void>((resolve) => { release = resolve; }),
    );
    render(
      <ConnectIconButton
        status="not_connected"
        onConnect={onConnect}
        onDisconnect={() => {}}
      />,
    );
    const btn = screen.getByRole("button", { name: "Connect plugin" });

    fireEvent.click(btn);
    fireEvent.click(btn); // second click while the first is still pending
    fireEvent.click(btn); // third

    // Only the first click started a flow; the rest were swallowed by the lock.
    expect(onConnect).toHaveBeenCalledTimes(1);
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(true));

    await act(async () => {
      release();
    });
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });

  it("re-enables after the connect resolves so a retry still works", async () => {
    const onConnect = vi.fn(() => Promise.resolve());
    render(
      <ConnectIconButton
        status="not_connected"
        onConnect={onConnect}
        onDisconnect={() => {}}
      />,
    );
    const btn = screen.getByRole("button", { name: "Connect plugin" });

    await act(async () => {
      fireEvent.click(btn);
    });
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    await act(async () => {
      fireEvent.click(btn);
    });

    expect(onConnect).toHaveBeenCalledTimes(2);
  });
});

// A revoked / expired plugin (status needs_reauth) must NOT look like a never-
// connected one. It surfaces a distinct "Reconnect" affordance that re-runs the
// same connect flow — the one-click repair for a dead token.
describe("ConnectIconButton reconnect state", () => {
  it("renders a Reconnect button for needs_reauth that triggers onConnect", async () => {
    const onConnect = vi.fn(() => Promise.resolve());
    render(
      <ConnectIconButton
        status="needs_reauth"
        onConnect={onConnect}
        onDisconnect={() => {}}
      />,
    );
    const btn = screen.getByRole("button", { name: "Reconnect plugin" });
    expect(btn).toBeDefined();
    await act(async () => {
      fireEvent.click(btn);
    });
    expect(onConnect).toHaveBeenCalledTimes(1);
  });

  it("never shows a plain Connect or Disconnect button for needs_reauth", () => {
    render(
      <ConnectIconButton
        status="needs_reauth"
        onConnect={() => {}}
        onDisconnect={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: "Connect plugin" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disconnect plugin" })).toBeNull();
  });
});

describe("PluginsView keeps revoked plugins visible", () => {
  it("shows a needs_reauth plugin under Installed with a Reconnect-needed badge", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/marketplace/plugins") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            version: 1,
            schema_version: "t",
            total: 1,
            connected: 0,
            plugins: [
              {
                id: "gmail",
                display_name: "Gmail",
                description: "Mail",
                category: "Communication",
                logo_slug: "gmail",
                auth: { mode: "oauth_pkce_loopback" },
                status: "needs_reauth",
                live_callable: false,
              },
            ],
          }),
        } as Response;
      }
      throw new Error(`unexpected fetch ${String(input)}`);
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;

    renderPluginsView();

    // Open the Installed tab — the revoked plugin must be there, not hidden in
    // Browse as if never connected.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Installed\b/i })).toBeDefined();
    });
    fireEvent.click(screen.getByRole("button", { name: /^Installed\b/i }));

    await waitFor(() => {
      expect(screen.getByText("Gmail")).toBeDefined();
    });
    // The row carries the amber "Reconnect needed" badge AND a Reconnect button.
    expect(screen.getByText("Reconnect needed")).toBeDefined();
    expect(
      screen.getByRole("button", { name: "Reconnect plugin" }),
    ).toBeDefined();
  });
});
