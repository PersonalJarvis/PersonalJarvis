import { describe, expect, it } from "vitest";
import type { AgentStatus, DropAttachment, IdeProject } from "@/lib/agenticIdeApi";
import type { PaneOccupant } from "./codingFloor";
import { launchableAgents, pickAll, workspaceProjects } from "./MissionControlPanel";
import { folderTarget } from "./NewWorkspaceFields";
import { liveGrid } from "./PaneLiveScreen";
import { briefWithFiles, heldPayload, holdDrop } from "./spawnFiles";

const project = (id: string, path: string, extra: Partial<IdeProject> = {}): IdeProject =>
  ({ id, path, name: id, scratch: false, archived: false, exists: true, workspaces: [], ...extra } as IdeProject);

const described = (reference: string): DropAttachment =>
  ({ name: reference, reference, kind: "image", detail: "a chart", described_by: "vision", note: "" });

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

  it("offers a new workspace only in real, reachable, connected folders", () => {
    const list = [project("shop", "/w/shop"), project("chats", "/w/c", { scratch: true }),
      project("old", "/w/old", { archived: true }), project("usb", "/mnt/usb", { exists: false })];
    expect(workspaceProjects(list).map((p) => p.id)).toEqual(["shop"]);
  });

  it("recognises a picked folder that is already a connected project", () => {
    const list = [project("shop", "C:\\work\\shop")];
    expect(folderTarget("c:/work/shop/", list)).toEqual({ path: "C:\\work\\shop", label: "shop", projectId: "shop" });
    expect(folderTarget("/home/me/new-app", list)).toEqual({ path: "/home/me/new-app", label: "new-app" });
  });
});

describe("spawn point files", () => {
  const shot = new File(["png"], "shot.png", { type: "image/png", lastModified: 1 });

  it("holds each dropped file once, paths and bytes alike", () => {
    const once = holdDrop([], { paths: ["C:\\docs\\spec.pdf"], files: [shot] });
    const twice = holdDrop(once, { paths: ["c:/docs/spec.pdf"], files: [shot] });
    expect(twice.map((h) => h.name)).toEqual(["spec.pdf", "shot.png"]);
    expect(heldPayload(twice)).toEqual({ paths: ["C:\\docs\\spec.pdf"], files: [shot] });
  });

  it("writes in only the files the analysis did not describe, never ending on one", () => {
    expect(briefWithFiles("Fix it", ["@a.png"], [described("@a.png")])).toBe("Fix it");
    const brief = briefWithFiles("Fix it", ["@a.png", "@big.mov"], [described("@a.png")]);
    expect(brief).toContain("@big.mov");
    expect(brief).not.toContain("@a.png");
    expect(brief.trimEnd().endsWith("@big.mov")).toBe(false);
  });
});
