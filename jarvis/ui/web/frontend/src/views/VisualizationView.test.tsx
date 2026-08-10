/**
 * The Visualization section — what it selects, what it shows, what it admits.
 *
 * Three contracts are worth pinning:
 * - only files that can actually be DRAWN are offered (a run's logs and JSON
 *   are not visuals, and listing them would make the gallery a file browser),
 * - the shell, not the view, decides the ground the section sits on — the
 *   readability halo that helps the app's own text damages a rendered picture,
 * - the bounded scan is stated on screen, so an older run that was not looked
 *   at never reads as a run that produced nothing.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SectionStage } from "@/App";
import { VisualizationView } from "@/views/VisualizationView";
import { classifyVisual, visualUrl } from "@/hooks/useVisualArtifacts";
import type { ArtifactSummary, OutputSummary } from "@/hooks/useOutputs";

// ViewHeader lives in ChatsView, which subscribes to a WS client on mount —
// null keeps that a deterministic no-op in jsdom (same pattern as OutputsView).
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function installFetchMock(
  runs: OutputSummary[],
  artifactsBySlug: Record<string, ArtifactSummary[]>,
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/capabilities")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ native_file_actions: false, platform: "linux" }),
      };
    }
    const artifacts = /\/api\/outputs\/([^/]+)\/artifacts/.exec(url);
    if (artifacts) {
      const slug = decodeURIComponent(artifacts[1]);
      return {
        ok: true,
        status: 200,
        json: async () => ({ files: artifactsBySlug[slug] ?? [] }),
      };
    }
    if (url.includes("/api/outputs")) {
      return { ok: true, status: 200, json: async () => ({ sessions: runs }) };
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
      <VisualizationView />
    </QueryClientProvider>,
  );
}

function file(path: string, over: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return {
    path,
    size: 2048,
    mtime: 1_700_000_000,
    is_text: false,
    preview: null,
    ...over,
  };
}

describe("classifyVisual", () => {
  it("accepts what the WebView can draw", () => {
    expect(classifyVisual("chart.png")).toBe("image");
    expect(classifyVisual("out/PHOTO.JPEG")).toBe("image");
    expect(classifyVisual("diagram.svg")).toBe("vector");
    expect(classifyVisual("report.html")).toBe("page");
    expect(classifyVisual("paper.pdf")).toBe("document");
  });

  it("rejects everything else, however data-shaped", () => {
    // These are real deliverables — they simply belong in Outputs, not on a
    // stage that promises a picture.
    for (const name of ["run.log", "data.csv", "notes.md", "result.json", "app.py"]) {
      expect(classifyVisual(name)).toBeNull();
    }
  });

  it("encodes each path segment, so '#' in a filename survives", () => {
    // encodeURI would leave the '#' raw and the server would see a fragment.
    expect(visualUrl("run-1", "figs/chart#2.png")).toContain("chart%232.png");
  });
});

describe("VisualizationView", () => {
  it("lists only the visual artifacts and stages the newest one", async () => {
    installFetchMock(
      [
        { slug: "run-new", utterance: "draw the architecture" },
        { slug: "run-old", utterance: "summarise the logs" },
      ],
      {
        "run-new": [
          file("artifacts/diagram.svg", { mtime: 2_000 }),
          file("artifacts/build.log", { mtime: 2_500, is_text: true }),
        ],
        "run-old": [file("artifacts/old-chart.png", { mtime: 1_000 })],
      },
    );

    renderView();

    const gallery = await screen.findByTestId("visualization-gallery");
    await waitFor(() => expect(gallery.querySelectorAll("li")).toHaveLength(2));
    // The .log is a deliverable, but not a picture.
    expect(screen.queryByText("build.log")).toBeNull();

    // Newest file first, and the stage opens on it without a click.
    const image = await screen.findByTestId("visualization-image");
    expect(image.getAttribute("src")).toContain("diagram.svg");
  });

  it("switches the stage to the artifact that was clicked", async () => {
    installFetchMock([{ slug: "run-1", utterance: "make two charts" }], {
      "run-1": [
        file("a/first.png", { mtime: 2_000 }),
        file("a/second.png", { mtime: 1_000 }),
      ],
    });

    renderView();

    const second = await screen.findByText("second.png");
    fireEvent.click(second);

    await waitFor(() =>
      expect(
        screen.getByTestId("visualization-image").getAttribute("src"),
      ).toContain("second.png"),
    );
  });

  it("frames a page rather than drawing it as an image", async () => {
    installFetchMock([{ slug: "run-1", utterance: "build a report" }], {
      "run-1": [file("report.html")],
    });

    renderView();

    const frame = await screen.findByTestId("visualization-frame");
    // Inert preview: an empty sandbox is an opaque origin with no scripts, on
    // top of the no-script CSP the backend already sends for inline HTML.
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(screen.queryByTestId("visualization-image")).toBeNull();
  });

  it("says so when the scanned runs produced nothing visual", async () => {
    installFetchMock([{ slug: "run-1", utterance: "tidy the notes" }], {
      "run-1": [file("notes.md", { is_text: true })],
    });

    renderView();

    await screen.findByTestId("visualization-empty");
    expect(screen.queryByTestId("visualization-stage")).toBeNull();
  });
});

describe("SectionStage", () => {
  it("drops the wallpaper readability halo for the visualization section", () => {
    const { container, rerender } = render(
      <SectionStage visualization={false}>
        <span>section</span>
      </SectionStage>,
    );
    expect(container.firstElementChild?.className).toContain("jarvis-section-stage");

    rerender(
      <SectionStage visualization>
        <span>section</span>
      </SectionStage>,
    );
    const stage = container.firstElementChild;
    expect(stage?.className).toContain("jarvis-visualization-stage");
    // Never both: the halo is inherited by every descendant, which is exactly
    // what must not happen over a rendered picture.
    expect(stage?.className).not.toContain("jarvis-section-stage");
  });
});
