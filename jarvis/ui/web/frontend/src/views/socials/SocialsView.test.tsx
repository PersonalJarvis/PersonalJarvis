/**
 * Component tests for SocialsView (grouped, read-only).
 *
 * Links are grouped by `platform`: one link → a direct external-open tile,
 * several links → a tile that opens an in-section detail page to pick a link.
 * The section is READ-ONLY: no add tile, no edit/delete controls — the links
 * are curated in the seed (socials_routes.py), which is the right model for an
 * open-source distribution (a downloader views/clicks, never manages the links).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { SocialsView } from "@/views/socials/SocialsView";

interface RouteResult {
  status?: number;
  body: unknown;
}
interface Call {
  url: string;
  method: string;
}

function installFetchMock(routes: Record<string, () => RouteResult>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method });
    const keys = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const key of keys) {
      const [routeMethod, prefix] = key.split(" ");
      if (method === routeMethod && url.startsWith(prefix)) {
        const { status = 200, body: resBody } = routes[key]();
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status >= 200 && status < 300 ? "OK" : "ERR",
          json: async () => resBody,
          text: async () => JSON.stringify(resBody),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return calls;
}

const DISCORD = {
  id: "d1",
  platform: "discord",
  label: "Discord",
  url: "https://discord.gg/UPu6pFWrJ",
  enabled: true,
  order: 0,
};
const GITHUB_REPO = {
  id: "g1",
  platform: "github",
  label: "GitHub (Repo)",
  url: "https://github.com/PersonalJarvis/PersonalJarvis",
  enabled: true,
  order: 1,
};
const GITHUB_PROFILE = {
  id: "g2",
  platform: "github",
  label: "GitHub (Profile)",
  url: "https://github.com/PersonalJarvis",
  enabled: true,
  order: 2,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SocialsView (grouped, read-only)", () => {
  it("a single-link platform is a direct link; a multi-link platform is a button", async () => {
    installFetchMock({
      "GET /api/socials": () => ({ body: { entries: [DISCORD, GITHUB_REPO, GITHUB_PROFILE] } }),
    });
    render(<SocialsView />);

    const discord = (await screen.findByRole("link", { name: /discord/i })) as HTMLAnchorElement;
    expect(discord.href).toContain("discord.gg/UPu6pFWrJ");
    expect(discord.target).toBe("_blank");

    expect(screen.queryByRole("link", { name: /github/i })).toBeNull();
    expect(screen.getByRole("button", { name: /github/i })).toBeTruthy();
  });

  it("clicking a multi-link platform opens a detail page listing all its links", async () => {
    installFetchMock({
      "GET /api/socials": () => ({ body: { entries: [DISCORD, GITHUB_REPO, GITHUB_PROFILE] } }),
    });
    render(<SocialsView />);

    fireEvent.click(await screen.findByRole("button", { name: /github/i }));

    const repo = (await screen.findByRole("link", { name: /repo/i })) as HTMLAnchorElement;
    const profile = screen.getByRole("link", { name: /profile/i }) as HTMLAnchorElement;
    expect(repo.href).toContain("PersonalJarvis");
    expect(profile.href).toContain("github.com/PersonalJarvis");
    expect(repo.target).toBe("_blank");
    expect(repo.rel).toContain("noopener");
  });

  it("is read-only: no add tile and no edit/delete controls (grid or detail)", async () => {
    installFetchMock({
      "GET /api/socials": () => ({ body: { entries: [DISCORD, GITHUB_REPO, GITHUB_PROFILE] } }),
    });
    render(<SocialsView />);
    await screen.findByRole("link", { name: /discord/i });

    expect(screen.queryByRole("button", { name: /add/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();

    // …and inside a platform detail page too.
    fireEvent.click(screen.getByRole("button", { name: /github/i }));
    await screen.findByRole("link", { name: /repo/i });
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
  });

  it("only makes GET requests — never a mutating call", async () => {
    const calls = installFetchMock({
      "GET /api/socials": () => ({ body: { entries: [DISCORD, GITHUB_REPO, GITHUB_PROFILE] } }),
    });
    render(<SocialsView />);
    fireEvent.click(await screen.findByRole("button", { name: /github/i }));
    await screen.findByRole("link", { name: /repo/i });

    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });
});
