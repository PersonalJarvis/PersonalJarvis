import { beforeEach, describe, expect, it } from "vitest";
import { createRng } from "./officeBehavior";
import {
  DOG_FOLLOW_MS, DOG_FOLLOW_NEAR, followPoint, insideRoom, nextDogStep, pickOtherBasket, useOfficeDog, type DogActivity,
} from "./dogLife";
import { buildOfficeLayout, type OfficeAgentInput } from "./officeLayout";
import { buildNavGrid, findPath } from "./officeNav";

const lead: OfficeAgentInput = { agentId: "l", name: "L", tier: "lead", providerLabel: "", state: "idle", createdMs: 1 };

describe("the office dog's day", () => {
  it("always finds its way back to a basket, and sometimes moves to another one", () => {
    const rng = createRng("dog-test");
    let moves = 0;
    for (let run = 0; run < 60; run += 1) {
      let activity: DogActivity = "sleep";
      let roamsLeft = 0;
      for (let step = 0; step < 40 && !(step > 0 && activity === "sleep"); step += 1) {
        const next = nextDogStep(activity, roamsLeft, rng, 3);
        activity = next.activity;
        roamsLeft = next.roamsLeft;
        if (activity === "move") moves += 1;
      }
      expect(activity).toBe("sleep");
    }
    expect(moves).toBeGreaterThan(0);
  });

  it("never moves house when there is only one basket", () => {
    const rng = createRng("one-basket");
    for (let i = 0; i < 100; i += 1) expect(nextDogStep("sleep", 0, rng, 1).activity).not.toBe("move");
  });

  it("picks a different basket to move to", () => {
    const rng = createRng("pick");
    for (let i = 0; i < 50; i += 1) {
      const next = pickOtherBasket(1, 3, rng);
      expect(next).not.toBe(1);
      expect(next).toBeGreaterThanOrEqual(0);
      expect(next).toBeLessThan(3);
    }
    expect(pickOtherBasket(0, 1, rng)).toBe(0);
  });

  it("follows the person after being petted, then goes home", () => {
    const rng = createRng("dog-pet");
    expect(nextDogStep("petted", 0, rng)).toMatchObject({ activity: "follow", durationMs: DOG_FOLLOW_MS });
    expect(nextDogStep("follow", 0, rng).activity).toBe("home");
  });

  it("carries the treat home after the trick and falls asleep after chewing", () => {
    const rng = createRng("dog-treat");
    expect(nextDogStep("trick", 0, rng).activity).toBe("home");
    expect(nextDogStep("chew", 0, rng).activity).toBe("sleep");
  });

  it("follows from a little behind the person, never on top of them", () => {
    const p = followPoint({ x: 0, z: 0, heading: 0 }, { x: 0, z: -3 });
    expect(p.z).toBeLessThan(0);
    expect(Math.hypot(p.x, p.z)).toBeGreaterThan(DOG_FOLLOW_NEAR * 0.8);
  });

  it("knows when the person has left its room", () => {
    const room = { minX: 0, maxX: 5, minZ: 0, maxZ: 4 };
    expect(insideRoom(room, { x: 2, z: 2 })).toBe(true);
    expect(insideRoom(room, { x: 2, z: 4.2 })).toBe(true);
    expect(insideRoom(room, { x: 2, z: 6 })).toBe(false);
  });
});

describe("petting and the treat", () => {
  beforeEach(() => useOfficeDog.setState({ near: false, nearJar: false, hasBone: false, pending: null }));

  it("pets once per press", () => {
    const before = useOfficeDog.getState().petSeq;
    useOfficeDog.setState({ near: true, pending: "pet" });
    expect(useOfficeDog.getState().interact()).toBe(true);
    expect(useOfficeDog.getState().petSeq).toBe(before + 1);
    expect(useOfficeDog.getState().pending).toBeNull();
  });

  it("takes a bone at the jar and gives it to the dog instead of petting", () => {
    const { petSeq, treatSeq } = useOfficeDog.getState();
    useOfficeDog.setState({ nearJar: true });
    expect(useOfficeDog.getState().interact()).toBe(true);
    expect(useOfficeDog.getState().hasBone).toBe(true);
    useOfficeDog.setState({ nearJar: false, near: true });
    useOfficeDog.getState().interact();
    expect(useOfficeDog.getState()).toMatchObject({ hasBone: false, treatSeq: treatSeq + 1, petSeq });
  });

  it("does nothing when neither the dog nor the jar is in reach", () => {
    expect(useOfficeDog.getState().interact()).toBe(false);
  });
});

describe("the dog on the floor plan", () => {
  const layout = buildOfficeLayout([lead]);
  const grid = buildNavGrid(layout);
  const beds = layout.furniture.filter((f) => f.kind === "dogBed");

  it("has a basket in the lead office, the team room and the break room", () => {
    expect(beds.map((b) => b.room).sort()).toEqual(["break", "lead", "team"]);
    const room = layout.rooms.find((r) => r.kind === "lead")!;
    const leadBed = beds.find((b) => b.room === "lead")!;
    expect(leadBed.x).toBeGreaterThan(room.maxX - 1);
    expect(leadBed.z).toBeLessThan(room.minZ + 1.5);
  });

  it("can walk from every basket to every other one", () => {
    for (const a of beds) {
      for (const b of beds) {
        if (a === b) continue;
        const path = findPath(grid, a, b);
        expect(path, `${a.id} -> ${b.id}`).not.toBeNull();
        const last = path![path!.length - 1];
        expect(Math.hypot(last.x - b.x, last.z - b.z)).toBeLessThan(0.01);
      }
    }
  });

  it("keeps the treat jar in the break room, reachable from the elevator", () => {
    const jar = layout.furniture.find((f) => f.kind === "treatJar")!;
    expect(jar.room).toBe("break");
    expect(findPath(grid, layout.spawn, { x: jar.x, z: jar.z + 0.6 })).not.toBeNull();
  });

  it("has no dog on the coding floor", () => {
    const coding = buildOfficeLayout([lead], { variant: "coding" });
    expect(coding.furniture.some((f) => f.kind === "dogBed" || f.kind === "treatJar")).toBe(false);
  });
});
