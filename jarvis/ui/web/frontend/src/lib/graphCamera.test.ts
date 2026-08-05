/**
 * The arithmetic behind "fill the screen and keep moving".
 *
 * The load-bearing rule is the percentile radius: a vault is allowed to
 * contain a page nothing links to, and framing that one stray along with
 * everything else is exactly what left the readable part of the map occupying
 * a fifth of the screen.
 */
import { describe, expect, it } from "vitest";

import {
  framingFor,
  orbitDistance,
  orbitFrom,
  orbitPoint,
} from "@/lib/graphCamera";

/** A ball of nodes around a known centre, plus optional outliers. */
function cluster(count: number, radius: number, centre = { x: 0, y: 0, z: 0 }) {
  return Array.from({ length: count }, (_, index) => {
    const angle = (index / count) * Math.PI * 2;
    return {
      x: centre.x + Math.cos(angle) * radius,
      y: centre.y,
      z: centre.z + Math.sin(angle) * radius,
    };
  });
}

describe("framingFor", () => {
  it("has nothing to frame when there are no nodes", () => {
    expect(framingFor([])).toBeNull();
  });

  it("finds the middle of an off-centre network", () => {
    const framing = framingFor(cluster(24, 50, { x: 100, y: 20, z: -40 }));
    expect(framing).not.toBeNull();
    expect(framing!.centre.x).toBeCloseTo(100, 5);
    expect(framing!.centre.y).toBeCloseTo(20, 5);
    expect(framing!.centre.z).toBeCloseTo(-40, 5);
    expect(framing!.radius).toBeCloseTo(50, 5);
  });

  it("does not let one stray node blow the radius up", () => {
    const nodes = [...cluster(40, 50), { x: 4000, y: 0, z: 0 }];
    const framing = framingFor(nodes, 0.95)!;
    // The 95th percentile of 41 nodes is still inside the cluster, so the
    // network keeps the frame and the outlier sits just past the edge.
    expect(framing.radius).toBeLessThan(200);
  });

  it("covers everything when asked for the full extent", () => {
    const nodes = [...cluster(40, 50), { x: 4000, y: 0, z: 0 }];
    const framing = framingFor(nodes, 1)!;
    expect(framing.radius).toBeGreaterThan(3000);
  });

  it("treats missing coordinates as the origin rather than NaN", () => {
    const framing = framingFor([{}, { x: 10 }, { x: -10 }])!;
    expect(Number.isFinite(framing.centre.x)).toBe(true);
    expect(Number.isFinite(framing.radius)).toBe(true);
  });

  it("keeps a floor so a single node is not framed from inside itself", () => {
    const framing = framingFor([{ x: 0, y: 0, z: 0 }])!;
    expect(framing.radius).toBeGreaterThan(0);
  });
});

describe("orbitDistance", () => {
  it("stands further back for a bigger network", () => {
    const near = orbitDistance(50, 50, 1.6);
    const far = orbitDistance(500, 50, 1.6);
    expect(far).toBeGreaterThan(near * 9);
  });

  it("uses the narrower of the two frustum angles", () => {
    // A tall window is limited horizontally, a wide one vertically; either way
    // the network must not spill out of the short side.
    const wide = orbitDistance(100, 50, 3);
    const tall = orbitDistance(100, 50, 0.4);
    expect(tall).toBeGreaterThan(wide);
  });

  it("frames tighter as the fill share rises", () => {
    const loose = orbitDistance(100, 50, 1.6, 0.5);
    const tight = orbitDistance(100, 50, 1.6, 0.95);
    expect(tight).toBeLessThan(loose);
  });

  it("never puts the camera inside the network", () => {
    expect(orbitDistance(100, 50, 1.6, 0.99)).toBeGreaterThan(100);
    expect(orbitDistance(100, 179, 1.6, 0.99)).toBeGreaterThan(100);
  });

  it("survives a viewport that has not been measured yet", () => {
    expect(Number.isFinite(orbitDistance(100, 50, 0))).toBe(true);
    expect(Number.isFinite(orbitDistance(100, 50, Number.NaN))).toBe(true);
  });
});

describe("orbitPoint / orbitFrom", () => {
  it("round-trips an orbit through a position and back", () => {
    const centre = { x: 12, y: -4, z: 30 };
    const orbit = { distance: 260, azimuth: 1.1, elevation: 0.24 };
    const back = orbitFrom(centre, orbitPoint(centre, orbit));
    expect(back.distance).toBeCloseTo(orbit.distance, 5);
    expect(back.azimuth).toBeCloseTo(orbit.azimuth, 5);
    expect(back.elevation).toBeCloseTo(orbit.elevation, 5);
  });

  it("keeps the camera at the same distance as it rotates", () => {
    const centre = { x: 0, y: 0, z: 0 };
    for (const azimuth of [0, 1, 2, 3, 4, 5, 6]) {
      const point = orbitPoint(centre, { distance: 100, azimuth, elevation: 0.3 });
      const length = Math.hypot(point.x, point.y, point.z);
      expect(length).toBeCloseTo(100, 5);
    }
  });

  it("reports a degenerate orbit rather than dividing by zero", () => {
    const orbit = orbitFrom({ x: 5, y: 5, z: 5 }, { x: 5, y: 5, z: 5 });
    expect(orbit.distance).toBe(0);
    expect(Number.isFinite(orbit.azimuth)).toBe(true);
    expect(Number.isFinite(orbit.elevation)).toBe(true);
  });
});
