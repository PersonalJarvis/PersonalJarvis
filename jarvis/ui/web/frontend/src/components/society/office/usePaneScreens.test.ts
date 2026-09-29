import { describe, expect, it } from "vitest";
import type { PaneScreen } from "@/lib/paneScreensApi";
import { dueTargets, MAX_SCREEN_TARGETS, mergeScreens } from "./usePaneScreens";
import { nearestSeated } from "./TerminalMonitors";

const target = (n: number) => ({ agentId: `pane:ws:${n}`, workspaceId: "ws", key: `T${n}` });
const screen = (key: string, at: number): PaneScreen => ({
  workspace_id: "ws", key, name: key, cols: 80, rows: 24, lines: [key], cursor: null, at,
});

describe("terminal screen polling", () => {
  it("polls seated panes only, never more than the cap", () => {
    const targets = Array.from({ length: 9 }, (_, i) => target(i));
    const seated = new Set(targets.filter((_, i) => i !== 1).map((t) => t.agentId));
    const due = dueTargets(targets, seated);
    expect(due).toHaveLength(MAX_SCREEN_TARGETS);
    expect(due.map((t) => t.key)).not.toContain("T1");
  });

  it("maps screens back to agents and keeps the map when nothing changed", () => {
    const due = [target(1), target(2)];
    const targeted = new Set(due.map((t) => t.agentId));
    const first = mergeScreens(new Map(), [screen("T1", 1), screen("T9", 1)], due, targeted);
    expect([...first.keys()]).toEqual(["pane:ws:1"]);
    expect(mergeScreens(first, [screen("T1", 1)], due, targeted)).toBe(first);
    const moved = mergeScreens(first, [screen("T1", 2)], due, targeted);
    expect(moved).not.toBe(first);
    expect(moved.get("pane:ws:1")?.at).toBe(2);
  });

  it("drops screens of agents no longer targeted", () => {
    const due = [target(1)];
    const first = mergeScreens(new Map(), [screen("T1", 1)], due, new Set(["pane:ws:1"]));
    expect(mergeScreens(first, [], [], new Set()).size).toBe(0);
  });

  it("picks the nearest seated monitors, in a stable order", () => {
    const picked = nearestSeated([
      { id: "c", distance: 3 }, { id: "a", distance: 9 }, { id: "b", distance: 1 }, { id: "far", distance: 30 },
    ], 3);
    expect(picked).toEqual(["a", "b", "c"]);
  });
});
