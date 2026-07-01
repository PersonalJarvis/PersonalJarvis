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
        { slug: "alex", title: "Alex", mtime: 1, size: 100 },
        { slug: "sam", title: "Sam", mtime: 2, size: 200 },
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

const SAM_PAGE: WikiPageResponse = {
  ok: true,
  slug: "sam",
  kind: "entity",
  title: "Sam",
  path: "entities/sam.md",
  frontmatter: {
    type: "entity",
    entity_kind: "person",
    slug: "sam",
    aliases: [],
    created: "2026-05-13",
    updated: "2026-05-13",
  },
  body_md: "# Sam\n\n## Relationships\n\nFather of [[alex]] — established via voice fact.\n",
  wikilinks: ["alex"],
  stats: { words: 17, bytes: 289, mtime: 1 },
};

const SAM_BACKLINKS: WikiBacklinksResponse = {
  ok: true,
  slug: "sam",
  backlinks: [
    {
      slug: "alex",
      title: "Alex",
      snippet: "...Father is [[sam]] — born 1976...",
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
      "/api/wiki/page/": () => SAM_PAGE,
      "/api/wiki/backlinks/": () => SAM_BACKLINKS,
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
      expect(screen.getByText("alex.md")).toBeDefined();
      expect(screen.getByText("sam.md")).toBeDefined();
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
      expect(screen.getByText("sam.md")).toBeDefined();
    });

    fireEvent.click(screen.getByText("sam.md"));

    await waitFor(() => {
      expect(screen.getByTestId("wiki-page-renderer")).toBeDefined();
    });
    expect(screen.getByTestId("wiki-page-title").textContent).toBe("Sam");
  });
});

describe("PageRenderer — wikilink behaviour", () => {
  it("preprocessWikilinks rewrites [[slug]] / [[folder/slug]] / [[slug|label]]", () => {
    expect(preprocessWikilinks("see [[alex]] here")).toBe(
      "see [alex](#wiki:alex) here",
    );
    expect(preprocessWikilinks("see [[entities/alex]] here")).toBe(
      "see [alex](#wiki:alex) here",
    );
    expect(preprocessWikilinks("see [[alex|the son]] here")).toBe(
      "see [the son](#wiki:alex) here",
    );
  });

  it("clicking a wikilink fires onWikilinkClick with the target slug", async () => {
    installFetchMock({
      "/api/wiki/tree": () => POPULATED_TREE,
      "/api/wiki/page/sam": () => SAM_PAGE,
    });
    const onClick = vi.fn();
    renderWithClient(
      <PageRenderer slug="sam" onWikilinkClick={onClick} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("wiki-page-renderer")).toBeDefined();
    });

    const link = document.querySelector(
      "a.wikilink[data-target-slug='alex']",
    ) as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    fireEvent.click(link!);
    expect(onClick).toHaveBeenCalledWith("alex");
  });

  it("renders a broken wikilink with the `.broken` class when the slug is unknown", async () => {
    const brokenPage: WikiPageResponse = {
      ...SAM_PAGE,
      body_md: "Refers to [[nonexistent-slug]] which doesn't exist.\n",
      wikilinks: ["nonexistent-slug"],
    };
    installFetchMock({
      "/api/wiki/tree": () => POPULATED_TREE,
      "/api/wiki/page/sam": () => brokenPage,
    });
    renderWithClient(
      <PageRenderer slug="sam" onWikilinkClick={vi.fn()} />,
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
        slug="sam"
        kind="entity"
        title="Sam"
        frontmatter={{
          type: "entity",
          entity_kind: "person",
          slug: "sam",
          aliases: ["herry", "h"],
          created: "2026-05-13",
          updated: "2026-05-13",
        }}
        vaultRelPath="entities/sam.md"
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
