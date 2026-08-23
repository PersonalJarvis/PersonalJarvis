import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ConnectIconButton,
  matchesQuery,
  matchesStatus,
  PatConnectDialog,
  PkceConnectDialog,
  type Plugin,
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

describe("PluginsView lists the catalog as a table", () => {
  it("shows one row per plugin, a Browse button and the All/Installed filter", async () => {
    installCatalogFetchMock();

    renderPluginsView();

    // One row per catalog entry, named after the plugin.
    await waitFor(() => {
      expect(screen.getByRole("row", { name: "GitHub" })).toBeDefined();
    });
    expect(screen.getByRole("row", { name: "Vercel" })).toBeDefined();
    expect(screen.getByRole("button", { name: /^Browse\b/i })).toBeDefined();
    // The status filter is a small tab strip, not a second page.
    expect(screen.getByRole("tab", { name: /^All\b/ })).toBeDefined();
    expect(screen.getByRole("tab", { name: /^Installed\b/ })).toBeDefined();
    // Nothing needs attention in this catalog, so that tab stays hidden.
    expect(screen.queryByRole("tab", { name: /Needs attention/ })).toBeNull();
    // The roadmap tab carried a static "AVAILABLE" list of Codex plugins that
    // were never installable in Jarvis — it has been removed entirely.
    expect(screen.queryByRole("button", { name: /^Roadmap\b/i })).toBeNull();
    expect(screen.queryByText("ChatGPT Codex plugins")).toBeNull();
  });
});

describe("PluginsView live badge", () => {
  it("shows the Live state for a connected plugin with live_callable: true", async () => {
    installCatalogFetchMock();

    renderPluginsView();

    // Wait until the catalog data has loaded: the header subtitle shows
    // "2 available · 1 connected" once the fetch resolves.
    await waitFor(() => {
      expect(screen.getByText(/available.*connected/i)).toBeDefined();
    });

    // GitHub is connected + live_callable: true → the status column says so.
    expect(screen.getByText("Connected · Live")).toBeDefined();
    // Vercel is not connected and must not carry the Live mark.
    expect(screen.getByText("Not connected")).toBeDefined();
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

// Hitting a 28px "+" is the wrong ask for "I want this plugin". The whole card
// starts the same connect flow — except a connected one, where a stray click
// must never disconnect anything.
describe("plugin row click", () => {
  it("opens the detail page, whose Connect button starts the connect flow", async () => {
    installCatalogFetchMock();

    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("row", { name: "Vercel" })).toBeDefined();
    });
    expect(screen.queryByRole("dialog")).toBeNull();

    // A row opens the detail page — it never starts a flow by itself.
    await act(async () => {
      fireEvent.click(screen.getByRole("row", { name: "Vercel" }));
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Plugins" })).toBeDefined();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Deployments, runtime logs, domains and env-vars")).toBeDefined();

    // Vercel is not_connected + pat_paste → the paste dialog IS its connect flow.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    });
    await waitFor(() => expect(screen.getByRole("dialog")).toBeDefined());
  });

  it("leaves a connected plugin alone — no dialog, no disconnect request", async () => {
    installCatalogFetchMock();

    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("row", { name: "GitHub" })).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("row", { name: "GitHub" }));
    });

    // The detail page states the connection; nothing destructive fires.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Plugins" })).toBeDefined();
    });
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    const urls = (
      globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    ).mock.calls.map((c: unknown[]) => String(c[0]));
    expect(urls.every((u: string) => u === "/api/marketplace/plugins")).toBe(true);
  });
});

