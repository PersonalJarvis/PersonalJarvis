import { describe, expect, it } from "vitest";
import {
  LEADER_PASSTHROUGH, RESERVED_ROOT_KEYS, assignAgentKeys, hotkeyHints, isLeaderChord, modeBar, neighborInDirection, resolveHotkey,
  type HotkeyEventLike, type IdeHotkeyStep,
} from "./ideHotkeys";

const press = (key: string, extra: Partial<HotkeyEventLike> = {}): HotkeyEventLike => ({
  key, code: /^[a-z]$/i.test(key) ? `Key${key.toUpperCase()}` : "", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...extra,
});

const AGENTS = assignAgentKeys([
  { name: "claude", label: "Claude Code" },
  { name: "codex", label: "Codex" },
  { name: "opencode", label: "OpenCode" },
  { name: "grok-build", label: "Grok Build" },
]);
const ROOT: IdeHotkeyStep = { menu: "root" };

describe("leader chord", () => {
  it("is Ctrl+B, and only Ctrl+B", () => {
    expect(isLeaderChord(press("b", { ctrlKey: true }))).toBe(true);
    expect(isLeaderChord(press("B", { ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(isLeaderChord(press("b", { metaKey: true }))).toBe(false);
    expect(isLeaderChord(press("b", { ctrlKey: true, altKey: true }))).toBe(false);
  });

  it("matches the physical key, whatever the layout prints on it", () => {
    expect(isLeaderChord({ ...press("b", { ctrlKey: true }), key: "∫" })).toBe(true);
  });

  it("passes through as the control code Ctrl+B sends", () => {
    expect(LEADER_PASSTHROUGH).toBe(String.fromCharCode(2));
  });
});

describe("agent letters", () => {
  it("gives Claude C and Codex X so the two C agents never collide", () => {
    expect(AGENTS.find((agent) => agent.name === "claude")?.key).toBe("c");
    expect(AGENTS.find((agent) => agent.name === "codex")?.key).toBe("x");
  });

  it("never hands an agent a letter the root menu uses for a command", () => {
    const many = assignAgentKeys("abcdefghijklmnopqrstuvwxyz".split("").map((letter) => ({ name: `${letter}-agent`, label: letter })));
    for (const agent of many) expect(RESERVED_ROOT_KEYS.has(agent.key)).toBe(false);
    expect(new Set(many.map((agent) => agent.key)).size).toBe(many.length);
  });

  it("gives an unknown CLI the first free letter of its own name", () => {
    const [agent] = assignAgentKeys([{ name: "zed-agent", label: "Zed" }]);
    expect(agent.key).toBe("d"); // z is reserved (maximize), e is reserved (even out)
  });
});

describe("key sequences", () => {
  it("C then → opens Claude Code to the right of the focused pane", () => {
    const first = resolveHotkey(ROOT, press("c"), AGENTS);
    expect(first).toEqual({ type: "step", step: { menu: "direction", agent: "claude", label: "Claude Code" } });
    if (first.type !== "step") throw new Error("expected a step");
    expect(resolveHotkey(first.step, press("ArrowRight"), AGENTS)).toEqual({ type: "run", action: { kind: "spawn", agent: "claude", direction: "right" } });
  });

  it("X then ↓ opens Codex below; Enter joins the even grid", () => {
    const step: IdeHotkeyStep = { menu: "direction", agent: "codex", label: "Codex" };
    expect(resolveHotkey(step, press("ArrowDown"), AGENTS)).toEqual({ type: "run", action: { kind: "spawn", agent: "codex", direction: "down" } });
    expect(resolveHotkey(step, press("ArrowUp"), AGENTS)).toEqual({ type: "run", action: { kind: "spawn", agent: "codex", direction: "above" } });
    expect(resolveHotkey(step, press("Enter"), AGENTS)).toEqual({ type: "run", action: { kind: "spawn", agent: "codex", direction: null } });
  });

  it("W then N starts a new workspace", () => {
    const first = resolveHotkey(ROOT, press("w"), AGENTS);
    expect(first).toEqual({ type: "step", step: { menu: "workspace" } });
    expect(resolveHotkey({ menu: "workspace" }, press("n"), AGENTS)).toEqual({ type: "run", action: { kind: "new-workspace" } });
  });

  it("arrows focus a neighbor; Shift+arrows swap with it", () => {
    expect(resolveHotkey(ROOT, press("ArrowLeft"), AGENTS)).toEqual({ type: "run", action: { kind: "focus-pane", direction: "left" } });
    expect(resolveHotkey(ROOT, press("ArrowLeft", { shiftKey: true }), AGENTS)).toEqual({ type: "run", action: { kind: "swap-pane", direction: "left" } });
  });

  it("digits jump to a workspace by position, Shift+digit included", () => {
    expect(resolveHotkey(ROOT, { ...press("!"), code: "Digit1", shiftKey: true }, AGENTS)).toEqual({ type: "run", action: { kind: "workspace-index", index: 0 } });
    expect(resolveHotkey(ROOT, { ...press("3"), code: "Digit3" }, AGENTS)).toEqual({ type: "run", action: { kind: "workspace-index", index: 2 } });
  });

  it("Escape closes, Backspace steps back, a modifier alone waits", () => {
    expect(resolveHotkey({ menu: "workspace" }, press("Escape"), AGENTS)).toEqual({ type: "close" });
    expect(resolveHotkey({ menu: "workspace" }, press("Backspace"), AGENTS)).toEqual({ type: "step", step: { menu: "root" } });
    expect(resolveHotkey(ROOT, press("Shift", { shiftKey: true }), AGENTS)).toEqual({ type: "ignore" });
  });

  it("swallows an unknown key, releases a Ctrl chord, and backs out of a sub-menu", () => {
    expect(resolveHotkey(ROOT, press("j"), AGENTS)).toEqual({ type: "unknown" });
    expect(resolveHotkey(ROOT, press("c", { ctrlKey: true }), AGENTS)).toEqual({ type: "release" });
    expect(resolveHotkey({ menu: "workspace" }, press("j"), AGENTS)).toEqual({ type: "step", step: { menu: "root" } });
    expect(resolveHotkey({ menu: "help" }, press("j"), AGENTS)).toEqual({ type: "step", step: { menu: "root" } });
  });

  it("speaks herdr's keys: V and - split, N/P switch, Shift+N/W/D manage workspaces", () => {
    expect(resolveHotkey(ROOT, press("v"), AGENTS)).toEqual({ type: "run", action: { kind: "split", direction: "right" } });
    expect(resolveHotkey(ROOT, { ...press("-"), code: "Minus" }, AGENTS)).toEqual({ type: "run", action: { kind: "split", direction: "down" } });
    expect(resolveHotkey(ROOT, press("n"), AGENTS)).toEqual({ type: "run", action: { kind: "workspace-step", step: 1 } });
    expect(resolveHotkey(ROOT, press("p"), AGENTS)).toEqual({ type: "run", action: { kind: "workspace-step", step: -1 } });
    expect(resolveHotkey(ROOT, press("N", { shiftKey: true }), AGENTS)).toEqual({ type: "run", action: { kind: "new-workspace" } });
    expect(resolveHotkey(ROOT, press("W", { shiftKey: true }), AGENTS)).toEqual({ type: "run", action: { kind: "rename-workspace" } });
    expect(resolveHotkey(ROOT, press("D", { shiftKey: true }), AGENTS)).toEqual({ type: "run", action: { kind: "close-workspace" } });
    expect(resolveHotkey(ROOT, { ...press("?"), shiftKey: true }, AGENTS)).toEqual({ type: "step", step: { menu: "help" } });
  });

  it("the mode bar names the mode and the agent letters", () => {
    const bar = modeBar(ROOT, AGENTS);
    expect(bar.badge).toBe("PREFIX");
    expect(bar.hints.map((hint) => hint.keys.join("+"))).toEqual(expect.arrayContaining(["C", "X", "?", "Ctrl+B"]));
    expect(modeBar({ menu: "direction", agent: "codex", label: "Codex" }, AGENTS).badge).toBe("CODEX");
  });

  it("lists every agent letter and every command it accepts", () => {
    const root = hotkeyHints(ROOT, AGENTS).flatMap((group) => group.hints.flatMap((hint) => hint.keys));
    for (const agent of AGENTS) expect(root).toContain(agent.key.toUpperCase());
    for (const letter of RESERVED_ROOT_KEYS) expect(root).toContain(letter.toUpperCase());
  });
});

describe("neighborInDirection", () => {
  // Two columns; the right column is split into two rows.
  const boxes = [
    { x: 0, y: 0, w: 0.5, h: 1 },
    { x: 0.5, y: 0, w: 0.5, h: 0.5 },
    { x: 0.5, y: 0.5, w: 0.5, h: 0.5 },
  ];

  it("finds the pane that lies in each direction", () => {
    expect(neighborInDirection(boxes, 0, "right")).toBe(1);
    expect(neighborInDirection(boxes, 1, "down")).toBe(2);
    expect(neighborInDirection(boxes, 2, "up")).toBe(1);
    expect(neighborInDirection(boxes, 2, "left")).toBe(0);
  });

  it("answers null at the edge of the grid", () => {
    expect(neighborInDirection(boxes, 0, "left")).toBeNull();
    expect(neighborInDirection(boxes, 1, "up")).toBeNull();
  });
});
