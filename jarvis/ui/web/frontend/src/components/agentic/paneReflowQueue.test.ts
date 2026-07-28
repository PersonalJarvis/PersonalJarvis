import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { cancelPaneReflow, queuePaneReflow } from "./paneReflowQueue";

/**
 * The queue exists so a workspace-wide relayout cannot block the thread in one
 * go — see the module docstring for the incident. These pin the two properties
 * that gives it: reflows land one FRAME apart, and a pane that goes away takes
 * its pending reflow with it.
 */
describe("pane reflow queue", () => {
  /** Frames the test drives by hand, so ordering is asserted rather than raced. */
  let frames: FrameRequestCallback[];

  beforeEach(() => {
    frames = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** Run every frame currently queued (a reflow may schedule the next one). */
  const runFrame = () => {
    const due = frames;
    frames = [];
    due.forEach((callback) => callback(0));
  };

  it("runs one pane per frame instead of the whole grid at once", () => {
    const order: string[] = [];
    queuePaneReflow(() => order.push("T1"));
    queuePaneReflow(() => order.push("T2"));
    queuePaneReflow(() => order.push("T3"));

    // Nothing has run yet: queueing is not doing.
    expect(order).toEqual([]);

    runFrame();
    expect(order).toEqual(["T1"]);
    runFrame();
    expect(order).toEqual(["T1", "T2"]);
    runFrame();
    expect(order).toEqual(["T1", "T2", "T3"]);
  });

  it("ignores a second request for a pane that is already waiting", () => {
    const reflow = vi.fn();
    queuePaneReflow(reflow);
    queuePaneReflow(reflow);
    queuePaneReflow(reflow);

    runFrame();
    runFrame();

    // Once, not three times: the extra requests would measure the same element
    // and arrive at the same size a frame later.
    expect(reflow).toHaveBeenCalledTimes(1);
  });

  it("lets a pane be queued again once its reflow has run", () => {
    const reflow = vi.fn();
    queuePaneReflow(reflow);
    runFrame();
    queuePaneReflow(reflow);
    runFrame();

    expect(reflow).toHaveBeenCalledTimes(2);
  });

  it("drops the pending reflow of a pane that was closed", () => {
    const closed = vi.fn();
    const kept = vi.fn();
    queuePaneReflow(closed);
    queuePaneReflow(kept);

    cancelPaneReflow(closed);
    runFrame();
    runFrame();

    // The closed pane's fit would run against a disposed terminal inside a
    // detached element; its neighbour must still be reflowed.
    expect(closed).not.toHaveBeenCalled();
    expect(kept).toHaveBeenCalledTimes(1);
  });

  it("keeps the queue moving when one pane's reflow throws", () => {
    const after = vi.fn();
    queuePaneReflow(() => {
      throw new Error("this pane could not be measured");
    });
    queuePaneReflow(after);

    expect(() => runFrame()).not.toThrow();
    runFrame();

    expect(after).toHaveBeenCalledTimes(1);
  });
});
