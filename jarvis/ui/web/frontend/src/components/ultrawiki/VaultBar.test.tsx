/**
 * VaultBar tests — the Obsidian half of the readable knowledge base.
 *
 * What matters here is that the bar stays truthful about a machine that does
 * not have Obsidian (every headless server, most fresh installs): the export
 * must still be offered and must still work, and the missing app must be
 * stated rather than shown as a dead button.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { VaultBar } from "@/components/ultrawiki/VaultBar";

const STATUS_FRESH = {
  ok: true,
  path: "/home/me/data/ultrawiki-vault",
  exists: false,
  notes: 0,
  last_export_at: "",
  obsidian: { installed: true, registered: false, config_path: "", error: "" },
};

const STATUS_EXPORTED = {
  ...STATUS_FRESH,
  exists: true,
  notes: 3145,
  last_export_at: "2026-07-26T09:00:00Z",
};

function installRoutes(routes: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      const key = Object.keys(routes)
        .sort((a, b) => b.length - a.length)
        .find((prefix) => url.startsWith(prefix));
      if (!key) throw new Error(`unrouted: ${url}`);
      const body = routes[key];
      return {
        ok: true,
        status: 200,
        json: async () => body,
      } as unknown as Response;
    }),
  );
  return calls;
}

function renderBar() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <VaultBar />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("state", () => {
  it("shows where the vault will go before anything was exported", async () => {
    installRoutes({ "/api/ultrawiki/vault/status": STATUS_FRESH });
    renderBar();

    const path = await screen.findByTestId("vault-path");
    expect(path.textContent).toContain("ultrawiki-vault");
    expect((await screen.findByTestId("vault-state")).textContent).toBeTruthy();
  });

  it("reports how many notes are on disk once exported", async () => {
    installRoutes({ "/api/ultrawiki/vault/status": STATUS_EXPORTED });
    renderBar();

    expect((await screen.findByTestId("vault-state")).textContent).toContain(
      "3145",
    );
  });
});

describe("exporting", () => {
  it("writes the vault and reports the result", async () => {
    const calls = installRoutes({
      "/api/ultrawiki/vault/status": STATUS_FRESH,
      "/api/ultrawiki/vault/export": {
        ok: true,
        path: "/home/me/data/ultrawiki-vault",
        topics: 1127,
        moments: 2092,
        written: 3223,
        unchanged: 0,
        removed: 0,
      },
    });
    renderBar();

    fireEvent.click(await screen.findByTestId("vault-export"));

    await waitFor(() =>
      expect(calls.some((c) => c.startsWith("POST") && c.includes("export"))).toBe(
        true,
      ),
    );
    expect((await screen.findByTestId("vault-result")).textContent).toContain(
      "3223",
    );
  });
});

describe("Obsidian", () => {
  it("offers registration once the vault exists", async () => {
    installRoutes({
      "/api/ultrawiki/vault/status": STATUS_EXPORTED,
      "/api/ultrawiki/vault/register": { ok: true, status: "added" },
    });
    renderBar();

    fireEvent.click(await screen.findByTestId("vault-register"));

    await waitFor(() =>
      expect(screen.getByTestId("vault-register").hasAttribute("disabled")).toBe(
        false,
      ),
    );
  });

  it("hides registration while there is nothing to register", async () => {
    installRoutes({ "/api/ultrawiki/vault/status": STATUS_FRESH });
    renderBar();

    await screen.findByTestId("vault-export");
    expect(screen.queryByTestId("vault-register")).toBeNull();
  });

  it("says Obsidian is missing instead of offering a dead button", async () => {
    installRoutes({
      "/api/ultrawiki/vault/status": {
        ...STATUS_EXPORTED,
        obsidian: {
          installed: false,
          registered: false,
          config_path: "",
          error: "",
        },
      },
    });
    renderBar();

    expect(await screen.findByTestId("vault-no-obsidian")).toBeTruthy();
    expect(screen.queryByTestId("vault-register")).toBeNull();
    // The export stays available: the files are the point, the app is not.
    expect(screen.getByTestId("vault-export")).toBeTruthy();
  });

  it("states that the vault is already known to Obsidian", async () => {
    installRoutes({
      "/api/ultrawiki/vault/status": {
        ...STATUS_EXPORTED,
        obsidian: {
          installed: true,
          registered: true,
          config_path: "",
          error: "",
        },
      },
    });
    renderBar();

    expect(await screen.findByTestId("vault-registered")).toBeTruthy();
    expect(screen.queryByTestId("vault-register")).toBeNull();
  });
});
