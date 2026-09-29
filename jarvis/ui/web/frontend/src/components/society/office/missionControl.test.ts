import { describe, expect, it } from "vitest";
import type { AgentStatus } from "@/lib/agenticIdeApi";
import type { PaneOccupant } from "./codingFloor";
import { launchableAgents, pickAll } from "./MissionControlPanel";
import { liveGrid } from "./PaneLiveScreen";

const cli = (name: string, extra: Partial<AgentStatus> = {}): AgentStatus => ({
  name, display_name: name, installed: true, version: null, install_command: null, ...extra,
} as AgentStatus);

const occupant = (id: string, state: PaneOccupant["agent"]["state"]): PaneOccupant =>
  ({ agent: { agentId: id, state } } as PaneOccupant);

describe("pane live screen", () => {
  it("fits the live window's font to the pane's width, within bounds", () => {
    // A narrow 40-column TUI in a wide window grows to the cap instead of hugging the left edge.
    expect(liveGrid(40, 900, 240).font).toBe(15);
    // A wide 200-column terminal shrinks to the floor and is cut on the right.
    const wide = liveGrid(200, 500, 240);
    expect(wide.font).toBe(10);
    expect(wide.cols).toBe(79);
    // As many rows as the height holds, never zero.
    expect(liveGrid(80, 600, 240).fit).toBe(Math.floor(240 / Math.round(liveGrid(80, 600, 240).font * 1.15)));
    expect(liveGrid(80, 0, 0).fit).toBe(1);
  });

});

describe("mission control", () => {
  it("launches only installed coding CLIs, never a plain shell", () => {
    const list = [cli("claude"), cli("codex", { installed: false }), cli("shell", { kind: "shell" }), cli("gemini", { kind: "cli" })];
    expect(launchableAgents(list).map((a) => a.name)).toEqual(["claude", "gemini"]);
  });

  it("selects everyone on the floor, and clears again once everyone is picked", () => {
    const floor = [occupant("a", "working"), occupant("b", "waiting"), occupant("c", "idle")];
    expect([...pickAll(floor, new Set(["b"]))]).toEqual(["a", "b", "c"]);
    expect([...pickAll(floor, new Set(["a", "b", "c"]))]).toEqual([]);
  });
});
