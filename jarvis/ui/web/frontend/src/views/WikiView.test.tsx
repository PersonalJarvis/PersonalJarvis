/**
 * Component tests for WikiView and its sub-components.
 *
 * Mocks the fetch surface defined in `src/lib/wikiApi.ts`. The contract is
 * documented in `docs/plans/b3/00-OVERVIEW.md` §3.1 — these tests pin the
 * client-side projection of that contract.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { WikiView } from "@/views/WikiView";
import { PageRenderer, preprocessWikilinks } from "@/components/wiki/PageRenderer";
import { PageHeader } from "@/components/wiki/PageHeader";
import type {
  WikiPageResponse,
  WikiTreeResponse,
  WikiBacklinksResponse,
  WikiHealthSnapshot,
} from "@/lib/wikiApi";

const { useWikiLiveMock } = vi.hoisted(() => ({
  useWikiLiveMock: vi.fn(() => ({ connected: true, lastEventAt: null })),
}));

vi.mock("@/hooks/useWikiLive", () => ({
  useWikiLive: useWikiLiveMock,
}));

vi.mock("@/components/wiki/WikiGraph", () => ({
  WikiGraph: ({ onNodeClick }: { onNodeClick: (slug: string) => void }) => (
    <button type="button" onClick={() => onNodeClick("example-parent")}>
      Example Parent graph node
    </button>
  ),
}));

function freshClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  });
}

function renderWithClient(node: React.ReactNode) {
  const client = freshClient();
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

/**
 * Install a mock for global `fetch` that returns whatever JSON the mapping
 * function provides for each URL prefix. Unknown URLs throw, so we notice
 * accidental network calls in tests.
 */
function installFetchMock(
  routes: Record<string, () => unknown>,
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/setup/obsidian/status")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({
          installed: true,
          config_exists: true,
          vault_registered: true,
          recommended_action: "ok",
        }),
      } as Response;
    }
    if (url.startsWith("/api/setup/state")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({
          completed: true,
        }),
      } as Response;
    }
    // Which mode owns the Wiki section. These tests are about the NORMAL wiki
    // body, and that body only renders once the backend has actually said the
    // mode is off — an unanswered status renders neither body, because
    // guessing "off" is what dropped Ultra installs back into the normal wiki
    // after every restart (WikiView.mode-persistence.test.tsx).
    if (url.startsWith("/api/ultrawiki/status")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({
          enabled: false,
          configured: false,
          started: false,
          db_backend: "sqlite",
          backend_in_use: "",
          slots: {},
          counts: {},
          pipeline: { running: false, processed: {} },
          sources: [],
          jobs: [],
          search_legs: {},
          degradations: [],
        }),
      } as Response;
    }
    for (const prefix of Object.keys(routes)) {
      if (url.startsWith(prefix)) {
        const body = routes[prefix]();
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => body,
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  // jsdom may not have a typed fetch by default; cast through `unknown` to
  // satisfy TS while still ensuring the runtime override happens.
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

const EMPTY_TREE: WikiTreeResponse = {
  ok: true,
  vault_root: "wiki/obsidian-vault",
  folders: [
    { name: "entities", kind: "entity", count: 0, files: [] },
    { name: "concepts", kind: "concept", count: 0, files: [] },
    { name: "projects", kind: "project", count: 0, files: [] },
    { name: "sessions", kind: "session", count: 0, files: [] },
  ],
  stats: { total_pages: 0, total_links: 0, last_curator_run: null },
};

const POPULATED_TREE: WikiTreeResponse = {
  ok: true,
  vault_root: "wiki/obsidian-vault",
  folders: [
    {
      name: "entities",
      kind: "entity",
      count: 2,
      files: [
        { slug: "example-user", title: "Example User", mtime: 1, size: 100 },
        { slug: "example-parent", title: "Example Parent", mtime: 2, size: 200 },
      ],
    },
    { name: "concepts", kind: "concept", count: 0, files: [] },
    {
      name: "projects",
      kind: "project",
      count: 1,
      files: [
        { slug: "pixel-art-editor", title: "Pixel Art Editor", mtime: 3, size: 50 },
      ],
    },
    { name: "sessions", kind: "session", count: 0, files: [] },
  ],
  stats: { total_pages: 3, total_links: 8, last_curator_run: "2026-05-13T13:59:00" },
};

const EXAMPLE_PARENT_PAGE: WikiPageResponse = {
  ok: true,
  slug: "example-parent",
  kind: "entity",
  title: "Example Parent",
  path: "entities/example-parent.md",
  frontmatter: {
    type: "entity",
    entity_kind: "person",
    slug: "example-parent",
    aliases: [],
    created: "2026-05-13",
    updated: "2026-05-13",
  },
  body_md: "# Example Parent\n\n## Relationships\n\nRelated to [[example-user]] in this synthetic fixture.\n",
  wikilinks: ["example-user"],
  stats: { words: 17, bytes: 289, mtime: 1 },
};

const EXAMPLE_PARENT_BACKLINKS: WikiBacklinksResponse = {
  ok: true,
  slug: "example-parent",
  backlinks: [
    {
      slug: "example-user",
      title: "Example User",
      snippet: "...Related to [[example-parent]] in this synthetic fixture...",
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  useWikiLiveMock.mockClear();
});

describe("WikiView — empty state", () => {
  it("renders the empty-state card when the tree returns 0 pages", async () => {
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
    });
    renderWithClient(<WikiView />);
    expect(useWikiLiveMock).toHaveBeenCalledTimes(1);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-empty-state")).toBeDefined();
    });
    expect(screen.getByText(/Your wiki is still empty/i)).toBeDefined();
  });
});