describe("PkceConnectDialog own-client + production hint", () => {
  const gmail = {
    id: "gmail",
    name: "Gmail",
    description: "Mail",
    category: "Communication",
    logoSlug: "gmail",
    authMode: "oauth_pkce_loopback",
    authConfig: { mode: "oauth_pkce_loopback" },
    status: "not_connected",
    oauthClientConfigured: true,
  } as unknown as Parameters<typeof PkceConnectDialog>[0]["plugin"];

  it("shows the Google production hint with a console link", () => {
    render(
      <PkceConnectDialog plugin={gmail} onClose={() => {}} onProceed={() => {}} />,
    );
    expect(screen.getByText(/production/i)).toBeDefined();
    const link = screen.getByRole("link", { name: /google cloud console/i });
    expect((link as HTMLAnchorElement).href).toContain("console.cloud.google.com");
  });

  it("proceeds without writing secrets when no client is entered", async () => {
    const fetchMock = vi.fn();
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;
    const onProceed = vi.fn(() => Promise.resolve());
    render(
      <PkceConnectDialog plugin={gmail} onClose={() => {}} onProceed={onProceed} />,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onProceed).toHaveBeenCalledTimes(1);
  });

  it("writes google_oauth_client_id/secret then proceeds when a client is entered", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;
    const onProceed = vi.fn(() => Promise.resolve());
    render(
      <PkceConnectDialog plugin={gmail} onClose={() => {}} onProceed={onProceed} />,
    );
    fireEvent.click(screen.getByText(/use your own oauth client/i));
    fireEvent.change(screen.getByLabelText(/client id/i), {
      target: { value: "myid.apps.googleusercontent.com" },
    });
    fireEvent.change(screen.getByLabelText(/client secret/i), {
      target: { value: "GOCSPX-x" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    });
    expect(calls).toContain("/api/secrets/google_oauth_client_id");
    expect(calls).toContain("/api/secrets/google_oauth_client_secret");
    expect(onProceed).toHaveBeenCalledTimes(1);
  });

  it("blocks a placeholder-only provider until a client id is entered", () => {
    const slack = {
      ...gmail,
      id: "slack",
      name: "Slack",
      oauthClientFamily: "slack",
      oauthClientConfigured: false,
    };
    render(
      <PkceConnectDialog plugin={slack} onClose={() => {}} onProceed={() => {}} />,
    );

    expect(screen.getByText(/has no Slack OAuth client yet/i)).toBeDefined();
    expect(
      (screen.getByRole("button", { name: /^continue$/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByLabelText(/client id/i)).toBeDefined();
  });
});

describe("PluginsView opens the PKCE pre-connect dialog", () => {
  it("shows the own-client dialog instead of starting OAuth immediately", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/marketplace/plugins") {
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
                status: "not_connected",
                live_callable: false,
              },
            ],
          }),
        } as Response;
      }
      // A /connect/start hit here would mean OAuth fired immediately (the old
      // behaviour) — make that an explicit failure.
      throw new Error(`unexpected fetch ${url}`);
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;

    renderPluginsView();
    // Wait for the real row's Connect button rather than the "Gmail" text: the
    // button only exists once the catalog row has actually rendered.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Connect plugin" }),
      ).toBeDefined(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Connect plugin" }));

    await waitFor(() =>
      expect(screen.getByRole("dialog")).toBeDefined(),
    );
    expect(screen.getByText(/Connect Gmail/i)).toBeDefined();
  });
});

describe("PluginsView publishes OAuth success immediately", () => {
  it("shows Gmail as connected while the catalog revalidation is still pending", async () => {
    let catalogReads = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/marketplace/plugins") {
        catalogReads += 1;
        if (catalogReads > 1) {
          return new Promise<Response>(() => {});
        }
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
                status: "not_connected",
                live_callable: false,
                oauth_client_configured: true,
              },
            ],
          }),
        } as Response;
      }
      if (url.endsWith("/connect/start")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            flow_id: "gmail-flow",
            plugin_id: "gmail",
            kind: "browser_redirect",
            open_url: "https://accounts.example.test/authorize",
            expires_at_ms: null,
          }),
        } as Response;
      }
      if (url === "/api/settings/open-external") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ opened: true }),
        } as Response;
      }
      if (url.endsWith("/connect/poll/gmail-flow")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ state: "connected", plugin_id: "gmail" }),
        } as Response;
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;

    renderPluginsView();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Connect plugin" })).toBeDefined(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Connect plugin" }));
    fireEvent.click(await screen.findByRole("button", { name: /^continue$/i }));

    await screen.findByText("Gmail connected");
    expect(
      screen.getByRole("button", { name: "Disconnect plugin" }),
    ).toBeDefined();
    expect(catalogReads).toBeGreaterThan(1);
  });
});

