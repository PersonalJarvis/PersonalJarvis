/**
 * The figure must fit its box whatever shape the box is. These are the cases
 * that actually went wrong: a wide, short column cropped the legs, and a fox
 * framed by its height alone lost its nose and tail.
 */
import { describe, expect, it } from "vitest";

import { FRAME_MARGIN, frameDistance } from "./figureFraming";

const FOV = 26;

/** Half the world-space height the frustum covers at `distance`. */
function halfHeightAt(distance: number): number {
  return Math.tan(((FOV * Math.PI) / 180) / 2) * distance;
}

function fits(spanM: number, aspect: number, zoom = 1): boolean {
  const distance = frameDistance({ spanM, fovDeg: FOV, aspect, zoom });
  const halfH = halfHeightAt(distance);
  const halfW = halfH * aspect;
  const need = (spanM * 0.5 * FRAME_MARGIN) / zoom;
  // A hair of tolerance for the floating point, not for a real overshoot.
  return halfH >= need - 1e-9 && halfW >= need - 1e-9;
}

describe("the figure fits the frame at any shape of column", () => {
  it.each([
    ["a tall column", 0.6],
    ["a square column", 1.0],
    ["the wide, short column the look editor left", 2.1],
    ["an extremely wide strip", 6.0],
  ])("fits a 1.75 m person in %s", (_name, aspect) => {
    expect(fits(1.75, aspect)).toBe(true);
  });

  it("fits a fox, whose length is its span, in a wide column", () => {
    expect(fits(1.6, 2.1)).toBe(true);
  });

  it("stands further back for a longer figure", () => {
    const person = frameDistance({ spanM: 1.75, fovDeg: FOV, aspect: 1.4, zoom: 1 });
    const giant = frameDistance({ spanM: 2.4, fovDeg: FOV, aspect: 1.4, zoom: 1 });
    expect(giant).toBeGreaterThan(person);
  });

  it("moves closer as the person zooms in, and still returns a real distance", () => {
    const near = frameDistance({ spanM: 1.75, fovDeg: FOV, aspect: 1.4, zoom: 2.4 });
    const far = frameDistance({ spanM: 1.75, fovDeg: FOV, aspect: 1.4, zoom: 0.55 });
    expect(near).toBeLessThan(far);
    expect(near).toBeGreaterThan(0);
  });

  it("survives a canvas measured before layout", () => {
    // R3F reports an aspect of 0 for one frame inside a dialog that mounts
    // mid-animation; a camera at distance 0 sits inside the figure.
    for (const bad of [{ aspect: 0 }, { zoom: 0 }, { spanM: 0 }]) {
      const distance = frameDistance({ spanM: 1.75, fovDeg: FOV, aspect: 1.4, zoom: 1, ...bad });
      expect(Number.isFinite(distance)).toBe(true);
      expect(distance).toBeGreaterThan(0);
    }
  });
});
