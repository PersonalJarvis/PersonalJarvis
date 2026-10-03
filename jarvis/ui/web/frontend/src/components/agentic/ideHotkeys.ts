/**
 * Leader-key shortcuts for the Agentic IDE.
 *
 * One chord — Ctrl+B, on every OS, like tmux — opens a small key menu; the
 * next keys pick what happens, the way tmux and herdr work: a one-line mode
 * bar at the bottom says PREFIX and names the keys, `?` opens the full list,
 * Esc or any unbound key drops back to the terminal. `Ctrl+B, C, →` opens a
 * Claude Code pane to the right of the focused pane; `Ctrl+B, V` splits the
 * focused pane with the same agent; `Ctrl+B, W, N` starts a new workspace.
 *
 * Why a leader and not a chord per action: a focused pane forwards nearly every
 * Ctrl chord to the coding agent running in it, and each agent claims its own
 * (Claude Code alone uses Ctrl+B, Ctrl+G, Ctrl+O, Ctrl+R, Ctrl+T). One reserved
 * chord leaves every other key with the agent. The one it takes, Ctrl+B, is
 * Claude Code's "run in background": pressing it twice types it into the pane
 * (`LEADER_PASSTHROUGH`), exactly as tmux passes its prefix through.
 *
 * Everything here is pure: the hook (`useIdeHotkeys`) feeds key events in and
 * carries out the actions that come back, so the whole key map is testable
 * without a DOM.
 */
import type { PaneSplitDirection } from "./WorkspaceTerminalHeader";

export type PaneDirection = "left" | "right" | "up" | "down";

/** What a finished key sequence asks the IDE to do. */
export type IdeHotkeyAction =
  | { kind: "spawn"; agent: string; direction: PaneSplitDirection | null }
  | { kind: "agent-picker" }
  | { kind: "split"; direction: PaneSplitDirection }
  | { kind: "focus-pane"; direction: PaneDirection }
  | { kind: "swap-pane"; direction: PaneDirection }
  | { kind: "maximize-pane" }
  | { kind: "close-pane" }
  | { kind: "rename-pane" }
  | { kind: "fork-pane" }
  | { kind: "balance" }
  | { kind: "toggle-voice" }
  | { kind: "workspace-index"; index: number }
  | { kind: "workspace-step"; step: 1 | -1 }
  | { kind: "new-workspace" }
  | { kind: "new-worktree-workspace" }
  | { kind: "rename-workspace" }
  | { kind: "close-workspace" }
  | { kind: "git-panel" }
  | { kind: "workspace-options" }
  | { kind: "connect-project" };

/** Where in a key sequence the menu stands. */
export type IdeHotkeyStep =
  | { menu: "root" }
  | { menu: "workspace" }
  | { menu: "help" }
  | { menu: "direction"; agent: string; label: string };

/** The minimal slice of a KeyboardEvent the matcher reads. */
export interface HotkeyEventLike {
  key: string;
  code: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
}

/** An agent the menu can open, already filtered to what this machine can run. */
export interface HotkeyAgent { name: string; label: string }

/** One agent and the letter that opens it. */
export interface AgentKey extends HotkeyAgent { key: string }

/** The result of one key press while the menu is open. */
export type HotkeyOutcome =
  | { type: "step"; step: IdeHotkeyStep }
  | { type: "run"; action: IdeHotkeyAction }
  | { type: "close" }
  | { type: "ignore" };

/**
 * Is this the leader chord? Ctrl+B on every OS — Ctrl, not Cmd, on a Mac too,
 * because that is the terminal key tmux users already have in their hands.
 */
export function isLeaderChord(event: HotkeyEventLike): boolean {
  // `code` first: it names the physical key on every layout. `key` covers a
  // host that reports no code (some remote-desktop and test events do).
  const isB = event.code === "KeyB" || (!event.code && event.key.toLowerCase() === "b");
  return event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && isB;
}

/** What Ctrl+B twice types into the focused pane: the control code Ctrl+B itself sends. */
export const LEADER_PASSTHROUGH = "\x02";

/** Letters the root menu keeps for its own commands; no agent may take one. */
export const RESERVED_ROOT_KEYS = new Set(["e", "f", "m", "n", "p", "q", "r", "v", "w", "z"]);

/**
 * The letter each known agent prefers. Claude and Codex both start with C, so
 * Codex takes the X of its name. A CLI added later falls through to the first free letter of
 * its own name (see `assignAgentKeys`).
 */