function installImmediateConnectionFetch(
  plugin: Record<string, unknown>,
  startPayload?: Record<string, unknown>,
) {
  const probe = { catalogReads: 0 };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/marketplace/plugins") {
      probe.catalogReads += 1;
      if (probe.catalogReads > 1) return new Promise<Response>(() => {});
      return {
        ok: true,
        status: 200,
        json: async () => ({
          version: 1,
          schema_version: "t",
          total: 1,
          connected: 0,
          plugins: [plugin],
        }),
      } as Response;
    }
    if (url.endsWith("/connect/pat")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ status: "connected" }),
      } as Response;
    }
    if (url.endsWith("/connect/start") && startPayload) {
      return {
        ok: true,
        status: 200,
        json: async () => startPayload,
      } as Response;
    }
    if (url === "/api/settings/open-external") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ opened: true }),
      } as Response;
    }
    if (url.includes("/connect/poll/")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ state: "connected", plugin_id: plugin.id }),
      } as Response;
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return probe;
}

describe("PluginsView publishes every supported connection path immediately", () => {
  it("publishes a PAT connection before catalog revalidation finishes", async () => {
    const probe = installImmediateConnectionFetch({
      id: "token_service",
      display_name: "Token Service",
      description: "Token-authenticated service",
      category: "Developer",
      logo_slug: "keybase",
      auth: {
        mode: "pat_paste",
        token_creation_url: "https://tokens.example.test",
        token_prefix: "",
        instruction_md: "Create a token.",
      },
      status: "not_connected",
      live_callable: false,
    });

    renderPluginsView();
    fireEvent.click(await screen.findByRole("button", { name: "Connect plugin" }));
    fireEvent.change(await screen.findByPlaceholderText("Token"), {
      target: { value: "test-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));

    await screen.findByRole("button", { name: "Disconnect plugin" });
    expect(probe.catalogReads).toBeGreaterThan(1);
  });

  it("publishes a DCR browser-OAuth connection before revalidation finishes", async () => {
    const probe = installImmediateConnectionFetch(
      {
        id: "dcr_service",
        display_name: "DCR Service",
        description: "Dynamic OAuth service",
        category: "Productivity",
        logo_slug: "oauth",
        auth: { mode: "hosted_mcp_oauth_dcr" },
        status: "not_connected",
        live_callable: false,
      },
      {
        flow_id: "dcr-flow",
        plugin_id: "dcr_service",
        kind: "browser_redirect",
        open_url: "https://oauth.example.test/authorize",
        expires_at_ms: null,
      },
    );

    renderPluginsView();
    fireEvent.click(await screen.findByRole("button", { name: "Connect plugin" }));

    await screen.findByText("DCR Service connected");
    expect(screen.getByRole("button", { name: "Disconnect plugin" })).toBeDefined();
    expect(probe.catalogReads).toBeGreaterThan(1);
  });

  it("publishes a device-flow connection before revalidation finishes", async () => {
    const probe = installImmediateConnectionFetch(
      {
        id: "device_service",
        display_name: "Device Service",
        description: "Device-code OAuth service",
        category: "Developer",
        logo_slug: "auth0",
        auth: { mode: "oauth_device_flow" },
        status: "not_connected",
        live_callable: false,
      },
      {
        flow_id: "device-flow",
        plugin_id: "device_service",
        kind: "device_flow",
        open_url: null,
        user_code: "ABCD-EFGH",
        verification_uri: "https://device.example.test",
        verification_uri_complete: null,
        expires_at_ms: Date.now() + 300_000,
      },
    );

    renderPluginsView();
    fireEvent.click(await screen.findByRole("button", { name: "Connect plugin" }));

    await screen.findByText("Device Service connected");
    expect(screen.getByRole("button", { name: "Disconnect plugin" })).toBeDefined();
    expect(probe.catalogReads).toBeGreaterThan(1);
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

    // Open the Installed filter — the revoked plugin must be there, not hidden
    // in the full list as if never connected.
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /^Installed\b/i })).toBeDefined();
    });
    fireEvent.click(screen.getByRole("tab", { name: /^Installed\b/i }));
    // The attention filter appears as soon as something needs reconnecting.
    expect(screen.getByRole("tab", { name: /Needs attention/ })).toBeDefined();

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

describe("PatConnectDialog asks for a self-hosted server address", () => {
  const selfHosted = {
    id: "home_assistant",
    name: "Home Assistant",
    description: "Smart home",
    category: "Home & Devices",
    logoSlug: "homeassistant",
    authMode: "pat_paste" as const,
    authConfig: {
      mode: "pat_paste",
      token_creation_url: "https://example.test/token",
      token_prefix: "",
      instruction_md: "Create a long-lived token.",
      instance_url: {
        label: "Your Home Assistant address",
        placeholder: "http://192.168.1.20:8123",
        help_md: "The address you use in your browser.",
      },
    },
    status: "not_connected" as const,
    longevity: "permanent" as const,
  };

  it("cannot submit without the address, then passes it through", () => {
    const onSubmit = vi.fn();
    render(
      <PatConnectDialog
        plugin={selfHosted as never}
        onClose={() => {}}
        onSubmit={onSubmit}
        isPending={false}
        errorMessage={null}
      />,
    );

    const token = screen.getByPlaceholderText("Token");
    fireEvent.change(token, { target: { value: "llat-secret" } });

    // A self-hosted plugin cannot be reached at all without its address, so
    // Connect stays disabled rather than failing a round-trip later.
    const connect = screen.getByRole("button", { name: /connect/i });
    expect((connect as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText("http://192.168.1.20:8123"), {
      target: { value: "http://192.168.1.20:8123/lovelace/0" },
    });
    fireEvent.click(screen.getByRole("button", { name: /connect/i }));

    expect(onSubmit).toHaveBeenCalledWith(
      "llat-secret",
      null,
      "http://192.168.1.20:8123/lovelace/0",
    );
  });
});

describe("PluginsView category sections are data-driven", () => {
  function catalogWith(plugins: unknown[], categoryOrder?: string[]) {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/marketplace/plugins") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            version: 1,
            schema_version: "t",
            total: plugins.length,
            connected: 0,
            category_order: categoryOrder,
            plugins,
          }),
        } as Response;
      }
      throw new Error(`unexpected fetch ${String(input)}`);
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;
  }

  const row = (id: string, category: string, extra: Record<string, unknown> = {}) => ({
    id,
    display_name: id,
    description: "d",
    category,
    logo_slug: id,
    auth: { mode: "pat_paste" },
    status: "not_connected",
    live_callable: false,
    ...extra,
  });

  it("renders a category the frontend has never heard of instead of crashing", async () => {
    // The previous fixed record threw on `byCat[category].push(...)`, which
    // blanked the entire view behind its error boundary. A backend-only
    // taxonomy change must not be able to do that.
    catalogWith(
      [row("mystery", "Something Brand New"), row("known", "Developer")],
      ["Developer"],
    );

    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("row", { name: "mystery" })).toBeDefined();
    });
    expect(screen.getByText("Something Brand New")).toBeDefined();
    // The category menu is fed by the same data, so it offers the new one too.
    fireEvent.click(screen.getByRole("combobox", { name: "Category" }));
    expect(
      screen.getByRole("option", { name: "Something Brand New" }),
    ).toBeDefined();
  });

  it("orders rows by the catalog's category_order", async () => {
    catalogWith(
      [row("b", "Second"), row("a", "First")],
      ["First", "Second"],
    );

    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("row", { name: "a" })).toBeDefined();
    });
    const names = screen
      .getAllByRole("row")
      .map((r) => r.getAttribute("aria-label"))
      .filter(Boolean);
    expect(names.indexOf("a")).toBeLessThan(names.indexOf("b"));
  });

  it("states how long the connection lasts before the user connects", async () => {
    catalogWith([
      row("forever", "Developer", { longevity: "permanent" }),
      row("limited", "Developer", {
        longevity: "provider_limited",
        longevity_note: "Google asks again every 7 days in Testing mode.",
      }),
    ]);

    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("row", { name: "limited" })).toBeDefined();
    });
    // The detail page carries the longevity fact sheet.
    fireEvent.click(screen.getByRole("row", { name: "limited" }));
    await waitFor(() => {
      expect(screen.getByText("Sign in again periodically")).toBeDefined();
    });
    expect(screen.getByText("Google asks again every 7 days in Testing mode.")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Plugins" }));
    fireEvent.click(await screen.findByRole("row", { name: "forever" }));
    await waitFor(() => {
      expect(screen.getByText("Stays connected")).toBeDefined();
    });
  });
});

