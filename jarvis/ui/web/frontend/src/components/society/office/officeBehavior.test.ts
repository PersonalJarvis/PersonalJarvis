import { describe, expect, it } from "vitest";
import type { AgentRunState } from "../data";
import { allDesks, buildOfficeLayout, seatOf, standOf, type DeskSlot, type OfficeAgentInput } from "./officeLayout";
import { buildNavGrid, isWalkable } from "./officeNav";
import { createRng, planFor, SpotBook, type ActivityKind, type PlanInput } from "./officeBehavior";

const roster: OfficeAgentInput[] = Array.from({ length: 8 }, (_, i) => ({
  agentId: `a${i}`, name: `a${i}`, tier: "specialist", providerLabel: i % 2 ? "Codex" : "Gemini", state: "idle", createdMs: i,
}));
const layout = buildOfficeLayout(roster);
const grid = buildNavGrid(layout);
const deskOf = (id: string): DeskSlot => allDesks(layout).find((d) => d.agentId === id)!;

function input(agentId: string, state: AgentRunState, extra: Partial<PlanInput> = {}): PlanInput {
  return {
    agentId, state, desk: deskOf(agentId), layout, grid, rng: createRng(agentId), book: new SpotBook(),
    previous: null, workingColleagues: [], calledTo: null, ...extra,
  };
}

describe("createRng", () => {
  it("is deterministic per seed and stays in [0, 1)", () => {
    const a = createRng("x"), b = createRng("x"), c = createRng("y");
    const sa = Array.from({ length: 20 }, a);
    expect(Array.from({ length: 20 }, b)).toEqual(sa);
    expect(Array.from({ length: 20 }, c)).not.toEqual(sa);
    expect(sa.every((v) => v >= 0 && v < 1)).toBe(true);
  });
});

describe("SpotBook", () => {
  it("gives a spot to one agent at a time and one spot per agent", () => {
    const book = new SpotBook();
    expect(book.claim("s1", "a")).toBe(true);
    expect(book.claim("s1", "b")).toBe(false);
    expect(book.holder("s1")).toBe("a");
    expect(book.claim("s2", "a")).toBe(true);
    expect(book.isFree("s1")).toBe(true);
    expect(book.holder("s2")).toBe("a");
    book.release("a");
    expect(book.isFree("s2")).toBe(true);
    expect(book.holder("s2")).toBeNull();
  });
});

