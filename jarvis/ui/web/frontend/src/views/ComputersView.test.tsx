/**
 * ComputersView: the empty page explains itself; connecting needs only an
 * address and a way in (tested before anything is saved); hosting accounts are
 * an optional shortcut; a machine opens on tabs and its console runs a command.
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

const PROVIDERS = [
  {
    id: "hostinger",
    name: "Hostinger",
    category: "hosting",
    api: { connected: false, console_url: "https://hpanel.hostinger.com/profile/api", setup_hint: "hPanel -> Profile -> API", attaches_keys: true, token_label: "API token" },
    ssh: { default_username: "root", ip_hint: "hPanel -> VPS -> Overview", key_hint: "hPanel -> VPS -> SSH keys", key_url: null, password_hint: "The root password you set" },
  },
  {
    id: "ionos",
    name: "IONOS",
    category: "hosting",
    api: null,
    ssh: { default_username: "root", ip_hint: "IONOS Cloud Panel -> Server", key_hint: "Add the key under SSH keys", key_url: "https://my.ionos.de", password_hint: "Initial password from the panel" },
  },
  {
    id: "raspberry_pi",
    name: "Raspberry Pi",
    category: "home",
    api: null,
    ssh: { default_username: "pi", ip_hint: "Look it up in your router", key_hint: "", key_url: null, password_hint: "" },
  },
];

describe("ComputersView", () => {
  beforeEach(async () => {
    setUiLanguage("en");
    await loadLocaleChunk("computers");
  });
  afterEach(() => cleanup());

  it("explains the empty page with one primary action", async () => {
    installFetch({ "GET /api/computers": () => ({ computers: [] }) });
    renderView();

    expect(await screen.findByTestId("computers-welcome")).toBeTruthy();
    expect(screen.getByText("No computers yet")).toBeTruthy();
    expect(screen.getByTestId("computers-add-first")).toBeTruthy();
  });

  it("connects a server from just its address and password", async () => {
    let rows: Computer[] = [];
    const calls = installFetch({
      "GET /api/computers": () => ({ computers: rows }),
      "GET /api/computers/providers": () => ({ providers: PROVIDERS }),
      "GET /api/computers/identity": () => IDENTITY,
      "POST /api/computers/test": () => ({
        ok: true,
        kind: null,
        message: null,
        host_fingerprint: "SHA256:server",
        facts: VPS.facts,
        latency_ms: 42,
      }),
      "POST /api/computers": () => {
        rows = [VPS];
        return VPS;
      },
    });
    renderView();

    fireEvent.click(await screen.findByTestId("computers-add-first"));
    const connect = await screen.findByTestId("cx-connect");
    expect((connect as HTMLButtonElement).disabled).toBe(true);
    // A whole "ssh user@host:port" line is understood.
    fireEvent.change(screen.getByTestId("cx-address"), { target: { value: "ssh admin@203.0.113.15:2222" } });
    fireEvent.change(screen.getByTestId("cx-password"), { target: { value: "hunter2" } });
    fireEvent.click(connect);

    expect(await screen.findByTestId("computer-detail")).toBeTruthy();
    const post = calls.find((c) => c.method === "POST" && c.url === "/api/computers");
    expect(post?.body).toMatchObject({
      host: "203.0.113.15",
      port: 2222,
      username: "admin",
      auth: "password",
      password: "hunter2",
    });
  });

  it("takes a pasted agent answer with a key and needs no password", async () => {
    let rows: Computer[] = [];
    const calls = installFetch({
      "GET /api/computers": () => ({ computers: rows }),
      "GET /api/computers/providers": () => ({ providers: PROVIDERS }),
      "GET /api/computers/identity": () => IDENTITY,
      "POST /api/computers/test": () => ({
        ok: true, kind: null, message: null, host_fingerprint: "SHA256:x", facts: VPS.facts, latency_ms: 9,
      }),
      "POST /api/computers": () => {
        rows = [VPS];
        return VPS;
      },
    });
    renderView();

    fireEvent.click(await screen.findByTestId("computers-add-first"));
    const answer = [
      "-----BEGIN OPENSSH PRIVATE KEY-----",
      "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ",
      "-----END OPENSSH PRIVATE KEY-----",
      "ssh -i ~/.ssh/id_ed25519_grokbot Administrator@192.168.178.132",
    ].join("\n");
    fireEvent.change(await screen.findByTestId("cx-address"), { target: { value: answer } });

    expect(screen.queryByTestId("cx-password")).toBeNull();
    expect(screen.getByTestId("cx-detected").textContent).toContain("Administrator@192.168.178.132");
    fireEvent.click(screen.getByTestId("cx-connect"));

    expect(await screen.findByTestId("computer-detail")).toBeTruthy();
    const post = calls.find((c) => c.method === "POST" && c.url === "/api/computers");
    expect(post?.body).toMatchObject({ host: "192.168.178.132", username: "Administrator", auth: "private_key" });
    expect((post?.body as { private_key: string }).private_key).toContain("BEGIN OPENSSH PRIVATE KEY");
  });

  it("offers hosting accounts as an optional shortcut", async () => {
    installFetch({
      "GET /api/computers": () => ({ computers: [] }),
      "GET /api/computers/providers": () => ({ providers: PROVIDERS }),
      "GET /api/computers/identity": () => IDENTITY,
    });
    renderView();

    fireEvent.click(await screen.findByTestId("computers-add-first"));
    fireEvent.click(await screen.findByTestId("cx-account-hostinger"));
    expect(await screen.findByTestId("wz-token")).toBeTruthy();
  });

  it("says how to fix a failed connection and saves nothing", async () => {
    const calls = installFetch({
      "GET /api/computers": () => ({ computers: [] }),
      "GET /api/computers/providers": () => ({ providers: PROVIDERS }),
      "GET /api/computers/identity": () => IDENTITY,
      "POST /api/computers/test": () => ({
        ok: false,
        kind: "auth",
        message: null,
        host_fingerprint: null,
        facts: null,
        latency_ms: null,
      }),
    });
    renderView();

    fireEvent.click(await screen.findByTestId("computers-add-first"));
    fireEvent.change(await screen.findByTestId("cx-address"), { target: { value: "192.168.1.20" } });
    fireEvent.change(screen.getByTestId("cx-password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByTestId("cx-connect"));

    expect(await screen.findByText(/refused the login/)).toBeTruthy();
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/computers")).toBe(false);
  });

  it("lists a machine, opens it and runs a console command", async () => {
    const calls = installFetch({
      "GET /api/computers": () => ({ computers: [VPS] }),
      "GET /api/computers/identity": () => IDENTITY,
      "GET /api/agentic-ide/offload-on-quit": () => ({ computer_id: null }),
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

    fireEvent.click(screen.getByRole("tab", { name: "Console" }));
    fireEvent.click(screen.getByRole("button", { name: "uptime" }));
    await waitFor(() =>
      expect(screen.getByTestId("computer-console-output").textContent).toContain("12:00 up 1 day"),
    );
    expect(
      calls.some(
        (c) => c.url === "/api/computers/c_1/run" && (c.body as { command: string }).command === "uptime",
      ),
    ).toBe(true);
  });
});
