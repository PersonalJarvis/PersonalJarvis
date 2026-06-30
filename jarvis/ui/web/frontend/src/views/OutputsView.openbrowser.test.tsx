/**
 * Outputs view — the "Browser" opener must reach the user's REAL browser.
 *
 * Regression for the "Browser does nothing" bug: the desktop app embeds
 * WebView2, which silently drops `window.open` / `target="_blank"`. The "Browser"
 * opener (and the ExternalLink open button) therefore must route through the
 * open-external bridge (`openExternalUrl` -> `open_url`, the OS default browser
 * on every OS), NOT a bare `window.open`. The url handed to the bridge must be
 * ABSOLUTE, because `open_url` validates an http(s) absolute url.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { OutputsView } from "@/views/OutputsView";
import type { ArtifactSummary, OutputSummary } from "@/hooks/useOutputs";

vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

// Spy on the bridge helper; the real implementation hits fetch + window.open.
vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(async () => {}),
}));
import { openExternalUrl } from "@/lib/openExternal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

const SLUG = "mission_019f0fa6-4ff6";

function installFetchMock(artifacts: ArtifactSummary[]) {
  const sessions: OutputSummary[] = [
    {
      slug: SLUG,
      utterance: "Some task",
      status: "success",
      mission_id: "m-1",
      started_at: 1_750_000_000,
    },
  ];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/artifacts")) {
      return { ok: true, status: 200, json: async () => ({ files: artifacts }) };
    }
    if (url.includes("/plan")) {
      return { ok: true, status: 200, json: async () => ({ plan: null, steps: [] }) };
    }
    if (url.includes("/capabilities")) {
      // Desktop run (WebView2) — this is the context that drops window.open.
      return {
        ok: true,
        status: 200,
        json: async () => ({ native_file_actions: true, platform: "win32" }),
      };
    }
    if (url.includes("/openers")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          openers: [
            { id: "default", label: "System default app" },
            { id: "browser", label: "Browser" },
          ],
        }),
      };
    }
    if (url.includes("/preferred-opener")) {
      return { ok: true, status: 200, json: async () => ({ opener: "" }) };
    }
    if (url.includes("/api/outputs")) {
      return { ok: true, status: 200, json: async () => ({ sessions }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
}

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OutputsView />
    </QueryClientProvider>,
  );
}

const REPORT: ArtifactSummary = {
  path: "tasks/019e3288/artifacts/files/report.md",
  size: 64,
  mtime: 1_750_000_100,
  is_text: true,
  preview: "# Report",
};

describe("OutputsView — Browser opener reaches the real browser", () => {
  it("routes the 'Browser' pick through openExternalUrl, not window.open", async () => {
    const winOpen = vi.spyOn(window, "open").mockReturnValue(null);
    installFetchMock([REPORT]);
    renderView();

    // The single session auto-selects, so its artifact row is on screen.
    await waitFor(() =>
      expect(screen.getByText(REPORT.path)).toBeTruthy(),
    );

    // Open the "Open with…" chooser (the ChevronDown next to the open button).
    fireEvent.click(screen.getByTitle("Change how this opens"));
    // Pick "Browser".
    fireEvent.click(await screen.findByRole("button", { name: "Browser" }));

    await waitFor(() =>
      expect(openExternalUrl).toHaveBeenCalledTimes(1),
    );
    const calledWith = vi.mocked(openExternalUrl).mock.calls[0][0];
    // Absolute http(s) url ending at the artifact's render endpoint.
    expect(calledWith).toMatch(/^https?:\/\//);
    expect(calledWith).toContain(
      `/api/outputs/${SLUG}/files/tasks/019e3288/artifacts/files/report.md/view`,
    );
    // The WebView2-dropped bare window.open must NOT be used for the browser pick.
    expect(winOpen).not.toHaveBeenCalled();
  });
});
