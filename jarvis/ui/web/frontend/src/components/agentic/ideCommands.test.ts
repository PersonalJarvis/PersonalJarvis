import { describe, expect, it } from "vitest";
import { buildIdeCommands, ideCommandMatches, type IdeCommandContext } from "./ideCommands";

const base: IdeCommandContext = {
  agents: [{ name: "claude", label: "Claude Code", key: "c" }, { name: "codex", label: "Codex", key: "x" }],
  leaderCaps: ["Ctrl", "B"],
  hasSession: true,
  threads: false,
  workspaces: [{ id: "w1", name: "Jarvis" }, { id: "w2", name: "Website" }],
  activeWorkspaceId: "w1",
  panelTabs: [{ id: "agents", label: "Agents" }, { id: "git", label: "Git" }],
  panelOpen: false,
  face: "grid",
};

const ids = (context: IdeCommandContext) => buildIdeCommands(context).map((command) => command.id);

describe("buildIdeCommands", () => {
  it("offers a pane per installed agent with the key menu's own keys", () => {
    const claude = buildIdeCommands(base).find((command) => command.id === "spawn:claude");
    expect(claude?.label).toBe("New Claude Code pane");
    expect(claude?.keys).toEqual([["Ctrl", "B"], ["C"], ["Enter"]]);
    expect(claude?.run).toEqual({ type: "hotkey", action: { kind: "spawn", agent: "claude", direction: null } });
  });

  it("teaches the two-step workspace keys", () => {
    const git = buildIdeCommands(base).find((command) => command.id === "git-panel");
    expect(git?.keys).toEqual([["Ctrl", "B"], ["W"], ["G"]]);
  });

  it("leaves out pane and workspace commands without a workspace", () => {
    const list = ids({ ...base, hasSession: false, workspaces: [], activeWorkspaceId: null });
    expect(list).not.toContain("split-right");
    expect(list).not.toContain("spawn:claude");
    expect(list).not.toContain("git-panel");
    expect(list).toContain("new-workspace");
    expect(list).toContain("connect-project");
  });

  it("drops grid commands and every shortcut in the thread layout", () => {
    const commands = buildIdeCommands({ ...base, threads: true, face: "threads" });
    expect(commands.map((command) => command.id)).not.toContain("split-right");
    expect(commands.every((command) => command.keys.length === 0)).toBe(true);
    expect(commands.map((command) => command.id)).toContain("face:grid");
    expect(commands.map((command) => command.id)).not.toContain("face:threads");
  });

  it("shows no keys when the key menu chord is switched off", () => {
    expect(buildIdeCommands({ ...base, leaderCaps: [] }).every((command) => command.keys.length === 0)).toBe(true);
  });

  it("lists the other workspaces by number, never the one in front", () => {
    const list = ids(base);
    expect(list).toContain("workspace:w2");
    expect(list).not.toContain("workspace:w1");
    const website = buildIdeCommands(base).find((command) => command.id === "workspace:w2");
    expect(website?.keys).toEqual([["Ctrl", "B"], ["2"]]);
  });

  it("names the side panel toggle after what it will do", () => {
    const closed = buildIdeCommands(base).find((command) => command.id === "panel-toggle");
    const open = buildIdeCommands({ ...base, panelOpen: true }).find((command) => command.id === "panel-toggle");
    expect(closed?.label).toBe("Open side panel");
    expect(open?.label).toBe("Close side panel");
  });

  it("gives every command a unique id", () => {
    const list = ids(base);
    expect(new Set(list).size).toBe(list.length);
  });
});

describe("ideCommandMatches", () => {
  const commands = buildIdeCommands(base);
  const find = (query: string) => commands.filter((command) => ideCommandMatches(command, query)).map((command) => command.id);

  it("matches every word in any order", () => {
    expect(find("right split")).toEqual(["split-right"]);
  });

  it("matches keywords, not only the label", () => {
    expect(find("maximize")).toContain("maximize-pane");
  });

  it("keeps everything for an empty query", () => {
    expect(find("  ")).toHaveLength(commands.length);
  });
});
