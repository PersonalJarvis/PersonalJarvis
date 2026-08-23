/**
 * The run graph — a run drawn as a workflow, behind the Artifacts section's
 * "Run" tab since 2026-08-23 (it used to BE the section).
 *
 * Contracts worth pinning:
 * - a run renders as connected nodes (request → steps → result, deliverables
 *   on their own track), never as a flat file list,
 * - a pre-feature archive with no step records still gets a graph (request +
 *   deliverables) and SAYS that the steps are missing,
 * - clicking a node opens the inspector; an image deliverable previews via
 *   `<img>`, a page via an inert sandboxed frame,
 * - a failed step alarms from its card frame, not from a 6px dot alone,
 * - the inspector surfaces the archived timing (`duration_s`) of a step,
 * - zoom (ctrl+wheel, fit) and pan (drag on the background) behave.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RunGraphPanel } from "@/components/visualization/RunGraphPanel";
import type {
  ArtifactSummary,
  OutputSummary,
  PlanResponse,
} from "@/hooks/useOutputs";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function installFetchMock(
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
    return { ok: true, status: 200, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPanel(run: OutputSummary) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RunGraphPanel run={run} />
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

const RUN: OutputSummary = {
  slug: "run-new",
  utterance: "Draw the architecture",
  status: "success",
};

const CHART_PLAN: PlanResponse = {
  plan: { plan_id: "run-new", vision: "Draw the architecture", status: "complete" },
  steps: [
    {
      step_id: "t1:0",
      name: "python plot.py",
      tool_name: "Bash",
      status: "done",
      output: "wrote diagram.svg",
      duration_s: 3.2,
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

describe("RunGraphPanel", () => {
  it("draws the run as request → steps → result plus deliverables", async () => {
    installFetchMock(
      { "run-new": [file("tasks/t1/artifacts/files/out/diagram.svg")] },
      { "run-new": CHART_PLAN },
    );

    renderPanel(RUN);

    await screen.findByTestId("graph-node-start");
    await waitFor(() =>
      expect(screen.getAllByTestId("graph-node-step")).toHaveLength(2),
    );
    expect(screen.getByTestId("graph-node-result")).toBeTruthy();
    expect(screen.getAllByTestId("graph-node-artifact")).toHaveLength(1);
  });

  it("still graphs a pre-feature archive and admits the steps are gone", async () => {
    installFetchMock(
      { "run-old": [file("tasks/t1/artifacts/files/old-chart.png")] },
      // No plan entry -> the endpoint's stub contract {plan: null, steps: []}.
    );

    renderPanel({ slug: "run-old", utterance: "Summarise the logs", status: "unknown" });

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
      { "run-new": [file("tasks/t1/artifacts/files/out/diagram.svg")] },
      { "run-new": CHART_PLAN },
    );

    renderPanel(RUN);

    fireEvent.click(await screen.findByTestId("graph-node-artifact"));

    await screen.findByTestId("visualization-inspector");
    const image = await screen.findByTestId("visualization-image");
    expect(image.getAttribute("src")).toContain("diagram.svg");
  });

  it("frames a page deliverable inertly rather than drawing it as an image", async () => {
    installFetchMock({ "run-1": [file("tasks/t1/artifacts/files/report.html")] });

    renderPanel({ slug: "run-1", utterance: "Build a report", status: "success" });

    fireEvent.click(await screen.findByTestId("graph-node-artifact"));

    const frame = await screen.findByTestId("visualization-frame");
    // Inert preview: an empty sandbox is an opaque origin with no scripts, on
    // top of the no-script CSP the backend already sends for inline HTML.
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(screen.queryByTestId("visualization-image")).toBeNull();
  });

  it("shows a step's real call and output in the inspector", async () => {
    installFetchMock({ "run-new": [] }, { "run-new": CHART_PLAN });

    renderPanel(RUN);

    fireEvent.click((await screen.findAllByTestId("graph-node-step"))[0]);

    const inspector = await screen.findByTestId("visualization-inspector");
    expect(inspector.textContent).toContain("python plot.py");
    expect(inspector.textContent).toContain("wrote diagram.svg");
    // The archived timing is shown, not discarded.
    expect(inspector.textContent).toContain("3.2 s");
  });

  it("draws a reasoning step as a dashed thought card, text in the inspector", async () => {
    installFetchMock(
      { "run-think": [] },
      {
        "run-think": {
          plan: { plan_id: "run-think", vision: "Explain", status: "complete" },
          steps: [
            {
              step_id: "t1:0",
              name: "Check the logs first.",
              tool_name: null,
              kind: "reasoning",
              status: "done",
              output: "Check the logs first, then decide.",
            },
            { step_id: "t1:1", name: "tail app.log", tool_name: "Bash", status: "done" },
          ],
          final_answer: "Done.",
        },
      },
    );

    renderPanel({ slug: "run-think", utterance: "Explain the plan", status: "success" });

    const nodes = await screen.findAllByTestId("graph-node-step");
    const thought = nodes.find(
      (n) => n.getAttribute("data-category") === "reasoning",
    );
    expect(thought).toBeTruthy();
    // A thought is not an action — its card frame says so at a glance.
    expect(thought!.className).toContain("border-dashed");

    fireEvent.click(thought!);
    const inspector = await screen.findByTestId("visualization-inspector");
    // The full thought, not tool telemetry that never existed.
    expect(inspector.textContent).toContain("Check the logs first, then decide.");
    expect(inspector.textContent).not.toContain("Tool");
  });

  it("frames a failed step in the alarm colour, not just a dot", async () => {
    installFetchMock(
      { "run-bad": [] },
      {
        "run-bad": {
          plan: { plan_id: "run-bad", vision: "Deploy", status: "failed" },
          steps: [
            {
              step_id: "t1:0",
              name: "npm run deploy",
              tool_name: "Bash",
              status: "failed",
              error: "exit 1",
            },
          ],
        },
      },
    );

    renderPanel({ slug: "run-bad", utterance: "Deploy the site", status: "error" });

    const node = await screen.findByTestId("graph-node-step");
    expect(node.className).toContain("border-destructive");
  });

  it("offers a fit-to-view zoom next to the step buttons", async () => {
    installFetchMock({ "run-new": [] }, { "run-new": CHART_PLAN });

    renderPanel(RUN);

    // jsdom has no layout (clientWidth 0) — the fit guard must keep the
    // click a harmless no-op rather than zooming to nonsense.
    fireEvent.click(await screen.findByTestId("visualization-zoom-fit"));
    expect(screen.getByText("100%")).toBeTruthy();
  });

  it("zooms with ctrl+wheel and leaves plain scrolling alone", async () => {
    installFetchMock({ "run-new": [] }, { "run-new": CHART_PLAN });

    renderPanel(RUN);

    const canvas = await screen.findByTestId("visualization-canvas");
    // A bare wheel is scrolling, not zooming — it must stay untouched.
    fireEvent.wheel(canvas, { deltaY: -100 });
    expect(screen.getByText("100%")).toBeTruthy();
    // Ctrl+wheel (also how browsers deliver a trackpad pinch) zooms in.
    fireEvent.wheel(canvas, { deltaY: -100, ctrlKey: true });
    expect(screen.getByText("122%")).toBeTruthy();
  });

  it("pans the canvas with a pointer drag on the background", async () => {
    installFetchMock({ "run-new": [] }, { "run-new": CHART_PLAN });

    renderPanel(RUN);

    const canvas = await screen.findByTestId("visualization-canvas");
    // jsdom has no PointerEvent constructor — a MouseEvent under the pointer
    // event's name carries the coordinates the pan handlers read.
    fireEvent(
      canvas,
      new MouseEvent("pointerdown", { bubbles: true, clientX: 100, clientY: 90 }),
    );
    fireEvent(
      canvas,
      new MouseEvent("pointermove", { bubbles: true, clientX: 60, clientY: 70 }),
    );
    expect(canvas.scrollLeft).toBe(40);
    expect(canvas.scrollTop).toBe(20);

    // Releasing ends the pan: further movement no longer scrolls.
    fireEvent(canvas, new MouseEvent("pointerup", { bubbles: true }));
    fireEvent(
      canvas,
      new MouseEvent("pointermove", { bubbles: true, clientX: 0, clientY: 0 }),
    );
    expect(canvas.scrollLeft).toBe(40);
  });

  it("points every edge with an arrowhead, alarm-coloured after a failure", async () => {
    installFetchMock(
      { "run-new": [] },
      {
        "run-new": {
          plan: { plan_id: "run-new", vision: "Deploy it", status: "failed" },
          steps: [
            {
              step_id: "t1:0",
              name: "npm run deploy",
              tool_name: "Bash",
              status: "failed",
              error: "exit 1",
            },
          ],
        },
      },
    );

    renderPanel({ slug: "run-new", utterance: "Deploy it", status: "error" });

    await screen.findByTestId("graph-node-step");
    const edges = screen.getAllByTestId("graph-edge");
    expect(edges.length).toBeGreaterThan(0);
    // Since the track wraps, an edge can run back to the left — only a
    // pointed end keeps the flow readable, on every single edge.
    for (const edge of edges) {
      expect(edge.getAttribute("marker-end")).toMatch(/url\(#viz-arrow/);
    }
    // The edge into the failed step wears the alarm arrow, not the brand one.
    expect(
      edges.some(
        (e) => e.getAttribute("marker-end") === "url(#viz-arrow-failed)",
      ),
    ).toBe(true);
  });

  it("keeps a press on a node a click, never a pan", async () => {
    installFetchMock({ "run-new": [] }, { "run-new": CHART_PLAN });

    renderPanel(RUN);

    const canvas = await screen.findByTestId("visualization-canvas");
    const node = await screen.findByTestId("graph-node-start");
    fireEvent(
      node,
      new MouseEvent("pointerdown", { bubbles: true, clientX: 100, clientY: 90 }),
    );
    fireEvent(
      canvas,
      new MouseEvent("pointermove", { bubbles: true, clientX: 60, clientY: 70 }),
    );
    // The press began on a card, so the canvas never grabbed it…
    expect(canvas.scrollLeft).toBe(0);
    // …and the click still opens the inspector.
    fireEvent.click(node);
    await screen.findByTestId("visualization-inspector");
  });
});
