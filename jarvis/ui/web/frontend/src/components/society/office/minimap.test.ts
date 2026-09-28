import { describe, expect, it } from "vitest";
import { buildOfficeLayout, type OfficeAgentInput } from "./officeLayout";
import {
  agentAt, clampToRect, drawMinimapBase, drawMinimapDynamic, mapToWorld, minimapHeightFor, minimapTransform, placeAt,
  resolveMinimapColours, worldToMap,
} from "./minimap";

const agent = (id: string, provider: string, extra: Partial<OfficeAgentInput> = {}): OfficeAgentInput => ({
  agentId: id, name: id, tier: "specialist", providerLabel: provider, state: "idle", createdMs: Number(id.replace(/\D/g, "")) || 0, ...extra,
});

/** A 2D context stand-in that records every call and property write, in order. */
function recorder(): { ctx: CanvasRenderingContext2D; log: string[] } {
  const log: string[] = [];
  const state: Record<string, unknown> = {};
  const ctx = new Proxy(state, {
    get(target, prop: string) {
      if (prop in target) return target[prop];
      return (...args: unknown[]) => { log.push(`${prop}(${args.map((a) => (typeof a === "number" ? a.toFixed(1) : String(a))).join(",")})`); };
    },
    set(target, prop: string, value) {
      target[prop] = value;
      log.push(`${prop}=${String(value)}`);
      return true;
    },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, log };
}

const colours = resolveMinimapColours((name) => ({ "--success": "142 70% 45%", "--warning": "32 95% 34%" } as Record<string, string>)[name] ?? "");

describe("minimap transform", () => {
  const bounds = { minX: -10, maxX: 10, minZ: -20, maxZ: 20 };

  it("keeps the aspect and centres the floor inside the padding", () => {
    const t = minimapTransform(bounds, 220, 300, 10);
    // Height limits: (300 - 20) / 40 = 7 px/m; width would allow 10 px/m.
    expect(t.scale).toBeCloseTo(7);
    const nw = worldToMap(t, { x: -10, z: -20 });
    const se = worldToMap(t, { x: 10, z: 20 });
    expect(nw.y).toBeCloseTo(10);
    expect(se.y).toBeCloseTo(290);
    expect(nw.x + se.x).toBeCloseTo(220); // centred horizontally
    expect(se.x - nw.x).toBeCloseTo(140);
  });

  it("is north-up: +x goes right and +z (south) goes down", () => {
    const t = minimapTransform(bounds, 200, 200);
    const origin = worldToMap(t, { x: 0, z: 0 });
    expect(worldToMap(t, { x: 1, z: 0 }).x).toBeGreaterThan(origin.x);
    expect(worldToMap(t, { x: 0, z: 1 }).y).toBeGreaterThan(origin.y);
    expect(origin).toEqual({ x: 100, y: 100 });
  });

  it("round-trips between world and map", () => {
    const t = minimapTransform(bounds, 220, 260, 6);
    for (const p of [{ x: 3.5, z: -7.25 }, { x: -10, z: 20 }, { x: 0, z: 0 }]) {
      const back = mapToWorld(t, worldToMap(t, p));
      expect(back.x).toBeCloseTo(p.x);
      expect(back.z).toBeCloseTo(p.z);
    }
  });

  it("sizes the map height by the floor aspect within limits", () => {
    expect(minimapHeightFor(bounds, 220, 10)).toBe(300); // natural 420 clamps down
    expect(minimapHeightFor({ minX: 0, maxX: 40, minZ: 0, maxZ: 30 }, 220, 10)).toBe(170);
    expect(minimapHeightFor({ minX: 0, maxX: 100, minZ: 0, maxZ: 1 }, 220, 10)).toBe(110);
  });

  it("clamps a click on the railing onto the walkable floor", () => {
    expect(clampToRect({ x: 50, z: -50 }, bounds)).toEqual({ x: 10, z: -20 });
  });
});

describe("minimap hit testing", () => {
  const t = minimapTransform({ minX: -10, maxX: 10, minZ: -10, maxZ: 10 }, 200, 200, 0); // 10 px/m
  const agents = [{ id: "a", x: 0, z: 0 }, { id: "b", x: 0.5, z: 0 }, { id: "c", x: 5, z: 5 }];

  it("picks the closest dot within the radius", () => {
    expect(agentAt(t, agents, { x: 104, y: 100 })?.id).toBe("b");
    expect(agentAt(t, agents, { x: 101, y: 101 })?.id).toBe("a");
    expect(agentAt(t, agents, { x: 150, y: 157 }, 8)?.id).toBe("c");
  });

  it("returns null when nothing is within the radius", () => {
    expect(agentAt(t, agents, { x: 150, y: 170 }, 8)).toBeNull();
  });

  it("names the place under a point: checkpoint, then room, then department", () => {
    const layout = buildOfficeLayout([agent("a1", "codex"), agent("a2", "gemini")]);
    const create = layout.checkpoints.find((c) => c.id === "create")!;
    expect(placeAt(layout, create)).toEqual({ kind: "checkpoint", id: "create" });
    const lead = layout.rooms.find((r) => r.kind === "lead")!;
    expect(placeAt(layout, { x: lead.minX + 0.3, z: lead.minZ + 0.3 })).toEqual({ kind: "room", id: "lead" });
    const dept = layout.departments[0];
    expect(placeAt(layout, { x: dept.minX + 0.2, z: dept.minZ + 0.2 })).toEqual({ kind: "department", label: dept.label });
    expect(placeAt(layout, { x: layout.bounds.minX + 0.01, z: 0 })).toBeNull();
  });
});

describe("minimap drawing", () => {
  const layout = buildOfficeLayout([agent("a1", "codex"), agent("a2", "codex", { state: "working" }), agent("l", "", { tier: "lead" })]);
  const t = minimapTransform(layout.bounds, 220, minimapHeightFor(layout.bounds, 220));

  it("reads theme tokens as hsl() colours with fallbacks", () => {
    expect(colours.working).toBe("hsl(142 70% 45%)");
    expect(colours.floor).toMatch(/^#/);
    expect(colours.rooms.break).toMatch(/^#/);
  });

  it("draws the floor, every wall segment and one hexagon per checkpoint", () => {
    const { ctx, log } = recorder();
    drawMinimapBase(ctx, layout, t, colours);
    expect(log[0]).toMatch(/^clearRect/);
    expect(log.filter((l) => l.startsWith("moveTo")).length).toBe(layout.walls.length + layout.checkpoints.length);
    expect(log.filter((l) => l.startsWith("closePath")).length).toBe(layout.checkpoints.length);
    expect(log).toContain(`fillStyle=${colours.checkpoint}`);
    expect(log).toContain(`fillStyle=${colours.rooms.lead}`);
    expect(log[log.length - 1]).not.toMatch(/^globalAlpha=0/);
  });

  it("colours agents by state, rings the selected one and draws the player", () => {
    const { ctx, log } = recorder();
    drawMinimapDynamic(ctx, t, {
      agents: [
        { id: "w", x: 0, z: 0, state: "working", selected: false },
        { id: "q", x: 1, z: 0, state: "waiting", selected: true },
        { id: "i", x: 2, z: 0, state: "idle", selected: false },
        { id: "p", x: 3, z: 0, state: "paused", selected: false },
      ],
      player: { x: 0, z: 2, heading: Math.PI },
      camera: null,
      colours,
    });
    expect(log).toContain(`fillStyle=${colours.working}`);
    expect(log).toContain(`fillStyle=${colours.waiting}`);
    expect(log).toContain(`fillStyle=${colours.idle}`);
    expect(log).toContain(`strokeStyle=${colours.paused}`);
    expect(log).toContain(`strokeStyle=${colours.selected}`);
    expect(log).toContain(`fillStyle=${colours.player}`);
    expect(log).not.toContain("clip()");
    // Heading π looks north: the arrow tip lies above the player on the map.
    const p = worldToMap(t, { x: 0, z: 2 });
    const tip = log.filter((l) => l.startsWith("moveTo")).pop()!;
    const [, y] = tip.slice("moveTo(".length, -1).split(",").map(Number);
    expect(y).toBeLessThan(p.y);
  });

  it("draws the camera wedge clipped to the slab when a camera is given", () => {
    const { ctx, log } = recorder();
    drawMinimapDynamic(ctx, t, { agents: [], player: { x: 0, z: 0, heading: 0 }, camera: { x: 5, z: 5, yaw: -Math.PI * 0.75, halfWidth: 0.4 }, colours });
    expect(log).toContain("clip()");
    expect(log).toContain(`fillStyle=${colours.camera}`);
  });
});
