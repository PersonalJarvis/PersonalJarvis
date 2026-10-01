import { describe, expect, it } from "vitest";
import { installMouseSelection } from "./terminalMouseSelection";

/** Dispatch a press on a child of `container` and read what xterm would see. */
function press(
  container: HTMLElement,
  init: MouseEventInit,
): { shiftKey: boolean; altKey: boolean } {
  const inner = document.createElement("div");
  container.appendChild(inner);
  let seen = { shiftKey: false, altKey: false };
  inner.addEventListener("mousedown", (event) => {
    seen = { shiftKey: event.shiftKey, altKey: event.altKey };
  });
  inner.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, ...init }));
  inner.remove();
  return seen;
}

function setup(tracking: string, isMac = false) {
  const container = document.createElement("div");
  const dispose = installMouseSelection(
    container,
    { modes: { mouseTrackingMode: tracking } },
    isMac,
  );
  return { container, dispose };
}

describe("installMouseSelection", () => {
  it("marks a plain left press as xterm's force-selection chord while tracking is on", () => {
    const { container } = setup("any");
    expect(press(container, { button: 0 })).toEqual({ shiftKey: true, altKey: false });
  });

  it("uses Option on macOS", () => {
    const { container } = setup("drag", true);
    expect(press(container, { button: 0 })).toEqual({ shiftKey: false, altKey: true });
  });

  it("leaves presses alone when the CLI has no mouse tracking", () => {
    const { container } = setup("none");
    expect(press(container, { button: 0 })).toEqual({ shiftKey: false, altKey: false });
  });

  it("keeps the right button and modified presses for the CLI", () => {
    const { container } = setup("any");
    expect(press(container, { button: 2 })).toEqual({ shiftKey: false, altKey: false });
    expect(press(container, { button: 0, ctrlKey: true })).toEqual({
      shiftKey: false,
      altKey: false,
    });
    expect(press(container, { button: 0, altKey: true })).toEqual({
      shiftKey: false,
      altKey: true,
    });
  });

  it("stops marking once disposed", () => {
    const { container, dispose } = setup("any");
    dispose();
    expect(press(container, { button: 0 })).toEqual({ shiftKey: false, altKey: false });
  });
});
