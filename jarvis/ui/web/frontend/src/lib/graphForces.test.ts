/**
 * The liveliness force: every page but the pivot moves — together, as a
 * wave, a breath and a small wobble — and the motion is a bounded, smooth,
 * periodic offset on top of the layout, never a drift and never a jump.
 */
import { describe, expect, it } from "vitest";

import {
  LIVELINESS_AMPLITUDE,
  LIVELINESS_BREATH,
  LIVELINESS_WOBBLE,
  createLivelinessForce,
  rhythmSeed,
  type LivelyNode,
} from "@/lib/graphForces";

function scene(): LivelyNode[] {
  return [
    { id: "me", x: 0, y: 0, z: 0 },
    { id: "projects/nova", x: 80, y: 5, z: -20 },
    { id: "people/alex", x: -60, y: -10, z: 40 },
    // Two neighbours standing close together — the wave must carry them
    // almost as one.
    { id: "notes/a", x: 100, y: 0, z: 100 },
    { id: "notes/b", x: 110, y: 0, z: 104 },
  ];
}

const BASES: Record<string, { x: number; y: number; z: number }> = {
  "projects/nova": { x: 80, y: 5, z: -20 },
  "people/alex": { x: -60, y: -10, z: 40 },
  "notes/a": { x: 100, y: 0, z: 100 },
  "notes/b": { x: 110, y: 0, z: 104 },
};

function makeForce(nodes: LivelyNode[]) {
  let t = 0;
  const force = createLivelinessForce({
    now: () => t,
    isPinned: (n) => n.id === "me",
    centre: () => nodes[0],
  });
  force.initialize(nodes);
  return { tick: (time: number) => { t = time; force(0); } };
}

/** The most an offset may reach: wave + breath share of the distance out. */
function bound(base: { x: number; y: number; z: number }, axis: "x" | "y" | "z"): number {
  const dist = Math.abs(base[axis]);
  const breath = dist * LIVELINESS_BREATH;
  return axis === "y" ? LIVELINESS_AMPLITUDE + breath : breath + LIVELINESS_WOBBLE;
}

describe("createLivelinessForce", () => {
  it("never moves the pivot", () => {
    const nodes = scene();
    const { tick } = makeForce(nodes);
    for (const t of [0, 300, 900, 1500, 4000, 9000]) tick(t);
    expect(nodes[0]).toMatchObject({ x: 0, y: 0, z: 0 });
  });

  it("moves every other page", () => {
    const nodes = scene();
    const { tick } = makeForce(nodes);
    tick(0);
    tick(1300);
    for (const node of nodes.slice(1)) {
      const base = BASES[String(node.id)];
      expect(Math.abs(node.y! - base.y)).toBeGreaterThan(0.5);
    }
  });

  it("carries close neighbours almost together — a wave, not noise", () => {
    const nodes = scene();
    const { tick } = makeForce(nodes);
    let maxGap = 0;
    for (let t = 0; t < 12_000; t += 100) {
      tick(t);
      const a = nodes[3].y! - BASES["notes/a"].y;
      const b = nodes[4].y! - BASES["notes/b"].y;
      maxGap = Math.max(maxGap, Math.abs(a - b));
    }
    // Independent rhythms would put them a full amplitude apart at times.
    expect(maxGap).toBeLessThan(LIVELINESS_AMPLITUDE * 0.75);
  });

  it("stays a bounded offset on top of the layout — no drift over time", () => {
    const nodes = scene();
    const { tick } = makeForce(nodes);
    for (let i = 0; i < 600; i++) tick(i * 37);
    for (const node of nodes.slice(1)) {
      const base = BASES[String(node.id)];
      for (const axis of ["x", "y", "z"] as const) {
        expect(Math.abs(node[axis]! - base[axis])).toBeLessThanOrEqual(bound(base, axis) + 1e-6);
      }
    }
  });

  it("moves smoothly at 60 fps — no tick jumps a page", () => {
    const nodes = scene();
    const { tick } = makeForce(nodes);
    tick(0);
    let maxStep = 0;
    let previous = nodes.map((n) => ({ x: n.x!, y: n.y!, z: n.z! }));
    for (let t = 16; t < 20_000; t += 16) {
      tick(t);
      nodes.forEach((n, i) => {
        const step = Math.hypot(n.x! - previous[i].x, n.y! - previous[i].y, n.z! - previous[i].z);
        maxStep = Math.max(maxStep, step);
      });
      previous = nodes.map((n) => ({ x: n.x!, y: n.y!, z: n.z! }));
    }
    // A node radius is 3–8 units; a frame must never move a page a visible jolt.
    expect(maxStep).toBeLessThan(0.5);
  });

  it("composes with a layout that keeps moving the node underneath", () => {
    const nodes = scene();
    const { tick } = makeForce(nodes);
    tick(0);
    // The layout shoves the node 100 units over between ticks…
    nodes[1].x = (nodes[1].x ?? 0) + 100;
    tick(500);
    // …and the force keeps only its own small offset on top of the new place.
    const base = { x: 180, y: 5, z: -20 };
    expect(Math.abs(nodes[1].x! - base.x)).toBeLessThanOrEqual(bound(base, "x") + 1e-6);
  });

  it("gives a page the same rhythm every time", () => {
    expect(rhythmSeed("projects/nova")).toBe(rhythmSeed("projects/nova"));
    expect(rhythmSeed("projects/nova")).not.toBe(rhythmSeed("people/alex"));
    expect(rhythmSeed(undefined)).toBeGreaterThanOrEqual(0);
    expect(rhythmSeed(undefined)).toBeLessThanOrEqual(1);
  });
});
