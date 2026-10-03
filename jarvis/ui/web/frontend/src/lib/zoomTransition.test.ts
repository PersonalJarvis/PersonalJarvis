import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  armZoomTransition,
  installZoomTransition,
  scrollAnchorUnderPointer,
  ZOOM_TRANSITION_MS,
} from "./zoomTransition";

interface Played {
  keyframes: Keyframe[];
  options: KeyframeAnimationOptions;
}

describe("installZoomTransition", () => {
  let played: Played[];
  let cleanup: () => void;

  function setViewport(dpr: number, inner: number, outer = 1600) {
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: dpr });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: inner });
    Object.defineProperty(window, "outerWidth", { configurable: true, value: outer });
  }

  beforeEach(() => {
    played = [];
    setViewport(1, 1600);
    document.documentElement.animate = vi.fn((keyframes: Keyframe[], options: KeyframeAnimationOptions) => {
      played.push({ keyframes, options });
      return { cancel: vi.fn(), onfinish: null, oncancel: null } as unknown as Animation;
    }) as unknown as HTMLElement["animate"];
    cleanup = installZoomTransition(window);
  });
  afterEach(() => cleanup());

  it("glides from the old size after a zoom this window asked for", () => {
    armZoomTransition();
    setViewport(1.25, 1280);
    window.dispatchEvent(new Event("resize"));

    expect(played).toHaveLength(1);
    expect(played[0].keyframes[0].transform).toBe("scale(0.8)");
    expect(played[0].keyframes[1].transform).toBe("scale(1)");
    expect(played[0].keyframes[0].transformOrigin).toBe("0 0");
    expect(played[0].options.duration).toBe(ZOOM_TRANSITION_MS);
  });

  it("reads the viewport width where the pixel ratio does not move", () => {
    armZoomTransition();
    setViewport(1, 2000);
    window.dispatchEvent(new Event("resize"));
    expect(played[0].keyframes[0].transform).toBe("scale(1.25)");
  });

  it("stays still for a resize nobody asked to zoom", () => {
    setViewport(1, 1200, 1200); // dragging the window edge
    window.dispatchEvent(new Event("resize"));
    expect(played).toHaveLength(0);
  });

  it("grows out of the pointer when it is over the window", () => {
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 400, clientY: 300 }));
    armZoomTransition();
    setViewport(1.25, 1280);
    window.dispatchEvent(new Event("resize"));

    expect(played[0].keyframes[0].transform).toBe("scale(0.8)");
    // 400 x 300 old CSS pixels are 320 x 240 at 125 %.
    expect(played[0].keyframes[0].transformOrigin).toBe("320px 240px");
  });

  it("falls back to the top-left corner once the pointer left the window", () => {
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 400, clientY: 300 }));
    document.documentElement.dispatchEvent(new MouseEvent("pointerleave"));
    armZoomTransition();
    setViewport(1.25, 1280);
    window.dispatchEvent(new Event("resize"));
    expect(played[0].keyframes[0].transformOrigin).toBe("0 0");
  });

  it("stays still when the window itself was resized during the zoom", () => {
    armZoomTransition();
    setViewport(1, 1200, 1300);
    window.dispatchEvent(new Event("resize"));
    expect(played).toHaveLength(0);
  });
});

describe("scrollAnchorUnderPointer", () => {
  it("scrolls the container so the remembered spot is under the pointer again", () => {
    const scroller = document.createElement("div");
    scroller.style.overflowY = "auto";
    const target = document.createElement("p");
    scroller.appendChild(target);
    document.body.appendChild(scroller);
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 2000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 500 });
    let top = 100;
    scroller.scrollTo = ((options: ScrollToOptions) => {
      top = options.top ?? top;
    }) as HTMLElement["scrollTo"];
    Object.defineProperty(scroller, "scrollTop", { configurable: true, get: () => top });
    // After the reflow the paragraph's middle is at y = 400 (minus what is scrolled).
    target.getBoundingClientRect = () => new DOMRect(0, 350 - (top - 100), 200, 100);

    scrollAnchorUnderPointer({ x: 100, y: 300, target, fx: 0.5, fy: 0.5 }, 100, 240);

    // 400 - 240 = 160 more pixels scrolled.
    expect(top).toBe(260);
    scroller.remove();
  });
});
