import { describe, expect, it } from "vitest";

import { agentsRug, AGENTS_RUGS } from "./AgentsFloorLook";
import { agentsAmbience } from "./agentsAmbience";
import { buildOfficeLayout, type OfficeAgentInput, type Rect } from "./officeLayout";
import { CODING_STUDIOS } from "./officePalette";

const agent = (id: string, provider: string): OfficeAgentInput => ({
  agentId: id, name: id, tier: "specialist", providerLabel: provider, state: "working", createdMs: 0,
});

const overlaps = (a: Rect, b: Rect) => a.minX < b.maxX && a.maxX > b.minX && a.minZ < b.maxZ && a.maxZ > b.minZ;

describe("agents floor ambience", () => {
  for (const count of [0, 12, 40]) {
    const layout = buildOfficeLayout(Array.from({ length: count }, (_, i) => agent(`a${i}`, `P${i % 5}`)));
    const items = agentsAmbience(layout);

    it(`stands in the window strip, clear of every department, room and the railing (${count} agents)`, () => {
      for (const kind of ["olive", "bench", "planter"] as const) expect(items.some((i) => i.kind === kind), kind).toBe(true);
      for (const item of items) {
        for (const area of [...layout.departments, ...layout.rooms]) expect(overlaps(item, area), item.id).toBe(false);
        // The railing's base is 8 cm wide on a line 0.2 m in from the slab edge.
        expect(item.minX, item.id).toBeGreaterThan(layout.bounds.minX + 0.24);
        expect(item.maxX, item.id).toBeLessThan(layout.bounds.maxX - 0.24);
      }
    });

    it(`is solid in the layout and leaves every window spot free (${count} agents)`, () => {
      for (const item of items) {
        expect(layout.obstacles.some((o) => o.minX === item.minX && o.maxZ === item.maxZ), item.id).toBe(true);
      }
      for (const spot of layout.spots.filter((s) => s.kind === "window")) {
        expect(items.some((i) => spot.x >= i.minX - 0.3 && spot.x <= i.maxX + 0.3 && spot.z >= i.minZ - 0.3 && spot.z <= i.maxZ + 0.3), spot.id).toBe(false);
      }
    });
  }

  it("puts a bench and a planter beside each window, mirrored between west and east", () => {
    const items = agentsAmbience(buildOfficeLayout([]));
    const kindAt = (side: "west" | "east", run: number) => items.find((i) => i.id.endsWith(`-${side}-0-${run}`))?.kind;
    expect(kindAt("west", 0)).toBe("bench");
    expect(kindAt("west", 1)).toBe("planter");
    expect(new Set(items.map((i) => i.id)).size).toBe(items.length);
  });

  it("adds nothing to the coding floor", () => {
    const coding = buildOfficeLayout([], { variant: "coding" });
    for (const item of agentsAmbience(coding)) {
      expect(coding.obstacles.some((o) => o.minX === item.minX && o.maxZ === item.maxZ), item.id).toBe(false);
    }
  });
});

describe("agents floor rugs", () => {
  it("gives the six departments a floor can hold six different rugs, none shared with the coding floor", () => {
    const rugs = Array.from({ length: 6 }, (_, i) => agentsRug(i));
    expect(new Set(rugs.map((r) => r.weave)).size).toBe(6);
    expect(new Set(rugs.map((r) => r.field)).size).toBe(6);
    const above = new Set(CODING_STUDIOS.map((s) => s.carpet));
    for (const rug of AGENTS_RUGS) expect(above.has(rug.field)).toBe(false);
  });

  it("cycles for any tint index", () => {
    expect(agentsRug(AGENTS_RUGS.length)).toBe(agentsRug(0));
    expect(agentsRug(-1)).toBe(agentsRug(AGENTS_RUGS.length - 1));
  });
});