const PREFERRED_AGENT_KEYS: Record<string, string> = {
  claude: "c",
  codex: "x",
  cursor: "u",
  opencode: "o",
  kimi: "k",
  glm: "l",
  "grok-build": "g",
  antigravity: "a",
  gemini: "i",
  "deepseek-harness": "d",
};

/**
 * Give every agent its own letter, stable across sessions.
 *
 * Known agents keep their preferred letter; the rest take the first unused
 * letter of their name, then of their label, then of the alphabet. An agent
 * that finds no letter at all is left out rather than given a key that does
 * something else.
 */
export function assignAgentKeys(agents: readonly HotkeyAgent[]): AgentKey[] {
  const taken = new Set(RESERVED_ROOT_KEYS);
  const assigned = new Map<string, string>();
  for (const agent of agents) {
    const preferred = PREFERRED_AGENT_KEYS[agent.name];
    if (preferred && !taken.has(preferred)) { assigned.set(agent.name, preferred); taken.add(preferred); }
  }
  for (const agent of agents) {
    if (assigned.has(agent.name)) continue;
    const candidates = `${agent.name}${agent.label}abcdefghijklmnopqrstuvwxyz`.toLowerCase().replace(/[^a-z]/g, "");
    const letter = [...candidates].find((char) => !taken.has(char));
    if (letter) { assigned.set(agent.name, letter); taken.add(letter); }
  }
  return agents.flatMap((agent) => {
    const key = assigned.get(agent.name);
    return key ? [{ ...agent, key }] : [];
  });
}

const ARROWS: Record<string, PaneDirection> = {
  ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down",
};

/** Arrow → the split direction the add-agent API speaks. */
const SPLIT_FOR_ARROW: Record<PaneDirection, PaneSplitDirection> = {
  left: "left", right: "right", up: "above", down: "down",
};

/** Pure modifier presses never move the menu; the user is still building a chord. */
const MODIFIER_KEYS = new Set(["Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock", "OS"]);

/** The letter a key event typed, layout-aware: QWERTZ users see their own Z. */
function letterOf(event: HotkeyEventLike): string | null {
  const key = event.key.length === 1 ? event.key.toLowerCase() : "";
  return /^[a-z]$/.test(key) ? key : null;
}

/** The digit 1–9 of a number-row or numpad key, by position (Shift+1 is "!" on most layouts). */
function digitOf(event: HotkeyEventLike): number | null {
  const match = /^(?:Digit|Numpad)([1-9])$/.exec(event.code) ?? /^([1-9])$/.exec(event.key);
  return match ? Number(match[1]) : null;
}

/**
 * Advance the menu by one key press.
 *
 * Escape closes from anywhere; Backspace steps back one level. A key the
 * current menu does not know closes it too, so a stray press never leaves the
 * IDE waiting for input the user no longer means to give.
 */
