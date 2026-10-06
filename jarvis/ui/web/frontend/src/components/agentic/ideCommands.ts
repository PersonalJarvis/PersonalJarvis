/**
 * Every command the Agentic IDE's command palette offers, as plain data.
 *
 * The palette is the searchable twin of the Ctrl+B key menu: each row runs the
 * same `IdeHotkeyAction` the key menu runs, and shows the keys that reach it
 * from the keyboard, so picking a command once teaches its shortcut. The views
 * (grid, threads, Verse) and the side panel tabs are here too, because the
 * caption and the panel header are the only other way to reach them.
 *
 * Pure on purpose: the list is built from what the view knows (installed
 * agents, open workspaces, the layout) and is testable without a DOM.
 */
import type { SidePanelTabId } from "@/store/ideSidePanel";
import type { IdeFace } from "./threads/IdeLayoutSwitch";
import type { AgentKey, IdeHotkeyAction } from "./ideHotkeys";

export type IdeCommandGroup = "agents" | "panes" | "workspaces" | "view";

/** The order the palette lists its groups in. */
export const IDE_COMMAND_GROUPS: readonly IdeCommandGroup[] = ["agents", "panes", "workspaces", "view"];

export const IDE_COMMAND_GROUP_LABEL: Record<IdeCommandGroup, string> = {
  agents: "Agents",
  panes: "Panes",
  workspaces: "Workspaces",
  view: "View",
};

/** What picking a command does. */
export type IdeCommandRun =
  | { type: "hotkey"; action: IdeHotkeyAction }
  | { type: "face"; face: IdeFace }
  | { type: "panel-tab"; tab: SidePanelTabId }
  | { type: "panel-toggle" };

export interface IdeCommand {
  /** Stable and unique: the palette's row value and test id. */
  id: string;
  group: IdeCommandGroup;
  label: string;
  /** Extra words the search matches besides the label. */
  keywords: string[];
  /**
   * The key presses that reach the same command, one inner list per press:
   * `[["Ctrl", "B"], ["V"]]` reads "Ctrl+B, then V". Empty when no key does.
   */
  keys: string[][];
  run: IdeCommandRun;
  /** Agent rows carry the agent so the palette can draw its mark. */
  agent?: string;
}

export interface IdeCommandContext {
  /** Installed coding agents with their key-menu letters. */
  agents: readonly AgentKey[];
  /** Keycaps of the key-menu chord; empty when the user switched it off. */
  leaderCaps: readonly string[];
  /** A workspace is open, so pane and workspace commands have a target. */
  hasSession: boolean;
  /** The thread layout is on: the grid and its key menu are out of view. */
  threads: boolean;
  /** Open workspaces in tab order, and which one is in front. */
  workspaces: readonly { id: string; name: string }[];
  activeWorkspaceId: string | null;
  /** Side panel tabs with their labels already translated. */
  panelTabs: readonly { id: SidePanelTabId; label: string }[];
  panelOpen: boolean;
  face: IdeFace;
}

/**
 * The palette's full list for this moment.
 *
 * Commands that cannot do anything right now are left out rather than greyed:
 * without a workspace there is no pane to split, and in the thread layout the
 * grid commands would act on terminals nobody can see.
 */
