/**
 * The Visualization section — the run graph it draws, what it admits, and
 * what a node click reveals.
 *
 * Contracts worth pinning:
 * - a run renders as connected nodes (request → steps → result, deliverables
 *   on their own track), never as a flat file list,
 * - a pre-feature archive with no step records still gets a graph (request +
 *   deliverables) and SAYS that the steps are missing,
 * - clicking a node opens the inspector; an image deliverable previews via
 *   `<img>`, a page via an inert sandboxed frame,
 * - the server-rendered mission map stays one click away ("open as page"),
 * - only files the WebView can draw are offered a preview (`classifyVisual`).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SectionStage } from "@/App";
import { VisualizationView } from "@/views/VisualizationView";
import { classifyVisual, visualUrl } from "@/hooks/useVisualArtifacts";
import type {
  ArtifactSummary,
  OutputSummary,
  PlanResponse,
} from "@/hooks/useOutputs";

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
  plansBySlug: Record<string, PlanResponse> = {},
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

const CHART_PLAN: PlanResponse = {
  plan: { plan_id: "run-new", vision: "Draw the architecture", status: "complete" },
  steps: [
    {
      step_id: "t1:0",
      name: "python plot.py",
      tool_name: "Bash",
      status: "done",
      output: "wrote diagram.svg",
    },
    {
      step_id: "t1:1",
      name: "out/diagram.svg",
      tool_name: "Write",
      status: "done",
      writes: ["out/diagram.svg"],
    },
  ],
  final_answer: "Architecture drawn.",
};

describe("classifyVisual", () => {
  it("accepts what the WebView can draw", () => {
    expect(classifyVisual("chart.png")).toBe("image");
    expect(classifyVisual("out/PHOTO.JPEG")).toBe("image");
    expect(classifyVisual("diagram.svg")).toBe("vector");
    expect(classifyVisual("report.html")).toBe("page");
    expect(classifyVisual("paper.pdf")).toBe("document");
  });

  it("rejects everything else, however data-shaped", () => {
    // These are real deliverables — they get a node and file actions, just no
    // rendered preview the WebView could only coin-flip on.
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
  it("draws the newest run as request → steps → result plus deliverables", async () => {
    installFetchMock(
      [{ slug: "run-new", utterance: "Draw the architecture", status: "success" }],
      { "run-new": [file("tasks/t1/artifacts/files/out/diagram.svg")] },
      { "run-new": CHART_PLAN },
    );

    renderView();

    await screen.findByTestId("graph-node-start");
    await waitFor(() =>
      expect(screen.getAllByTestId("graph-node-step")).toHaveLength(2),
    );
    expect(screen.getByTestId("graph-node-result")).toBeTruthy();
    expect(screen.getAllByTestId("graph-node-artifact")).toHaveLength(1);
    // The graph replaced the gallery: no flat file list anywhere.
    expect(screen.queryByTestId("visualization-gallery")).toBeNull();
  });

  it("still graphs a pre-feature archive and admits the steps are gone", async () => {
    installFetchMock(
      [{ slug: "run-old", utterance: "Summarise the logs", status: "unknown" }],
      { "run-old": [file("tasks/t1/artifacts/files/old-chart.png")] },
      // No plan entry -> the endpoint's stub contract {plan: null, steps: []}.
    );

    renderView();

    await screen.findByTestId("graph-node-start");
    await waitFor(() =>
      expect(screen.getAllByTestId("graph-node-artifact")).toHaveLength(1),
    );
    expect(screen.queryAllByTestId("graph-node-step")).toHaveLength(0);
    // The honesty line: missing step records are stated, not glossed over.
    await screen.findByText(/No step records survived/);
  });

  it("opens the inspector with an image preview when a deliverable is clicked", async () => {
    installFetchMock(
      [{ slug: "run-new", utterance: "Draw the architecture", status: "success" }],
      { "run-new": [file("tasks/t1/artifacts/files/out/diagram.svg")] },
      { "run-new": CHART_PLAN },
    );

    renderView();

    fireEvent.click(await screen.findByTestId("graph-node-artifact"));

    await screen.findByTestId("visualization-inspector");
    const image = await screen.findByTestId("visualization-image");
    expect(image.getAttribute("src")).toContain("diagram.svg");
  });

  it("frames a page deliverable inertly rather than drawing it as an image", async () => {
    installFetchMock(
      [{ slug: "run-1", utterance: "Build a report", status: "success" }],
      { "run-1": [file("tasks/t1/artifacts/files/report.html")] },
    );

    renderView();

    fireEvent.click(await screen.findByTestId("graph-node-artifact"));

    const frame = await screen.findByTestId("visualization-frame");
    // Inert preview: an empty sandbox is an opaque origin with no scripts, on
    // top of the no-script CSP the backend already sends for inline HTML.
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(screen.queryByTestId("visualization-image")).toBeNull();
  });

  it("shows a step's real call and output in the inspector", async () => {
    installFetchMock(
      [{ slug: "run-new", utterance: "Draw the architecture", status: "success" }],
      { "run-new": [] },
      { "run-new": CHART_PLAN },
    );

    renderView();

    fireEvent.click((await screen.findAllByTestId("graph-node-step"))[0]);

    const inspector = await screen.findByTestId("visualization-inspector");
    expect(inspector.textContent).toContain("python plot.py");
    expect(inspector.textContent).toContain("wrote diagram.svg");
  });

  it("keeps the server-rendered mission map one click away", async () => {
    installFetchMock(
      [{ slug: "run-new", utterance: "Draw the architecture", status: "success" }],
      { "run-new": [] },
      { "run-new": CHART_PLAN },
    );

    renderView();

    // The no-JS sibling of this canvas — shareable, printable, CSP-inert.
    await screen.findByTestId("visualization-open-map");
  });

  it("says so when there are no runs at all", async () => {
    installFetchMock([], {});

    renderView();

    await screen.findByTestId("visualization-empty");
    expect(screen.queryByTestId("visualization-canvas")).toBeNull();
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