describe("plugin search matches more than the display name", () => {
  const plugin = (extra: Record<string, unknown> = {}): Plugin => ({
    id: "stripe",
    name: "Stripe",
    description: "Payments, customers, invoices and balance",
    category: "Developer",
    logoSlug: "stripe",
    authMode: "pat_paste",
    authConfig: { mode: "pat_paste" },
    status: "not_connected",
    longevity: "permanent",
    oauthClientConfigured: false,
    fromMarketplace: false,
    selfUploaded: false,
    ...extra,
  });

  it("matches everything when the query is blank or whitespace", () => {
    expect(matchesQuery(plugin(), "")).toBe(true);
    expect(matchesQuery(plugin(), "   ")).toBe(true);
  });

  it("matches on the description, so a capability word finds the plugin", () => {
    // The old name-only match returned nothing here — the word "payments"
    // never appears in "Stripe".
    expect(matchesQuery(plugin(), "payments")).toBe(true);
  });

  it("matches on the category and on the catalog id", () => {
    expect(matchesQuery(plugin(), "developer")).toBe(true);
    expect(matchesQuery(plugin({ name: "Google Drive", id: "google_drive" }), "google_drive")).toBe(
      true,
    );
  });

  const gmail = () =>
    plugin({
      id: "gmail",
      name: "Gmail",
      description: "Read and send mail from your inbox",
      category: "Calendar & Mail",
    });

  it("matches the vendor family, so 'google' finds Gmail", () => {
    // "Google" appears nowhere in Gmail's name, description, category or id —
    // only in the OAuth client family it shares with Drive and Calendar.
    expect(matchesQuery(gmail(), "google")).toBe(true);
  });

  it("ANDs the words, so a second word narrows instead of widening", () => {
    const drive = plugin({
      id: "google_drive",
      name: "Google Drive",
      description: "Files Jarvis creates or you share with it",
      category: "Files & Photos",
    });
    expect(matchesQuery(gmail(), "google mail")).toBe(true);
    expect(matchesQuery(drive, "google mail")).toBe(false);
  });

  it("ignores case and rejects a word that appears in no field", () => {
    expect(matchesQuery(plugin(), "INVOICES")).toBe(true);
    expect(matchesQuery(plugin(), "kubernetes")).toBe(false);
  });
});

