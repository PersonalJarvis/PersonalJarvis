/**
 * Outputs view — a selected HTML artifact shows the RENDERED page, not markup.
 *
 * Regression for "HTML files appear as raw source in the outputs section":
 * `.html` counts as a text file server-side, so the old preview dumped the
 * markup into a <pre>. The viewer frames the file from the `/page` endpoint
 * (scripts run, network shut) with `sandbox="allow-scripts"` and no
 * same-origin; the source stays reachable behind an explicit "Source" toggle.
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
vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(async () => {}),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

const SLUG = "mission_019f0fa6-4ff6";
const HTML_PATH = "tasks/019e3288/artifacts/files/HelloBot.html";
const HTML_SOURCE = "<html><body><h1>Hello</h1></body></html>";

function installFetchMock(artifacts: ArtifactSummary[]) {
  const sessions: OutputSummary[] = [
    {
      slug: SLUG,
      utterance: "Build a page",
      status: "success",
      mission_id: "m-1",
      started_at: 1_750_000_000,
    },
  ];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/raw")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          path: HTML_PATH,
          size: HTML_SOURCE.length,
          text: HTML_SOURCE,
          truncated: false,
        }),
      };
    }
    if (url.includes("/artifacts")) {
      return { ok: true, status: 200, json: async () => ({ files: artifacts }) };
    }
    if (url.includes("/plan")) {
      return { ok: true, status: 200, json: async () => ({ plan: null, steps: [] }) };
    }
    if (url.includes("/capabilities")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ native_file_actions: true, platform: "win32" }),
      };
    }
    if (url.includes("/openers")) {
      return { ok: true, status: 200, json: async () => ({ openers: [] }) };
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
  return fetchMock;
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

const HTML_ARTIFACT: ArtifactSummary = {
  path: HTML_PATH,
  size: HTML_SOURCE.length,
  mtime: 1_750_000_100,
  is_text: true,
  preview: HTML_SOURCE,
};

describe("OutputsView — HTML artifacts render as pages", () => {
  it("shows an html artifact in a sandboxed iframe, not a source dump", async () => {
    installFetchMock([HTML_ARTIFACT]);
    renderView();

    await waitFor(() => expect(screen.getByText("HelloBot.html")).toBeTruthy());
    fireEvent.click(screen.getByText("HelloBot.html"));

    const iframe = await waitFor(() => {
      const el = document.querySelector("iframe");
      expect(el).toBeTruthy();
      return el as HTMLIFrameElement;
    });
    // The page endpoint (scripts allowed, every way out shut) inside a frame
    // that runs scripts in an opaque origin — never allow-same-origin.
    expect(iframe.getAttribute("src")).toBe(
      `/api/outputs/${SLUG}/files/${HTML_PATH}/page`,
    );
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    // The markup is NOT dumped as visible text by default.
    expect(screen.queryByText(HTML_SOURCE)).toBeNull();
  });

  it("keeps the source reachable behind the explicit toggle", async () => {
    const fetchMock = installFetchMock([HTML_ARTIFACT]);
    renderView();

    await waitFor(() => expect(screen.getByText("HelloBot.html")).toBeTruthy());
    fireEvent.click(screen.getByText("HelloBot.html"));
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());

    // Rendered mode never fetches the raw text.
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/raw")),
    ).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    await waitFor(() => expect(screen.getByText(HTML_SOURCE)).toBeTruthy());
    expect(document.querySelector("iframe")).toBeNull();

    // And back to the rendered page.
    fireEvent.click(screen.getByRole("button", { name: "Rendered" }));
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());
  });
});
