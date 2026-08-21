/**
 * Component tests for MarketplaceView — the storefront section.
 *
 * What is pinned here is what the section exists for: one search across all
 * three published kinds, a detail drawer that shows the published files BEFORE
 * an install button is reachable (this is unreviewed third-party content), and
 * a landing that names where the thing went instead of a bare green tick.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// ViewHeader lives in ChatsView, which drags the whole chat surface into the
// render; the view only needs its shape.
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({
    title,
    subtitle,
    right,
  }: {
    title: string;
    subtitle?: string;
    right?: React.ReactNode;
  }) => (
    <header>
      <h2>{title}</h2>
      {subtitle && <p>{subtitle}</p>}
      {right}
    </header>
  ),
}));

const openExternalUrl = vi.fn();
vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: (url: string) => openExternalUrl(url),
}));

import { MarketplaceView } from "@/views/MarketplaceView";
import { setUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import type { CommunityResponse } from "@/views/PluginsCommunity";

const INDEX: CommunityResponse = {
  status: "fresh",
  revision: 24,
  generated_at: "2026-08-17T12:58:53Z",
  plugins: [
    {
      name: "sentry",
      valid: true,
      id: "sentry",
      display_name: "Sentry",
      description: "Errors, issues and releases from your Sentry projects",
      category: "Developer",
      logo_slug: "sentry",
      logo_color: "362D59",
      publisher: "octocat",
      version: "1.0.0",
      source_url: "https://github.com/PersonalJarvis/marketplace/tree/main/plugins/sentry",
      auth: { mode: "hosted_mcp_oauth_dcr" },
      // Exactly the shape the live index serves for this entry.
      mcp_server: { transport: "http", url: "https://mcp.sentry.dev/mcp" },
      installed: false,
    },
  ],
  skills: [
    {
      name: "three-bullet-brief",
      title: "Three Bullet Brief",
      description: "Turns any topic into exactly three crisp bullets",
      publisher: "octocat",
      version: "1.0.0",
      categories: ["productivity", "writing"],
      source_url: "https://github.com/PersonalJarvis/marketplace",
      raw_url: "https://raw.example/skills/three-bullet-brief/SKILL.md",
      installed: false,
    },
  ],
  wallpapers: [
    {
      name: "flooded-observatory",
      title: "Flooded Observatory",
      description: "A drowned observatory under a storm-lit sky",
      publisher: "octocat",
      version: "1.0.0",
      categories: [],
      source_url: "https://github.com/PersonalJarvis/marketplace",
      raw_url: "https://example.test/wallpaper.webp",
      thumb_url: "https://example.test/thumb.webp",
      theme: "dark",
      installed: false,
    },
  ],
};

const CONTENTS = {
  kind: "skill" as const,
  name: "three-bullet-brief",
  title: "Three Bullet Brief",
  root: "skills/three-bullet-brief",
  files: [
    {
      path: "SKILL.md",
      size: 812,
      text: "---\nname: three-bullet-brief\n---\nWrite three bullets.",
      truncated: false,
    },
  ],
};

function installFetchMock(overrides?: Partial<CommunityResponse>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/marketplace/community" && method === "GET") {
      return { ok: true, status: 200, json: async () => ({ ...INDEX, ...overrides }) } as Response;
    }
    if (url.endsWith("/contents")) {
      return { ok: true, status: 200, json: async () => CONTENTS } as Response;
    }
    if (url === "/api/marketplace/community/install/three-bullet-brief") {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ok: true,
          kind: "skill",
          id: "three-bullet-brief",
          title: "Three Bullet Brief",
          location: "skills/three-bullet-brief",
          ready: true,
          next_action: null,
        }),
      } as Response;
    }
    if (url === "/api/marketplace/community/install/sentry") {
      return {
        ok: false,
        status: 502,
        json: async () => ({ detail: "The registry did not answer in time." }),
      } as Response;
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MarketplaceView />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  openExternalUrl.mockReset();
  setUiLanguage("en");
});

describe("MarketplaceView", () => {
  it("shows all three published kinds in one storefront", async () => {
    installFetchMock();
    renderView();

    expect(await screen.findByText("Sentry")).toBeTruthy();
    expect(screen.getByText("Three Bullet Brief")).toBeTruthy();
    expect(screen.getByText("Flooded Observatory")).toBeTruthy();
    // The count in the subtitle covers every kind, not just the plugins.
    expect(screen.getByText(/3 published entries/)).toBeTruthy();
  });

  it("searches across kinds at once", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.change(screen.getByLabelText(/Search plugins/i), {
      target: { value: "observatory" },
    });

    expect(screen.queryByText("Sentry")).toBeNull();
    expect(screen.queryByText("Three Bullet Brief")).toBeNull();
    expect(screen.getByText("Flooded Observatory")).toBeTruthy();
  });

  it("filters to one kind and says how many there are", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.click(screen.getByRole("button", { name: /Skills 1/ }));

    expect(screen.queryByText("Sentry")).toBeNull();
    expect(screen.getByText("Three Bullet Brief")).toBeTruthy();
  });

  it("says where a plugin would send data before it can be installed", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.click(screen.getByText("Sentry"));

    // The destination is spelled out verbatim — a file listing alone does not
    // tell anybody where their access token ends up.
    expect(
      await screen.findByText(/requests and your access token go to/i),
    ).toBeTruthy();
    expect(screen.getByText("https://mcp.sentry.dev/mcp")).toBeTruthy();
    expect(screen.getByText("OAuth sign-in")).toBeTruthy();
  });

  it("names the command a stdio plugin would run", async () => {
    installFetchMock({
      plugins: [
        {
          ...INDEX.plugins[0],
          name: "local-tool",
          id: "local-tool",
          display_name: "Local Tool",
          auth: undefined,
          mcp_server: { transport: "stdio", install: ["npx", "-y", "some-server"] },
        },
      ],
    });
    renderView();

    fireEvent.click(await screen.findByText("Local Tool"));

    expect(await screen.findByText(/runs on your computer/i)).toBeTruthy();
    expect(screen.getByText("npx -y some-server")).toBeTruthy();
  });

  it("shows the published files before install is reachable", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.click(screen.getByText("Three Bullet Brief"));

    // The unreviewed-content warning and the real file are both on screen.
    expect(await screen.findByText("SKILL.md")).toBeTruthy();
    expect(screen.getByText(/nobody reviewed it by hand/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Install" })).toBeTruthy();
  });

  it("names where an installed entry landed and offers the way there", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");
    fireEvent.click(screen.getByText("Three Bullet Brief"));
    fireEvent.click(await screen.findByRole("button", { name: "Install" }));

    expect(await screen.findByText(/Three Bullet Brief installed/)).toBeTruthy();
    expect(screen.getByText(/It is in Skills now/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Show me" }));
    await waitFor(() => {
      expect(useEventStore.getState().activeSection).toBe("skills");
    });
  });

  it("names the cause when an install fails", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");
    fireEvent.click(screen.getByText("Sentry"));
    fireEvent.click(await screen.findByRole("button", { name: "Install" }));

    expect(
      await screen.findByText("The registry did not answer in time."),
    ).toBeTruthy();
  });

  it("says the catalogue is stale instead of pretending it is current", async () => {
    installFetchMock({ status: "stale" });
    renderView();

    expect(
      await screen.findByText(/could not be reached just now/i),
    ).toBeTruthy();
  });

  it("speaks every locale, not only English", async () => {
    installFetchMock();
    setUiLanguage("de");
    renderView();

    // The German strings resolve from the locale file, including the count
    // template whose {count} token this view fills itself.
    expect(await screen.findByText(/3 veröffentlichte Einträge/)).toBeTruthy();
    // The word appears twice on purpose — the filter chip and the shelf head
    // name the same thing, and they must agree.
    expect(screen.getAllByText("Hintergründe").length).toBe(2);
    expect(screen.getByLabelText(/Plugins, Skills und Hintergründe/)).toBeTruthy();

    setUiLanguage("es");
    expect((await screen.findAllByText("Fondos")).length).toBe(2);
  });

  it("opens the public storefront in a real browser", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.click(screen.getByRole("button", { name: /Storefront/ }));

    expect(openExternalUrl).toHaveBeenCalledWith("https://personaljarvis.ai/marketplace/");
  });
});