describe("plugin status filter", () => {
  const withStatus = (status: Plugin["status"]): Plugin => ({
    id: "p",
    name: "P",
    description: "d",
    category: "Developer",
    logoSlug: "p",
    authMode: "pat_paste",
    authConfig: { mode: "pat_paste" },
    status,
    longevity: "permanent",
    oauthClientConfigured: false,
    fromMarketplace: false,
    selfUploaded: false,
  });

  it("lets everything through on 'all'", () => {
    for (const s of ["connected", "not_connected", "needs_reauth", "error"] as const) {
      expect(matchesStatus(withStatus(s), "all")).toBe(true);
    }
  });

  it("separates connected from not connected", () => {
    expect(matchesStatus(withStatus("connected"), "connected")).toBe(true);
    expect(matchesStatus(withStatus("not_connected"), "connected")).toBe(false);
    expect(matchesStatus(withStatus("not_connected"), "not_connected")).toBe(true);
    expect(matchesStatus(withStatus("connected"), "not_connected")).toBe(false);
  });

  it("groups needs_reauth and error under 'attention'", () => {
    // Both mean the same thing to the user: listed, but not callable until
    // they act. A connected plugin must not appear in that bucket.
    expect(matchesStatus(withStatus("needs_reauth"), "attention")).toBe(true);
    expect(matchesStatus(withStatus("error"), "attention")).toBe(true);
    expect(matchesStatus(withStatus("connected"), "attention")).toBe(false);
    expect(matchesStatus(withStatus("not_connected"), "attention")).toBe(false);
  });
});

