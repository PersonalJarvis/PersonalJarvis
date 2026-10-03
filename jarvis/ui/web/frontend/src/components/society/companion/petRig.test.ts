import { describe, expect, it } from "vitest";
import { BUILTIN_COMPANIONS, type CompanionPet } from "./petCompanions";
import { createRigState, rigPose, stepRig, type PetMood } from "./petRig";

const DT = 1 / 60;
const spec = (id: string): CompanionPet => ({ id, ...BUILTIN_COMPANIONS[id] });

describe("pet rig", () => {
  it("advances the gait by ground covered, so the paws never slide", () => {
    const miso = spec("miso");
    const slow = createRigState(), fast = createRigState();
    for (let i = 0; i < 30; i++) {
      stepRig(slow, miso, { dt: DT, speed: 0.5, mood: "idle", reduced: false });
      stepRig(fast, miso, { dt: DT, speed: 1, mood: "idle", reduced: false });
    }
    expect(slow.phase).toBeCloseTo(((0.5 * 30 * DT) / miso.strideM) * Math.PI * 2, 5);
    expect(fast.phase).toBeCloseTo((slow.phase * 2) % (Math.PI * 2), 5);
  });

  it("swings legs while walking and brings them to rest when standing", () => {
    const miso = spec("miso");
    const state = createRigState();
    let maxSwing = 0;
    for (let i = 0; i < 120; i++) {
      stepRig(state, miso, { dt: DT, speed: 1.2, mood: "idle", reduced: false });
      maxSwing = Math.max(maxSwing, Math.abs(rigPose(miso, state, "idle").get("miso_Leg_FL")!.rx));
    }
    expect(maxSwing).toBeGreaterThan(0.3);
    for (let i = 0; i < 240; i++) stepRig(state, miso, { dt: DT, speed: 0, mood: "idle", reduced: false });
    expect(Math.abs(rigPose(miso, state, "idle").get("miso_Leg_FL")!.rx)).toBeLessThan(0.05);
  });

  it("keeps the trot diagonal: front-left moves with back-right", () => {
    const miso = spec("miso");
    const state = createRigState();
    for (let i = 0; i < 40; i++) stepRig(state, miso, { dt: DT, speed: 1, mood: "idle", reduced: false });
    const pose = rigPose(miso, state, "idle");
    expect(pose.get("miso_Leg_FL")!.rx).toBeCloseTo(pose.get("miso_Leg_BR")!.rx);
    expect(pose.get("miso_Leg_FL")!.rx).toBeCloseTo(-pose.get("miso_Leg_FR")!.rx);
  });

  it("beats the dragon's wings in mirror image", () => {
    const ember = spec("ember");
    const state = createRigState();
    for (let i = 0; i < 17; i++) stepRig(state, ember, { dt: DT, speed: 0, mood: "idle", reduced: false });
    const pose = rigPose(ember, state, "idle");
    expect(pose.get("ember_Wing_L")!.rz).toBeCloseTo(-pose.get("ember_Wing_R")!.rz);
    expect(Math.abs(pose.get("ember_Wing_R")!.rz)).toBeGreaterThan(0);
  });

  it("closes the eyes asleep and freezes every gait under reduced motion", () => {
    const moods: PetMood[] = ["idle", "work", "talk", "sleep"];
    for (const id of Object.keys(BUILTIN_COMPANIONS)) {
      const pet = spec(id);
      for (const mood of moods) {
        const state = createRigState();
        for (let i = 0; i < 90; i++) stepRig(state, pet, { dt: DT, speed: 1.5, mood, reduced: true });
        expect(state.stride).toBe(0);
        expect(state.t).toBe(0);
        const pose = rigPose(pet, state, mood);
        for (const motion of pose.values()) {
          for (const value of Object.values(motion)) expect(Number.isFinite(value)).toBe(true);
        }
        const eye = pose.get(`${id}_Eye_L`);
        expect(eye).toBeDefined();
        if (mood === "sleep") expect(eye!.sy).toBeLessThan(0.2);
      }
    }
  });

  it("fills the battery's charge bars one by one while working", () => {
    const bolt = spec("bolt");
    const state = createRigState();
    const levels = new Set<number>();
    for (let i = 0; i < 120; i++) {
      stepRig(state, bolt, { dt: DT, speed: 0, mood: "work", reduced: false });
      const pose = rigPose(bolt, state, "work");
      levels.add([1, 2, 3].filter((n) => pose.get(`bolt_Bar_${n}`)!.sx > 0.5).length);
    }
    expect([...levels].sort()).toEqual([0, 1, 2, 3]);
  });
});
