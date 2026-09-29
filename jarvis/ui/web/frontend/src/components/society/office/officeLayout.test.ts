import { describe, expect, it } from "vitest";
import {
  allDesks, archPosts, buildOfficeLayout, countStates, departmentKey, FURNITURE_SIZE, footprint, groupDepartments,
  MAX_DEPARTMENTS, MIN_DEPARTMENTS, type OfficeAgentInput,
} from "./officeLayout";
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

  it("makes the posts of an open room's name arch solid", () => {
    const layout = buildOfficeLayout([agent("a1", "Codex")]);
    const open = layout.rooms.filter((r) => !r.walled);
    expect(open.length).toBeGreaterThan(0);
    for (const post of open.flatMap(archPosts)) {
      const covered = layout.obstacles.some((o) => post.x >= o.minX && post.x <= o.maxX && post.z >= o.minZ && post.z <= o.maxZ);
      expect(covered).toBe(true);
    }
  });

  for (const variant of ["agents", "coding"] as const) {
    it(`keeps every checkpoint centre clear of solid furniture (${variant})`, () => {
      const layout = buildOfficeLayout(Array.from({ length: 12 }, (_, i) => agent(`a${i}`, i % 2 ? "Codex" : "Gemini")), { variant });
      const solid = layout.furniture.filter((f) => FURNITURE_SIZE[f.kind].solid).map(footprint);
      for (const cp of layout.checkpoints) {
        const inside = solid.some((o) => cp.x >= o.minX && cp.x <= o.maxX && cp.z >= o.minZ && cp.z <= o.maxZ);
        expect(inside, cp.id).toBe(false);
      }
    });

    it(`puts the elevator checkpoint right in front of the elevator (${variant})`, () => {
      const layout = buildOfficeLayout([], { variant });
      const elevator = layout.furniture.find((f) => f.kind === "elevator")!;
      const stop = layout.checkpoints.find((c) => c.id === "elevator")!;
      expect(stop.room).toBe("reception");
      expect(stop.z).toBeCloseTo(elevator.z, 9);
      expect(stop.x).toBeGreaterThan(elevator.x);
      expect(Math.hypot(stop.x - elevator.x, stop.z - elevator.z)).toBeLessThan(1.5);
      expect(new Set(layout.checkpoints.map((c) => c.id)).size).toBe(layout.checkpoints.length);
    });

    it(`keeps room, furniture and spot ids unique and inside the floor (${variant})`, () => {
      const layout = buildOfficeLayout(Array.from({ length: 20 }, (_, i) => agent(`a${i}`, `P${i % 3}`)), { variant });
      for (const list of [layout.rooms, layout.furniture, layout.spots]) {
        expect(new Set(list.map((x) => x.id)).size).toBe(list.length);
      }
      for (const item of [...layout.furniture, ...layout.spots]) {
        const room = item.room === "floor" ? layout.floor : layout.rooms.find((r) => r.kind === item.room)!;
        expect(room, item.id).toBeDefined();
        expect(item.x, item.id).toBeGreaterThanOrEqual(room.minX);
        expect(item.x, item.id).toBeLessThanOrEqual(room.maxX);
        expect(item.z, item.id).toBeGreaterThanOrEqual(room.minZ);
        expect(item.z, item.id).toBeLessThanOrEqual(room.maxZ);
      }
    });
  }

  it("keeps the agents office as it was when no variant is named", () => {
    const roster = [agent("a1", "Codex"), agent("a2", "", { tier: "lead" })];
    const plain = buildOfficeLayout(roster);
    expect(plain).toEqual(buildOfficeLayout(roster, { variant: "agents" }));
    expect(plain.variant).toBe("agents");
    expect(plain.rooms.map((r) => r.kind)).toEqual(["lead", "team", "wardrobe", "reception", "break"]);
    expect(plain.checkpoints.map((c) => c.id)).toEqual(["create", "manage", "team", "wardrobe", "lead", "break", "elevator"]);
  });

  it("builds the coding floor: workspaces as departments, focus zone and server room, no lead desks", () => {
    const roster = [agent("p1", "Personal Jarvis"), agent("p2", "Website"), agent("p3", "Personal Jarvis")];
    const layout = buildOfficeLayout(roster, { variant: "coding" });
    expect(layout.variant).toBe("coding");
    expect(layout.rooms.map((r) => r.kind)).toEqual(["focus", "team", "server", "reception", "break"]);
    expect(layout.lead.desks).toEqual([]);
    expect(layout.checkpoints.map((c) => c.id)).toEqual(["mission", "elevator", "break"]);
    expect(layout.departments.map((d) => d.label)).toEqual(["Personal Jarvis", "Website", "", ""]);
    expect(layout.furniture.some((f) => f.room === "lead" || f.room === "wardrobe")).toBe(false);
    expect(layout.spots.some((s) => s.room === "focus")).toBe(true);
    expect(layout.spots.some((s) => s.room === "server")).toBe(true);
    // The coding floor is wider (Mission Control's aisle), but the lobby is the
    // same: the elevator stands at the same spot relative to the west edge.
    const below = buildOfficeLayout(roster);
    const lift = (l: typeof layout) => l.checkpoints.find((c) => c.id === "elevator")!;
    expect(lift(layout).z).toBe(lift(below).z);
    expect(lift(layout).x - layout.bounds.minX).toBeCloseTo(lift(below).x - below.bounds.minX);
    expect(layout.spawn.x - layout.bounds.minX).toBeCloseTo(below.spawn.x - below.bounds.minX);
  });

  it("puts Mission Control in the middle of the coding floor, clear of every department", () => {
    const layout = buildOfficeLayout([agent("p1", "Personal Jarvis"), agent("p2", "Website")], { variant: "coding" });
    const hub = layout.furniture.find((f) => f.kind === "missionConsole")!;
    const stop = layout.checkpoints.find((c) => c.id === "mission")!;
    expect(hub.x).toBe(0);
    const depts = layout.departments;
    expect(hub.z).toBeCloseTo((Math.min(...depts.map((d) => d.minZ)) + Math.max(...depts.map((d) => d.maxZ))) / 2);
    const box = footprint(hub);
    for (const dept of depts) expect(box.maxX < dept.minX || box.minX > dept.maxX).toBe(true);
    // The stop is in front of the console (south, towards the camera), outside its footprint.
    expect(stop.x).toBe(0);
    expect(stop.z - stop.radius).toBeGreaterThan(box.maxZ - 0.5);
    expect(buildOfficeLayout([agent("a1", "codex")]).furniture.some((f) => f.kind === "missionConsole")).toBe(false);
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
