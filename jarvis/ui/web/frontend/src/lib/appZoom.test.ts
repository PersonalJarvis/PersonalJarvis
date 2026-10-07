import { describe, expect, it } from "vitest";

import {
  appZoomCaps,
  appZoomComboFromEvent,
  appZoomComboProblem,
  appZoomIntentFor,
  defaultAppZoomBindings,
  nextAppZoom,
  snapAppZoom,
  type AppZoomKeyEvent,
} from "./appZoom";

const PC = defaultAppZoomBindings("pc");
const MAC = defaultAppZoomBindings("mac");

function key(init: Partial<AppZoomKeyEvent> & Pick<AppZoomKeyEvent, "key" | "code">): AppZoomKeyEvent {
  return { ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...init };
}

describe("the default chords on a German keyboard", () => {
  it("zooms in with the Plus key, which US calls BracketRight", () => {
    expect(appZoomIntentFor(key({ key: "+", code: "BracketRight", ctrlKey: true }), PC)).toBe("in");
  });

  it("zooms out with the Minus key, which US calls Slash", () => {
    expect(appZoomIntentFor(key({ key: "-", code: "Slash", ctrlKey: true }), PC)).toBe("out");
  });

  it("does not read Ctrl+Shift+0 (the = sign there) as zoom in or reset", () => {
    expect(appZoomIntentFor(key({ key: "=", code: "Digit0", ctrlKey: true, shiftKey: true }), PC)).toBeNull();
  });

  it("ignores AltGr+Plus, the tilde", () => {
    const altGr = key({ key: "~", code: "BracketRight", ctrlKey: true, altKey: true });
    expect(appZoomIntentFor(altGr, PC)).toBeNull();
    const reported = { ...key({ key: "+", code: "BracketRight", ctrlKey: true }), getModifierState: (k: string) => k === "AltGraph" };
    expect(appZoomIntentFor(reported, PC)).toBeNull();
  });
});

describe("the default chords on a US keyboard", () => {
  it("zooms in with Ctrl+= and with Ctrl+Shift+= (the + sign)", () => {
    expect(appZoomIntentFor(key({ key: "=", code: "Equal", ctrlKey: true }), PC)).toBe("in");
    expect(appZoomIntentFor(key({ key: "+", code: "Equal", ctrlKey: true, shiftKey: true }), PC)).toBe("in");
  });

  it("zooms out with Ctrl+- and Ctrl+_", () => {
    expect(appZoomIntentFor(key({ key: "-", code: "Minus", ctrlKey: true }), PC)).toBe("out");
    expect(appZoomIntentFor(key({ key: "_", code: "Minus", ctrlKey: true, shiftKey: true }), PC)).toBe("out");
  });

  it("resets with Ctrl+0 but not Ctrl+Shift+0", () => {
    expect(appZoomIntentFor(key({ key: "0", code: "Digit0", ctrlKey: true }), PC)).toBe("reset");
    expect(appZoomIntentFor(key({ key: ")", code: "Digit0", ctrlKey: true, shiftKey: true }), PC)).toBeNull();
  });
});

describe("the numeric keypad", () => {
  it("counts on every layout, NumLock or not", () => {
    expect(appZoomIntentFor(key({ key: "+", code: "NumpadAdd", ctrlKey: true }), PC)).toBe("in");
    expect(appZoomIntentFor(key({ key: "-", code: "NumpadSubtract", ctrlKey: true }), PC)).toBe("out");
    expect(appZoomIntentFor(key({ key: "0", code: "Numpad0", ctrlKey: true }), PC)).toBe("reset");
    expect(appZoomIntentFor(key({ key: "Insert", code: "NumpadInsert", ctrlKey: true }), PC)).toBe("reset");
  });
});

describe("the platform modifier", () => {
  it("is Command on a Mac and Ctrl elsewhere", () => {
    expect(appZoomIntentFor(key({ key: "=", code: "Equal", metaKey: true }), MAC)).toBe("in");
    expect(appZoomIntentFor(key({ key: "=", code: "Equal", ctrlKey: true }), MAC)).toBeNull();
    expect(appZoomIntentFor(key({ key: "+", code: "BracketRight", metaKey: true }), PC)).toBeNull();
  });

  it("plain typing never zooms", () => {
    expect(appZoomIntentFor(key({ key: "+", code: "BracketRight" }), PC)).toBeNull();
  });
});

describe("recording", () => {
  it("stores Plus and Minus as characters, without the Shift a layout needs", () => {
    expect(appZoomComboFromEvent(key({ key: "+", code: "Equal", ctrlKey: true, shiftKey: true }), "pc")).toBe("ctrl+plus");
    expect(appZoomComboFromEvent(key({ key: "-", code: "Slash", ctrlKey: true }), "pc")).toBe("ctrl+minus");
  });

  it("stores letters by position and keeps Shift", () => {
    expect(appZoomComboFromEvent(key({ key: "Z", code: "KeyZ", ctrlKey: true, shiftKey: true }), "pc")).toBe("ctrl+shift+z");
  });

  it("waits while only modifiers are held", () => {
    expect(appZoomComboFromEvent(key({ key: "Control", code: "ControlLeft", ctrlKey: true }), "pc")).toBeNull();
  });

  it("a recorded custom chord matches back", () => {
    const combo = appZoomComboFromEvent(key({ key: "ArrowUp", code: "ArrowUp", altKey: true }), "pc");
    expect(appZoomIntentFor(key({ key: "ArrowUp", code: "ArrowUp", altKey: true }), { ...PC, in: combo! })).toBe("in");
  });

  it("refuses typing keys, the Windows key and duplicates", () => {
    expect(appZoomComboProblem("plus", [], "pc")).toBe("typing_key");
    expect(appZoomComboProblem("f9", [], "pc")).toBeNull();
    expect(appZoomComboProblem("win+plus", [], "pc")).toBe("os_reserved");
    expect(appZoomComboProblem("cmd+plus", [], "mac")).toBeNull();
    expect(appZoomComboProblem("ctrl+minus", ["control+minus"], "pc")).toBe("duplicate");
  });
});

describe("levels", () => {
  it("steps along the browser ladder and stops at its ends", () => {
    expect(nextAppZoom(1, "in")).toBe(1.1);
    expect(nextAppZoom(1, "out")).toBe(0.9);
    expect(nextAppZoom(3, "in")).toBe(3);
    expect(nextAppZoom(0.5, "out")).toBe(0.5);
    expect(nextAppZoom(1.75, "reset")).toBe(1);
  });

  it("snaps a stray stored value", () => {
    expect(snapAppZoom(1.12)).toBe(1.1);
    expect(snapAppZoom(Number.NaN)).toBe(1);
  });
});

it("draws the chord as keycaps", () => {
  expect(appZoomCaps("ctrl+plus", "pc")).toEqual(["Ctrl", "+"]);
  expect(appZoomCaps("cmd+minus", "mac")).toEqual(["⌘", "-"]);
});
