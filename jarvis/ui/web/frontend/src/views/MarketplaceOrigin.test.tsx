/**
 * What the app shows after something is installed from the marketplace.
 *
 * Installing used to be a one-way trip: the thing landed somewhere on disk and
 * no view could say it had arrived, let alone where it came from. These tests
 * pin the three places that now answer that — a plugin in Plugins, a skill in
 * Skills, a picture in Wallpaper — because "it installed fine" is worth
 * nothing to somebody who cannot find it.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PluginsView } from "@/views/PluginsView";
import { uploadAsEntry } from "@/hooks/useWallpaperUploads";

function client() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

const SEED_PLUGIN = {
  id: "github",
  display_name: "GitHub",
  description: "Repos, issues, pull requests and Actions runs",
  category: "Developer",
  logo_slug: "github",
  auth: { mode: "pat_paste" },
  status: "not_connected",
  source: "seed",
};

const INSTALLED_COMMUNITY_PLUGIN = {
  id: "todo-fox",
  display_name: "TodoFox",
  description: "Tasks and reminders from TodoFox",
  category: "Lists & Tasks",
  logo_slug: "todofox",
  auth: { mode: "pat_paste" },
  // The whole point: installed from the marketplace, not connected yet.
  status: "not_connected",
  source: "community",
  publisher: "octocat",
  version: "1.2.0",
};

function mockCatalog(plugins: unknown[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/marketplace/plugins") {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          version: 1,
          schema_version: "2026-05-09",
          total: plugins.length,
          connected: 0,
          plugins,
        }),
      } as Response;
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("an installed community plugin is findable", () => {
  it("counts in the Installed tab even before it is connected", async () => {
    mockCatalog([SEED_PLUGIN, INSTALLED_COMMUNITY_PLUGIN]);
    render(
      <QueryClientProvider client={client()}>
        <PluginsView />
      </QueryClientProvider>,
    );

    // The shipped catalog is always "available", so a community plugin that
    // only showed up in Browse was indistinguishable from the ones the app
    // came with — Installed is where somebody looks for what they added.
    const installedTab = await screen.findByRole("button", { name: /^Installed\b/i });
    await waitFor(() => {
      expect(installedTab.textContent).toContain("1");
    });

    fireEvent.click(installedTab);
    await waitFor(() => {
      expect(screen.getByText("TodoFox")).toBeDefined();
    });
    // The seed plugin is NOT installed and must not have followed it in.
    expect(screen.queryByText("GitHub")).toBeNull();
  });

  it("marks it as coming from the marketplace, and leaves a seed plugin unmarked", async () => {
    mockCatalog([SEED_PLUGIN, INSTALLED_COMMUNITY_PLUGIN]);
    render(
      <QueryClientProvider client={client()}>
        <PluginsView />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("TodoFox")).toBeDefined();
    });
    const marks = screen.getAllByLabelText(/Installed from the marketplace/i);
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toContain("Marketplace");
    // …and the publisher rides along in the title, so the mark answers
    // "from whom" and not just "from somewhere".
    expect(marks[0].getAttribute("title")).toContain("octocat");
  });
});

describe("wallpapers keep their two origins apart", () => {
  it("files an installed picture under Marketplace, not under Yours", () => {
    const installed = uploadAsEntry({
      id: "ua1b2c3d4e5f60718",
      title: "Moonlit Wave",
      theme: "dark",
      createdAt: 1,
      source: "marketplace",
      sourceId: "moonlit-wave",
      publisher: "octocat",
    });
    expect(installed.style).toBe("marketplace");
    expect(installed.styleLabel).toBe("Marketplace");
    expect(installed.fromMarketplace).toBe(true);
    expect(installed.publisher).toBe("octocat");
    // Removable and re-themeable like any stored picture.
    expect(installed.isUpload).toBe(true);
  });

  it("leaves a picture the owner brought as theirs", () => {
    const own = uploadAsEntry({
      id: "ub1b2c3d4e5f60718",
      title: "My Photo",
      theme: "light",
      createdAt: 2,
    });
    expect(own.style).toBe("yours");
    expect(own.styleLabel).toBe("Yours");
    expect(own.fromMarketplace).toBe(false);
  });
});