describe("WikiView — populated tree", () => {
  beforeEach(() => {
    installFetchMock({
      "/api/wiki/tree": () => POPULATED_TREE,
      "/api/wiki/page/": () => EXAMPLE_PARENT_PAGE,
      "/api/wiki/backlinks/": () => EXAMPLE_PARENT_BACKLINKS,
    });
  });

  it("renders folder counts and visible leaves for non-empty folders", async () => {
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-tree-sidebar")).toBeDefined();
    });

    // Three populated leaves are visible because entities and projects open
    // by default (per mockup contract).
    await waitFor(() => {
      expect(screen.getByText("example-user.md")).toBeDefined();
      expect(screen.getByText("example-parent.md")).toBeDefined();
      expect(screen.getByText("pixel-art-editor.md")).toBeDefined();
    });

    // Concepts folder shows count "0".
    const conceptsButton = document.querySelector(
      "[data-folder='concepts']",
    );
    expect(conceptsButton).not.toBeNull();
    expect(conceptsButton!.textContent).toContain("0");
  });

  it("expands the Memory Map across the wiki workspace and restores the side panels", async () => {
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-tree-sidebar")).toBeDefined();
    });

    const workspace = screen.getByTestId("wiki-workspace");
    const expandButton = screen.getByRole("button", {
      name: "Expand the Memory Map",
    });
    expect(workspace.getAttribute("data-graph-expanded")).toBe("false");
    expect(expandButton.getAttribute("aria-controls")).toBe("wiki-workspace");
    expect(expandButton.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("wiki-backlinks-placeholder")).not.toBeNull();

    fireEvent.click(expandButton);

    expect(workspace.getAttribute("data-graph-expanded")).toBe("true");
    expect(workspace.classList.contains("z-[100]")).toBe(true);
    expect(screen.queryByTestId("wiki-tree-sidebar")).toBeNull();
    expect(screen.queryByTestId("wiki-backlinks-placeholder")).toBeNull();

    const restoreButton = screen.getByRole("button", {
      name: "Restore the standard wiki layout",
    });
    expect(restoreButton.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(restoreButton);

    expect(workspace.getAttribute("data-graph-expanded")).toBe("false");
    expect(screen.queryByTestId("wiki-tree-sidebar")).not.toBeNull();
    expect(screen.queryByTestId("wiki-backlinks-placeholder")).not.toBeNull();
  });

  it("clicking a leaf in the tree switches to the page tab and loads the page", async () => {
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByText("example-parent.md")).toBeDefined();
    });

    fireEvent.click(screen.getByText("example-parent.md"));

    await waitFor(() => {
      expect(screen.getByTestId("wiki-page-renderer")).toBeDefined();
    });
    expect(screen.getByTestId("wiki-page-title").textContent).toBe("Example Parent");
  }, 10_000);

  it("clicking the already-selected graph node returns to its page", async () => {
    renderWithClient(<WikiView />);

    fireEvent.click(await screen.findByText("example-parent.md"));
    await screen.findByTestId("wiki-page-renderer");
    fireEvent.click(screen.getByRole("button", { name: "Memory Map" }));
    fireEvent.click(await screen.findByRole("button", {
      name: "Example Parent graph node",
    }));

    await waitFor(() => {
      expect(screen.getByTestId("wiki-page-renderer")).toBeDefined();
    });
  });
});