export function resolveHotkey(step: IdeHotkeyStep, event: HotkeyEventLike, agents: readonly AgentKey[]): HotkeyOutcome {
  if (MODIFIER_KEYS.has(event.key)) return { type: "ignore" };
  if (event.key === "Escape") return { type: "close" };
  if (event.key === "Backspace") return step.menu === "root" ? { type: "close" } : { type: "step", step: { menu: "root" } };
  // The full key list: any key leaves it, the way herdr's prefix+? help does.
  if (step.menu === "help") return { type: "close" };
  // Ctrl/Cmd/Alt combinations belong to the app and the OS, never to the menu.
  if (event.ctrlKey || event.metaKey || event.altKey) return { type: "close" };
  const arrow = ARROWS[event.key];
  const letter = letterOf(event);

  if (step.menu === "direction") {
    if (arrow) return { type: "run", action: { kind: "spawn", agent: step.agent, direction: SPLIT_FOR_ARROW[arrow] } };
    if (event.key === "Enter" || event.key === " ") return { type: "run", action: { kind: "spawn", agent: step.agent, direction: null } };
    return { type: "close" };
  }

  if (step.menu === "workspace") {
    const workspaceKeys: Record<string, IdeHotkeyAction> = {
      n: { kind: "new-workspace" },
      t: { kind: "new-worktree-workspace" },
      r: { kind: "rename-workspace" },
      q: { kind: "close-workspace" },
      g: { kind: "git-panel" },
      o: { kind: "workspace-options" },
      p: { kind: "connect-project" },
    };
    if (letter && workspaceKeys[letter]) return { type: "run", action: workspaceKeys[letter] };
    if (arrow === "right" || arrow === "down") return { type: "run", action: { kind: "workspace-step", step: 1 } };
    if (arrow === "left" || arrow === "up") return { type: "run", action: { kind: "workspace-step", step: -1 } };
    const digit = digitOf(event);
    if (digit) return { type: "run", action: { kind: "workspace-index", index: digit - 1 } };
    return { type: "close" };
  }

  // Root menu.
  if (arrow) return { type: "run", action: { kind: event.shiftKey ? "swap-pane" : "focus-pane", direction: arrow } };
  if (event.key === "Tab") return { type: "run", action: { kind: "workspace-step", step: event.shiftKey ? -1 : 1 } };
  if (event.key === "?") return { type: "step", step: { menu: "help" } };
  if (event.key === "+") return { type: "run", action: { kind: "agent-picker" } };
  if (event.key === "-") return { type: "run", action: { kind: "split", direction: "down" } };
  const digit = digitOf(event);
  if (digit) return { type: "run", action: { kind: "workspace-index", index: digit - 1 } };
  // herdr's capital workspace keys: Shift+N new, Shift+W rename, Shift+D close.
  if (letter && event.shiftKey) {
    const shifted: Record<string, IdeHotkeyAction> = {
      n: { kind: "new-workspace" }, w: { kind: "rename-workspace" }, d: { kind: "close-workspace" },
    };
    return shifted[letter] ? { type: "run", action: shifted[letter] } : { type: "close" };
  }
  if (letter) {
    const rootKeys: Record<string, IdeHotkeyAction | IdeHotkeyStep> = {
      e: { kind: "balance" },
      f: { kind: "fork-pane" },
      m: { kind: "toggle-voice" },
      n: { kind: "workspace-step", step: 1 },
      p: { kind: "workspace-step", step: -1 },
      q: { kind: "close-pane" },
      r: { kind: "rename-pane" },
      v: { kind: "split", direction: "right" },
      w: { menu: "workspace" },
      z: { kind: "maximize-pane" },
    };
    const command = rootKeys[letter];
    if (command) return "menu" in command ? { type: "step", step: command } : { type: "run", action: command };
    const agent = agents.find((entry) => entry.key === letter);
    if (agent) return { type: "step", step: { menu: "direction", agent: agent.name, label: agent.label } };
  }
  return { type: "close" };
}

/** The one-line mode bar: a badge naming the mode, then the keys that work in it. */
export interface ModeBar { badge: string; hints: HotkeyHint[] }

/** What the bar at the bottom says for a step; the full list lives behind `?`. */
export function modeBar(step: IdeHotkeyStep, agents: readonly AgentKey[]): ModeBar {
  if (step.menu === "direction") {
    return {
      badge: step.label.toUpperCase(),
      hints: [
        { keys: ["→"], label: "right" }, { keys: ["←"], label: "left" }, { keys: ["↑"], label: "above" },
        { keys: ["↓"], label: "below" }, { keys: ["Enter"], label: "even grid" }, { keys: ["Esc"], label: "cancel" },
      ],
    };
  }
  if (step.menu === "workspace") {
    return {
      badge: "WORKSPACE",
      hints: [
        { keys: ["N"], label: "new" }, { keys: ["T"], label: "worktree" }, { keys: ["R"], label: "rename" },
        { keys: ["Q"], label: "close" }, { keys: ["G"], label: "git" }, { keys: ["O"], label: "options" },
        { keys: ["P"], label: "connect folder" }, { keys: ["←→"], label: "switch" }, { keys: ["Esc"], label: "back" },
      ],
    };
  }
  if (step.menu === "help") return { badge: "KEYS", hints: [{ keys: ["any key"], label: "close" }] };
  return {
    badge: "PREFIX",
    hints: [
      ...agents.map((agent) => ({ keys: [agent.key.toUpperCase()], label: agent.label })),
      { keys: ["V", "-"], label: "split" },
      { keys: ["←↑→↓"], label: "move" },
      { keys: ["Z"], label: "zoom" },
      { keys: ["Q"], label: "close" },
      { keys: ["W"], label: "workspaces" },
      { keys: ["?"], label: "all keys" },
      { keys: ["Esc"], label: "cancel" },
      { keys: ["Ctrl", "B"], label: "send Ctrl+B" },
    ],
  };
}

/** One row of the key menu as the overlay draws it. */
export interface HotkeyHint { keys: string[]; label: string }
export interface HotkeyHintGroup { title: string; hints: HotkeyHint[] }

