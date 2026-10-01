/**
 * Every way to act in the office, in one list: the reception's controls guide
 * renders it, and a new hotkey is added here next to its handler so the guide
 * never goes stale. A binding is a list of alternatives; each alternative is a
 * row of key caps ("+" joins a chord, e.g. Ctrl + drag). Caps starting with
 * "mouse:" are mouse gestures and get a translated label, "Arrow…" caps are
 * drawn as arrows; every other cap is printed as is.
 */

export type ControlGroup = "move" | "camera" | "interact" | "map" | "arcade";

export interface OfficeControl {
  id: string;
  group: ControlGroup;
  keys: readonly (readonly string[])[];
  /** Only on the agents floor (the coding floor has no lead chair). */
  agentsOnly?: boolean;
}

export const CONTROL_GROUPS: readonly ControlGroup[] = ["move", "camera", "interact", "map", "arcade"];

export type MouseGesture = "click" | "double" | "drag" | "right_drag" | "wheel";

export const OFFICE_CONTROLS: readonly OfficeControl[] = [
  { id: "walk", group: "move", keys: [["W", "A", "S", "D"], ["ArrowUp", "ArrowLeft", "ArrowDown", "ArrowRight"]] },
  { id: "run", group: "move", keys: [["Shift"]] },
  { id: "jump", group: "move", keys: [["Space"]] },
  { id: "sprint_jump", group: "move", keys: [["Shift", "+", "Space"]] },
  { id: "click_walk", group: "move", keys: [["mouse:click"]] },
  { id: "rotate", group: "camera", keys: [["mouse:drag"]] },
  { id: "pan", group: "camera", keys: [["mouse:right_drag"], ["Ctrl/⌘", "+", "mouse:drag"]] },
  { id: "zoom", group: "camera", keys: [["mouse:wheel"]] },
  { id: "interact", group: "interact", keys: [["E"]] },
  { id: "talk", group: "interact", keys: [["T"]] },
  { id: "sit", group: "interact", keys: [["E"]], agentsOnly: true },
  { id: "screen", group: "interact", keys: [["mouse:click"]] },
  { id: "close", group: "interact", keys: [["Esc"]] },
  { id: "map", group: "map", keys: [["M"]] },
  { id: "guide", group: "map", keys: [["H"]] },
  { id: "minimap_focus", group: "map", keys: [["mouse:click"]] },
  { id: "minimap_walk", group: "map", keys: [["mouse:double"]] },
  { id: "minimap_zoom", group: "map", keys: [["mouse:wheel"]] },
  { id: "arcade_fly", group: "arcade", keys: [["W", "A", "S", "D"], ["ArrowUp", "ArrowLeft", "ArrowDown", "ArrowRight"]] },
  { id: "arcade_shoot", group: "arcade", keys: [["Space"]] },
  { id: "arcade_pause", group: "arcade", keys: [["P"]] },
  { id: "arcade_leave", group: "arcade", keys: [["E"], ["Esc"]] },
];

/** The mouse gesture a cap names, or null for a plain key. */
export function mouseGesture(cap: string): MouseGesture | null {
  return cap.startsWith("mouse:") ? (cap.slice(6) as MouseGesture) : null;
}

export type ArrowKey = "up" | "down" | "left" | "right";

/** The direction an "Arrow…" cap names, or null for any other cap. */
export function arrowCap(cap: string): ArrowKey | null {
  const m = /^Arrow(Up|Down|Left|Right)$/.exec(cap);
  return m ? (m[1]!.toLowerCase() as ArrowKey) : null;
}

/** The controls of one group that apply on this floor. */
export function controlsFor(group: ControlGroup, floor: "agents" | "coding"): OfficeControl[] {
  return OFFICE_CONTROLS.filter((c) => c.group === group && (!c.agentsOnly || floor === "agents"));
}
