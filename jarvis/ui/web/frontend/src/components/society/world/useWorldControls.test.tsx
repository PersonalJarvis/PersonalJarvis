/**
 * Dragging the island must never paint its signs blue.
 *
 * The labels over the houses and the HUD are DOM on top of the canvas, so a
 * held left button used to start a text selection across them — which also
 * made the browser cancel the pointer stream and drop the pan halfway. The
 * guard below is the contract: while the button is down nothing may start a
 * selection, and the moment it is released the page is readable again.
 */
import { useRef } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useWorldControls } from "./useWorldControls";

function Stage() {
  const ref = useRef<HTMLDivElement>(null);
  useWorldControls(ref, true);
  return <div ref={ref} data-testid="stage" tabIndex={0} />;
}

/** jsdom ships no PointerEvent; the hook only reads `button` and `pointerId`. */
function pointer(type: string): Event {
  return new MouseEvent(type, { button: 0, bubbles: true, cancelable: true });
}

/** Did a selection get to start? */
function selectionStarts(): boolean {
  const event = new Event("selectstart", { bubbles: true, cancelable: true });
  document.dispatchEvent(event);
  return !event.defaultPrevented;
}

afterEach(cleanup);

describe("useWorldControls", () => {
  it("blocks text selection while the left button is held, and only then", () => {
    const { getByTestId } = render(<Stage />);
    const host = getByTestId("stage");

    expect(selectionStarts()).toBe(true);

    host.dispatchEvent(pointer("pointerdown"));
    expect(selectionStarts()).toBe(false);

    host.dispatchEvent(pointer("pointerup"));
    expect(selectionStarts()).toBe(true);
  });

  it("releases the guard when a drag is cancelled, not only on a clean release", () => {
    const { getByTestId } = render(<Stage />);
    const host = getByTestId("stage");

    host.dispatchEvent(pointer("pointerdown"));
    expect(selectionStarts()).toBe(false);

    host.dispatchEvent(pointer("pointercancel"));
    expect(selectionStarts()).toBe(true);
  });

  it("leaves the page selectable after the stage unmounts mid-drag", () => {
    const { getByTestId, unmount } = render(<Stage />);
    getByTestId("stage").dispatchEvent(pointer("pointerdown"));

    unmount();

    expect(selectionStarts()).toBe(true);
  });
});
