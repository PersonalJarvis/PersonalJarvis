import { describe, expect, it, vi } from "vitest";

import { installZoomKeyBridge, zoomIntentFor, type ZoomIntent } from "./terminalZoom";

function press(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent("keydown", { cancelable: true, ...init });
}

describe("recognising the zoom chords on Windows and Linux", () => {
  const pc = { isMac: false };

  it("takes Ctrl and the plus key — the reported ask", () => {
    expect(zoomIntentFor(press({ key: "+", ctrlKey: true }), pc)).toBe("in");
  });

  it("takes the US spelling of the same chord, with and without Shift", () => {
    expect(zoomIntentFor(press({ key: "=", ctrlKey: true }), pc)).toBe("in");
    expect(zoomIntentFor(press({ key: "+", ctrlKey: true, shiftKey: true }), pc)).toBe(
      "in",
    );
  });

  it("takes Ctrl and minus, and Ctrl+0 as the way back to the default", () => {
    expect(zoomIntentFor(press({ key: "-", ctrlKey: true }), pc)).toBe("out");
    expect(zoomIntentFor(press({ key: "_", ctrlKey: true }), pc)).toBe("out");
    expect(zoomIntentFor(press({ key: "0", ctrlKey: true }), pc)).toBe("reset");
  });

  it("takes the numeric keypad, which `key` alone stops naming without NumLock", () => {
    // NumLock off: the keypad still reports its code, but `key` is a name the
    // chord table does not contain.
    expect(
      zoomIntentFor(press({ key: "Insert", code: "Numpad0", ctrlKey: true }), pc),
    ).toBe("reset");
    expect(zoomIntentFor(press({ key: "+", code: "NumpadAdd", ctrlKey: true }), pc)).toBe(
      "in",
    );
    expect(
      zoomIntentFor(press({ key: "-", code: "NumpadSubtract", ctrlKey: true }), pc),
    ).toBe("out");
  });

  it("leaves AltGr+plus alone — that is the tilde a German layout types", () => {
    expect(
      zoomIntentFor(press({ key: "~", ctrlKey: true, altKey: true }), pc),
    ).toBeNull();
    expect(
      zoomIntentFor(press({ key: "+", ctrlKey: true, altKey: true }), pc),
    ).toBeNull();
  });

  it("leaves the unmodified key and the wrong modifier alone", () => {
    expect(zoomIntentFor(press({ key: "+" }), pc)).toBeNull();
    // Win+plus is the Windows magnifier, not this.
    expect(zoomIntentFor(press({ key: "+", metaKey: true }), pc)).toBeNull();
  });

  it("leaves every other Ctrl chord alone", () => {
    expect(zoomIntentFor(press({ key: "c", ctrlKey: true }), pc)).toBeNull();
    expect(zoomIntentFor(press({ key: "1", ctrlKey: true }), pc)).toBeNull();
  });
});

describe("recognising the zoom chords on an Apple keyboard", () => {
  const mac = { isMac: true };

  it("takes Cmd and the plus, minus and zero keys", () => {
    expect(zoomIntentFor(press({ key: "+", metaKey: true }), mac)).toBe("in");
    expect(zoomIntentFor(press({ key: "-", metaKey: true }), mac)).toBe("out");
    expect(zoomIntentFor(press({ key: "0", metaKey: true }), mac)).toBe("reset");
  });

  it("leaves Ctrl+minus alone there — inside a terminal that is a control code", () => {
    expect(zoomIntentFor(press({ key: "-", ctrlKey: true }), mac)).toBeNull();
    expect(
      zoomIntentFor(press({ key: "-", metaKey: true, ctrlKey: true }), mac),
    ).toBeNull();
  });
});

/** A window stand-in that hands the captured keystroke back to the test. */
function fakeTarget(): {
  addEventListener: EventTarget["addEventListener"];
  removeEventListener: EventTarget["removeEventListener"];
  fire: (event: KeyboardEvent) => void;
  captured: boolean;
  listeners: number;
} {
  const handlers = new Set<EventListener>();
  const state = {
    addEventListener: ((_type: string, handler: EventListener, options?: unknown) => {
      state.captured = options === true;
      handlers.add(handler);
      state.listeners = handlers.size;
    }) as EventTarget["addEventListener"],
    removeEventListener: ((_type: string, handler: EventListener) => {
      handlers.delete(handler);
      state.listeners = handlers.size;
    }) as EventTarget["removeEventListener"],
    fire: (event: KeyboardEvent) => {
      for (const handler of handlers) handler(event);
    },
    captured: false,
    listeners: 0,
  };
  return state;
}

describe("the zoom key bridge", () => {
  it("claims the chord so neither the WebView nor the pane also acts on it", () => {
    const target = fakeTarget();
    const applied: ZoomIntent[] = [];
    installZoomKeyBridge(target, {
      isMac: false,
      enabled: () => true,
      apply: (intent) => applied.push(intent),
    });

    const event = press({ key: "+", ctrlKey: true });
    const stopPropagation = vi.spyOn(event, "stopPropagation");
    target.fire(event);

    expect(applied).toEqual(["in"]);
    expect(event.defaultPrevented).toBe(true);
    expect(stopPropagation).toHaveBeenCalled();
    // Ahead of every pane's own key handling, which is the only phase that can
    // see a keystroke typed into an xterm textarea.
    expect(target.captured).toBe(true);
  });

  it("lets everything else through untouched", () => {
    const target = fakeTarget();
    const apply = vi.fn();
    installZoomKeyBridge(target, { isMac: false, enabled: () => true, apply });

    const event = press({ key: "a" });
    target.fire(event);

    expect(apply).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it("stays out of the way while the workspace is not the section on screen", () => {
    const target = fakeTarget();
    const apply = vi.fn();
    let onScreen = false;
    installZoomKeyBridge(target, { isMac: false, enabled: () => onScreen, apply });

    const hidden = press({ key: "+", ctrlKey: true });
    target.fire(hidden);
    expect(apply).not.toHaveBeenCalled();
    expect(hidden.defaultPrevented).toBe(false);

    onScreen = true;
    target.fire(press({ key: "+", ctrlKey: true }));
    expect(apply).toHaveBeenCalledWith("in");
  });

  it("removes its listener on cleanup", () => {
    const target = fakeTarget();
    const dispose = installZoomKeyBridge(target, {
      isMac: false,
      enabled: () => true,
      apply: vi.fn(),
    });
    expect(target.listeners).toBe(1);
    dispose();
    expect(target.listeners).toBe(0);
  });
});
