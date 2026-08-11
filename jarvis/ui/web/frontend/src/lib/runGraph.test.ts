/**
 * The run graph model — what becomes a node, what connects to what.
 *
 * Contracts worth pinning:
 * - the main track is start → steps in order → result, each linked to its
 *   predecessor, so the flow reads left to right like a workflow editor,
 * - an artifact hangs off the step that wrote it (matched by filename),
 *   and off the result/start node when no step claims it — an archived run
 *   with no surviving stream still gets a complete, honest graph,
 * - a live run's newest unconfirmed call renders as "running", while the
 *   same shape in a finished run stays "skipped" (anti-hearsay).
 */
import { describe, expect, it } from "vitest";

import type { ArtifactSummary, PlanStep } from "@/hooks/useOutputs";
import { buildRunGraph, edgePath, NODE_H, NODE_W } from "@/lib/runGraph";

function step(over: Partial<PlanStep> & { step_id: string }): PlanStep {
  return {
    name: "do something",
    status: "done",
    tool_name: "Bash",
    ...over,
  };
}

function file(path: string): ArtifactSummary {
  return { path, size: 10, mtime: 1, is_text: false, preview: null };
}

describe("buildRunGraph", () => {
  it("lays the main track start → steps → result, connected in order", () => {
    const graph = buildRunGraph({
      slug: "run-1",
      utterance: "draw a chart",
      runStatus: "success",
      plan: {
        plan: { plan_id: "run-1", vision: "draw a chart", status: "complete" },
        steps: [step({ step_id: "a" }), step({ step_id: "b" })],
        final_answer: "Chart drawn.",
      },
      files: [],
    });

    expect(graph.nodes.map((n) => n.id)).toEqual([
      "start",
      "step:a",
      "step:b",
      "result",
    ]);
    expect(graph.edges.map((e) => `${e.from}->${e.to}`)).toEqual([
      "start->step:a",
      "step:a->step:b",
      "step:b->result",
    ]);
    // One row: the main track shares a y, x strictly increases.
    const [s, a, b, r] = graph.nodes;
    expect(new Set([s.y, a.y, b.y, r.y]).size).toBe(1);
    expect(a.x).toBeGreaterThan(s.x);
    expect(r.x).toBeGreaterThan(b.x);
  });

  it("hangs an artifact off the step that wrote it, matched by filename", () => {
    const graph = buildRunGraph({
      slug: "run-1",
      runStatus: "success",
      plan: {
        plan: { plan_id: "run-1", vision: "", status: "complete" },
        steps: [
          step({ step_id: "a", tool_name: "Write", writes: ["out/chart.png"] }),
          step({ step_id: "b" }),
        ],
        final_answer: "done",
      },
      files: [file("tasks/t1/artifacts/files/out/chart.png")],
    });

    const edge = graph.edges.find((e) =>
      e.to.startsWith("artifact:"),
    );
    expect(edge?.from).toBe("step:a");
    // Second track: the artifact sits below the main track.
    const artifact = graph.nodes.find((n) => n.kind === "artifact");
    expect(artifact!.y).toBeGreaterThan(graph.nodes[0].y);
  });

  it("gives an unclaimed artifact to the result node, or start without one", () => {
    const withResult = buildRunGraph({
      slug: "run-1",
      runStatus: "success",
      plan: {
        plan: { plan_id: "run-1", vision: "", status: "complete" },
        steps: [step({ step_id: "a" })],
        final_answer: "done",
      },
      files: [file("tasks/t1/artifacts/files/report.md")],
    });
    expect(
      withResult.edges.find((e) => e.to.startsWith("artifact:"))?.from,
    ).toBe("result");

    // Pre-feature archive: no plan at all — the deliverables still show.
    const bare = buildRunGraph({
      slug: "run-old",
      runStatus: "unknown",
      plan: { plan: null, steps: [] },
      files: [file("tasks/t1/artifacts/files/report.md")],
    });
    expect(bare.nodes.map((n) => n.kind)).toEqual(["start", "artifact"]);
    expect(bare.edges[0]).toMatchObject({ from: "start" });
  });

  it("shows the newest unconfirmed call as running only while the run lives", () => {
    const plan = {
      plan: { plan_id: "r", vision: "", status: "complete" },
      steps: [
        step({ step_id: "a", status: "skipped" as const }),
        step({ step_id: "b", status: "skipped" as const }),
      ],
    };
    const live = buildRunGraph({ slug: "r", runStatus: "running", plan, files: [] });
    expect(live.nodes.find((n) => n.id === "step:b")?.status).toBe("running");
    expect(live.nodes.find((n) => n.id === "step:a")?.status).toBe("skipped");

    const done = buildRunGraph({ slug: "r", runStatus: "error", plan, files: [] });
    expect(done.nodes.find((n) => n.id === "step:b")?.status).toBe("skipped");
  });

  it("marks the edge into a failed step so the track can alarm", () => {
    const graph = buildRunGraph({
      slug: "r",
      runStatus: "error",
      plan: {
        plan: { plan_id: "r", vision: "", status: "failed" },
        steps: [step({ step_id: "a", status: "failed", error: "boom" })],
      },
      files: [],
    });
    expect(graph.edges[0].failed).toBe(true);
  });

  it("sizes the canvas to hold every node plus padding", () => {
    const graph = buildRunGraph({
      slug: "r",
      runStatus: "success",
      plan: {
        plan: { plan_id: "r", vision: "", status: "complete" },
        steps: [step({ step_id: "a" })],
        final_answer: "ok",
      },
      files: [file("tasks/t1/artifacts/files/a.md")],
    });
    for (const node of graph.nodes) {
      expect(node.x + NODE_W).toBeLessThanOrEqual(graph.width);
      expect(node.y + NODE_H).toBeLessThanOrEqual(graph.height);
    }
  });
});

describe("edgePath", () => {
  it("connects same-row nodes port to port, horizontally", () => {
    const graph = buildRunGraph({
      slug: "r",
      runStatus: "success",
      plan: {
        plan: { plan_id: "r", vision: "", status: "complete" },
        steps: [step({ step_id: "a" })],
        final_answer: "ok",
      },
      files: [],
    });
    const [from, to] = graph.nodes;
    const path = edgePath(from, to);
    expect(path).toContain(`M ${from.x + NODE_W} ${from.y + NODE_H / 2}`);
    expect(path.endsWith(`${to.x} ${to.y + NODE_H / 2}`)).toBe(true);
  });

  it("connects cross-row nodes bottom to top, vertically", () => {
    const graph = buildRunGraph({
      slug: "r",
      runStatus: "success",
      plan: { plan: null, steps: [] },
      files: [file("tasks/t1/artifacts/files/a.md")],
    });
    const [start, artifact] = graph.nodes;
    const path = edgePath(start, artifact);
    expect(path).toContain(`M ${start.x + NODE_W / 2} ${start.y + NODE_H}`);
    expect(path.endsWith(`${artifact.x + NODE_W / 2} ${artifact.y}`)).toBe(true);
  });
});
