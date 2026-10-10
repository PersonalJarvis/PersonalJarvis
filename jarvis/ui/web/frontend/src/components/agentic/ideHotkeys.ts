/**
 * Leader-key shortcuts for the Agentic IDE.
 *
 * One chord — Ctrl+B, on every OS, like tmux — opens a small key menu; the
 * next keys pick what happens, with a mode bar at
 * the bottom names the keys, `?` opens the full list. The mode is sticky — it
 * stays on across moves, splits and workspace switches until Esc (or a
 * dialog opening) hands the keyboard back to the terminal. `Ctrl+B, C, →` opens a
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
import { appZoomChordMatches } from "@/lib/appZoom";
import { parseChord } from "@/lib/quickSwitchChord";
import { fill, translate } from "@/i18n";

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
  /** Leave the mode; the key is spent. */
  | { type: "close" }
  /** Leave the mode and let the key through (a Ctrl/Alt/Cmd chord). */
  | { type: "release" }
  /** A bare modifier: nothing yet. */
  | { type: "ignore" }
  /** A key the mode does not use: swallowed, the mode stays on. */
  | { type: "unknown" };

/**
 * Actions after which the mode stays on, so arrows can walk the grid and
 * splits can follow each other. Everything else opens a dialog or ends the
 * pane the mode was acting on, and hands the keyboard back.
 */
export const STICKY_ACTIONS: ReadonlySet<IdeHotkeyAction["kind"]> = new Set([
  "spawn", "split", "focus-pane", "swap-pane", "maximize-pane", "balance",
  "toggle-voice", "workspace-index", "workspace-step",
]);

/** The shipped leader chord, as stored under Settings → Keyboard shortcuts. */
export const DEFAULT_LEADER = "ctrl+b";

/**
 * Is this the leader chord? Ctrl+B on every OS by default — Ctrl, not Cmd, on a
 * Mac too, because that is the terminal key tmux users already have in their
 * hands. `combo` is the chord the user chose ("" = off); another chord is
 * matched like every other in-app chord.
 */
export function isLeaderChord(event: HotkeyEventLike, combo: string = DEFAULT_LEADER): boolean {
  if (!combo) return false;
  if (combo !== DEFAULT_LEADER) return appZoomChordMatches(event, combo);
  // `code` first: it names the physical key on every layout. `key` covers a
  // host that reports no code (some remote-desktop and test events do).
  const isB = event.code === "KeyB" || (!event.code && event.key.toLowerCase() === "b");
  return event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && isB;
}

/** What Ctrl+B twice types into the focused pane: the control code Ctrl+B itself sends. */
export const LEADER_PASSTHROUGH = "\x02";

/**
 * What the leader pressed twice types into the focused pane: the control code
 * the chord itself sends (Ctrl+B is `LEADER_PASSTHROUGH`). Only a plain
 * Ctrl+letter has one; any other leader passes nothing through.
 */
export function leaderPassthrough(combo: string = DEFAULT_LEADER): string | null {
  const { mods, keys } = parseChord(combo);
  const letter = keys.length === 1 && /^[a-z]$/.test(keys[0]) ? keys[0] : null;
  if (!letter || !mods.has("ctrl") || mods.size !== 1) return null;
  return String.fromCharCode(letter.charCodeAt(0) - 96);
}

/** Letters the root menu keeps for its own commands; no agent may take one. */
export const RESERVED_ROOT_KEYS = new Set(["e", "f", "m", "n", "p", "q", "r", "v", "w", "z"]);

/**
 * The letter each known agent prefers. Claude and Codex both start with C, so
 * Codex takes the X of its name — the same split its own docs and most
 * launchers use. A CLI added later falls through to the first free letter of
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
 * Advance the mode by one key press.
 *
 * Escape leaves from anywhere; Backspace steps back one level. A key a
 * sub-menu does not know returns to the top level; one the top level does not
 * know is swallowed, so a typo never lands in an agent's prompt.
 */
