import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { PluginsView } from "./PluginsView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("runs the real product probe endpoint from plugin details", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/verify")) return new Response(JSON.stringify({
      status: "connected", capability_state: "live", resource_read: true,
    }));
    if (path.endsWith("/files")) return new Response(JSON.stringify({ files: [] }));
    return new Response(JSON.stringify({ version: 1, total: 1, connected: 1, plugins: [{
      id: "slack", display_name: "Slack", description: "Messages", category: "Messaging",
      logo_slug: "slack", status: "connected", auth: { mode: "oauth_pkce_loopback" },
      capability_state: "live", live_callable: true,
    }] }));
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PluginsView inDialog /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Slack" }));
  fireEvent.click(await screen.findByRole("button", { name: "Check access" }));
  expect(await screen.findByText("Read-only check passed")).toBeDefined();
  expect(fetcher.mock.calls.some(([path]) => String(path) === "/api/marketplace/plugins/slack/verify")).toBe(true);
});

it.each([
  ["limited", "Limited access"],
  ["rate_limited", "Rate limited"],
  ["unavailable", "Temporarily unavailable"],
])("keeps authentication connected while capability is %s", async (state, label) => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    version: 1, total: 1, connected: 1,
    plugins: [{
      id: "google_cloud", display_name: "Google Cloud", category: "Developer",
      description: "Read cloud resources", logo_slug: "googlecloud",
      auth: { mode: "oauth_pkce_loopback" }, status: "connected",
      capability_state: state, live_callable: false,
      auth_standard: { ready: true, source: "catalog", fallback: false },
    }],
  }))));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PluginsView inDialog /></QueryClientProvider>);
  expect(await screen.findByText(`Connected · ${label}`)).toBeDefined();
  expect(screen.getByRole("button", { name: "Disconnect" })).toBeDefined();
  expect(screen.queryByText("Connected · Live")).toBeNull();
});
