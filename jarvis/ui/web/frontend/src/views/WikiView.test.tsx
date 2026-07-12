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
        { slug: "ruben", title: "Ruben", mtime: 1, size: 100 },
        { slug: "harald", title: "Harald", mtime: 2, size: 200 },
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

const HARALD_PAGE: WikiPageResponse = {
  ok: true,
  slug: "harald",
  kind: "entity",
  title: "Harald",
  path: "entities/harald.md",
  frontmatter: {
    type: "entity",
    entity_kind: "person",
    slug: "harald",
    aliases: [],
    created: "2026-05-13",
    updated: "2026-05-13",
  },
  body_md: "# Harald\n\n## Relationships\n\nFather of [[ruben]] — established via voice fact.\n",
  wikilinks: ["ruben"],
  stats: { words: 17, bytes: 289, mtime: 1 },
};

const HARALD_BACKLINKS: WikiBacklinksResponse = {
  ok: true,
  slug: "harald",
  backlinks: [
    {
      slug: "ruben",
      title: "Ruben",
      snippet: "...Father is [[harald]] — born 1976...",
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WikiView — empty state", () => {
  it("renders the empty-state card when the tree returns 0 pages", async () => {
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
    });
    renderWithClient(<WikiView />);

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
      "/api/wiki/page/": () => HARALD_PAGE,
      "/api/wiki/backlinks/": () => HARALD_BACKLINKS,
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
      expect(screen.getByText("ruben.md")).toBeDefined();
      expect(screen.getByText("harald.md")).toBeDefined();
      expect(screen.getByText("pixel-art-editor.md")).toBeDefined();
    });

    // Concepts folder shows count "0".
    const conceptsButton = document.querySelector(
      "[data-folder='concepts']",
    );
    expect(conceptsButton).not.toBeNull();
    expect(conceptsButton!.textContent).toContain("0");
  });

  it("clicking a leaf in the tree switches to the page tab and loads the page", async () => {
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByText("harald.md")).toBeDefined();
    });

    fireEvent.click(screen.getByText("harald.md"));

    await waitFor(() => {
      expect(screen.getByTestId("wiki-page-renderer")).toBeDefined();
    });
    expect(screen.getByTestId("wiki-page-title").textContent).toBe("Harald");
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
      pages: ["ruben"],
      error: null,
      source: "curator",
    },
    last_chain_failure: null,
    journal_backlog: 0,
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
      error: "Permission denied writing entities/ruben.md",
      source: "curator",
    },
    last_chain_failure: null,
    journal_backlog: 3,
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
  });

  it("renders the error text for a failed last write, and the backlog count", async () => {
    installFetchMock({
      "/api/wiki/tree": () => EMPTY_TREE,
      "/api/wiki/health": () => ({ ok: true, health: FAILED_HEALTH }),
    });
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-health-write").textContent).toContain(
        "Permission denied writing entities/ruben.md",
      );
    });
    expect(
      screen.getByTestId("wiki-health-dot").getAttribute("data-visual"),
    ).toBe("red");
    expect(screen.getByTestId("wiki-health-backlog").textContent).toContain("3");
  });
});

describe("PageRenderer — wikilink behaviour", () => {
  it("preprocessWikilinks rewrites [[slug]] / [[folder/slug]] / [[slug|label]]", () => {
    expect(preprocessWikilinks("see [[ruben]] here")).toBe(
      "see [ruben](#wiki:ruben) here",
    );
    expect(preprocessWikilinks("see [[entities/ruben]] here")).toBe(
      "see [ruben](#wiki:ruben) here",
    );
    expect(preprocessWikilinks("see [[ruben|the son]] here")).toBe(
      "see [the son](#wiki:ruben) here",
    );
  });

  it("clicking a wikilink fires onWikilinkClick with the target slug", async () => {
    installFetchMock({
      "/api/wiki/tree": () => POPULATED_TREE,
      "/api/wiki/page/harald": () => HARALD_PAGE,
    });
    const onClick = vi.fn();
    renderWithClient(
      <PageRenderer slug="harald" onWikilinkClick={onClick} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("wiki-page-renderer")).toBeDefined();
    });

    const link = document.querySelector(
      "a.wikilink[data-target-slug='ruben']",
    ) as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    fireEvent.click(link!);
    expect(onClick).toHaveBeenCalledWith("ruben");
  });

  it("renders a broken wikilink with the `.broken` class when the slug is unknown", async () => {
    const brokenPage: WikiPageResponse = {
      ...HARALD_PAGE,
      body_md: "Refers to [[nonexistent-slug]] which doesn't exist.\n",
      wikilinks: ["nonexistent-slug"],
    };
    installFetchMock({
      "/api/wiki/tree": () => POPULATED_TREE,
      "/api/wiki/page/harald": () => brokenPage,
    });
    renderWithClient(
      <PageRenderer slug="harald" onWikilinkClick={vi.fn()} />,
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
        slug="harald"
        kind="entity"
        title="Harald"
        frontmatter={{
          type: "entity",
          entity_kind: "person",
          slug: "harald",
          aliases: ["herry", "h"],
          created: "2026-05-13",
          updated: "2026-05-13",
        }}
        vaultRoot="C:/vault/Jarvis"
        vaultRelPath="entities/harald.md"
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
