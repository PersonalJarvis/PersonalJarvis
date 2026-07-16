import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DocsOverview } from "./DocsOverview";
import * as openExternal from "@/lib/openExternal";

function renderOverview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DocsOverview onSelect={() => {}} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("DocsOverview", () => {
  it("shows a full loading surface while the local index is pending", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    renderOverview();

    expect(screen.getByText("Preparing your documentation")).toBeTruthy();
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("renders local quick links and opens the redesigned online docs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            unclassified: [
              {
                title: "Architecture overview",
                slug: "architecture-overview",
                diataxis: "unclassified",
                summary: "See how the main parts work together.",
                section: "Reference",
                section_order: 7,
                order: 1,
                tags: [],
                related: [],
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(undefined);

    renderOverview();

    await waitFor(() => {
      expect(screen.getByText("Architecture overview")).toBeTruthy();
      expect(screen.getByText("Browse by Topic")).toBeTruthy();
    });
    fireEvent.click(
      screen.getByRole("link", { name: /open redesigned online docs/i }),
    );

    expect(openSpy).toHaveBeenCalledWith("https://personaljarvis.ai/docs/");
  });
});
