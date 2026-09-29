import { describe, expect, it } from "vitest";

import { codingAmbience } from "./codingAmbience";
import { buildOfficeLayout, type OfficeAgentInput, type Rect } from "./officeLayout";

const agent = (id: string, provider: string): OfficeAgentInput => ({
  agentId: id, name: id, tier: "specialist", providerLabel: provider, state: "working", createdMs: 0,
});

const overlaps = (a: Rect, b: Rect) => a.minX < b.maxX && a.maxX > b.minX && a.minZ < b.maxZ && a.maxZ > b.minZ;

describe("coding floor ambience", () => {
  for (const count of [0, 12, 40]) {
    const layout = buildOfficeLayout(Array.from({ length: count }, (_, i) => agent(`a${i}`, `P${i % 5}`)), { variant: "coding" });
    const items = codingAmbience(layout);

    it(`stands in the window strip, clear of every department and room (${count} agents)`, () => {
      expect(items.some((i) => i.kind === "tree")).toBe(true);
      expect(items.some((i) => i.kind === "library")).toBe(true);
      expect(items.some((i) => i.kind === "trough")).toBe(true);
      for (const item of items) {
        for (const area of [...layout.departments, ...layout.rooms]) expect(overlaps(item, area), item.id).toBe(false);
        expect(item.minX, item.id).toBeGreaterThan(layout.bounds.minX + 0.2);
        expect(item.maxX, item.id).toBeLessThan(layout.bounds.maxX - 0.2);
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

  it("adds nothing to the agents office and keeps ids unique", () => {
    const agents = buildOfficeLayout([]);
    for (const item of codingAmbience(agents)) {
      expect(agents.obstacles.some((o) => o.minX === item.minX && o.maxZ === item.maxZ), item.id).toBe(false);
    }
    const items = codingAmbience(buildOfficeLayout([], { variant: "coding" }));
    expect(new Set(items.map((i) => i.id)).size).toBe(items.length);
  });
});
