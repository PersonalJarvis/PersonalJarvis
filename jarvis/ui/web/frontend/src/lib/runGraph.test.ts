/**
 * The run graph model — what becomes a node, what connects to what.
 *
 * Contracts worth pinning:
 * - the main track is start → steps in order → result, each linked to its
 *   predecessor, so the flow reads left to right like a workflow editor,
 * - a long main track wraps after MAIN_TRACK_COLUMNS and reads like text —
 *   left to right, then down — instead of stretching into one endless row,
 * - an artifact hangs off the step that wrote it (matched by filename) and
 *   sits UNDER that step where the row allows; it falls to the result/start
 *   node when no step claims it — an archived run with no surviving stream
 *   still gets a complete, honest graph,
 * - a live run's newest unconfirmed call renders as "running", while the
 *   same shape in a finished run stays "skipped" (anti-hearsay).
 */
import { describe, expect, it } from "vitest";

import type { ArtifactSummary, PlanStep } from "@/hooks/useOutputs";
import {
  buildRunGraph,
  edgePath,
  MAIN_TRACK_COLUMNS,
  NODE_H,
  NODE_W,
} from "@/lib/runGraph";

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

  it("wraps a long main track after MAIN_TRACK_COLUMNS, reading like text", () => {
    const steps = Array.from({ length: 12 }, (_, i) =>
      step({ step_id: `s${i}` }),
    );
    const graph = buildRunGraph({
      slug: "long",
      runStatus: "success",
      plan: {
        plan: { plan_id: "long", vision: "", status: "complete" },
        steps,
        final_answer: "done",
      },
      files: [],
    });

    // 14 main nodes in columns of MAIN_TRACK_COLUMNS → three rows.
    const rows = new Set(graph.nodes.map((n) => n.y));
    expect(rows.size).toBe(Math.ceil(14 / MAIN_TRACK_COLUMNS));
    // The first node after a wrap returns to the left edge, one row down.
    const first = graph.nodes[0];
    const lastOfRow = graph.nodes[MAIN_TRACK_COLUMNS - 1];
    const firstOfNextRow = graph.nodes[MAIN_TRACK_COLUMNS];
    expect(firstOfNextRow.x).toBe(first.x);
    expect(firstOfNextRow.y).toBeGreaterThan(lastOfRow.y);
    // The canvas stays bounded to the column count, not the step count.
    expect(graph.width).toBeLessThan(MAIN_TRACK_COLUMNS * (NODE_W + 100) + 100);
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
    // Second track: the artifact sits below the main track, and directly
    // UNDER its source step — provenance readable from position alone.
    const artifact = graph.nodes.find((n) => n.kind === "artifact");
    const source = graph.nodes.find((n) => n.id === "step:a");
    expect(artifact!.y).toBeGreaterThan(graph.nodes[0].y);
    expect(artifact!.x).toBe(source!.x);
  });

  it("shifts colliding artifacts apart instead of stacking them", () => {
    const graph = buildRunGraph({
      slug: "run-1",
      runStatus: "success",
      plan: {
        plan: { plan_id: "run-1", vision: "", status: "complete" },
        steps: [
          step({
            step_id: "a",
            tool_name: "Write",
            writes: ["out/a.png", "out/b.png"],
          }),
        ],
        final_answer: "done",
      },
      files: [
        file("tasks/t1/artifacts/files/out/a.png"),
        file("tasks/t1/artifacts/files/out/b.png"),
      ],
    });

    const artifacts = graph.nodes.filter((n) => n.kind === "artifact");
    expect(artifacts).toHaveLength(2);
    const [left, right] = [...artifacts].sort((a, b) => a.x - b.x);
    expect(right.x - left.x).toBeGreaterThanOrEqual(NODE_W);
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

  it("denies a cancelled run without an answer the success dot", () => {
    const plan = {
      plan: { plan_id: "r", vision: "", status: "cancelled" },
      steps: [step({ step_id: "a" })],
    };
    const cancelled = buildRunGraph({
      slug: "r",
      runStatus: "cancelled",
      plan,
      files: [],
    });
    expect(cancelled.nodes.find((n) => n.id === "result")?.status).toBe(
      "skipped",
    );

    // With a recorded final answer the worker DID finish its say — that
    // stays "done" even though the mission ended as cancelled.
    const answered = buildRunGraph({
      slug: "r",
      runStatus: "cancelled",
      plan: { ...plan, final_answer: "partial result" },
      files: [],
    });
    expect(answered.nodes.find((n) => n.id === "result")?.status).toBe("done");
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

  it("branches a multi-worker mission into parallel lanes that merge at the result", () => {
    const graph = buildRunGraph({
      slug: "r",
      runStatus: "success",
      plan: {
        plan: { plan_id: "r", vision: "", status: "complete" },
        steps: [
          step({ step_id: "w1:0", task_key: "w1" }),
          step({ step_id: "w1:1", task_key: "w1" }),
          step({ step_id: "w2:0", task_key: "w2" }),
          step({ step_id: "w2:1", task_key: "w2" }),
        ],
        final_answer: "done",
      },
      files: [],
    });

    // Both workers branch out of the start node…
    const fromStart = graph.edges
      .filter((e) => e.from === "start")
      .map((e) => e.to);
    expect(fromStart).toEqual(["step:w1:0", "step:w2:0"]);
    // …into their own row bands, column-aligned…
    const w1 = graph.nodes.find((n) => n.id === "step:w1:0")!;
    const w2 = graph.nodes.find((n) => n.id === "step:w2:0")!;
    expect(w2.y).toBeGreaterThan(w1.y);
    expect(w2.x).toBe(w1.x);
    // …and every lane's last step merges into ONE result.
    const intoResult = graph.edges
      .filter((e) => e.to === "result")
      .map((e) => e.from);
    expect(intoResult).toEqual(["step:w1:1", "step:w2:1"]);
  });

  it("keeps legacy steps without task keys in one shared lane", () => {
    const graph = buildRunGraph({
      slug: "r",
      runStatus: "success",
      plan: {
        plan: { plan_id: "r", vision: "", status: "complete" },
        steps: [step({ step_id: "a" }), step({ step_id: "b" })],
        final_answer: "ok",
      },
      files: [],
    });
    // One chain on one row — never one lane per step.
    const ys = new Set(graph.nodes.map((n) => n.y));
    expect(ys.size).toBe(1);
  });

  it("draws ports only where an edge actually attaches", () => {
    const graph = buildRunGraph({
      slug: "r",
      runStatus: "success",
      plan: {
        plan: { plan_id: "r", vision: "", status: "complete" },
        steps: [step({ step_id: "t:0" })],
        final_answer: "ok",
      },
      files: [file("tasks/t1/artifacts/files/report.md")],
    });

    // The request only ever feeds forward — no input connector.
    const start = graph.nodes.find((n) => n.id === "start")!;
    expect(start.ports.left).toBe(false);
    expect(start.ports.right).toBe(true);
    // The result receives the track and hands the unclaimed file down.
    const result = graph.nodes.find((n) => n.id === "result")!;
    expect(result.ports.left).toBe(true);
    expect(result.ports.right).toBe(false);
    expect(result.ports.bottom).toBe(true);
    // A deliverable is an endpoint: one input from above, nothing else.
    const artifact = graph.nodes.find((n) => n.kind === "artifact")!;
    expect(artifact.ports).toEqual({
      left: false,
      right: false,
      top: true,
      bottom: false,
    });
  });

  it("threads reasoning steps through the flow as ordinary nodes", () => {
    const graph = buildRunGraph({
      slug: "r",
      runStatus: "success",
      plan: {
        plan: { plan_id: "r", vision: "", status: "complete" },
        steps: [
          step({
            step_id: "t:0",
            kind: "reasoning",
            tool_name: null,
            name: "Plan the deploy first.",
          }),
          step({ step_id: "t:1" }),
        ],
        final_answer: "ok",
      },
      files: [],
    });

    expect(graph.nodes.map((n) => n.id)).toEqual([
      "start",
      "step:t:0",
      "step:t:1",
      "result",
    ]);
    // Without a tool name the thought itself is the card headline.
    expect(graph.nodes[1].title).toBe("Plan the deploy first.");
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