/** The rows the overlay shows for a step — the same table `resolveHotkey` reads. */
export function hotkeyHints(step: IdeHotkeyStep, agents: readonly AgentKey[]): HotkeyHintGroup[] {
  if (step.menu === "direction") {
    return [{
      title: `Open ${step.label}`,
      hints: [
        { keys: ["→"], label: "To the right" },
        { keys: ["←"], label: "To the left" },
        { keys: ["↑"], label: "Above" },
        { keys: ["↓"], label: "Below" },
        { keys: ["Enter"], label: "Even grid" },
      ],
    }];
  }
  if (step.menu === "workspace") {
    return [{
      title: "Workspace",
      hints: [
        { keys: ["N"], label: "New workspace" },
        { keys: ["T"], label: "New worktree workspace" },
        { keys: ["R"], label: "Rename workspace" },
        { keys: ["Q"], label: "Close workspace" },
        { keys: ["G"], label: "Git panel" },
        { keys: ["O"], label: "Workspace options" },
        { keys: ["P"], label: "Connect a project folder" },
        { keys: ["←", "→"], label: "Previous / next workspace" },
        { keys: ["1–9"], label: "Go to workspace 1–9" },
      ],
    }];
  }
  return [
    {
      title: "New pane",
      hints: [
        ...agents.map((agent) => ({ keys: [agent.key.toUpperCase()], label: `${agent.label}, then an arrow` })),
        { keys: ["V"], label: "Split right, same agent" },
        { keys: ["-"], label: "Split below, same agent" },
        { keys: ["+"], label: "Choose agent…" },
      ],
    },
    {
      title: "Panes",
      hints: [
        { keys: ["←↑→↓"], label: "Focus neighbor" },
        { keys: ["Shift", "←↑→↓"], label: "Swap with neighbor" },
        { keys: ["Z"], label: "Maximize / restore" },
        { keys: ["R"], label: "Rename pane" },
        { keys: ["F"], label: "Fork pane" },
        { keys: ["Q"], label: "Close pane" },
        { keys: ["E"], label: "Even out layout" },
      ],
    },
    {
      title: "Workspaces",
      hints: [
        { keys: ["W"], label: "Workspace menu…" },
        { keys: ["1–9"], label: "Go to workspace" },
        { keys: ["N", "P"], label: "Next / previous workspace" },
        { keys: ["Shift", "N"], label: "New workspace" },
        { keys: ["Shift", "W"], label: "Rename workspace" },
        { keys: ["Shift", "D"], label: "Close workspace" },
        { keys: ["M"], label: "Voice bubble" },
        { keys: ["Ctrl", "B"], label: "Send Ctrl+B to the pane" },
      ],
    },
  ];
}

/**
 * The pane that lies in `direction` from `from`, on a grid of boxes in 0–1
 * coordinates: nearest by centre along the axis, overlapping across it, ties
 * broken by how close the centres sit across the axis.
 */
export interface PaneBox { x: number; y: number; w: number; h: number }
export function neighborInDirection(boxes: readonly (PaneBox | undefined)[], from: number, direction: PaneDirection): number | null {
  const box = boxes[from];
  if (!box) return null;
  const horizontal = direction === "left" || direction === "right";
  const sign = direction === "left" || direction === "up" ? -1 : 1;
  const centre = horizontal ? box.x + box.w / 2 : box.y + box.h / 2;
  const crossCentre = horizontal ? box.y + box.h / 2 : box.x + box.w / 2;
  let best: { index: number; distance: number; cross: number } | null = null;
  boxes.forEach((other, index) => {
    if (!other || index === from) return;
    const distance = ((horizontal ? other.x + other.w / 2 : other.y + other.h / 2) - centre) * sign;
    const overlap = horizontal
      ? Math.min(box.y + box.h, other.y + other.h) - Math.max(box.y, other.y)
      : Math.min(box.x + box.w, other.x + other.w) - Math.max(box.x, other.x);
    if (distance <= 0.001 || overlap <= 0.001) return;
    const cross = Math.abs((horizontal ? other.y + other.h / 2 : other.x + other.w / 2) - crossCentre);
    if (!best || distance < best.distance || (distance === best.distance && cross < best.cross)) best = { index, distance, cross };
  });
  return best ? (best as { index: number }).index : null;
}

/** The window event that carries a pane command from the key menu to the grid that owns the pane. */
export const PANE_COMMAND_EVENT = "jarvis:ide-pane-command";
export type PaneCommand =
  | { kind: "focus"; direction: PaneDirection }
  | { kind: "swap"; direction: PaneDirection }
  | { kind: "maximize" }
  | { kind: "fork" }
  | { kind: "rename"; name: string };
export interface PaneCommandDetail { workspaceId: string; pane: string; command: PaneCommand }

/** The window event that types text into one pane's terminal, as if from the keyboard. */
export const PANE_INPUT_EVENT = "jarvis:ide-pane-input";
export interface PaneInputDetail { workspaceId: string; pane: string; data: string }
