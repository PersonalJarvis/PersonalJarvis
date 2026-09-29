import { describe, expect, it } from "vitest";
import { createRng } from "./officeBehavior";
import { DOG_FOLLOW_MS, DOG_FOLLOW_NEAR, followPoint, nextDogStep, useOfficeDog, type DogActivity } from "./dogLife";
import { buildOfficeLayout, type OfficeAgentInput } from "./officeLayout";

describe("the office dog's day", () => {
  it("always finds its way back to the basket", () => {
    const rng = createRng("dog-test");
    for (let run = 0; run < 50; run += 1) {
      let activity: DogActivity = "sleep";
      let roamsLeft = 0;
      const seen = new Set<DogActivity>();
      for (let step = 0; step < 40 && !(step > 0 && activity === "sleep"); step += 1) {
        const next = nextDogStep(activity, roamsLeft, rng);
        activity = next.activity;
        roamsLeft = next.roamsLeft;
        seen.add(activity);
      }
      expect(activity).toBe("sleep");
      expect(seen.has("home")).toBe(true);
      expect(seen.has("roam")).toBe(true);
    }
  });

  it("follows the person after being petted, then goes home", () => {
    const rng = createRng("dog-pet");
    const follow = nextDogStep("petted", 0, rng);
    expect(follow).toMatchObject({ activity: "follow", durationMs: DOG_FOLLOW_MS });
    expect(nextDogStep("follow", 0, rng).activity).toBe("home");
  });

  it("follows from a little behind the person, never on top of them", () => {
    const person = { x: 0, z: 0, heading: 0 };
    const p = followPoint(person, { x: 0, z: -3 });
    expect(p.z).toBeLessThan(0);
    expect(Math.hypot(p.x, p.z)).toBeGreaterThan(DOG_FOLLOW_NEAR * 0.8);
  });

  it("counts each pet once", () => {
    const before = useOfficeDog.getState().petSeq;
    useOfficeDog.getState().set({ pending: true });
    useOfficeDog.getState().pet();
    expect(useOfficeDog.getState().petSeq).toBe(before + 1);
    expect(useOfficeDog.getState().pending).toBe(false);
  });

  it("keeps the basket in the lead office's north-east corner", () => {
    const lead: OfficeAgentInput = { agentId: "l", name: "L", tier: "lead", providerLabel: "", state: "idle", createdMs: 1 };
    const layout = buildOfficeLayout([lead]);
    const room = layout.rooms.find((r) => r.kind === "lead")!;
    const bed = layout.furniture.find((f) => f.kind === "dogBed")!;
    expect(bed.x).toBeGreaterThan(room.maxX - 1);
    expect(bed.z).toBeLessThan(room.minZ + 1.5);
  });
});
