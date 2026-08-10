/**
 * ContentsPanel tests — the database inventory.
 *
 * Pins that the tab actually lists the stored items (not just counts), that
 * "Load more" appends the next page instead of replacing it, and that an empty
 * store points the user at the Sources tab rather than looking broken.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { ContentsPanel } from "@/components/ultrawiki/ContentsPanel";
import type { UltraWikiItem, UltraWikiStatus } from "@/lib/ultrawikiApi";

function status(overrides: Partial<UltraWikiStatus> = {}): UltraWikiStatus {
  return {
    enabled: true,
    started: true,
    db_backend: "sqlite",
    backend_in_use: "sqlite",
    slots: {},
    counts: {
      captured: 1,
      keyword_indexed: 0,
      embedded: 0,
      distilled: 2,
      failed: 0,
      total: 3,
    },
    pipeline: { running: true, state: "idle", processed: {} },
    sources: [
      {
        id: "src-docs",
        connector: "local-folder",
        label: "My documents",
        consent: "approved",
        enabled: true,
        areas: [],
        counts: null,
        sync_state: null,
        last_sync_at: null,
        last_error: null,
      },
    ],
    jobs: [],
    search_legs: {},
    degradations: [],
    ...overrides,
  };
}

function item(index: number): UltraWikiItem {
  return {
    id: index,
    source_id: "src-docs",
    title: `Note ${index}`,
    state: "distilled",
    permalink: `file:///notes/${index}.md`,
    timestamp_utc: "2026-07-01T08:00:00Z",
    ingested_at: new Date(Date.now() - 2 * 60_000).toISOString(),
    updated_at: "2026-07-25T08:00:00Z",
  };
}

/** Records every /items request so the test can assert on the paging. */
function installItemsMock(pages: { items: UltraWikiItem[]; total: number }[]) {
  const seen: string[] = [];
  let call = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    seen.push(url);
    const page = pages[Math.min(call, pages.length - 1)];
    call += 1;
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        items: page.items,
        total: page.total,
        limit: 50,
        offset: 0,
      }),
    } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return { fetchMock, seen };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ContentsPanel", () => {
  it("lists the stored items with their source, stage and load time", async () => {
    installItemsMock([{ items: [item(1), item(2)], total: 2 }]);

    render(<ContentsPanel status={status()} />);

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-item-1")).toBeDefined();
    });
    const row = screen.getByTestId("ultrawiki-item-1");
    expect(row.textContent).toContain("Note 1");
    // The source is shown by its LABEL, not its generated id.
    expect(row.textContent).toContain("My documents");
    // The stage is named in words, not by its database value: "distilled" is
    // a pipeline term, and this row is read by someone who did not write the
    // pipeline. The raw name must not leak back in.
    expect(row.textContent).toContain("Fully processed");
    expect(row.textContent).not.toContain("distilled");
    expect(row.textContent).toContain("2 min ago");
    // The title links back to where the item came from.
    const link = row.querySelector("a");
    expect(link?.getAttribute("href")).toBe("file:///notes/1.md");
    // The header states what the whole store holds.
    expect(screen.getByTestId("ultrawiki-contents-total").textContent).toContain(
      "3",
    );
  });

  it("appends the next page instead of replacing the first", async () => {
    const { seen } = installItemsMock([
      { items: [item(1), item(2)], total: 4 },
      { items: [item(3), item(4)], total: 4 },
    ]);

    render(<ContentsPanel status={status()} />);
    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-contents-load-more")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("ultrawiki-contents-load-more"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-item-4")).toBeDefined();
    });
    // The first page is still there — this is a page APPEND, not a swap.
    expect(screen.getByTestId("ultrawiki-item-1")).toBeDefined();
    expect(seen[1]).toContain("offset=2");
    expect(
      screen.getByTestId("ultrawiki-contents-showing").textContent,
    ).toContain("4");
  });

  it("re-queries from the top when a filter changes", async () => {
    const { seen } = installItemsMock([
      { items: [item(1)], total: 1 },
      { items: [], total: 0 },
    ]);

    render(<ContentsPanel status={status()} />);
    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-item-1")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("ultrawiki-contents-state-filter"));
    const panel = await screen.findByTestId(
      "ultrawiki-contents-state-filter-panel",
    );
    const failed = [...panel.querySelectorAll('[role="option"]')].find(
      (option) => option.getAttribute("data-value") === "failed",
    );
    fireEvent.click(failed!);

    await waitFor(() => {
      expect(seen.some((url) => url.includes("state=failed"))).toBe(true);
    });
    const filtered = seen.find((url) => url.includes("state=failed")) ?? "";
    expect(filtered).toContain("offset=0");
  });

  it("points an empty store at the Sources tab", async () => {
    installItemsMock([{ items: [], total: 0 }]);
    const onOpenSources = vi.fn();

    render(
      <ContentsPanel
        status={status({ counts: { total: 0 } })}
        onOpenSources={onOpenSources}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-contents-empty")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("ultrawiki-contents-open-sources"));
    expect(onOpenSources).toHaveBeenCalledTimes(1);
  });

  it("reports a failed load instead of showing an empty table", async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("network down");
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;

    render(<ContentsPanel status={status()} />);

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-contents-error")).toBeDefined();
    });
    expect(
      screen.getByTestId("ultrawiki-contents-error").textContent,
    ).toContain("network down");
    expect(screen.queryByTestId("ultrawiki-contents-empty")).toBeNull();
  });
});