export function resolveHotkey(step: IdeHotkeyStep, event: HotkeyEventLike, agents: readonly AgentKey[]): HotkeyOutcome {
  if (MODIFIER_KEYS.has(event.key)) return { type: "ignore" };
  if (event.key === "Escape") return { type: "close" };
  const back: HotkeyOutcome = { type: "step", step: { menu: "root" } };
  if (event.key === "Backspace") return step.menu === "root" ? { type: "unknown" } : back;
  // The full key list: any key goes back to the mode, without leaving the shortcut mode.
  if (step.menu === "help") return back;
  // Ctrl/Cmd/Alt chords belong to the agent, the app and the OS: they end the
  // mode and go through, so Ctrl+C still reaches the pane.
  if (event.ctrlKey || event.metaKey || event.altKey) return { type: "release" };
  const arrow = ARROWS[event.key];
  const letter = letterOf(event);

  if (step.menu === "direction") {
    if (arrow) return { type: "run", action: { kind: "spawn", agent: step.agent, direction: SPLIT_FOR_ARROW[arrow] } };
    if (event.key === "Enter" || event.key === " ") return { type: "run", action: { kind: "spawn", agent: step.agent, direction: null } };
    return back;
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
    return back;
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
    return shifted[letter] ? { type: "run", action: shifted[letter] } : { type: "unknown" };
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
  return { type: "unknown" };
}

/** The one-line mode bar: a badge naming the mode, then the keys that work in it. */
export interface ModeBar { badge: string; hints: HotkeyHint[] }

/** What the bar at the bottom says for a step; the full list lives behind `?`. */
export function modeBar(
  step: IdeHotkeyStep,
  agents: readonly AgentKey[],
  leaderCaps: readonly string[] = ["Ctrl", "B"],
): ModeBar {
  if (step.menu === "direction") {
    return {
      badge: step.label.toUpperCase(),
      hints: [
        { keys: ["→"], label: translate("ide_hotkeys.bar.right") }, { keys: ["←"], label: translate("ide_hotkeys.bar.left") }, { keys: ["↑"], label: translate("ide_hotkeys.bar.above") },
        { keys: ["↓"], label: translate("ide_hotkeys.bar.below") }, { keys: ["Enter"], label: translate("ide_hotkeys.bar.even_grid") }, { keys: ["⌫"], label: translate("ide_hotkeys.bar.back") },
      ],
    };
  }
  if (step.menu === "workspace") {
    return {
      badge: translate("ide_hotkeys.badge.workspace"),
      hints: [
        { keys: ["N"], label: translate("ide_hotkeys.bar.new") }, { keys: ["T"], label: translate("ide_hotkeys.bar.worktree") }, { keys: ["R"], label: translate("ide_hotkeys.bar.rename") },
        { keys: ["Q"], label: translate("ide_hotkeys.bar.close") }, { keys: ["G"], label: translate("ide_hotkeys.bar.git") }, { keys: ["O"], label: translate("ide_hotkeys.bar.options") },
        { keys: ["P"], label: translate("ide_hotkeys.bar.connect_folder") }, { keys: ["←→"], label: translate("ide_hotkeys.bar.switch") }, { keys: ["⌫"], label: translate("ide_hotkeys.bar.back") },
      ],
    };
  }
  if (step.menu === "help") return { badge: translate("ide_hotkeys.badge.keys"), hints: [{ keys: [translate("ide_hotkeys.bar.any_key")], label: translate("ide_hotkeys.bar.back") }] };
  return {
    badge: translate("ide_hotkeys.badge.prefix"),
    hints: [
      ...agents.map((agent) => ({ keys: [agent.key.toUpperCase()], label: agent.label })),
      { keys: ["V", "-"], label: translate("ide_hotkeys.bar.split") },
      { keys: ["←↑→↓"], label: translate("ide_hotkeys.bar.move") },
      { keys: ["Z"], label: translate("ide_hotkeys.bar.zoom") },
      { keys: ["Q"], label: translate("ide_hotkeys.bar.close") },
      { keys: ["W"], label: translate("ide_hotkeys.bar.workspaces") },
      { keys: ["?"], label: translate("ide_hotkeys.bar.all_keys") },
      { keys: [...leaderCaps], label: fill(translate("ide_hotkeys.bar.send_leader"), { keys: leaderCaps.join("+") }) },
    ],
  };
}

/** One row of the key menu as the overlay draws it. */
export interface HotkeyHint { keys: string[]; label: string }
export interface HotkeyHintGroup { title: string; hints: HotkeyHint[] }

/** The rows the overlay shows for a step — the same table `resolveHotkey` reads. */
export function hotkeyHints(
  step: IdeHotkeyStep,
  agents: readonly AgentKey[],
  leaderCaps: readonly string[] = ["Ctrl", "B"],
): HotkeyHintGroup[] {
  if (step.menu === "direction") {
    return [{
      title: fill(translate("ide_hotkeys.help.open_agent"), { agent: step.label }),
      hints: [
        { keys: ["→"], label: translate("ide_hotkeys.help.to_right") },
        { keys: ["←"], label: translate("ide_hotkeys.help.to_left") },
        { keys: ["↑"], label: translate("ide_hotkeys.help.above") },
        { keys: ["↓"], label: translate("ide_hotkeys.help.below") },
        { keys: ["Enter"], label: translate("ide_hotkeys.help.even_grid") },
      ],
    }];
  }
  if (step.menu === "workspace") {
    return [{
      title: translate("ide_hotkeys.help.group_workspace"),
      hints: [
        { keys: ["N"], label: translate("ide_hotkeys.help.new_workspace") },
        { keys: ["T"], label: translate("ide_hotkeys.help.new_worktree_workspace") },
        { keys: ["R"], label: translate("ide_hotkeys.help.rename_workspace") },
        { keys: ["Q"], label: translate("ide_hotkeys.help.close_workspace") },
        { keys: ["G"], label: translate("ide_hotkeys.help.git_panel") },
        { keys: ["O"], label: translate("ide_hotkeys.help.workspace_options") },
        { keys: ["P"], label: translate("ide_hotkeys.help.connect_folder") },
        { keys: ["←", "→"], label: translate("ide_hotkeys.help.prev_next_workspace") },
        { keys: ["1–9"], label: translate("ide_hotkeys.help.go_to_workspace_n") },
      ],
    }];
  }
  return [
    {
      title: translate("ide_hotkeys.help.group_new_pane"),
      hints: [
        ...agents.map((agent) => ({ keys: [agent.key.toUpperCase()], label: fill(translate("ide_hotkeys.help.agent_then_arrow"), { agent: agent.label }) })),
        { keys: ["V"], label: translate("ide_hotkeys.help.split_right_same") },
        { keys: ["-"], label: translate("ide_hotkeys.help.split_below_same") },
        { keys: ["+"], label: translate("ide_hotkeys.help.choose_agent") },
      ],
    },
    {
      title: translate("ide_hotkeys.help.group_panes"),
      hints: [
        { keys: ["←↑→↓"], label: translate("ide_hotkeys.help.focus_neighbor") },
        { keys: ["Shift", "←↑→↓"], label: translate("ide_hotkeys.help.swap_neighbor") },
        { keys: ["Z"], label: translate("ide_hotkeys.help.maximize_restore") },
        { keys: ["R"], label: translate("ide_hotkeys.help.rename_pane") },
        { keys: ["F"], label: translate("ide_hotkeys.help.fork_pane") },
        { keys: ["Q"], label: translate("ide_hotkeys.help.close_pane") },
        { keys: ["E"], label: translate("ide_hotkeys.help.even_layout") },
      ],
    },
    {
      title: translate("ide_hotkeys.help.group_workspaces"),
      hints: [
        { keys: ["W"], label: translate("ide_hotkeys.help.workspace_menu") },
        { keys: ["1–9"], label: translate("ide_hotkeys.help.go_to_workspace") },
        { keys: ["N", "P"], label: translate("ide_hotkeys.help.next_prev_workspace") },
        { keys: ["Shift", "N"], label: translate("ide_hotkeys.help.new_workspace") },
        { keys: ["Shift", "W"], label: translate("ide_hotkeys.help.rename_workspace") },
        { keys: ["Shift", "D"], label: translate("ide_hotkeys.help.close_workspace") },
        { keys: ["M"], label: translate("agentic_grid.voice_bubble.button_label") },
        { keys: [...leaderCaps], label: fill(translate("ide_hotkeys.help.send_leader"), { keys: leaderCaps.join("+") }) },
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