export function buildIdeCommands(context: IdeCommandContext): IdeCommand[] {
  const { agents, leaderCaps, hasSession, threads, workspaces, activeWorkspaceId, panelTabs, panelOpen, face } = context;
  // The key menu only listens in the grid; a shortcut that does nothing in the
  // threads would teach the wrong thing.
  const viaLeader = (...steps: string[][]): string[][] =>
    !threads && leaderCaps.length > 0 ? [[...leaderCaps], ...steps] : [];
  const commands: IdeCommand[] = [];
  const hotkey = (
    id: string, group: IdeCommandGroup, label: string, action: IdeHotkeyAction,
    keys: string[][], keywords: string[] = [],
  ) => commands.push({ id, group, label, keywords, keys, run: { type: "hotkey", action } });

  if (hasSession && !threads) {
    hotkey("agent-picker", "agents", "Add coding agent…", { kind: "agent-picker" }, viaLeader(["+"]), ["new", "pane", "terminal", "spawn"]);
    for (const agent of agents) {
      commands.push({
        id: `spawn:${agent.name}`,
        group: "agents",
        label: `New ${agent.label} pane`,
        keywords: [agent.name, "open", "start", "terminal", "agent"],
        keys: viaLeader([agent.key.toUpperCase()], ["Enter"]),
        run: { type: "hotkey", action: { kind: "spawn", agent: agent.name, direction: null } },
        agent: agent.name,
      });
    }
    hotkey("split-right", "panes", "Split pane right", { kind: "split", direction: "right" }, viaLeader(["V"]), ["vertical", "side"]);
    hotkey("split-down", "panes", "Split pane down", { kind: "split", direction: "down" }, viaLeader(["-"]), ["horizontal", "below"]);
    hotkey("maximize-pane", "panes", "Zoom pane (fill the grid)", { kind: "maximize-pane" }, viaLeader(["Z"]), ["maximize", "fullscreen", "focus"]);
    hotkey("balance", "panes", "Even out the grid", { kind: "balance" }, viaLeader(["E"]), ["balance", "equal", "layout", "arrange"]);
    hotkey("fork-pane", "panes", "Fork pane", { kind: "fork-pane" }, viaLeader(["F"]), ["copy", "duplicate", "branch"]);
    hotkey("close-pane", "panes", "Close pane", { kind: "close-pane" }, viaLeader(["Q"]), ["stop", "kill", "end"]);
  }

  hotkey("new-workspace", "workspaces", "New workspace", { kind: "new-workspace" }, viaLeader(["W"], ["N"]), ["create", "session"]);
  if (hasSession) {
    hotkey("new-worktree-workspace", "workspaces", "New workspace in a git worktree", { kind: "new-worktree-workspace" }, viaLeader(["W"], ["T"]), ["branch", "git", "isolated"]);
    hotkey("git-panel", "workspaces", "Git: branches, commits and worktrees", { kind: "git-panel" }, viaLeader(["W"], ["G"]), ["commit", "branch", "push", "diff"]);
    hotkey("workspace-options", "workspaces", "Workspace options", { kind: "workspace-options" }, viaLeader(["W"], ["O"]), ["settings", "appearance", "style"]);
    hotkey("rename-workspace", "workspaces", "Rename workspace", { kind: "rename-workspace" }, viaLeader(["W"], ["R"]), ["name", "title"]);
    hotkey("close-workspace", "workspaces", "Close workspace", { kind: "close-workspace" }, viaLeader(["W"], ["Q"]), ["stop", "end"]);
  }
  hotkey("connect-project", "workspaces", "Connect a project folder…", { kind: "connect-project" }, viaLeader(["W"], ["P"]), ["open", "folder", "add", "repository"]);
  if (workspaces.length > 1) {
    hotkey("workspace-next", "workspaces", "Next workspace", { kind: "workspace-step", step: 1 }, viaLeader(["N"]), ["switch", "forward"]);
    hotkey("workspace-previous", "workspaces", "Previous workspace", { kind: "workspace-step", step: -1 }, viaLeader(["P"]), ["switch", "back"]);
    workspaces.slice(0, 9).forEach((workspace, index) => {
      if (workspace.id === activeWorkspaceId) return;
      hotkey(`workspace:${workspace.id}`, "workspaces", `Go to workspace ${workspace.name}`,
        { kind: "workspace-index", index }, viaLeader([String(index + 1)]), ["switch", "open"]);
    });
  }

  const faces: { face: IdeFace; label: string; keywords: string[] }[] = [
    { face: "grid", label: "Show the terminal grid", keywords: ["layout", "panes", "terminals"] },
    { face: "threads", label: "Show threads", keywords: ["layout", "chat", "conversation"] },
    { face: "verse", label: "Show the Jarvis Verse", keywords: ["office", "world", "3d", "map"] },
  ];
  for (const entry of faces) {
    if (entry.face === face) continue;
    commands.push({ id: `face:${entry.face}`, group: "view", label: entry.label, keywords: entry.keywords, keys: [], run: { type: "face", face: entry.face } });
  }
  commands.push({
    id: "panel-toggle",
    group: "view",
    label: panelOpen ? "Close side panel" : "Open side panel",
    keywords: ["sidebar", "right", "panel", "toggle"],
    keys: [],
    run: { type: "panel-toggle" },
  });
  for (const tab of panelTabs) {
    commands.push({
      id: `panel:${tab.id}`,
      group: "view",
      label: `Side panel: ${tab.label}`,
      keywords: ["open", "show", "tab", tab.id],
      keys: [],
      run: { type: "panel-tab", tab: tab.id },
    });
  }
  hotkey("toggle-voice", "view", "Show or hide voice", { kind: "toggle-voice" }, viaLeader(["M"]), ["microphone", "talk", "speak", "call"]);
  return commands;
}

/**
 * Does `command` match what was typed? Every word of the query must appear in
 * the label, a keyword or the group — so "split right" and "right split" both
 * find the same row, and "git" finds every git command.
 */
export function ideCommandMatches(command: IdeCommand, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = [command.label, IDE_COMMAND_GROUP_LABEL[command.group], ...command.keywords].join(" ").toLowerCase();
  return words.every((word) => haystack.includes(word));
}
