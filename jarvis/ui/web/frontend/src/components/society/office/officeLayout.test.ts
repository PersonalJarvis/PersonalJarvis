import { describe, expect, it } from "vitest";
import {
  allDesks, archPosts, buildOfficeLayout, countStates, departmentKey, FURNITURE_SIZE, footprint, groupDepartments,
  MAX_DEPARTMENTS, MIN_DEPARTMENTS, COMMAND_DESK, COMMAND_REACH, LEVEL_HALL, SPAWN, type OfficeAgentInput,
} from "./officeLayout";
import { buildNavGrid, findPath, isWalkable } from "./officeNav";
import { REWARD_IDS } from "../progression/levelCatalog";
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
        // A ring centred on a solid prop (Mission Control's screen) walks to its approach point instead.
        const at = cp.approach ?? cp;
        const inside = solid.some((o) => at.x >= o.minX && at.x <= o.maxX && at.z >= o.minZ && at.z <= o.maxZ);
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
    expect(plain.rooms.map((r) => r.kind)).toEqual(["lead", "team", "wardrobe", "reception", "break", "levels"]);
    expect(plain.checkpoints.map((c) => c.id)).toEqual(["spawn", "create", "manage", "team", "wardrobe", "lead", "studio", "levels", "break", "elevator"]);
  });

  it.each([0, 12, 40])("builds the Level Hall as the agents floor's east wing (%i agents)", (count) => {
    const roster = Array.from({ length: count }, (_, i) => agent(`a${i}`, `P${i % 6}`));
    const layout = buildOfficeLayout(roster);
    const hall = layout.rooms.find((r) => r.kind === "levels")!;
    const office = Math.max(...layout.rooms.filter((r) => r.kind !== "levels").map((r) => r.maxX));
    // One aisle east of the office, across the floor's whole depth, walled with its doors facing the office.
    expect(hall.walled).toBe(true);
    expect(hall.minX - office).toBeCloseTo(LEVEL_HALL.aisle, 9);
    expect(hall.maxX - hall.minX).toBeCloseTo(LEVEL_HALL.width, 9);
    expect(hall.minZ).toBe(Math.min(...layout.rooms.map((r) => r.minZ)));
    expect(hall.maxZ).toBe(Math.max(...layout.rooms.map((r) => r.maxZ)));
    expect(hall.doors.every((d) => d.side === "west")).toBe(true);
    const spawn = layout.checkpoints.find((c) => c.id === "spawn")!;
    expect(hall.doors[0].at).toBe(spawn.z);
    // A pedestal per reward on either side of the road, inside the hall, rising towards the stage.
    const pedestals = layout.furniture.filter((f) => f.kind === "rewardPedestal");
    expect(pedestals.map((p) => p.id)).toEqual(REWARD_IDS.map((_, n) => `level-pedestal-${n}`));
    const cx = (hall.minX + hall.maxX) / 2;
    pedestals.forEach((p, n) => {
      const box = footprint(p);
      expect(box.minX > hall.minX && box.maxX < hall.maxX && box.minZ > hall.minZ && box.maxZ < hall.maxZ, p.id).toBe(true);
      expect(Math.sign(p.x - cx)).toBe(n % 2 === 0 ? -1 : 1);
      if (n >= 2) expect(p.z).toBeLessThan(pedestals[n - 2].z);
    });
    const stage = layout.furniture.find((f) => f.kind === "studioStage")!;
    const studio = layout.checkpoints.find((c) => c.id === "studio")!;
    expect([studio.x, studio.z]).toEqual([stage.x, stage.z]);
    expect(stage.z).toBeLessThan(Math.min(...pedestals.map((p) => p.z)));
    // Both checkpoints are reachable on foot from the spawn point, through each of the hall's doors.
    const grid = buildNavGrid(layout);
    const levels = layout.checkpoints.find((c) => c.id === "levels")!;
    for (const target of [studio.approach!, { x: levels.x, z: levels.z }]) {
      expect(isWalkable(grid, target)).toBe(true);
      expect(findPath(grid, layout.arrival, target)).not.toBeNull();
    }
    for (const door of hall.doors) {
      // The aisle in front of every door stays open: no olive tree closes it.
      expect(isWalkable(grid, { x: office + LEVEL_HALL.aisle / 2, z: door.at }), `door at ${door.at}`).toBe(true);
      expect(isWalkable(grid, { x: hall.minX + 0.6, z: door.at }), `inside door at ${door.at}`).toBe(true);
    }
  });

  it("builds the coding floor: workspaces as departments, Mission Control and server room, no lead desks", () => {
    const roster = [agent("p1", "Personal Jarvis"), agent("p2", "Website"), agent("p3", "Personal Jarvis")];
    const layout = buildOfficeLayout(roster, { variant: "coding" });
    expect(layout.variant).toBe("coding");
    expect(layout.rooms.map((r) => r.kind)).toEqual(["command", "team", "server", "reception", "break"]);
    expect(layout.lead.desks).toEqual([]);
    expect(layout.checkpoints.map((c) => c.id)).toEqual(["launch", "mission", "elevator", "break"]);
    expect(layout.departments.map((d) => d.label)).toEqual(["Personal Jarvis", "Website", "", ""]);
    expect(layout.furniture.some((f) => f.room === "lead" || f.room === "wardrobe")).toBe(false);
    expect(layout.spots.some((s) => s.room === "command")).toBe(true);
    expect(layout.spots.some((s) => s.room === "server")).toBe(true);
    // Both floors share the office's footprint (the agents floor adds the Level Hall as an east wing),
    // and the elevator stands at the same spot on each.
    const below = buildOfficeLayout(roster);
    expect(layout.rooms.some((r) => r.kind === "levels")).toBe(false);
    expect({ ...layout.bounds, maxX: 0 }).toEqual({ ...below.bounds, maxX: 0 });
    expect(below.bounds.maxX - layout.bounds.maxX).toBeCloseTo(LEVEL_HALL.aisle + LEVEL_HALL.width, 9);
    const lift = (l: typeof layout) => l.checkpoints.find((c) => c.id === "elevator")!;
    expect(lift(layout).z).toBe(lift(below).z);
    expect(lift(layout).x - layout.bounds.minX).toBeCloseTo(lift(below).x - below.bounds.minX);
    expect(layout.spawn.x - layout.bounds.minX).toBeCloseTo(below.spawn.x - below.bounds.minX);
  });

  it("builds Mission Control as its own office: slat wall, desk, lounge, and a ring round the desk", () => {
    const layout = buildOfficeLayout([agent("p1", "Personal Jarvis"), agent("p2", "Website")], { variant: "coding" });
    const room = layout.rooms.find((r) => r.kind === "command")!;
    const stop = layout.checkpoints.find((c) => c.id === "mission")!;
    const inRoom = (p: { x: number; z: number }) => p.x > room.minX && p.x < room.maxX && p.z > room.minZ && p.z < room.maxZ;
    for (const kind of ["commandWall", "commandDesk", "couch", "coffeeTable", "bookshelf"] as const) {
      expect(layout.furniture.some((f) => f.kind === kind && f.room === "command" && inRoom(f)), kind).toBe(true);
    }
    expect(stop.room).toBe("command");
    expect(stop.radius).toBe(COMMAND_REACH);
    // The whole ring stays inside the room's walls.
    expect(stop.x - stop.radius).toBeGreaterThan(room.minX);
    expect(stop.x + stop.radius).toBeLessThan(room.maxX);
    expect(stop.z - stop.radius).toBeGreaterThan(room.minZ);
    expect(stop.z + stop.radius).toBeLessThan(room.maxZ);
    expect(buildOfficeLayout([agent("a1", "codex")]).furniture.some((f) => f.kind === "commandDesk")).toBe(false);
  });

  it("lets the person work Mission Control's desk from every side, with no invisible walls", () => {
    const layout = buildOfficeLayout([agent("p1", "Personal Jarvis")], { variant: "coding" });
    const stop = layout.checkpoints.find((c) => c.id === "mission")!;
    const grid = buildNavGrid(layout);
    // A loop round the desk and chair: free to stand on everywhere, and inside the ring.
    for (let i = 0; i < 16; i += 1) {
      const a = (i / 16) * Math.PI * 2;
      // An ellipse: the desk is long and shallow, so the path round it is too.
      const p = { x: stop.x + Math.cos(a) * 1.85, z: stop.z + Math.sin(a) * 1.6 };
      expect(isWalkable(grid, p), `angle ${i}`).toBe(true);
      expect(Math.hypot(p.x - stop.x, p.z - stop.z)).toBeLessThanOrEqual(stop.radius);
    }
    // Only the desk top and the chair are solid: right beside the chair is open floor.
    // The desk faces the door; its chair stands between it and the wall.
    const deskZ = stop.z + 0.3;
    const chairZ = deskZ - COMMAND_DESK.chairZ;
    expect(isWalkable(grid, { x: stop.x, z: deskZ })).toBe(false);
    expect(isWalkable(grid, { x: stop.x, z: chairZ })).toBe(false);
    // Beside the chair, a step back from the desk edge, the floor is open on both sides.
    expect(isWalkable(grid, { x: stop.x + 0.75, z: chairZ - 0.25 })).toBe(true);
    expect(isWalkable(grid, { x: stop.x - 0.75, z: chairZ - 0.25 })).toBe(true);
    expect(isWalkable(grid, stop.approach!)).toBe(true);
  });

  for (const [variant, id] of [["agents", "spawn"], ["coding", "launch"]] as const) {
    for (const count of [0, 12, 40]) {
      it(`puts the spawn point on the aisle crossing in the middle of the floor (${variant}, ${count} agents)`, () => {
        const roster = Array.from({ length: count }, (_, i) => agent(`a${i}`, `P${i % 6}`));
        const layout = buildOfficeLayout(roster, { variant });
        const stop = layout.checkpoints.find((c) => c.id === id)!;
        const terminal = layout.furniture.find((f) => f.kind === "spawnTerminal")!;
        const pad = layout.furniture.find((f) => f.kind === "spawnPad")!;
        expect(stop.room).toBe("floor");
        expect([terminal.x, terminal.z]).toEqual([stop.x, stop.z]);
        expect([pad.x, pad.z]).toEqual([stop.x, stop.z]);
        // On the centre line between the two department columns, in a cross aisle nearest the floor's middle.
        const columns = { minX: Math.min(...layout.departments.map((d) => d.minX)), maxX: Math.max(...layout.departments.map((d) => d.maxX)) };
        expect(stop.x).toBeCloseTo((columns.minX + columns.maxX) / 2, 9);
        const rowStarts = [...new Set(layout.departments.map((d) => d.minZ))].sort((p, q) => p - q);
        const crossings = rowStarts.slice(1).map((start, i) => {
          const above = Math.max(...layout.departments.filter((d) => d.minZ === rowStarts[i]).map((d) => d.maxZ));
          return (above + start) / 2;
        });
        expect(crossings.length).toBeGreaterThan(0);
        const middle = (layout.bounds.minZ + layout.bounds.maxZ) / 2;
        const nearest = Math.min(...crossings.map((c) => Math.abs(c - middle)));
        expect(Math.abs(stop.z - middle)).toBeCloseTo(nearest, 6);
        // The whole pad stays clear of every department, and the ring sits on the pad.
        const padBox = footprint(pad);
        for (const dept of layout.departments) {
          const overlap = padBox.minX < dept.maxX && dept.minX < padBox.maxX && padBox.minZ < dept.maxZ && dept.minZ < padBox.maxZ;
          expect(overlap, dept.id).toBe(false);
        }
        expect(stop.radius).toBeLessThanOrEqual(FURNITURE_SIZE.spawnPad.w / 2);
        // The token floats above the terminal; newcomers appear where "walk there" ends, south of the screen.
        expect(stop.tokenY).toBeGreaterThan(FURNITURE_SIZE.spawnTerminal.h);
        expect(layout.arrival).toEqual(stop.approach);
        expect(layout.arrival.z - stop.z).toBeCloseTo(SPAWN.approach, 9);
        expect(isWalkable(buildNavGrid(layout), layout.arrival)).toBe(true);
      });
    }
  }

  it("keeps only one spawn point per floor, and the agents floor's Agent board in the lobby", () => {
    const agents = buildOfficeLayout([agent("a1", "Codex")]);
    const coding = buildOfficeLayout([agent("p1", "Personal Jarvis")], { variant: "coding" });
    for (const layout of [agents, coding]) {
      expect(layout.furniture.filter((f) => f.kind === "spawnTerminal")).toHaveLength(1);
      expect(layout.furniture.filter((f) => f.kind === "spawnPad")).toHaveLength(1);
    }
    const board = agents.furniture.find((f) => f.kind === "agentTotem")!;
    expect(board.room).toBe("reception");
    const manage = agents.checkpoints.find((c) => c.id === "manage")!;
    expect([manage.x, manage.z - 1.4]).toEqual([board.x, board.z]);
    expect(coding.furniture.some((f) => f.kind === "agentTotem")).toBe(false);
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
