import { describe, expect, it } from "vitest";
import type { AgentStatus } from "@/lib/agenticIdeApi";
import type { PaneOccupant } from "./codingFloor";
import { launchableAgents, pickFleet } from "./MissionControlPanel";
import { QUICK_ORDERS, screenTail } from "./PaneCommandPanel";

const cli = (name: string, extra: Partial<AgentStatus> = {}): AgentStatus => ({
  name, display_name: name, installed: true, version: null, install_command: null, ...extra,
} as AgentStatus);

const occupant = (id: string, state: PaneOccupant["agent"]["state"]): PaneOccupant =>
  ({ agent: { agentId: id, state } } as PaneOccupant);

describe("pane command panel", () => {
  it("shows the bottom of the terminal, without the blank rows under the cursor", () => {
    expect(screenTail(["a", "b", "c", "", "  "], 2)).toEqual(["b", "c"]);
    expect(screenTail(["", ""], 5)).toEqual([]);
    expect(screenTail(["one"], 12)).toEqual(["one"]);
  });

  it("offers the four one-tap orders", () => {
    expect(QUICK_ORDERS).toEqual(["continue", "status", "tests", "commit"]);
  });
});

describe("mission control", () => {
  it("launches only installed coding CLIs, never a plain shell", () => {
    const list = [cli("claude"), cli("codex", { installed: false }), cli("shell", { kind: "shell" }), cli("gemini", { kind: "cli" })];
    expect(launchableAgents(list).map((a) => a.name)).toEqual(["claude", "gemini"]);
  });

  it("picks the fleet by run state", () => {
    const floor = [occupant("a", "working"), occupant("b", "waiting"), occupant("c", "idle"), occupant("d", "working")];
    expect([...pickFleet(floor, "all")]).toEqual(["a", "b", "c", "d"]);
    expect([...pickFleet(floor, "working")]).toEqual(["a", "d"]);
    expect([...pickFleet(floor, "waiting")]).toEqual(["b"]);
    expect([...pickFleet(floor, "none")]).toEqual([]);
  });
});