describe("planFor", () => {
  it("seats a working agent at its desk, typing, until the state changes", () => {
    const plan = planFor(input("a1", "working"));
    const seat = seatOf(deskOf("a1"));
    expect(plan).toMatchObject({ kind: "work", target: { x: seat.x, z: seat.z }, facing: seat.facing, pose: "work", dwellMs: Infinity });
  });

  it("puts a deskless worker at a window", () => {
    const plan = planFor(input("ghost", "working", { desk: null }));
    expect(plan.kind).toBe("work");
    expect(plan.pose).toBe("work");
    expect(plan.spotId).toMatch(/^window-/);
  });

  it("stands a waiting agent beside its chair, waving", () => {
    const plan = planFor(input("a2", "waiting"));
    const stand = standOf(deskOf("a2"));
    expect(plan).toMatchObject({ kind: "wait", target: { x: stand.x, z: stand.z }, pose: "wave", dwellMs: Infinity });
  });

  it("sends paused agents to sleep, then to the couch, then to their desk", () => {
    const book = new SpotBook();
    const sleepSpots = layout.spots.filter((s) => s.pose === "sleep").length;
    // More seats than paused agents: the couches fill up with whoever is left after the beds.
    const couchSeats = Math.min(layout.spots.filter((s) => s.kind === "couch" && s.pose !== "sleep").length, roster.length - sleepSpots);
    const plans = roster.map((a) => planFor(input(a.agentId, "paused", { book })));
    expect(plans.every((p) => p.kind === "nap" && p.dwellMs === Infinity)).toBe(true);
    expect(plans.filter((p) => p.pose === "sleep")).toHaveLength(sleepSpots);
    const onCouch = plans.filter((p) => p.spotId && layout.spots.find((s) => s.id === p.spotId)?.kind === "couch" && p.pose === "sit");
    expect(onCouch).toHaveLength(couchSeats);
    const atDesk = plans.filter((p) => p.spotId === null);
    expect(atDesk.length).toBe(roster.length - sleepSpots - couchSeats);
    for (const p of atDesk) expect(p.pose).toBe("sit");
    const ids = plans.map((p) => p.spotId).filter(Boolean);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("answers a call by walking up to the caller and talking", () => {
    const caller = layout.checkpoints.find((c) => c.id === "create")!;
    const plan = planFor(input("a3", "idle", { calledTo: caller }));
    expect(plan.kind).toBe("called");
    expect(plan.pose).toBe("talk");
    expect(plan.dwellMs).toBe(15000);
    expect(isWalkable(grid, plan.target)).toBe(true);
    expect(Math.hypot(plan.target.x - caller.x, plan.target.z - caller.z)).toBeLessThan(2);
  });

  it("keeps a working agent at its screen even when called", () => {
    const caller = layout.checkpoints.find((c) => c.id === "create")!;
    expect(planFor(input("a3", "working", { calledTo: caller })).kind).toBe("work");
  });

  function idleSequence(seed: string, steps: number, colleagues: PlanInput["workingColleagues"] = []): ActivityKind[] {
    const rng = createRng(seed);
    const book = new SpotBook();
    const kinds: ActivityKind[] = [];
    let previous: ActivityKind | null = null;
    for (let i = 0; i < steps; i += 1) {
      const plan = planFor({ ...input("a4", "idle", { rng, book, previous, workingColleagues: colleagues }) });
      kinds.push(plan.kind);
      previous = plan.kind;
      expect(plan.dwellMs).toBeGreaterThanOrEqual(6000);
      expect(plan.dwellMs).toBeLessThanOrEqual(45000);
    }
    return kinds;
  }

  it("gives the same idle life for the same seed and never repeats an activity back to back", () => {
    const colleagues = [{ agentId: "a5", desk: deskOf("a5") }];
    const first = idleSequence("seed-1", 200, colleagues);
    expect(idleSequence("seed-1", 200, colleagues)).toEqual(first);
    expect(idleSequence("seed-2", 200, colleagues)).not.toEqual(first);
    for (let i = 1; i < first.length; i += 1) {
      // A full-spot fallback may land on wander after wander; a chosen activity never repeats.
      if (first[i] !== "wander") expect(first[i]).not.toBe(first[i - 1]);
    }
    expect(new Set(first).size).toBeGreaterThan(6);
  });

  it("visits a working colleague by standing next to their chair", () => {
    const colleague = { agentId: "a5", desk: deskOf("a5") };
    for (let i = 0; i < 200; i += 1) {
      const plan = planFor(input("a4", "idle", { rng: createRng(`visit-${i}`), workingColleagues: [colleague] }));
      if (plan.kind !== "visit") continue;
      const stand = standOf(colleague.desk);
      expect(plan).toMatchObject({ with: "a5", pose: "talk", target: { x: stand.x, z: stand.z } });
      return;
    }
    throw new Error("no visit in 200 seeds");
  });

  it("falls back to wandering when every spot of the chosen kind is taken", () => {
    const book = new SpotBook();
    for (const spot of layout.spots) book.claim(spot.id, `holder-${spot.id}`);
    const office = layout.departments;
    for (let i = 0; i < 50; i += 1) {
      const plan = planFor(input("a6", "idle", { rng: createRng(`full-${i}`), book, desk: null }));
      expect(["wander", "visit"]).toContain(plan.kind);
      expect(plan.spotId).toBeNull();
      if (plan.kind === "wander") {
        expect(plan.facing).toBeNull();
        expect(isWalkable(grid, plan.target)).toBe(true);
        expect(office.some((d) => plan.target.x >= d.minX - 3.3 && plan.target.x <= d.maxX + 3.3)).toBe(true);
      }
    }
  });

  it("releases an agent's spot when it goes back to work", () => {
    const book = new SpotBook();
    const idle = planFor(input("a7", "paused", { book }));
    expect(idle.spotId).not.toBeNull();
    expect(book.holder(idle.spotId!)).toBe("a7");
    planFor(input("a7", "working", { book }));
    expect(book.isFree(idle.spotId!)).toBe(true);
  });
});

describe("SpotBook.retain", () => {
  it("keeps claims on surviving spots and drops the rest", () => {
    const book = new SpotBook();
    book.claim("couch-a1", "a1");
    book.claim("window-dept-9", "a2");
    book.retain(new Set(["couch-a1"]));
    expect(book.holder("couch-a1")).toBe("a1");
    expect(book.isFree("window-dept-9")).toBe(true);
    // The dropped agent can claim again.
    expect(book.claim("couch-a2", "a2")).toBe(true);
  });
});
