import { describe, expect, it, vi } from "vitest";

import {
  installZoomKeyBridge,
  installZoomWheelBridge,
  zoomIntentFor,
  zoomIntentForWheel,
  type ZoomIntent,
} from "./terminalZoom";

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

function turn(init: WheelEventInit): WheelEvent {
  return new WheelEvent("wheel", { cancelable: true, ...init });
}

describe("recognising Ctrl+wheel as a text-size gesture", () => {
  it("reads a notch away from the user as bigger, and towards it as smaller", () => {
    expect(zoomIntentForWheel(turn({ deltaY: -120, ctrlKey: true }))).toBe("in");
    expect(zoomIntentForWheel(turn({ deltaY: 120, ctrlKey: true }))).toBe("out");
  });

  /*
   * The one deliberate divergence from the KEY chord table, which refuses Ctrl
   * on macOS. A pinch is delivered as a synthetic Ctrl+wheel with no key held,
   * so requiring Cmd would leave the gesture a Mac user reaches for first doing
   * nothing at all. See the note on `zoomIntentForWheel`.
   */
  it("takes the macOS trackpad pinch, which arrives as a synthetic Ctrl+wheel", () => {
    expect(zoomIntentForWheel(turn({ deltaY: -8, ctrlKey: true }))).toBe("in");
  });

  it("leaves an ordinary scroll alone — that is the pane's own history", () => {
    expect(zoomIntentForWheel(turn({ deltaY: -120 }))).toBeNull();
  });

  it("leaves the system gestures alone: AltGr, and Win/Cmd + wheel", () => {
    expect(
      zoomIntentForWheel(turn({ deltaY: -120, ctrlKey: true, altKey: true })),
    ).toBeNull();
    // Win+wheel is the Windows magnifier; Cmd+scroll is a zoom in no macOS app.
    expect(
      zoomIntentForWheel(turn({ deltaY: -120, ctrlKey: true, metaKey: true })),
    ).toBeNull();
    expect(zoomIntentForWheel(turn({ deltaY: -120, metaKey: true }))).toBeNull();
  });

  it("ignores a horizontal-only gesture rather than guessing a direction", () => {
    expect(zoomIntentForWheel(turn({ deltaY: 0, ctrlKey: true }))).toBeNull();
  });
});

/** A pane surface stand-in that records how the listener was registered. */
function fakeWheelTarget(): {
  addEventListener: EventTarget["addEventListener"];
  removeEventListener: EventTarget["removeEventListener"];
  fire: (event: WheelEvent) => void;
  options: AddEventListenerOptions | undefined;
  listeners: number;
} {
  const handlers = new Set<EventListener>();
  const state = {
    addEventListener: ((_type: string, handler: EventListener, options?: unknown) => {
      state.options = options as AddEventListenerOptions;
      handlers.add(handler);
      state.listeners = handlers.size;
    }) as EventTarget["addEventListener"],
    removeEventListener: ((_type: string, handler: EventListener) => {
      handlers.delete(handler);
      state.listeners = handlers.size;
    }) as EventTarget["removeEventListener"],
    fire: (event: WheelEvent) => {
      for (const handler of handlers) handler(event);
    },
    options: undefined as AddEventListenerOptions | undefined,
    listeners: 0,
  };
  return state;
}

describe("the zoom wheel bridge", () => {
  it("steps once per mouse notch and claims the gesture from the WebView", () => {
    const target = fakeWheelTarget();
    const applied: ZoomIntent[] = [];
    installZoomWheelBridge(target, { apply: (intent) => applied.push(intent) });

    const event = turn({ deltaY: -120, ctrlKey: true });
    const stopPropagation = vi.spyOn(event, "stopPropagation");
    target.fire(event);

    expect(applied).toEqual(["in"]);
    expect(event.defaultPrevented).toBe(true);
    expect(stopPropagation).toHaveBeenCalled();
    // Ahead of xterm's own wheel handling and of the pane's scroll containment,
    // and cancellable — a passive listener could not stop the page zoom.
    expect(target.options).toMatchObject({ capture: true, passive: false });
  });

  /*
   * The whole reason the bridge accumulates. A pinch reports a stream of small
   * deltas, and one step each took a pane across the full 10-20 px range in a
   * single gesture.
   */
  it("accumulates a trackpad pinch into one step per threshold of travel", () => {
    const target = fakeWheelTarget();
    const applied: ZoomIntent[] = [];
    installZoomWheelBridge(target, { apply: (intent) => applied.push(intent) });

    for (let i = 0; i < 3; i += 1) {
      const partial = turn({ deltaY: -10, ctrlKey: true });
      target.fire(partial);
      // Cancelled on the way to the threshold too, or the app page-zooms
      // underneath the pane while the fingers are still moving.
      expect(partial.defaultPrevented).toBe(true);
    }
    expect(applied).toEqual([]);

    target.fire(turn({ deltaY: -10, ctrlKey: true }));
    expect(applied).toEqual(["in"]);
  });

  it("spends nothing it banked the other way when the gesture turns around", () => {
    const target = fakeWheelTarget();
    const applied: ZoomIntent[] = [];
    installZoomWheelBridge(target, { apply: (intent) => applied.push(intent) });

    target.fire(turn({ deltaY: -30, ctrlKey: true }));
    // A correction takes effect on its own travel, rather than crossing the
    // threshold instantly on the back of the 30 px spent going the other way.
    target.fire(turn({ deltaY: 30, ctrlKey: true }));
    expect(applied).toEqual([]);

    target.fire(turn({ deltaY: 10, ctrlKey: true }));
    expect(applied).toEqual(["out"]);
  });

  it("steps immediately for a device that reports lines rather than pixels", () => {
    const target = fakeWheelTarget();
    const applied: ZoomIntent[] = [];
    installZoomWheelBridge(target, { apply: (intent) => applied.push(intent) });

    // deltaMode 1 = DOM_DELTA_LINE: the device has already quantised it.
    target.fire(turn({ deltaY: -1, deltaMode: 1, ctrlKey: true }));
    expect(applied).toEqual(["in"]);
  });

  it("never steps more than once for one coalesced burst", () => {
    const target = fakeWheelTarget();
    const applied: ZoomIntent[] = [];
    installZoomWheelBridge(target, { apply: (intent) => applied.push(intent) });

    // Four thresholds' worth in a single event. A size that jumps four steps
    // reads as having been yanked rather than adjusted.
    target.fire(turn({ deltaY: -160, ctrlKey: true }));
    expect(applied).toEqual(["in"]);
  });

  it("hands an ordinary scroll straight on to the terminal's history", () => {
    const target = fakeWheelTarget();
    const apply = vi.fn();
    installZoomWheelBridge(target, { apply });

    const event = turn({ deltaY: -120 });
    target.fire(event);

    expect(apply).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it("stands down while the surface it belongs to is not being looked at", () => {
    const target = fakeWheelTarget();
    const apply = vi.fn();
    let onScreen = false;
    installZoomWheelBridge(target, { enabled: () => onScreen, apply });

    const hidden = turn({ deltaY: -120, ctrlKey: true });
    target.fire(hidden);
    expect(apply).not.toHaveBeenCalled();
    expect(hidden.defaultPrevented).toBe(false);

    onScreen = true;
    target.fire(turn({ deltaY: -120, ctrlKey: true }));
    expect(apply).toHaveBeenCalledWith("in");
  });

  it("removes its listener on cleanup", () => {
    const target = fakeWheelTarget();
    const dispose = installZoomWheelBridge(target, { apply: vi.fn() });
    expect(target.listeners).toBe(1);
    dispose();
    expect(target.listeners).toBe(0);
  });
});
