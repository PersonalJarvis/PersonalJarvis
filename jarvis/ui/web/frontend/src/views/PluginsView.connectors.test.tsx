import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { PluginsView } from "./PluginsView";

it("starts instance browser sign-in with an address and no token", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    if (String(input).endsWith("/connect/start")) {
      return new Response(JSON.stringify({ error_code: "provider_unreachable" }), { status: 502 });
    }
    return new Response(JSON.stringify({ version: 1, plugins: [{
      id: "home_assistant", display_name: "Home Assistant", description: "Smart home", category: "Home & Devices",
      logo_slug: "homeassistant", status: "not_connected", auth: { mode: "instance_browser" },
    }] }));
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><PluginsView /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Connect plugin" }));
  expect(screen.queryByLabelText(/token/i)).toBeNull();
  const proceed = screen.getByRole("button", { name: "Continue in browser" }) as HTMLButtonElement;
  expect(proceed.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Instance address"), { target: { value: "http://homeassistant.local:8123" } });
  fireEvent.click(proceed);
  await waitFor(() => expect(fetcher.mock.calls.find(([url]) => String(url).endsWith("/connect/start"))).toBeDefined());
  const request = fetcher.mock.calls.find(([url]) => String(url).endsWith("/connect/start"))![1]!;
  expect(JSON.parse(String(request.body))).toEqual({ instance_url: "http://homeassistant.local:8123" });
  await screen.findByRole("alertdialog", { name: "Connection unavailable" });
  expect(screen.getByLabelText("Instance address")).toBeDefined();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function setupLocal(available: boolean) {
  let connected = false;
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/connect/start")) {
      connected = available;
      return new Response(JSON.stringify(available
        ? { kind: "local", state: "connected", plugin_id: "amd_gpu" }
        : { detail: "AMD SMI is unavailable on this host" }), { status: available ? 200 : 409 });
    }
    return new Response(JSON.stringify({
      version: 1, schema_version: "test", total: 1, connected: connected ? 1 : 0,
      plugins: [{
        id: "amd_gpu", display_name: "AMD GPU Status", description: "Read GPU telemetry",
        category: "Home & Devices", logo_slug: "amd", auth: { mode: "local" },
        status: connected ? "connected" : "not_connected", live_callable: available,
        unavailable_reason: available ? null : "AMD SMI is unavailable on this host",
      }],
    }));
  });
  vi.stubGlobal("fetch", fetcher);
  const alerts = vi.spyOn(window, "alert").mockImplementation(() => {});
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><PluginsView /></QueryClientProvider>);
  return { fetcher, alerts };
}

it("enables a supported local device without opening an OAuth browser", async () => {
  const { fetcher, alerts } = setupLocal(true);
  fireEvent.click(await screen.findByRole("button", { name: "Connect plugin" }));
  await screen.findByRole("button", { name: "Disconnect plugin" });
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/amd_gpu/connect/start"))).toBe(true);
  expect(alerts).not.toHaveBeenCalled();
});

it("keeps an unavailable device disconnected and explains the missing capability", async () => {
  const { alerts, fetcher } = setupLocal(false);
  const connect = await screen.findByRole("button", { name: "Connect plugin" });
  expect((connect as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Unsupported on this device")).toBeDefined();
  fireEvent.click(connect);
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/connect/start"))).toBe(false);
  expect(alerts).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Disconnect plugin" })).toBeNull();
});


it("shows a normalized connection error without exposing provider response bodies", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/connect/start")) {
      return new Response(JSON.stringify({ error_code: "provider_unreachable", detail: "PRIVATE PROVIDER BODY" }), { status: 502 });
    }
    return new Response(JSON.stringify({ version: 1, plugins: [{
      id: "service", display_name: "Service", description: "Service", category: "Developer",
      logo_slug: "service", status: "not_connected", auth: { mode: "hosted_mcp_oauth_dcr" },
    }] }));
  }));
  const alerts = vi.spyOn(window, "alert").mockImplementation(() => {});
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><PluginsView /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Connect plugin" }));
  await screen.findByRole("alertdialog", { name: "Connection unavailable" });
  expect(screen.getByText(/provider could not be reached/i)).toBeDefined();
  expect(screen.queryByText(/PRIVATE PROVIDER BODY/)).toBeNull();
  expect(alerts).not.toHaveBeenCalled();
});

it("keeps unprovisioned device login pending with an explicit collapsed token alternative", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ version: 1, plugins: [{
    id: "github", display_name: "GitHub", description: "Repositories", category: "Developer",
    logo_slug: "github", status: "not_connected", auth: { mode: "oauth_device_flow" },
    auth_standard: { ready: false, source: "missing", fallback: true },
    fallback_auth: { mode: "pat_paste", instruction_md: "Paste a token" },
  }] })));
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><PluginsView /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Connect plugin" }));
  await screen.findByRole("dialog");
  expect(screen.getByText(/pending publisher setup/i)).toBeDefined();
  const alternative = screen.getByText("Expert token alternative").closest("details");
  expect(alternative?.open).toBe(false);
  expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