describe("WikiView — health strip", () => {
  const HEALTHY_HEALTH: WikiHealthSnapshot = {
    bootstrap_ok: true,
    bootstrap_error: null,
    vault_root: "wiki/obsidian-vault",
    vault_root_source: "config",
    vault_legacy_conflict: false,
    last_write: {
      ts: 1750000000,
      ok: true,
      pages: ["example-user"],
      error: null,
      source: "curator",
    },
    last_chain_failure: null,
    journal_backlog: 0,
    indexed_pages: 1,
    vault_pages: 1,
    index_state: "ok",
    capture_funnel: {
      window_hours: 24,
      total: 107,
      started: 1,
      filtered: 30,
      empty: 46,
      candidates: 31,
      failed: 2,
      facts: 44,
      sessions_swept: 3,
      stage2_pending: 4,
      stage2_add: 4,
      stage2_update: 5,
      stage2_noop: 8,
      stage2_invalidate: 1,
      stage2_rejected: 2,
      stage2_skipped: 1,
      writes: 9,
    },
    capture_error: null,
  };

  const FAILED_HEALTH: WikiHealthSnapshot = {
    bootstrap_ok: true,
    bootstrap_error: null,
    vault_root: "wiki/obsidian-vault",
    vault_root_source: "config",
    vault_legacy_conflict: false,
    last_write: {
      ts: 1750000000,
      ok: false,
      pages: [],
      error: "Permission denied writing entities/example-user.md",
      source: "curator",
    },
    last_chain_failure: null,
    journal_backlog: 3,
    indexed_pages: 0,
    vault_pages: 1,
    index_state: "stale",
    capture_funnel: {
      window_hours: 24,
      total: 0,
      started: 0,
      filtered: 0,
      empty: 0,
      candidates: 0,
      failed: 0,
      facts: 0,
      sessions_swept: 0,
      stage2_pending: 0,
      stage2_add: 0,
      stage2_update: 0,
      stage2_noop: 0,
      stage2_invalidate: 0,
      stage2_rejected: 0,
      stage2_skipped: 0,
      writes: 0,
    },
    capture_error: null,
  };

  it("renders the vault path for a healthy snapshot", async () => {
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
      "/api/wiki/health": () => ({ ok: true, health: HEALTHY_HEALTH }),
    });
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-health-strip")).toBeDefined();
    });
    await waitFor(() => {
      expect(screen.getByTestId("wiki-health-vault").textContent).toContain(
        "wiki/obsidian-vault",
      );
    });
    expect(
      screen.getByTestId("wiki-health-dot").getAttribute("data-visual"),
    ).toBe("green");
    expect(screen.getByTestId("wiki-capture-funnel").textContent).toContain(
      "Last 24h capture",
    );
    expect(screen.getByTestId("wiki-capture-reviewed").textContent).toContain("107");
    expect(screen.getByTestId("wiki-capture-candidate-reviews").textContent).toContain(
      "31",
    );
    expect(screen.getByTestId("wiki-capture-candidate-facts").textContent).toContain(
      "44",
    );
    expect(screen.getByTestId("wiki-capture-writes").textContent).toContain("9");
    expect(screen.getByTestId("wiki-capture-session-sweeps").textContent).toContain("3");
    expect(screen.getByTestId("wiki-capture-failed-detail").textContent).toContain(
      "2",
    );
  });

  it("renders the error text for a failed last write, and the backlog count", async () => {
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
      "/api/wiki/health": () => ({ ok: true, health: FAILED_HEALTH }),
    });
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-health-write").textContent).toContain(
        "Permission denied writing entities/example-user.md",
      );
    });
    expect(
      screen.getByTestId("wiki-health-dot").getAttribute("data-visual"),
    ).toBe("red");
    expect(screen.getByTestId("wiki-health-backlog").textContent).toContain("3");
  });

  it("rebuilds a stale search index from the health strip", async () => {
    const stale = { ...HEALTHY_HEALTH, indexed_pages: 0, index_state: "stale" as const };
    const fetchMock = installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
      "/api/wiki/health": () => ({ ok: true, health: stale }),
      "/api/wiki/reindex": () => ({ ok: true, indexed_pages: 1, vault_pages: 1 }),
    });
    renderWithClient(<WikiView />);

    const button = await screen.findByTestId("wiki-health-reindex");
    fireEvent.click(button);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/wiki/reindex",
        { method: "POST" },
      );
    });
  });

  it("keeps the reindex failure detail visible beside the retry action", async () => {
    const stale = { ...HEALTHY_HEALTH, indexed_pages: 0, index_state: "stale" as const };
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
      "/api/wiki/health": () => ({ ok: true, health: stale }),
      "/api/wiki/reindex": () => ({ ok: false, error: "FTS database is locked" }),
    });
    renderWithClient(<WikiView />);

    fireEvent.click(await screen.findByTestId("wiki-health-reindex"));

    await waitFor(() => {
      expect(screen.getByTestId("wiki-health-reindex-error").textContent).toContain(
        "FTS database is locked",
      );
    });
  });

  it("localises a reindex failure without a backend detail", async () => {
    const stale = { ...HEALTHY_HEALTH, indexed_pages: 0, index_state: "stale" as const };
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
      "/api/wiki/health": () => ({ ok: true, health: stale }),
      "/api/wiki/reindex": () => ({ ok: false }),
    });
    renderWithClient(<WikiView />);

    fireEvent.click(await screen.findByTestId("wiki-health-reindex"));

    await waitFor(() => {
      expect(screen.getByTestId("wiki-health-reindex-error").textContent).toContain(
        "The wiki search index could not be rebuilt.",
      );
    });
    expect(screen.getByTestId("wiki-health-reindex-error").textContent).not.toContain(
      "index rebuild failed",
    );
  });

  it("shows the capture-store error detail instead of a generic badge", async () => {
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
      "/api/wiki/health": () => ({
        ok: true,
        health: { ...HEALTHY_HEALTH, capture_error: "capture database is locked" },
      }),
    });
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-capture-error").textContent).toContain(
        "capture database is locked",
      );
    });
  });

  it("localises the capture-store sentinel returned by the health API", async () => {
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
      "/api/wiki/health": () => ({
        ok: true,
        health: { ...HEALTHY_HEALTH, capture_error: "capture_store_unavailable" },
      }),
    });
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-capture-error").textContent).toBe(
        "Capture store unavailable",
      );
    });
    expect(screen.getByTestId("wiki-capture-error").textContent).not.toContain(
      "capture_store_unavailable",
    );
  });
});