describe("PluginsView search and filter controls", () => {
  function openSearch() {
    fireEvent.click(screen.getByRole("button", { name: "Search plugins…" }));
    return screen.getByPlaceholderText("Search plugins…");
  }

  it("offers the status filter as tabs next to the search", async () => {
    installCatalogFetchMock();
    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("tablist", { name: "Show" })).toBeDefined();
    });
    for (const label of [/^All\b/, /^Installed\b/]) {
      expect(screen.getByRole("tab", { name: label })).toBeDefined();
    }
  });

  it("hides the match count and reset button until a filter is active", async () => {
    installCatalogFetchMock();
    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Disconnect plugin" })).toBeDefined();
    });
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();
    expect(screen.queryByText(/matches$/)).toBeNull();

    fireEvent.change(openSearch(), { target: { value: "deployments" } });

    // Vercel's description carries "Deployments"; GitHub's does not.
    await waitFor(() => {
      expect(screen.getByText("1 matches")).toBeDefined();
    });
    expect(screen.getByRole("row", { name: "Vercel" })).toBeDefined();
    expect(screen.queryByRole("row", { name: "GitHub" })).toBeNull();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeDefined();
  });

  it("restores the full list when the filters are cleared", async () => {
    installCatalogFetchMock();
    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Disconnect plugin" })).toBeDefined();
    });
    fireEvent.change(openSearch(), { target: { value: "kubernetes" } });
    await waitFor(() => {
      expect(screen.getByText("0 matches")).toBeDefined();
    });

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();
    });
    expect(screen.getByRole("button", { name: "Disconnect plugin" })).toBeDefined();
    expect(screen.getByRole("row", { name: "Vercel" })).toBeDefined();
  });

  it("explains an empty result from a non-text filter instead of blanking", async () => {
    // Filtering by connection state alone can empty the list. That path used
    // to render nothing at all — no message, no way back.
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/marketplace/plugins") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ...CATALOG,
            connected: 0,
            plugins: CATALOG.plugins.map((p) => ({
              ...p,
              status: "not_connected",
              live_callable: false,
            })),
          }),
        } as Response;
      }
      throw new Error(`unexpected fetch ${String(input)}`);
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;
    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("row", { name: "GitHub" })).toBeDefined();
    });

    fireEvent.click(screen.getByRole("tab", { name: /^Installed\b/ }));

    await waitFor(() => {
      expect(screen.getByText("No plugin matches the current filters.")).toBeDefined();
    });
    fireEvent.click(screen.getByRole("button", { name: "Show all plugins" }));

    await waitFor(() => {
      expect(screen.getByRole("row", { name: "GitHub" })).toBeDefined();
    });
  });

  it("never claims nothing is connected while a filter is hiding the plugins", async () => {
    installCatalogFetchMock();
    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Disconnect plugin" })).toBeDefined();
    });
    fireEvent.click(screen.getByRole("tab", { name: /^Installed\b/ }));
    fireEvent.change(openSearch(), { target: { value: "kubernetes" } });

    await waitFor(() => {
      expect(screen.getByText(/No plugin matches/)).toBeDefined();
    });
    expect(screen.queryByText("Nothing connected yet")).toBeNull();
  });

  it("lists every plugin exactly once — no promo strip, no Coming-soon decoy", async () => {
    // The carousel used to put GitHub on screen a second time, and the
    // Coming-soon strip listed names that had long had real catalog entries.
    installCatalogFetchMock();
    renderPluginsView();

    await waitFor(() => {
      expect(screen.getByRole("row", { name: "GitHub" })).toBeDefined();
    });
    expect(screen.getAllByText("GitHub")).toHaveLength(1);
    expect(screen.queryByText("Coming soon")).toBeNull();
  });
});
