import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/society/en.json";
import { arrowCap, CONTROL_GROUPS, controlsFor, mouseGesture, OFFICE_CONTROLS } from "./officeControls";
import { isRunning } from "./officeSettings";

const guide = (en as { society: { office: { guide: Record<string, string> } } }).society.office.guide;

describe("office controls guide", () => {
  it("names every control, group and mouse gesture in the locale", () => {
    for (const group of CONTROL_GROUPS) expect(guide[`group_${group}`]).toBeTruthy();
    for (const control of OFFICE_CONTROLS) {
      expect(guide[`ctl_${control.id}`], control.id).toBeTruthy();
      for (const cap of control.keys.flat()) {
        const gesture = mouseGesture(cap);
        if (gesture) expect(guide[`mouse_${gesture}`], cap).toBeTruthy();
      }
    }
  });

  it("keeps control ids unique and every group filled", () => {
    const ids = OFFICE_CONTROLS.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const group of CONTROL_GROUPS) expect(controlsFor(group, "coding").length).toBeGreaterThan(0);
  });

  it("drops the lead chair on the coding floor", () => {
    expect(controlsFor("interact", "agents").some((c) => c.id === "sit")).toBe(true);
    expect(controlsFor("interact", "coding").some((c) => c.id === "sit")).toBe(false);
  });

  it("lists the hotkeys the office handles", () => {
    const caps = new Set(OFFICE_CONTROLS.flatMap((c) => c.keys.flat()));
    for (const key of ["W", "ArrowUp", "Shift", "E", "T", "M", "H", "Esc", "Space", "P"]) expect(caps.has(key), key).toBe(true);
  });
});

describe("always run", () => {
  it("makes Shift walk instead of run", () => {
    expect(isRunning(false, false)).toBe(false);
    expect(isRunning(true, false)).toBe(true);
    expect(isRunning(false, true)).toBe(true);
    expect(isRunning(true, true)).toBe(false);
  });
});

describe("arrow caps", () => {
  it("names each arrow's direction and nothing else", () => {
    expect(arrowCap("ArrowUp")).toBe("up");
    expect(arrowCap("ArrowRight")).toBe("right");
    expect(arrowCap("W")).toBeNull();
    for (const dir of ["up", "down", "left", "right"]) expect(guide[`key_${dir}`]).toBeTruthy();
  });
});
