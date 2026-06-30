/**
 * Vitest+RTL tests for JarvisAgentPanel (Phase 9 Wave 4 UI).
 *
 * Covers:
 *  - Empty-state when no mission is selected
 *  - Empty-state when the selected mission has no worker snapshots
 *  - Renders all columns (Model, Cost, State-Dir, Logfile, Reattach-Status)
 *  - Reattach-status badge shows correct data-attribute (live/killed/ended)
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { JarvisAgentPanel } from "@/components/missions/JarvisAgentPanel";
import { useMissionsStore } from "@/components/missions/store";
import type { OpenClawWorkerSnapshot } from "@/types/missions";

afterEach(() => {
  cleanup();
  useMissionsStore.getState().reset();
});

function makeWorker(overrides: Partial<OpenClawWorkerSnapshot> = {}): OpenClawWorkerSnapshot {
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
  it("zeigt Empty-State wenn keine Mission ausgewaehlt ist", () => {
    render(<JarvisAgentPanel />);
    expect(
      screen.getByText("Mission auswaehlen, um OpenClaw-Worker zu sehen."),
    ).toBeDefined();
  });

  it("zeigt Empty-State wenn Mission keine OpenClaw-Worker hat", () => {
    useMissionsStore.setState({
      selectedMissionId: "mid-1",
      workerSnapshotsByMission: { "mid-1": [] },
    });
    render(<JarvisAgentPanel />);
    expect(
      screen.getByText("Keine OpenClaw-Worker in dieser Mission."),
    ).toBeDefined();
  });

  it("rendert alle Spalten fuer einen live OpenClaw-Worker", () => {
    useMissionsStore.setState({
      selectedMissionId: "mid-1",
      workerSnapshotsByMission: { "mid-1": [makeWorker()] },
    });
    render(<JarvisAgentPanel />);

    // Modell
    const model = screen.getByTestId("openclaw-model");
    expect(model.textContent).toBe("gemini/gemini-3.1-pro-preview");

    // Cost
    const cost = screen.getByTestId("openclaw-cost");
    expect(cost.textContent).toContain("$0.0234");
    expect(cost.textContent).toContain("12.5k tok");

    // State-Dir
    const stateDir = screen.getByTestId("openclaw-state-dir");
    expect(stateDir.textContent).toBe(
      "C:/wt/oc-1/.openclaw_state/sess-cafebabe/openclaw_state",
    );

    // Logfile
    const logPath = screen.getByTestId("openclaw-log-path");
    expect(logPath.textContent).toContain("run.log");

    // Reattach-Status: live
    const badge = screen.getByTestId("openclaw-reattach-badge");
    expect(badge.getAttribute("data-reattach-status")).toBe("live");
    expect(badge.textContent).toBe("live");
  });

  it("zeigt killed-Badge mit ended-reason wenn Worker explicit gekillt wurde", () => {
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

    const badge = screen.getByTestId("openclaw-reattach-badge");
    expect(badge.getAttribute("data-reattach-status")).toBe("killed");
    expect(badge.textContent).toBe("killed");

    const reason = screen.getByTestId("openclaw-ended-reason");
    expect(reason.textContent).toBe("user");
  });

  it("zeigt mehrere Worker in der Reihenfolge der Aggregator-Liste", () => {
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

    const rows = screen.getAllByTestId("openclaw-worker-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute("data-worker-id")).toBe("w-aaa");
    expect(rows[1].getAttribute("data-worker-id")).toBe("w-bbb");

    // Cost == 0 wird als "—" gerendert
    const costs = screen.getAllByTestId("openclaw-cost");
    expect(costs[1].textContent).toContain("—");
  });
});
