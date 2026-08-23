/**
 * The Artifacts section — every page and picture a run produced, full-size.
 *
 * Contracts worth pinning:
 * - the newest artifact is on stage when the section opens; the rail lists
 *   every artifact newest-first and labels a page by its own <title>,
 * - a PAGE is framed from `/page` with `sandbox="allow-scripts"` and no
 *   same-origin (its scripts run, the app stays out of reach); an image goes
 *   through <img>; a PDF through an empty sandbox,
 * - "Code" shows the page's source, "Run" the node graph of the run that made
 *   it — the graph is one tab away, never in front of the page,
 * - a `create_artifact` mission still running shows as a "building…" row and
 *   stage, recognised by the brief's "Artifact:" lead line,
 * - `?run=<slug>` pre-selects that run's artifact; a click wins over newest,
 * - only files the WebView can draw are offered (`classifyVisual`).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SectionStage } from "@/App";
import { VisualizationView, parseArtifactUtterance } from "@/views/VisualizationView";
import {
  classifyVisual,
  pageTitleFromPreview,
  visualUrl,
} from "@/hooks/useVisualArtifacts";
import type {
  ArtifactSummary,
  OutputSummary,
  PlanResponse,
} from "@/hooks/useOutputs";

// ViewHeader lives in ChatsView, which subscribes to a WS client on mount —
// null keeps that a deterministic no-op in jsdom.
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
  plansBySlug: Record<string, PlanResponse> = {},
  rawBySlug: Record<string, string> = {},
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
    const plan = /\/api\/outputs\/([^/]+)\/plan/.exec(url);
    if (plan) {
      const slug = decodeURIComponent(plan[1]);
      return {
        ok: true,
        status: 200,
        json: async () => plansBySlug[slug] ?? { plan: null, steps: [] },
      };
    }
    const raw = /\/api\/outputs\/([^/]+)\/files\/.+\/raw/.exec(url);
    if (raw) {
      const slug = decodeURIComponent(raw[1]);
      return {
        ok: true,
        status: 200,
        json: async () => ({
          path: "x",
          size: 1,
          text: rawBySlug[slug] ?? "",
          truncated: false,
        }),
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

const DASH_HTML =
  "<!doctype html><html><head><title>Umsatz-Dashboard</title></head>" +
  "<body><script>document.title='x'</script></body></html>";

const ARTIFACT_UTTERANCE =
  "Artifact: Umsatz-Dashboard\nUmsatz pro Monat 2026 als Balken.\n\n## What to build\n…";

const DASH_RUN: OutputSummary = {
  slug: "mission_dash",
  utterance: ARTIFACT_UTTERANCE,
  status: "success",
};

const DASH_FILE = file("tasks/t1/artifacts/files/umsatz-dashboard.html", {
  is_text: true,
  preview: DASH_HTML,
  mtime: 1_700_000_900,
});

describe("classifyVisual", () => {
  it("accepts what the WebView can draw", () => {
    expect(classifyVisual("a/b/chart.PNG")).toBe("image");
    expect(classifyVisual("x.svg")).toBe("vector");
    expect(classifyVisual("report.html")).toBe("page");
    expect(classifyVisual("deck.pdf")).toBe("document");
  });

  it("rejects everything else, however data-shaped", () => {
    expect(classifyVisual("data.json")).toBeNull();
    expect(classifyVisual("notes.md")).toBeNull();
    expect(classifyVisual("archive.zip")).toBeNull();
    expect(classifyVisual("noext")).toBeNull();
  });

  it("encodes each path segment, so '#' in a filename survives", () => {
    expect(visualUrl("run-1", "tasks/t/artifacts/files/chart#2.png")).toContain(
      "chart%232.png",
    );
  });
});

describe("pageTitleFromPreview", () => {
  it("reads a page's own title and decodes the common entities", () => {
    expect(pageTitleFromPreview(DASH_HTML)).toBe("Umsatz-Dashboard");
    expect(pageTitleFromPreview("<title>  A &amp; B\n  </title>")).toBe("A & B");
    expect(pageTitleFromPreview("<html><body>no title</body></html>")).toBeNull();
    expect(pageTitleFromPreview(null)).toBeNull();
  });
});

describe("parseArtifactUtterance", () => {
  it("recognises the brief's lead line and returns title + request", () => {
    expect(parseArtifactUtterance(ARTIFACT_UTTERANCE)).toEqual({
      title: "Umsatz-Dashboard",
      request: "Umsatz pro Monat 2026 als Balken.",
    });
    expect(parseArtifactUtterance("Build a flask app")).toBeNull();
    expect(parseArtifactUtterance(undefined)).toBeNull();
  });
});

describe("VisualizationView", () => {
  it("stages the newest artifact full-size, labelled by its page title", async () => {
    installFetchMock([DASH_RUN], { mission_dash: [DASH_FILE] });

    renderView();

    const frame = await screen.findByTestId("visualization-frame");
    // The artifact-page route: scripts allowed server-side …
    expect(frame.getAttribute("src")).toContain("/files/tasks/t1/artifacts/files/umsatz-dashboard.html/page");
    // … and client-side the page runs in an opaque origin — scripts yes,
    // same-origin never.
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(screen.getByTestId("visualization-title").textContent).toBe("Umsatz-Dashboard");
    // The rail row carries the same title, not the filename.
    const rows = screen.getAllByTestId("visualization-artifact-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("Umsatz-Dashboard");
    expect(rows[0].textContent).toContain("Umsatz pro Monat 2026 als Balken.");
    // The run graph is a tab away, not on stage.
    expect(screen.queryByTestId("graph-node-start")).toBeNull();
  });

  it("lists every artifact newest-first and switches on click", async () => {
    installFetchMock(
      [
        { slug: "run-a", utterance: "Draw the architecture", status: "success" },
        { slug: "run-b", utterance: "Summarise", status: "success" },
      ],
      {
        "run-a": [file("tasks/t1/artifacts/files/diagram.svg", { mtime: 1_700_000_500 })],
        "run-b": [file("tasks/t1/artifacts/files/old-chart.png", { mtime: 1_700_000_100 })],
      },
    );

    renderView();

    const rows = await screen.findAllByTestId("visualization-artifact-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("diagram.svg"),
      expect.stringContaining("old-chart.png"),
    ]);
    // Newest on stage first …
    expect((await screen.findByTestId("visualization-image")).getAttribute("src")).toContain(
      "diagram.svg",
    );
    // … a click moves to the older one.
    fireEvent.click(rows[1]);
    await waitFor(() =>
      expect(screen.getByTestId("visualization-image").getAttribute("src")).toContain(
        "old-chart.png",
      ),
    );
  });

  it("shows the source under Code and the run graph under Run", async () => {
    installFetchMock(
      [DASH_RUN],
      { mission_dash: [DASH_FILE] },
      {
        mission_dash: {
          plan: { plan_id: "mission_dash", vision: "Build it", status: "complete" },
          steps: [
            { step_id: "t1:0", name: "umsatz-dashboard.html", tool_name: "Write", status: "done" },
          ],
          final_answer: "Done.",
        },
      },
      { mission_dash: DASH_HTML },
    );

    renderView();
    await screen.findByTestId("visualization-frame");

    fireEvent.click(screen.getByTestId("visualization-tab-code"));
    const source = await screen.findByTestId("visualization-source");
    expect(source.textContent).toContain("<title>Umsatz-Dashboard</title>");
    expect(screen.queryByTestId("visualization-frame")).toBeNull();

    fireEvent.click(screen.getByTestId("visualization-tab-run"));
    await screen.findByTestId("graph-node-start");
    await screen.findByTestId("graph-node-step");
    // The no-JS mission map stays one click away from the graph.
    expect(screen.getByTestId("visualization-open-map")).toBeTruthy();

    fireEvent.click(screen.getByTestId("visualization-tab-preview"));
    await screen.findByTestId("visualization-frame");
  });

  it("frames a PDF in an empty sandbox and draws an image through <img>", async () => {
    installFetchMock(
      [{ slug: "run-1", utterance: "Export", status: "success" }],
      {
        "run-1": [
          file("tasks/t1/artifacts/files/deck.pdf", { mtime: 1_700_000_200 }),
          file("tasks/t1/artifacts/files/cover.png", { mtime: 1_700_000_100 }),
        ],
      },
    );

    renderView();

    const frame = await screen.findByTestId("visualization-frame");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("src")).toContain("deck.pdf");
    // No Code tab for a PDF — there is no source to show.
    expect(screen.queryByTestId("visualization-tab-code")).toBeNull();

    fireEvent.click(screen.getAllByTestId("visualization-artifact-row")[1]);
    const image = await screen.findByTestId("visualization-image");
    expect(image.getAttribute("src")).toContain("cover.png");
  });

  it("shows a running artifact build as a row and a stage until the page lands", async () => {
    installFetchMock(
      [
        { slug: "mission_building", utterance: ARTIFACT_UTTERANCE, status: "running" },
        { slug: "mission_other", utterance: "Refactor the parser", status: "running" },
        DASH_RUN,
      ],
      { mission_building: [], mission_other: [], mission_dash: [DASH_FILE] },
    );

    renderView();

    // Only the artifact build shows as building — an unrelated running
    // mission is not posing as a page in the making.
    const building = await screen.findAllByTestId("visualization-building-row");
    expect(building).toHaveLength(1);
    expect(building[0].textContent).toContain("Umsatz-Dashboard");
    // With nothing picked, the newest build is what the stage shows.
    await screen.findByTestId("visualization-building");
    // The finished artifact is still in the rail, one click away.
    fireEvent.click(screen.getByTestId("visualization-artifact-row"));
    await screen.findByTestId("visualization-frame");
  });

  it("pre-selects the run named by ?run= in the URL", async () => {
    installFetchMock(
      [
        { slug: "run-new", utterance: "Draw the architecture", status: "success" },
        { slug: "run-old", utterance: "Summarise the logs", status: "success" },
      ],
      {
        "run-new": [file("tasks/t1/artifacts/files/new.svg", { mtime: 1_700_000_900 })],
        "run-old": [file("tasks/t1/artifacts/files/old.png", { mtime: 1_700_000_100 })],
      },
    );
    window.history.replaceState(null, "", "/?view=visualization&run=run-old");

    try {
      renderView();
      // The deep-linked run wins over the newest-first default.
      const image = await screen.findByTestId("visualization-image");
      expect(image.getAttribute("src")).toContain("old.png");
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });

  it("lists a run without a page as a run row, never as an artifact", async () => {
    // Since the Outputs section folded into Artifacts (2026-08-23) every run
    // is here: one that produced only a text file is a run row (its file is
    // behind the Files tab), not an artifact row and not an empty stage.
    installFetchMock(
      [{ slug: "run-text", utterance: "Write notes", status: "success" }],
      { "run-text": [file("tasks/t1/artifacts/files/notes.md", { is_text: true })] },
    );

    renderView();

    const rows = await screen.findAllByTestId("visualization-run-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("Write notes");
    expect(screen.queryByTestId("visualization-frame")).toBeNull();
    expect(screen.queryByTestId("visualization-empty")).toBeNull();
    expect(screen.queryAllByTestId("visualization-artifact-row")).toHaveLength(0);
  });

  it("says so when there are no runs at all", async () => {
    installFetchMock([], {});

    renderView();

    await screen.findByTestId("visualization-empty");
    expect(screen.queryByTestId("visualization-frame")).toBeNull();
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
    // what must not happen over a rendered page.
    expect(stage?.className).not.toContain("jarvis-section-stage");
  });
});
