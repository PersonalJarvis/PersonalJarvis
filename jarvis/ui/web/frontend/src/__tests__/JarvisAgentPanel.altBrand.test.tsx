/**
 * Vitest+RTL tests for JarvisAgentPanel (Phase 9 Wave 4 UI).
 *
 * Covers:
 *  - Empty-state when no mission is selected
 *  - Empty-state when the selected mission has no worker snapshots
 *  - Renders all columns (Model, Cost, State-Dir, Logfile, Reattach-Status)
 *  - Reattach-status badge shows correct data-attribute (live/killed/ended)
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { JarvisAgentPanel } from "@/components/missions/JarvisAgentPanel";
import { useMissionsStore } from "@/components/missions/store";
import { useEventStore } from "@/store/events";
import type { JarvisAgentWorkerSnapshot } from "@/types/missions";

// A different arbitrary name than the sibling test file on purpose: the brand
// must follow ANY wake-word-derived assistant name, not one blessed value.
beforeEach(() => {
  useEventStore.setState({ assistantName: "Harald" });
});

afterEach(() => {
  cleanup();
  useMissionsStore.getState().reset();
  useEventStore.setState({ assistantName: "Assistant" });
});

function makeWorker(overrides: Partial<JarvisAgentWorkerSnapshot> = {}): JarvisAgentWorkerSnapshot {
  return {
    worker_id: "oc-worker-1",
    model: "gemini/gemini-3.1-pro-preview",
    session_id: "sess-cafebabe",
    state_dir: "C:/wt/oc-1/.openclaw_state/sess-cafebabe/openclaw_state",
    log_path: "C:/wt/oc-1/.openclaw_state/sess-cafebabe/openclaw_state/run.log",
    cost_usd: 0.0234,
    tokens_used: 12500,
    reattach_status: "live",
    spawned_ms: 1000,
    ended_ms: null,
    ended_reason: null,
    pid: 4242,
    worktree: "C:/wt/oc-1",
    ...overrides,
  };
}

describe("JarvisAgentPanel", () => {
  it("shows the empty state when no mission is selected", () => {
    render(<JarvisAgentPanel />);
    expect(
      screen.getByText("Select a mission to see Harald-Agent workers."),
    ).toBeDefined();
  });

  it("shows the empty state when the mission has no Jarvis-Agent workers", () => {
    useMissionsStore.setState({
      selectedMissionId: "mid-1",
      workerSnapshotsByMission: { "mid-1": [] },
    });
    render(<JarvisAgentPanel />);
    expect(
      screen.getByText("No Harald-Agent workers in this mission."),
    ).toBeDefined();
  });

  it("renders all columns for a live Jarvis-Agent worker", () => {
    useMissionsStore.setState({
      selectedMissionId: "mid-1",
      workerSnapshotsByMission: { "mid-1": [makeWorker()] },
    });
    render(<JarvisAgentPanel />);

    // Model
    const model = screen.getByTestId("jarvis-agent-model");
    expect(model.textContent).toBe("gemini/gemini-3.1-pro-preview");

    // Cost
    const cost = screen.getByTestId("jarvis-agent-cost");
    expect(cost.textContent).toContain("$0.0234");
    expect(cost.textContent).toContain("12.5k tok");

    // State-Dir
    const stateDir = screen.getByTestId("jarvis-agent-state-dir");
    expect(stateDir.textContent).toBe(
      "C:/wt/oc-1/.openclaw_state/sess-cafebabe/openclaw_state",
    );

    // Logfile
    const logPath = screen.getByTestId("jarvis-agent-log-path");
    expect(logPath.textContent).toContain("run.log");

    // Reattach-Status: live
    const badge = screen.getByTestId("jarvis-agent-reattach-badge");
    expect(badge.getAttribute("data-reattach-status")).toBe("live");
    expect(badge.textContent).toBe("live");
  });

  it("shows the killed badge with ended-reason when the worker was explicitly killed", () => {
    useMissionsStore.setState({
      selectedMissionId: "mid-1",
      workerSnapshotsByMission: {
        "mid-1": [
          makeWorker({
            reattach_status: "killed",
            ended_ms: 2000,
            ended_reason: "user",
          }),
        ],
      },
    });
    render(<JarvisAgentPanel />);

    const badge = screen.getByTestId("jarvis-agent-reattach-badge");
    expect(badge.getAttribute("data-reattach-status")).toBe("killed");
    expect(badge.textContent).toBe("killed");

    const reason = screen.getByTestId("jarvis-agent-ended-reason");
    expect(reason.textContent).toBe("user");
  });

  it("shows multiple workers in the order of the aggregator list", () => {
    useMissionsStore.setState({
      selectedMissionId: "mid-1",
      workerSnapshotsByMission: {
        "mid-1": [
          makeWorker({ worker_id: "w-aaa", model: "gemini/g1" }),
          makeWorker({
            worker_id: "w-bbb",
            model: "claude-api/sonnet-4-6",
            cost_usd: 0,
            tokens_used: 0,
          }),
        ],
      },
    });
    render(<JarvisAgentPanel />);

    const rows = screen.getAllByTestId("jarvis-agent-worker-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute("data-worker-id")).toBe("w-aaa");
    expect(rows[1].getAttribute("data-worker-id")).toBe("w-bbb");

    // Cost == 0 is rendered as "—"
    const costs = screen.getAllByTestId("jarvis-agent-cost");
    expect(costs[1].textContent).toContain("—");
  });
});
