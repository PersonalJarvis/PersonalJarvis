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

/** The publishing identity the view asks for; signed out unless a test says so. */
let identity: Record<string, unknown> = { enabled: true, signed_in: false };

function installFetchMock(overrides?: Partial<CommunityResponse>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/marketplace/community" && method === "GET") {
      return { ok: true, status: 200, json: async () => ({ ...INDEX, ...overrides }) } as Response;
    }
    if (url === "/api/marketplace/publish/identity" && method === "GET") {
      return { ok: true, status: 200, json: async () => identity } as Response;
    }
    if (url === "/api/marketplace/publish/signin/start") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ flow_id: "f", user_code: "WXYZ-9876", interval: 60 }),
      } as Response;
    }
    if (url.startsWith("/api/marketplace/publish/signin/poll/")) {
      return { ok: true, status: 200, json: async () => ({ status: "pending" }) } as Response;
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
  identity = { enabled: true, signed_in: false };
});

describe("MarketplaceView", () => {
  it("shows both published kinds in one storefront", async () => {
    installFetchMock();
    renderView();

    expect(await screen.findByText("Sentry")).toBeTruthy();
    expect(screen.getByText("Three Bullet Brief")).toBeTruthy();
    // The count in the subtitle covers every kind, not just the plugins.
    expect(screen.getByText(/2 published entries/)).toBeTruthy();
    // Wallpapers were retired as a marketplace kind: no filter, no shelf.
    expect(screen.queryByRole("button", { name: /Wallpapers/ })).toBeNull();
  });

  it("searches across kinds at once", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.change(screen.getByLabelText(/Search plugins/i), {
      target: { value: "crisp bullets" },
    });

    expect(screen.queryByText("Sentry")).toBeNull();
    expect(screen.getByText("Three Bullet Brief")).toBeTruthy();
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
    expect(await screen.findByText(/2 veröffentlichte Einträge/)).toBeTruthy(); // i18n-allow
    expect(screen.getByLabelText(/Plugins und Skills durchsuchen/)).toBeTruthy(); // i18n-allow

    setUiLanguage("es");
    expect(await screen.findByLabelText(/Buscar plugins y skills/)).toBeTruthy(); // i18n-allow
  });

  it("opens the public storefront in a real browser", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.click(screen.getByRole("button", { name: /Storefront/ }));

    expect(openExternalUrl).toHaveBeenCalledWith("https://github.com/PersonalJarvis/marketplace");
  });

  it("opens the Publish Studio in the app instead of sending people to the website", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.click(screen.getByTestId("marketplace-publish"));
    expect(await screen.findByTestId("publish-studio")).toBeTruthy();
    // Nothing left the app: the studio is the publish path now.
    expect(openExternalUrl).not.toHaveBeenCalled();
  });

  it("offers the GitHub sign-in from the header and shows the code", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");
    fireEvent.click(await screen.findByTestId("publisher-chip-signed-out"));
    expect(await screen.findByTestId("github-signin-dialog")).toBeTruthy();
    expect(await screen.findByTestId("device-code")).toBeTruthy();
    expect(screen.getByText("WXYZ")).toBeTruthy();
  });

  it("never prints a leaked YAML marker as a description", async () => {
    installFetchMock({
      skills: [{ ...INDEX.skills[0], name: "humanizer", title: "Humanizer", description: "|" }],
    });
    renderView();

    expect(await screen.findByText("Humanizer")).toBeTruthy();
    expect(screen.queryByText("|")).toBeNull();
    expect(screen.getByText("No description yet.")).toBeTruthy();
  });

  it("counts what the search leaves, not the whole index", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.change(screen.getByLabelText(/Search plugins/i), {
      target: { value: "crisp bullets" },
    });

    expect(screen.getByRole("button", { name: /Plugins 0/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Skills 1/ })).toBeTruthy();
  });

  it("points an empty search at the built-in catalog", async () => {
    installFetchMock();
    renderView();
    await screen.findByText("Sentry");

    fireEvent.change(screen.getByLabelText(/Search plugins/i), {
      target: { value: "notion" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Browse built-in plugins" }));

    await waitFor(() => {
      expect(useEventStore.getState().activeSection).toBe("plugins");
    });
  });

  it("offers the update when the installed plugin is older than the published one", async () => {
    installFetchMock({
      plugins: [
        { ...INDEX.plugins[0], installed: true, installed_version: "0.9.0", version: "1.0.0" },
      ],
    });
    renderView();

    fireEvent.click(await screen.findByText("Sentry"));

    expect(await screen.findByRole("button", { name: "Update to v1.0.0" })).toBeTruthy();
    expect(screen.getByText("You have v0.9.0")).toBeTruthy();
  });

  it("opens an installed entry's home section from its sheet", async () => {
    installFetchMock({
      skills: [{ ...INDEX.skills[0], installed: true }],
    });
    renderView();

    fireEvent.click(await screen.findByText("Three Bullet Brief"));
    expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: /Open in Skills/ }));

    await waitFor(() => {
      expect(useEventStore.getState().activeSection).toBe("skills");
    });
  });

  it("hides the publish promise when publishing is switched off", async () => {
    identity = { enabled: false, signed_in: false };
    installFetchMock();
    renderView();

    // The identity answers after the index; wait for the hero to settle on it.
    await waitFor(() => {
      expect(screen.getByTestId("marketplace-hero").textContent).not.toMatch(/sign in with GitHub/i);
    });
    expect(screen.queryByTestId("hero-publish")).toBeNull();
  });

  it("filters to the signed-in account's own publications", async () => {
    identity = {
      enabled: true,
      signed_in: true,
      login: "octocat",
      avatar_url: null,
    };
    installFetchMock({
      skills: [
        { ...INDEX.skills[0] },
        {
          ...INDEX.skills[0],
          name: "someone-elses",
          title: "Someone Else's Skill",
          publisher: "stranger",
        },
      ],
    });
    renderView();
    await screen.findByText("Sentry");

    const chip = await screen.findByTestId("publisher-chip");
    expect(chip.textContent).toContain("@octocat");

    fireEvent.click(screen.getByRole("button", { name: /Mine 2/ }));
    expect(screen.getByText("Three Bullet Brief")).toBeTruthy();
    expect(screen.getByText("Sentry")).toBeTruthy();
    expect(screen.queryByText("Someone Else's Skill")).toBeNull();
  });
});
