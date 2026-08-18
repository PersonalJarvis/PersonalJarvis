/**
 * The liveliness force: every page but the pivot moves on its own, and the
 * motion is a bounded, periodic offset on top of the layout — never a drift.
 */
import { describe, expect, it } from "vitest";

import {
  LIVELINESS_AMPLITUDE,
  LIVELINESS_SWAY,
  createLivelinessForce,
  rhythmSeed,
  type LivelyNode,
} from "@/lib/graphForces";

function scene(): LivelyNode[] {
  return [
    { id: "me", x: 0, y: 0, z: 0 },
    { id: "projects/nova", x: 80, y: 5, z: -20 },
    { id: "people/alex", x: -60, y: -10, z: 40 },
  ];
}

function run(nodes: LivelyNode[], times: number[]): void {
  let t = 0;
  const force = createLivelinessForce({ now: () => t, isPinned: (n) => n.id === "me" });
  force.initialize(nodes);
  for (const time of times) {
    t = time;
    force(0); // alpha is ignored on purpose
  }
}

describe("createLivelinessForce", () => {
  it("never moves the pivot", () => {
    const nodes = scene();
    run(nodes, [0, 300, 900, 1500, 4000, 9000]);
    expect(nodes[0]).toMatchObject({ x: 0, y: 0, z: 0 });
  });

  it("moves every other page, each with its own rhythm", () => {
    const nodes = scene();
    run(nodes, [0, 700]);
    const a = nodes[1];
    const b = nodes[2];
    expect(a.y).not.toBe(5);
    expect(b.y).not.toBe(-10);
    // Different pages, different phase: they are not in step.
    expect(a.y! - 5).not.toBeCloseTo(b.y! + 10, 3);
  });

  it("stays a bounded offset on top of the layout — no drift over time", () => {
    const nodes = scene();
    const times = Array.from({ length: 400 }, (_, i) => i * 37);
    run(nodes, times);
    for (const node of nodes.slice(1)) {
      const base = node.id === "projects/nova" ? { x: 80, y: 5, z: -20 } : { x: -60, y: -10, z: 40 };
      expect(Math.abs(node.y! - base.y)).toBeLessThanOrEqual(LIVELINESS_AMPLITUDE + 1e-9);
      expect(Math.abs(node.x! - base.x)).toBeLessThanOrEqual(LIVELINESS_SWAY + 1e-9);
      expect(Math.abs(node.z! - base.z)).toBeLessThanOrEqual(LIVELINESS_SWAY + 1e-9);
    }
  });

  it("composes with a layout that keeps moving the node underneath", () => {
    const nodes = scene();
    let t = 0;
    const force = createLivelinessForce({ now: () => t, isPinned: () => false });
    force.initialize(nodes);
    force(1);
    // The layout shoves the node 100 units over between ticks…
    nodes[1].x = (nodes[1].x ?? 0) + 100;
    t = 500;
    force(1);
    // …and the force keeps only its own small offset on top of the new place.
    expect(Math.abs(nodes[1].x! - 180)).toBeLessThanOrEqual(LIVELINESS_SWAY + 1e-9);
  });

  it("gives a page the same rhythm every time", () => {
    expect(rhythmSeed("projects/nova")).toBe(rhythmSeed("projects/nova"));
    expect(rhythmSeed("projects/nova")).not.toBe(rhythmSeed("people/alex"));
    expect(rhythmSeed(undefined)).toBeGreaterThanOrEqual(0);
    expect(rhythmSeed(undefined)).toBeLessThanOrEqual(1);
  });
});
