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

  it("draws the first topic as a reading path and the rest as topic cards", async () => {
    const doc = (slug: string, title: string, section: string, order: number) => ({
      title,
      slug,
      diataxis: "howto",
      summary: `About ${title}.`,
      section,
      section_order: order,
      order: 1,
      tags: [],
      related: [],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            howto: [
              doc("welcome", "Welcome", "Start here", 1),
              doc("architecture-overview", "Architecture overview", "Reference", 7),
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(true);
    const selected: string[] = [];
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <DocsOverview onSelect={(slug) => selected.push(slug)} />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Get started")).toBeTruthy();
      expect(screen.getByText("Browse by topic")).toBeTruthy();
    });
    expect(screen.getByText("Welcome")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Architecture overview/ }));
    expect(selected).toEqual(["architecture-overview"]);

    fireEvent.click(screen.getByRole("link", { name: /view on github/i }));
    expect(openSpy).toHaveBeenCalledWith("https://github.com/PersonalJarvis/PersonalJarvis/tree/main/docs");
  });
});
