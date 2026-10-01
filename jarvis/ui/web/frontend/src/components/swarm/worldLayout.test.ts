import { describe, expect, it } from "vitest";
import { Color } from "three";
import { agentFixture, snapshotFixture } from "./testFixtures";
import { memoryPosition, worldNodes, worldRadius } from "./worldLayout";
import { cssHslColor } from "./worldPalette";

describe("Swarm world legibility", () => {
  it("keeps drilled workers visible if the team aggregate flag is retained", () => {
    const snapshot = snapshotFixture();
    snapshot.aggregated = true;
    snapshot.agents = [{ ...agentFixture(), role: "worker" }];
    expect(worldNodes(snapshot)).toHaveLength(1);
    expect(worldNodes(snapshot)[0].agent?.id).toBe(snapshot.agents[0].id);
    expect(worldNodes(snapshot)[0].group).toBeUndefined();
  });

  it("frames a small team closely and leaves the memory house clear of figures", () => {
    const snapshot = snapshotFixture();
    const single = worldNodes(snapshot);
    expect(worldRadius(single)).toBeLessThan(15);
    snapshot.agents.push({ ...agentFixture("alpha", "worker-one"), role: "worker" });
    const nodes = worldNodes(snapshot);
    const memory = memoryPosition(nodes);
    expect(nodes.every(node => Math.hypot(node.position[0] - memory[0], node.position[2] - memory[2]) > 4)).toBe(true);
    expect(Math.hypot(nodes[1].position[0], nodes[1].position[2])).toBeCloseTo(4.5);
  });

  it("matches CSS sRGB colors in light and dark themes instead of washing out midtones", () => {
    for (const [token, css] of [["0 0% 50%", "hsl(0, 0%, 50%)"], ["222 47% 11%", "hsl(222, 47%, 11%)"], ["210 40% 96%", "hsl(210, 40%, 96%)"]]) {
      const actual = cssHslColor(token);
      const expected = new Color(css);
      expect(actual.r).toBeCloseTo(expected.r, 6);
      expect(actual.g).toBeCloseTo(expected.g, 6);
      expect(actual.b).toBeCloseTo(expected.b, 6);
    }
    expect(cssHslColor("0 0% 50%").r).toBeLessThan(.25);
  });
});
