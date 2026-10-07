import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as openExternal from "@/lib/openExternal";
import { DocsContent } from "./DocsContent";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("DocsContent", () => {
  it("opens external guide links through the desktop-safe browser bridge", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/docs/grouped")) {
          return new Response(JSON.stringify({}), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(
          JSON.stringify({
            title: "A useful guide",
            slug: "useful-guide",
            diataxis: "howto",
            status: "active",
            owner: "maintainers",
            last_reviewed: "2026-07-15",
            phase: "public",
            audience: "end-user",
            summary: "Learn how the feature works without unnecessary complexity.",
            section: "Start Here",
            section_order: 1,
            order: 1,
            tags: [],
            related: [],
            deprecates: null,
            deprecated_by: null,
            next_review_due: null,
            version_min: null,
            path: "useful-guide.md",
            body_hash: "hash",
            error: null,
            heading_count: 1,
            body: "## Next Steps\n\nRead the [online docs](https://personaljarvis.ai/docs/).",
            headings: [{ level: 2, text: "Next Steps", slug: "next-steps" }],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(true);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });

    render(
      <QueryClientProvider client={client}>
        <DocsContent
          slug="useful-guide"
          onSelect={() => {}}
          onShowOverview={() => {}}
        />
      </QueryClientProvider>,
    );

    const link = await waitFor(() => screen.getByRole("link", { name: "online docs" }));
    fireEvent.click(link);

    expect(openSpy).toHaveBeenCalledWith("https://personaljarvis.ai/docs/");
  });
  it("labels callouts and shows the reading time from tokens, not raw colours", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/docs/grouped")) {
          return new Response(JSON.stringify({}), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(
          JSON.stringify({
            title: "Keys",
            slug: "keys",
            diataxis: "howto",
            summary: "Keep keys safe.",
            section: "Privacy",
            section_order: 1,
            order: 1,
            tags: [],
            related: [],
            last_reviewed: null,
            path: "docs/product/keys.md",
            body: "> [!warning] Never say a key out loud.",
            headings: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });

    render(
      <QueryClientProvider client={client}>
        <DocsContent slug="keys" onSelect={() => {}} onShowOverview={() => {}} />
      </QueryClientProvider>,
    );

    const label = await waitFor(() => screen.getByText("Warning"));
    const callout = label.closest("aside");
    expect(callout?.textContent).toContain("Never say a key out loud.");
    expect(callout?.className).toContain("border-warning");
    expect(screen.getByText("1 min read")).toBeTruthy();
  });
});
