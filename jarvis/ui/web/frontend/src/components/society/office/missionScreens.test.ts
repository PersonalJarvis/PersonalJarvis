import { describe, expect, it } from "vitest";
import type { SocietyAgent } from "../data";
import type { PaneOccupant } from "./codingFloor";
import { deskLive } from "./CommandOffice";
import { ideGrid, idePanes, ideTree, pickShownAgent } from "./MissionScreens";
import { screenFillDistance } from "./OfficeCameraRig";
import { useOfficeStore } from "./officeStore";

const agent = (agentId: string, extra: Partial<SocietyAgent> = {}): SocietyAgent =>
  ({ agentId, tier: "specialist", state: "idle", chatSessionId: `chat-${agentId}`, ...extra } as SocietyAgent);

const tile = (key: string, workspace: string, active = false): PaneOccupant =>
  ({ pane: { key, workspace_id: workspace, workspace_active: active } } as PaneOccupant);

describe("mission control desk monitors", () => {
  it("shows a working agent's chat on the Agents monitor, never the lead's", () => {
    const lead = agent("lead", { tier: "lead", state: "working" });
    expect(pickShownAgent([lead, agent("a"), agent("b", { state: "working" })])?.agentId).toBe("b");
    expect(pickShownAgent([lead, agent("a"), agent("w", { state: "waiting" })])?.agentId).toBe("w");
    expect(pickShownAgent([lead, agent("nochat", { chatSessionId: null })])).toBeNull();
  });

  it("puts the front workspace's panes on the IDE monitor, in grid order", () => {
    const panes = [tile("T1", "back"), tile("T1", "front", true), tile("T2", "front", true), tile("T2", "back")];
    const shown = idePanes(panes);
    expect(shown.workspace).toBe("front");
    expect(shown.tiles.map((t) => t.pane.key)).toEqual(["T1", "T2"]);
    // No front workspace: the first one stands in; no panes at all: nothing.
    expect(idePanes([tile("T1", "only")]).workspace).toBe("only");
    expect(idePanes([]).tiles).toEqual([]);
  });

  it("groups the IDE sidebar by project folder, then workspace", () => {
    const pane = (workspace: string, folder: string, state: "working" | "idle") =>
      ({ pane: { workspace_id: workspace, workspace_name: workspace, folder }, agent: { state } } as PaneOccupant);
    const tree = ideTree([pane("a", "/p/jarvis", "idle"), pane("a", "/p/jarvis", "working"), pane("b", "C:\\x\\web", "idle")]);
    expect(tree.map((p) => p.name)).toEqual(["jarvis", "web"]);
    expect(tree[0].workspaces).toEqual([{ id: "a", name: "a", count: 2, working: true }]);
  });

  it("splits the IDE monitor like the IDE grid", () => {
    expect(ideGrid(1)).toEqual({ cols: 1, rows: 1 });
    expect(ideGrid(2)).toEqual({ cols: 2, rows: 1 });
    expect(ideGrid(3)).toEqual({ cols: 3, rows: 1 });
    expect(ideGrid(4)).toEqual({ cols: 2, rows: 2 });
    expect(ideGrid(6)).toEqual({ cols: 3, rows: 2 });
  });

  it("runs the live sections only near the desk, with no flicker at the edge", () => {
    expect(deskLive(20, false)).toBe(true);
    expect(deskLive(24, false)).toBe(false);
    expect(deskLive(24, true)).toBe(true);
    expect(deskLive(27, true)).toBe(false);
  });

  it("dives into a larger monitor from further back, so it still fills the view", () => {
    expect(screenFillDistance(35, 1.6, 0.72, 0.42)).toBeGreaterThan(screenFillDistance(35, 1.6));
  });

  it("a monitor click queues the dive and the section it opens", () => {
    useOfficeStore.getState().diveToSection("costs", [1, 1.1, 2], 0.4, [0.72, 0.42]);
    const { zoom, sectionDive } = useOfficeStore.getState();
    expect(zoom).toMatchObject({ target: [1, 1.1, 2], facing: 0.4, size: [0.72, 0.42] });
    expect(sectionDive?.section).toBe("costs");
  });
});
