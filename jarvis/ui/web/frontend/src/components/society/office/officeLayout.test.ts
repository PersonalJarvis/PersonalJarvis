import { describe, expect, it } from "vitest";
import { allDesks, buildOfficeLayout, countStates, departmentKey, groupDepartments, MAX_DEPARTMENTS, MIN_DEPARTMENTS, type OfficeAgentInput } from "./officeLayout";
import { cameraHome, fitDistance, focusBounds } from "./officeCamera";

const agent = (id: string, provider: string, extra: Partial<OfficeAgentInput> = {}): OfficeAgentInput => ({
  agentId: id, name: id, tier: "specialist", providerLabel: provider, state: "idle", createdMs: Number(id.replace(/\D/g, "")) || 0, ...extra,
});

describe("office layout", () => {
  it("groups agents by provider family and falls back to Jarvis", () => {
    expect(departmentKey({ providerLabel: "  " })).toBe("Jarvis");
    const groups = groupDepartments([agent("a1", "codex"), agent("a2", ""), agent("a3", "codex")]);
    expect(groups.map((g) => g.label)).toEqual(["Codex", "Jarvis"]);
    expect(buildOfficeLayout([agent("a1", "codex")]).departments.map((d) => d.label)).toEqual(["Codex", "", "", ""]);
  });

  it("gives every staff agent exactly one desk and the lead its own office", () => {
    const roster = [agent("a1", "Codex"), agent("a2", "Claude Code"), agent("a3", "Codex"), agent("a4", "", { tier: "lead" })];
    const layout = buildOfficeLayout(roster);
    const seated = allDesks(layout).map((d) => d.agentId).filter(Boolean);
    expect(new Set(seated)).toEqual(new Set(["a1", "a2", "a3", "a4"]));
    expect(seated).toHaveLength(4);
    expect(layout.lead.desks[0].agentId).toBe("a4");
  });

  it("keeps seating stable when run state changes or the roster is reordered", () => {
    const roster = [agent("a1", "Codex"), agent("a2", "Codex"), agent("a3", "Gemini")];
    const before = allDesks(buildOfficeLayout(roster)).map((d) => `${d.id}=${d.agentId}`);
    const after = allDesks(buildOfficeLayout([...roster].reverse().map((a) => ({ ...a, state: "working" as const }))))
      .map((d) => `${d.id}=${d.agentId}`);
    expect(after).toEqual(before);
  });

  it("folds surplus providers into one department and never overlaps departments", () => {
    const roster = Array.from({ length: 10 }, (_, i) => agent(`a${i}`, `P${i}`));
    const layout = buildOfficeLayout(roster);
    expect(layout.departments).toHaveLength(MAX_DEPARTMENTS);
    expect(layout.departments.at(-1)?.label).toBe("Other");
    for (const a of layout.departments) for (const b of layout.departments) {
      if (a === b) continue;
      const overlap = a.minX < b.maxX && b.minX < a.maxX && a.minZ < b.maxZ && b.minZ < a.maxZ;
      expect(overlap).toBe(false);
    }
    for (const desk of allDesks(layout)) {
      expect(desk.x).toBeGreaterThan(layout.bounds.minX);
      expect(desk.x).toBeLessThan(layout.bounds.maxX);
      expect(desk.z).toBeGreaterThan(layout.bounds.minZ);
      expect(desk.z).toBeLessThan(layout.bounds.maxZ);
    }
  });

  it("builds a furnished floor for an empty roster", () => {
    const layout = buildOfficeLayout([]);
    expect(layout.departments).toHaveLength(MIN_DEPARTMENTS);
    expect(layout.departments.every((d) => d.label === "" && d.desks.length > 0)).toBe(true);
  });

  it("counts states", () => {
    expect(countStates([{ state: "working" }, { state: "idle" }, { state: "working" }])).toEqual({ working: 2, idle: 1, waiting: 0, paused: 0 });
  });
});

describe("office camera", () => {
  const bounds = { minX: -12, maxX: 12, minZ: -15, maxZ: 15 };
  it("looks at the floor centre from the south-east, above it", () => {
    const pose = cameraHome(bounds, 16 / 9);
    expect(pose.target).toEqual([0, 0, 0]);
    expect(pose.position[0]).toBeGreaterThan(0);
    expect(pose.position[1]).toBeGreaterThan(0);
    expect(pose.position[2]).toBeGreaterThan(0);
  });
  it("backs off further for a narrow viewport and a bigger floor", () => {
    expect(fitDistance(bounds, 0.6)).toBeGreaterThan(fitDistance(bounds, 2));
    expect(fitDistance({ minX: -30, maxX: 30, minZ: -30, maxZ: 30 }, 1.6)).toBeGreaterThan(fitDistance(bounds, 1.6));
  });
  it("frames the occupied desks, padded to a readable neighbourhood", () => {
    expect(focusBounds([])).toBeNull();
    const focus = focusBounds([{ x: 10, z: -4 }])!;
    expect((focus.minX + focus.maxX) / 2).toBe(10);
    expect(focus.maxX - focus.minX).toBeGreaterThanOrEqual(18);
    expect(cameraHome(bounds, 1.6, focus).target).toEqual([10, 0, -4]);
  });
});
