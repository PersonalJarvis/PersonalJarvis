/**
 * The node-graph model behind the Visualization section.
 *
 * A run is drawn the way a workflow editor would draw it: what was asked
 * (start node), what the worker actually did (one node per tool call, in
 * order, on the main track, wrapping like text after MAIN_TRACK_COLUMNS),
 * what it said at the end (result node), and what it left behind (one node
 * per deliverable, on a second track below, placed under the step that wrote
 * it and connected to it). The data comes from two reads the app already
 * makes — `/api/outputs/{slug}/plan` and `/api/outputs/{slug}/artifacts` —
 * so a run archived long before this feature existed still gets its graph.
 *
 * Everything here is pure: build the nodes, lay them out, hand back
 * positions. The view only renders. That split is what keeps the layout
 * testable without a DOM and lets the numbers below be tuned in one place.
 */

import type {
  ArtifactSummary,
  OutputStatus,
  PlanResponse,
  PlanStep,
} from "@/hooks/useOutputs";

export type RunNodeKind = "start" | "step" | "result" | "artifact";

/** Node state, unified across kinds so one dot component can render them. */
export type RunNodeStatus = "done" | "failed" | "skipped" | "running" | "none";

export interface RunGraphNode {
  /** Stable id — selection key, React key and edge endpoint. */
  id: string;
  kind: RunNodeKind;
  /** Card headline (tool name, "Request", "Result", filename). */
  title: string;
  /** Card detail line (command, path, answer preview) — may be empty. */
  subtitle: string;
  status: RunNodeStatus;
  /** For `step` nodes: the full backend step, for the inspector. */
  step?: PlanStep;
  /** For `artifact` nodes: the listed file, for preview + actions. */
  artifact?: ArtifactSummary;
  /** Layout position (top-left corner) in unscaled canvas pixels. */
  x: number;
  y: number;
}

export interface RunGraphEdge {
  id: string;
  from: string;
  to: string;
  /** Whether the edge leads to a failed node — drawn in the alarm colour. */
  failed: boolean;
}

export interface RunGraph {
  nodes: RunGraphNode[];
  edges: RunGraphEdge[];
  /** Unscaled canvas extents, padding included. */
  width: number;
  height: number;
  /** Steps the backend dropped beyond its cap — stated, never hidden. */
  droppedSteps: number;
}

/* Layout constants — one place to tune the whole canvas. */
export const NODE_W = 216;
export const NODE_H = 76;
const GAP_X = 56;
const ROW_GAP = 72;
/* The deliverables track sits a little further off than wrapped main rows,
 * so "second track" stays visually distinct from "next line of the story". */
const TRACK_GAP = 104;
const PADDING = 24;

/**
 * Main-track nodes per row before the track wraps to the next line.
 *
 * A run is read like text: left to right, then down. Without the wrap a
 * thirty-step run is an eight-thousand-pixel horizontal strip that no zoom
 * level makes readable; with it, the same run is a handful of rows that fit
 * one screen. Five columns keeps a row inside a typical canvas at 100%.
 */
export const MAIN_TRACK_COLUMNS = 5;

/** Top-left corner of the main-track slot `index` (start = slot 0). */
function slotPosition(index: number): { x: number; y: number } {
  const column = index % MAIN_TRACK_COLUMNS;
  const row = Math.floor(index / MAIN_TRACK_COLUMNS);
  return {
    x: PADDING + column * (NODE_W + GAP_X),
    y: PADDING + row * (NODE_H + ROW_GAP),
  };
}

/** Last path segment, tolerant of both separators. */
function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/**
 * The step that produced this artifact, by write-target basename.
 *
 * The worker wrote to a worktree path ("report/chart.png", often absolute)
 * while the archive lists "tasks/<id>/artifacts/files/report/chart.png" —
 * the two only reliably share their filename. When several steps wrote the
 * same name, the LAST one wins (it is the surviving content).
 */
function findSourceStep(steps: PlanStep[], artifactPath: string): PlanStep | null {
  const name = basename(artifactPath);
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const writes = steps[i].writes ?? [];
    if (writes.some((target) => basename(target) === name)) return steps[i];
  }
  return null;
}

function stepStatus(
  step: PlanStep,
  isLastUnconfirmed: boolean,
  runStatus: OutputStatus | undefined,
): RunNodeStatus {
  if (step.status === "failed") return "failed";
  if (step.status === "done") return "done";
  // The backend reports an uncorrelated tool call as "skipped" (its result
  // frame never arrived). While the mission is still live, the newest such
  // call is not lost — it is the one in flight right now.
  if (isLastUnconfirmed && runStatus === "running") return "running";
  return "skipped";
}

export interface RunGraphInput {
  slug: string;
  utterance?: string;
  runStatus?: OutputStatus;
  plan: PlanResponse | undefined;
  files: ArtifactSummary[];
}

