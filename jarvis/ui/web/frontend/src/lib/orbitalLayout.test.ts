/**
 * The memory map seats like a solar system: neighbours of the sun close in,
 * strangers out, the same vault always the same sky.
 */
import { describe, expect, it } from "vitest";

import {
  hopsFromHub,
  occupiedShells,
  orbitPose,
  seatAllOnShells,
  shellRadius,
  snapToShell,
  KUIPER_RADIUS,
  SHELL_RADIUS,
} from "@/lib/orbitalLayout";

describe("hopsFromHub", () => {
  it("counts wikilink hops from the sun and sends isolates to infinity", () => {
    const hops = hopsFromHub(
      ["me", "spotify", "album", "stray"],
      [
        { source: "me", target: "spotify" },
        { source: "spotify", target: "album" },
      ],
      "me",
    );
    expect(hops.get("me")).toBe(0);
    expect(hops.get("spotify")).toBe(1);
    expect(hops.get("album")).toBe(2);
    expect(hops.get("stray")).toBe(Number.POSITIVE_INFINITY);
  });

  it("treats a missing hub as a sky of isolates", () => {
    const hops = hopsFromHub(["a", "b"], [{ source: "a", target: "b" }], "me");
    expect(hops.get("a")).toBe(Number.POSITIVE_INFINITY);
    expect(hops.get("b")).toBe(Number.POSITIVE_INFINITY);
  });
});

describe("shellRadius", () => {
  it("puts the sun at the origin and isolates in the kuiper belt", () => {
    expect(shellRadius(0)).toBe(0);
    expect(shellRadius(1)).toBe(SHELL_RADIUS[1]);
    expect(shellRadius(2)).toBe(SHELL_RADIUS[2]);
    expect(shellRadius(Number.POSITIVE_INFINITY)).toBe(KUIPER_RADIUS);
    expect(shellRadius(99)).toBe(KUIPER_RADIUS);
  });
});

describe("orbitPose / seatAllOnShells", () => {
  it("gives a page the same seat every time", () => {
    expect(orbitPose("spotify", 1, 0, 3)).toEqual(orbitPose("spotify", 1, 0, 3));
  });

  it("sits direct neighbours closer in than a stranger", () => {
    const nodes: Array<{ id: string; x?: number; y?: number; z?: number }> = [
      { id: "me" },
      { id: "spotify" },
      { id: "calendar" },
      { id: "lost" },
    ];
    const seated = seatAllOnShells(
      nodes,
      [
        { source: "me", target: "spotify" },
        { source: "me", target: "calendar" },
      ],
      "me",
    );
    expect(seated).toBe(3);
    expect(nodes[0]).toMatchObject({ x: 0, y: 0, z: 0 });
    const inner = Math.hypot(nodes[1].x ?? 0, nodes[1].z ?? 0);
    const alsoInner = Math.hypot(nodes[2].x ?? 0, nodes[2].z ?? 0);
    const outer = Math.hypot(nodes[3].x ?? 0, nodes[3].z ?? 0);
    expect(inner).toBeGreaterThan(50);
    expect(inner).toBeLessThan(100);
    expect(alsoInner).toBeGreaterThan(50);
    expect(alsoInner).toBeLessThan(100);
    expect(outer).toBeGreaterThan(inner + 80);
  });

  it("keeps two neighbours off one spot", () => {
    const a = orbitPose("one", 1, 0, 2);
    const b = orbitPose("two", 1, 1, 2);
    expect(Math.hypot(a.x - b.x, a.z - b.z)).toBeGreaterThan(40);
  });
});

describe("snapToShell / occupiedShells", () => {
  it("keeps the angle and only changes how far out the page sits", () => {
    const node = { id: "spotify", x: 10, y: 4, z: 0 };
    snapToShell(node, 1);
    const angle = Math.atan2(node.z ?? 0, node.x ?? 0);
    expect(angle).toBeCloseTo(0, 5);
    expect(Math.hypot(node.x ?? 0, node.z ?? 0)).toBeGreaterThan(50);
    expect(node.y).toBe(4);
  });

  it("names each band that actually has a page on it", () => {
    const hops = new Map<string, number>([
      ["me", 0],
      ["a", 1],
      ["b", 1],
      ["c", Number.POSITIVE_INFINITY],
    ]);
    expect(occupiedShells(hops, "me")).toEqual([SHELL_RADIUS[1], KUIPER_RADIUS]);
  });
});