describe("PageRenderer — wikilink behaviour", () => {
  it("preprocessWikilinks rewrites [[slug]] / [[folder/slug]] / [[slug|label]]", () => {
    expect(preprocessWikilinks("see [[example-user]] here")).toBe(
      "see [example-user](#wiki:example-user) here",
    );
    expect(preprocessWikilinks("see [[entities/example-user]] here")).toBe(
      "see [example-user](#wiki:example-user) here",
    );
    expect(preprocessWikilinks("see [[example-user|the related entity]] here")).toBe(
      "see [the related entity](#wiki:example-user) here",
    );
  });

  it("clicking a wikilink fires onWikilinkClick with the target slug", async () => {
    installFetchMock({
      "/api/wiki/tree": () => POPULATED_TREE,
      "/api/wiki/page/example-parent": () => EXAMPLE_PARENT_PAGE,
    });
    const onClick = vi.fn();
    renderWithClient(
      <PageRenderer slug="example-parent" onWikilinkClick={onClick} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("wiki-page-renderer")).toBeDefined();
    });

    const link = document.querySelector(
      "a.wikilink[data-target-slug='example-user']",
    ) as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    fireEvent.click(link!);
    expect(onClick).toHaveBeenCalledWith("example-user");
  });

  it("renders a broken wikilink with the `.broken` class when the slug is unknown", async () => {
    const brokenPage: WikiPageResponse = {
      ...EXAMPLE_PARENT_PAGE,
      body_md: "Refers to [[nonexistent-slug]] which doesn't exist.\n",
      wikilinks: ["nonexistent-slug"],
    };
    installFetchMock({
      "/api/wiki/tree": () => POPULATED_TREE,
      "/api/wiki/page/example-parent": () => brokenPage,
    });
    renderWithClient(
      <PageRenderer slug="example-parent" onWikilinkClick={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("wiki-page-renderer")).toBeDefined();
    });

    const link = document.querySelector(
      "a.wikilink[data-target-slug='nonexistent-slug']",
    ) as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    expect(link!.className).toContain("broken");
  });
});

describe("PageHeader — frontmatter pills", () => {
  it("renders pills for known frontmatter keys and skips slug + aliases", () => {
    render(
      <PageHeader
        slug="example-parent"
        kind="entity"
        title="Example Parent"
        frontmatter={{
          type: "entity",
          entity_kind: "person",
          slug: "example-parent",
          aliases: ["herry", "h"],
          created: "2026-05-13",
          updated: "2026-05-13",
        }}
        vaultRoot="C:/vault/Jarvis"
        vaultRelPath="entities/example-parent.md"
      />,
    );

    const pills = document.querySelectorAll("[data-pill-key]");
    const keys = Array.from(pills).map((el) =>
      el.getAttribute("data-pill-key"),
    );
    expect(keys).toContain("type");
    expect(keys).toContain("entity_kind");
    expect(keys).toContain("created");
    expect(keys).toContain("updated");
    expect(keys).not.toContain("slug");
    expect(keys).not.toContain("aliases");
  });
});
