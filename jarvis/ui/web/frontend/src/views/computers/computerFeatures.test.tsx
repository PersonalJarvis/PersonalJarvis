/**
 * The Computers features adapted from T3 Code: machine glyphs, the on/off
 * switch, several addresses per machine, Tailscale suggestions, the hosts this
 * PC already knows, automatic placement and GitHub sharing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { loadLocaleChunk, setUiLanguage } from "@/i18n";
import type { Computer } from "@/lib/computersApi";
import { RUN_ON_AUTO, RunOnPicker } from "@/components/agentic/RunOnPicker";
import { ComputerRow } from "./ComputerRow";
import { GithubAccess } from "./GithubAccess";
import { KnownHosts } from "./KnownHosts";
import { isLanHost, machineKind } from "./machineKind";
import { PlacementSection } from "./Placement";

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
    const handler = routes[`${method} ${url}`];
    if (!handler) throw new Error(`unexpected fetch ${method} ${url}`);
    const payload = handler(body);
    return { ok: true, status: 200, statusText: "OK", json: async () => payload } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return calls;
}

function computer(overrides: Partial<Computer> = {}): Computer {
  return {
    id: "c_1",
    name: "VPS",
    kind: "server",
    provider: "generic",
    host: "203.0.113.15",
    port: 22,
    username: "root",
    auth: "key",
    provider_ref: null,
    region: null,
    plan: null,
    host_key: "ssh-ed25519 AAAA",
    host_fingerprint: "SHA256:server",
    created_at: 1_700_000_000,
    facts: {
      hostname: "vps-01",
      os_id: "ubuntu",
      os_name: "Ubuntu 24.04.1 LTS",
      kernel: null,
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
      uptime_s: 1000,
      trace_id: null,
    },
    enabled: true,
    routes: [
      { id: "r_main", host: "203.0.113.15", port: 22, label: null, source: "manual", last_ok_at: null },
    ],
    active_route_id: "r_main",
    placement_weight: 2,
    github_shared_at: null,
    busy: false,
    ...overrides,
  };
}

function withQuery(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(async () => {
  setUiLanguage("en");
  await loadLocaleChunk("computers");
});
afterEach(() => cleanup());

describe("machine kind", () => {
  it("reads the kind from where the machine came from and what it reported", () => {
    expect(machineKind(computer({ provider: "hetzner" }))).toBe("cloud");
    expect(machineKind(computer())).toBe("cloud");
    expect(machineKind(computer({ host: "192.168.1.20", routes: [] }))).toBe("server");
    expect(machineKind(computer({ provider: "raspberry_pi" }))).toBe("mini");
    expect(machineKind(computer({ kind: "local_vm", provider: "multipass" }))).toBe("linux");
    const windows = computer();
    windows.facts = { ...windows.facts!, os_id: "windows", hostname: "DESK-01" };
    expect(machineKind(windows)).toBe("desktop");
    windows.facts = { ...windows.facts!, os_id: "macos", hostname: "Adas-MacBook-Pro" };
    expect(machineKind(windows)).toBe("laptop");
    const pi = computer({ provider: "home_server" });
    pi.facts = { ...pi.facts!, arch: "aarch64", mem_total_mb: 4096 };
    expect(machineKind(pi)).toBe("mini");
    expect(machineKind(computer({ provider: "home_server" }))).toBe("server");
  });

  it("tells home-network addresses apart", () => {
    for (const host of ["10.0.0.4", "192.168.178.132", "172.20.1.1", "nas.local", "pi.fritz.box"]) {
      expect(isLanHost(host)).toBe(true);
    }
    for (const host of ["203.0.113.4", "172.32.0.1", "box.tail1234.ts.net", "example.com"]) {
      expect(isLanHost(host)).toBe(false);
    }
  });
});

describe("computer row", () => {
  it("switches a computer off and shows it as switched off", async () => {
    const calls = installFetch({
      "PATCH /api/computers/c_1": () => computer({ enabled: false }),
    });
    const { rerender } = withQuery(<ComputerRow computer={computer()} checking={false} onOpen={() => undefined} />);

    fireEvent.click(screen.getByTestId("computer-switch-c_1"));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ enabled: false });
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <ComputerRow computer={computer({ enabled: false })} checking={false} onOpen={() => undefined} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("computer-item-c_1").getAttribute("data-enabled")).toBe("false");
    expect(screen.getByText("Switched off")).toBeTruthy();
  });

  it("opens the detail page from the name, not from the switch", () => {
    installFetch({ "PATCH /api/computers/c_1": () => computer({ enabled: false }) });
    const onOpen = vi.fn();
    withQuery(<ComputerRow computer={computer()} checking={false} onOpen={onOpen} />);

    fireEvent.click(screen.getByTestId("computer-switch-c_1"));
    expect(onOpen).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("computer-row-c_1"));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("lists, reorders, adds and removes addresses", async () => {
    const two = computer({
      routes: [
        { id: "r_main", host: "192.168.1.20", port: 22, label: null, source: "manual", last_ok_at: null },
        { id: "r_ts", host: "vps-01.tail1234.ts.net", port: 22, label: "Tailscale", source: "tailscale", last_ok_at: 1 },
      ],
    });
    const calls = installFetch({
      "PUT /api/computers/c_1/routes/order": () => two,
      "POST /api/computers/c_1/routes": () => two,
      "DELETE /api/computers/c_1/routes/r_ts": () => computer(),
    });
    withQuery(<ComputerRow computer={two} checking={false} onOpen={() => undefined} />);

    fireEvent.click(screen.getByTestId("computer-routes-toggle-c_1"));
    const list = screen.getByTestId("computer-routes-c_1");
    expect(within(list).getByText("192.168.1.20")).toBeTruthy();
    expect(within(list).getByText("In use")).toBeTruthy();

    fireEvent.click(within(screen.getByTestId("computer-route-r_main")).getByRole("button", { name: "Move down" }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith("/routes/order"))?.body).toEqual({ route_ids: ["r_ts", "r_main"] }),
    );

    fireEvent.change(screen.getByTestId("computer-route-host-c_1"), { target: { value: "box.example.com" } });
    fireEvent.click(within(list).getByRole("button", { name: "Add address" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({ host: "box.example.com", port: 22 }),
    );

    fireEvent.click(screen.getByTestId("computer-route-remove-r_ts"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("offers a Tailscale address with one click", async () => {
    const calls = installFetch({ "POST /api/computers/c_1/routes": () => computer() });
    withQuery(
      <ComputerRow
        computer={computer()}
        checking={false}
        onOpen={() => undefined}
        suggestions={[{ host: "vps-01.tail1234.ts.net", port: 22, label: "Tailscale", online: true }]}
      />,
    );

    fireEvent.click(screen.getByTestId("computer-routes-toggle-c_1"));
    const suggestion = screen.getByTestId("computer-route-suggestion");
    expect(within(suggestion).getByText("On Tailscale as vps-01.tail1234.ts.net")).toBeTruthy();
    fireEvent.click(within(suggestion).getByRole("button"));

    await waitFor(() =>
      expect(calls[0]?.body).toEqual({
        host: "vps-01.tail1234.ts.net",
        port: 22,
        label: "Tailscale",
        source: "tailscale",
      }),
    );
  });

  it("a failed check offers its error ID to copy", () => {
    installFetch({});
    const down = computer({
      health: { ...computer().health, status: "offline", message: "The server did not answer in time.", trace_id: "cmp-1a2b3c4d" },
    });
    withQuery(<ComputerRow computer={down} checking={false} onOpen={() => undefined} />);

    expect(screen.getByTestId("computer-copy-error-c_1")).toBeTruthy();
  });

  it("an older backend without routes still shows one address", () => {
    installFetch({});
    const old = computer();
    delete old.routes;
    delete old.enabled;
    withQuery(<ComputerRow computer={old} checking={false} onOpen={() => undefined} />);

    expect(screen.getByText("1 address")).toBeTruthy();
    fireEvent.click(screen.getByTestId("computer-routes-toggle-c_1"));
    expect(within(screen.getByTestId("computer-routes-c_1")).getByText("203.0.113.15")).toBeTruthy();
  });
});

describe("known hosts", () => {
  it("lists this PC's hosts and fills the form on a click", async () => {
    installFetch({
      "GET /api/computers/ssh-hosts": () => ({
        hosts: [
          { alias: "hz", host: "203.0.113.7", port: 2222, username: "deploy", source: "ssh_config", has_identity_file: true, needs_proxy: false, added_as: null },
          { alias: "inner", host: "inner", port: 22, username: "root", source: "ssh_config", has_identity_file: false, needs_proxy: true, added_as: null },
          { alias: "198.51.100.4", host: "198.51.100.4", port: 22, username: null, source: "known_hosts", has_identity_file: false, needs_proxy: false, added_as: "c_9" },
        ],
      }),
    });
    const onPick = vi.fn();
    withQuery(<KnownHosts onPick={onPick} />);

    const hz = await screen.findByTestId("cx-known-hz");
    expect(within(hz).getByText("deploy@203.0.113.7:2222")).toBeTruthy();
    expect((screen.getByTestId("cx-known-inner") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("cx-known-198.51.100.4") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(hz);
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ alias: "hz", port: 2222 }));
  });

  it("shows nothing when this PC knows no servers", async () => {
    const calls = installFetch({ "GET /api/computers/ssh-hosts": () => ({ hosts: [] }) });
    const { container } = withQuery(<KnownHosts onPick={() => undefined} />);

    await waitFor(() => expect(calls.length).toBe(1));
    expect(container.textContent).toBe("");
  });
});

describe("automatic placement", () => {
  it("switches on and sets each machine's share", async () => {
    let enabled = false;
    const calls = installFetch({
      "GET /api/computers/placement": () => ({ enabled, local_weight: 2 }),
      "PUT /api/computers/placement": (body) => {
        enabled = Boolean((body as { enabled?: boolean }).enabled ?? enabled);
        return { enabled, local_weight: (body as { local_weight?: number }).local_weight ?? 2 };
      },
      "PATCH /api/computers/c_1": () => computer({ placement_weight: 3 }),
    });
    withQuery(<PlacementSection computers={[computer()]} />);

    fireEvent.click(await screen.findByTestId("computers-placement-switch"));
    await waitFor(() => expect(screen.getByTestId("computers-placement-local")).toBeTruthy());

    fireEvent.click(within(screen.getByTestId("computers-placement-c_1")).getByRole("radio", { name: "More" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ placement_weight: 3 }),
    );
    fireEvent.click(within(screen.getByTestId("computers-placement-local")).getByRole("radio", { name: "Never" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({ local_weight: 0 }),
    );
  });

  it("stays hidden while every computer is switched off", async () => {
    installFetch({ "GET /api/computers/placement": () => ({ enabled: true, local_weight: 2 }) });
    const { container } = withQuery(<PlacementSection computers={[computer({ enabled: false })]} />);

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(container.textContent).toBe("");
  });
});

describe("runs-on picker", () => {
  it("never offers a switched-off computer", async () => {
    installFetch({
      "GET /api/computers": () => ({
        computers: [computer(), computer({ id: "c_off", name: "Old box", enabled: false })],
      }),
    });
    render(<RunOnPicker value={null} onChange={() => undefined} />);

    expect(await screen.findByTestId("ide-run-on-c_1")).toBeTruthy();
    expect(screen.queryByTestId("ide-run-on-c_off")).toBeNull();
  });

  it("offers Automatic only while automatic placement is on", async () => {
    installFetch({
      "GET /api/computers": () => ({ computers: [computer()] }),
      "GET /api/computers/placement": () => ({ enabled: true, local_weight: 2 }),
    });
    const onChange = vi.fn();
    render(<RunOnPicker value={null} onChange={onChange} allowAuto />);

    fireEvent.click(await screen.findByTestId(`ide-run-on-${RUN_ON_AUTO}`));
    expect(onChange).toHaveBeenCalledWith(RUN_ON_AUTO);
  });

  it("falls back to this PC when Automatic was chosen but is switched off now", async () => {
    installFetch({
      "GET /api/computers": () => ({ computers: [computer()] }),
      "GET /api/computers/placement": () => ({ enabled: false, local_weight: 2 }),
    });
    const onChange = vi.fn();
    render(<RunOnPicker value={RUN_ON_AUTO} onChange={onChange} allowAuto />);

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(null));
    expect(screen.queryByTestId(`ide-run-on-${RUN_ON_AUTO}`)).toBeNull();
  });
});

describe("GitHub for the agents", () => {
  it("shares with a warning beside it, then stops sharing", async () => {
    const calls = installFetch({
      "POST /api/computers/c_1/github": () => computer({ github_shared_at: Date.now() / 1000 }),
      "DELETE /api/computers/c_1/github": () => computer(),
    });
    const { rerender } = withQuery(<GithubAccess computer={computer()} />);

    expect(screen.getByText(/same access to GitHub as you/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("computer-github-share"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <GithubAccess computer={computer({ github_shared_at: Date.now() / 1000 })} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("computer-github-share").textContent).toContain("Share again");
    fireEvent.click(screen.getByTestId("computer-github-unshare"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("cannot share with a computer that is off or unreachable", () => {
    installFetch({});
    withQuery(<GithubAccess computer={computer({ enabled: false })} />);

    expect((screen.getByTestId("computer-github-share") as HTMLButtonElement).disabled).toBe(true);
  });
});