/** Build the fully laid-out graph for one run. Pure. */
export function buildRunGraph(input: RunGraphInput): RunGraph {
  const steps = input.plan?.steps ?? [];
  const finalAnswer = input.plan?.final_answer ?? null;
  const nodes: RunGraphNode[] = [];
  const edges: RunGraphEdge[] = [];

  const lastUnconfirmed = [...steps].reverse().find((s) => s.status === "skipped");

  /* Main track: start → steps → result, wrapping after MAIN_TRACK_COLUMNS
   * so a long run reads like text — left to right, then down — instead of
   * becoming a horizontal strip no screen can hold. */
  let slot = 0;

  nodes.push({
    id: "start",
    kind: "start",
    title: input.utterance?.trim() || input.slug,
    subtitle: input.slug,
    status:
      input.runStatus === "running"
        ? "running"
        : input.runStatus === "error"
          ? "failed"
          : "none",
    ...slotPosition(slot),
  });
  let previousId = "start";
  slot += 1;

  for (const step of steps) {
    const id = `step:${step.step_id}`;
    const status = stepStatus(step, step === lastUnconfirmed, input.runStatus);
    nodes.push({
      id,
      kind: "step",
      title: step.tool_name || step.name,
      subtitle: step.tool_name && step.name !== step.tool_name ? step.name : "",
      status,
      step,
      ...slotPosition(slot),
    });
    edges.push({
      id: `${previousId}->${id}`,
      from: previousId,
      to: id,
      failed: status === "failed",
    });
    previousId = id;
    slot += 1;
  }

  /* The result node closes the track whenever there is anything to close it
   * with — an answer, or a terminal run outcome worth naming. */
  const terminal =
    input.runStatus === "success" ||
    input.runStatus === "error" ||
    input.runStatus === "cancelled";
  if (finalAnswer || (steps.length > 0 && terminal)) {
    nodes.push({
      id: "result",
      kind: "result",
      title: "result",
      subtitle: finalAnswer ?? "",
      // A cancelled run that never answered must not wear the success dot —
      // "no confirmed result" is the honest reading of that ending.
      status:
        input.runStatus === "error"
          ? "failed"
          : input.runStatus === "cancelled" && !finalAnswer
            ? "skipped"
            : "done",
      ...slotPosition(slot),
    });
    edges.push({
      id: `${previousId}->result`,
      from: previousId,
      to: "result",
      failed: input.runStatus === "error",
    });
    slot += 1;
  }
  const hasResult = nodes[nodes.length - 1].id === "result";

  /* Deliverables track: one node per archived file, connected to the step
   * that wrote it (result/start node when no step claims it). Each artifact
   * sits UNDER its source node where the row allows — provenance readable
   * from position alone, not only by tracing an edge across the canvas. */
  const mainRows = Math.max(1, Math.ceil(slot / MAIN_TRACK_COLUMNS));
  const artifactY =
    PADDING + (mainRows - 1) * (NODE_H + ROW_GAP) + NODE_H + TRACK_GAP;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const placed = input.files.map((file) => {
    const source = findSourceStep(steps, file.path);
    const from = source ? `step:${source.step_id}` : hasResult ? "result" : "start";
    return { file, from, desiredX: byId.get(from)?.x ?? PADDING };
  });
  placed.sort((a, b) => a.desiredX - b.desiredX);
  let artifactX = PADDING;
  for (const { file, from, desiredX } of placed) {
    const id = `artifact:${file.path}`;
    const x = Math.max(desiredX, artifactX);
    nodes.push({
      id,
      kind: "artifact",
      title: basename(file.path),
      subtitle: file.path,
      status: "none",
      artifact: file,
      x,
      y: artifactY,
    });
    edges.push({ id: `${from}->${id}`, from, to: id, failed: false });
    artifactX = x + NODE_W + GAP_X;
  }

  const width =
    Math.max(...nodes.map((n) => n.x + NODE_W), PADDING + NODE_W) + PADDING;
  const height =
    Math.max(...nodes.map((n) => n.y + NODE_H), PADDING + NODE_H) + PADDING;

  return {
    nodes,
    edges,
    width,
    height,
    droppedSteps: input.plan?.dropped_steps ?? 0,
  };
}

/**
 * The SVG path of one edge, n8n-style.
 *
 * Same-row edges leave the source's right port and enter the target's left
 * port with a horizontal S-curve; cross-row edges leave the source's bottom
 * and enter the target's top with a vertical one. Ports, not centers: an edge
 * that visibly leaves a card edge is what makes the cards read as connected
 * nodes rather than decorated boxes.
 */
export function edgePath(from: RunGraphNode, to: RunGraphNode): string {
  if (from.y === to.y) {
    const x1 = from.x + NODE_W;
    const y1 = from.y + NODE_H / 2;
    const x2 = to.x;
    const y2 = to.y + NODE_H / 2;
    const bend = Math.max(24, (x2 - x1) / 2);
    return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
  }
  const downward = to.y > from.y;
  const x1 = from.x + NODE_W / 2;
  const y1 = downward ? from.y + NODE_H : from.y;
  const x2 = to.x + NODE_W / 2;
  const y2 = downward ? to.y : to.y + NODE_H;
  const bend = Math.max(28, Math.abs(y2 - y1) / 2) * (downward ? 1 : -1);
  return `M ${x1} ${y1} C ${x1} ${y1 + bend}, ${x2} ${y2 - bend}, ${x2} ${y2}`;
}
