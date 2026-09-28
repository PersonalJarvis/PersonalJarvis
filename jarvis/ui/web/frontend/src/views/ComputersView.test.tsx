/**
 * ComputersView: the empty page opens onto the three ways in; a machine in the
 * list opens its detail page; the console runs a command; the server form
 * sends the password-once login and paints the returned machine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ComputersView } from "@/views/ComputersView";
import { loadLocaleChunk, setUiLanguage } from "@/i18n";
import type { Computer } from "@/lib/computersApi";

interface Call {
  url: string;
  method: string;
  body: unknown;
}

function installFetch(routes: Record<string, (body: unknown) => unknown>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((k) => {
        const [m, path] = k.split(" ");
        return m === method && url === path;
      });
    if (!key) throw new Error(`unexpected fetch ${method} ${url}`);
    const payload = routes[key](body);
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => payload,
    } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return calls;
}

const IDENTITY = {
  public_key: "ssh-ed25519 AAAAC3Nza personal-jarvis@desk",
  fingerprint: "SHA256:abc",
  algorithm: "ssh-ed25519",
};

const VPS: Computer = {
  id: "c_1",
  name: "Hostinger VPS",
  kind: "server",
  provider: "hostinger",
  host: "203.0.113.15",
  port: 22,
  username: "root",
  auth: "key",
  provider_ref: "17923",
  region: "Phoenix",
  plan: "KVM 4",
  host_key: "ssh-ed25519 AAAA",
  host_fingerprint: "SHA256:server",
  created_at: 1_700_000_000,
  facts: {
    hostname: "srv17923",
    os_id: "ubuntu",
    os_name: "Ubuntu 24.04.1 LTS",
    kernel: "Linux 6.8.0",
    arch: "x86_64",
    cpu_count: 4,
    mem_total_mb: 8192,
    disk_total_gb: 50,
  },
  health: {
    status: "online",
    checked_at: Date.now() / 1000,
    latency_ms: 42,
    message: null,
    load_1m: 0.5,
    mem_used_pct: 30,
    disk_used_pct: 25,
    uptime_s: 90_000,
  },
  busy: false,
};

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ComputersView />
    </QueryClientProvider>,
  );
}

describe("ComputersView", () => {
  beforeEach(async () => {
    setUiLanguage("en");
    await loadLocaleChunk("computers");
  });
  afterEach(() => cleanup());

  it("opens onto the three ways in when nothing is connected", async () => {
    installFetch({ "GET /api/computers": () => ({ computers: [] }) });
    renderView();

    expect(await screen.findByTestId("computers-welcome")).toBeTruthy();
    expect(screen.getByText(/^Give .+ more computers$/)).toBeTruthy();
    for (const path of ["server", "cloud", "local"]) {
      expect(screen.getByTestId(`computers-path-${path}`)).toBeTruthy();
    }
  });

  it("adds a server with the password-once login", async () => {
    let rows: Computer[] = [];
    const calls = installFetch({
      "GET /api/computers": () => ({ computers: rows }),
      "GET /api/computers/identity": () => IDENTITY,
      "POST /api/computers": () => {
        rows = [VPS];
        return VPS;
      },
    });
    renderView();

    fireEvent.click(await screen.findByTestId("computers-path-server"));
    const dialog = await screen.findByTestId("computers-add-dialog");
    fireEvent.change(screen.getByPlaceholderText("203.0.113.10"), {
      target: { value: "203.0.113.15" },
    });
    const password = dialog.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(password, { target: { value: "hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: /^Connect$/ }));

    await screen.findByTestId("computer-detail");
    const post = calls.find((c) => c.method === "POST" && c.url === "/api/computers");
    expect(post?.body).toMatchObject({
      host: "203.0.113.15",
      username: "root",
      auth: "password",
      password: "hunter2",
      keep_password: false,
    });
  });

  it("lists a machine, opens it and runs a console command", async () => {
    const calls = installFetch({
      "GET /api/computers": () => ({ computers: [VPS] }),
      "GET /api/computers/identity": () => IDENTITY,
      "POST /api/computers/c_1/run": () => ({
        exit_status: 0,
        stdout: " 12:00 up 1 day\n",
        stderr: "",
        duration_ms: 80,
        truncated: false,
      }),
    });
    renderView();

    fireEvent.click(await screen.findByTestId("computer-row-c_1"));
    expect(await screen.findByTestId("computer-detail")).toBeTruthy();
    expect(screen.getByText("Ubuntu 24.04.1 LTS")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "uptime" }));
    await waitFor(() =>
      expect(screen.getByTestId("computer-console-output").textContent).toContain("12:00 up 1 day"),
    );
    expect(calls.some((c) => c.url === "/api/computers/c_1/run" && (c.body as { command: string }).command === "uptime")).toBe(true);
  });
});
