import { beforeEach, describe, expect, it } from "vitest";
import { equippedFor, levelFraction, progressFor, unlockedFor } from "./cosmetics";
import { parseAwardEvent, parseSnapshot, type ProgressionSnapshot, type RewardRow } from "./progressionApi";
import { awayCelebrations, useProgression } from "./progressionStore";
import { footprintAt, pruneSamples, pushSample, rainbowHue, RIBBON_MAX_SAMPLES, writeRibbon, type TrailSample } from "./effects/trailModel";
import { REWARD_IDS, slotOf } from "./levelCatalog";

const REWARDS: RewardRow[] = [
  { rewardId: "frame_bronze", slot: "frame", levels: { person: 3, agent: 3, pet: 3 } },
  { rewardId: "frame_silver", slot: "frame", levels: { person: 10, agent: 10, pet: 10 } },
  { rewardId: "trail_footprints", slot: "trail", levels: { person: 2, agent: null, pet: null } },
  { rewardId: "trail_sparkle", slot: "trail", levels: { person: 5, agent: 5, pet: 2 } },
  { rewardId: "aura_glow", slot: "aura", levels: { person: 7, agent: 8, pet: 5 } },
];

/** Total XP at which each level starts, the server's `level_xp` for the first few levels. */
const CURVE = [0, 40, 105, 195, 310, 450];

function snapshot(overrides: Partial<ProgressionSnapshot> = {}): ProgressionSnapshot {
  return {
    subjects: [], petId: "ember", recent: [], latestSeq: 0, maxLevel: 50, levelXp: CURVE, rules: [], rewards: REWARDS,
    titles: { person: [{ level: 1, title: "newcomer" }, { level: 5, title: "apprentice" }], agent: [], pet: [] },
    ...overrides,
  };
}

describe("cosmetics", () => {
  it("unlocks by the subject kind's own level", () => {
    expect(unlockedFor(REWARDS, "person", 2).map((r) => r.rewardId)).toEqual(["trail_footprints"]);
    expect(unlockedFor(REWARDS, "pet", 2).map((r) => r.rewardId)).toEqual(["trail_sparkle"]);
    expect(unlockedFor(REWARDS, "agent", 4).map((r) => r.rewardId)).toEqual(["frame_bronze"]);
  });

  it("wears the latest unlock per slot unless the person chose", () => {
    expect(equippedFor(REWARDS, "person", 7)).toEqual({ frame: "frame_bronze", trail: "trail_sparkle", aura: "aura_glow" });
    expect(equippedFor(REWARDS, "person", 7, { trail: "trail_footprints", aura: "none" }))
      .toEqual({ frame: "frame_bronze", trail: "trail_footprints" });
  });

  it("ignores a chosen piece that is not unlocked (any more)", () => {
    expect(equippedFor(REWARDS, "person", 4, { frame: "frame_silver" })).toEqual({ frame: "frame_bronze", trail: "trail_footprints" });
  });

  it("measures progress inside a level from the server's curve", () => {
    expect(progressFor(CURVE, 120, 3)).toEqual({ into: 15, size: 90 });
    expect(progressFor([0, 40], 999, 2)).toEqual({ into: 959, size: 0 });
    expect(levelFraction({ xpIntoLevel: 15, xpForNext: 60 })).toBe(0.25);
    expect(levelFraction({ xpIntoLevel: 0, xpForNext: 0 })).toBe(1);
    expect(levelFraction(undefined)).toBe(0);
  });
});

describe("progressionApi", () => {
  it("parses the snapshot and drops unknown rewards", () => {
    const parsed = parseSnapshot({
      subjects: [{ subject_id: "person", kind: "person", xp: 50, level: 2, xp_into_level: 10, xp_for_next: 65, title: "newcomer" }],
      pet_id: "miso", recent: [], latest_seq: 7, max_level: 50, level_xp: [0, 40],
      rules: [{ source: "chat_turn", kind: "person", xp: 5, trigger: "server", cooldown_s: 0, daily_cap: 100 }],
      rewards: [{ reward_id: "aura_glow", slot: "aura", levels: { person: 7, agent: 8, pet: null } }, { reward_id: "hat_tall", slot: "gadget", levels: {} }],
      titles: { person: [{ level: 1, title: "newcomer" }] },
    });
    expect(parsed.subjects[0]).toMatchObject({ subjectId: "person", level: 2, xpIntoLevel: 10, xpForNext: 65 });
    expect(parsed.petId).toBe("miso");
    expect(parsed.rewards.map((r) => r.rewardId)).toEqual(["aura_glow"]);
    expect(parsed.rewards[0].levels.pet).toBeNull();
    expect(parsed.rules[0]).toMatchObject({ source: "chat_turn", dailyCap: 100 });
  });

  it("parses an award push and refuses one without a subject", () => {
    expect(parseAwardEvent({ subject_id: "agent:scout", subject_kind: "agent", xp: 40, total_xp: 108, level: 3, previous_level: 2, unlocked: ["frame_bronze", "nope"] }))
      .toMatchObject({ subjectId: "agent:scout", kind: "agent", level: 3, unlocked: ["frame_bronze"] });
    expect(parseAwardEvent({ xp: 5 })).toBeNull();
    expect(parseAwardEvent(null)).toBeNull();
  });
});

describe("progressionStore", () => {
  beforeEach(() => {
    useProgression.setState({ snapshot: null, subjects: {}, banners: [], toasts: [], popups: [], bursts: [], cheerUntil: 0, choices: {} });
    useProgression.getState().hydrate(snapshot(), []);
  });

  it("applies a gain as a popup without a celebration", () => {
    useProgression.getState().apply({ seq: 1, subjectId: "person", kind: "person", source: "chat_turn", xp: 5, totalXp: 5, level: 1, previousLevel: 1, title: "newcomer", unlocked: [] }, 1000);
    const s = useProgression.getState();
    expect(s.subjects.person).toMatchObject({ xp: 5, xpIntoLevel: 5, xpForNext: 40 });
    expect(s.popups).toHaveLength(1);
    expect(s.banners).toHaveLength(0);
    expect(s.bursts).toHaveLength(0);
  });

  it("celebrates the person with a banner, a burst and a cheer; an agent with a toast", () => {
    useProgression.getState().apply({ seq: 2, subjectId: "person", kind: "person", source: "agent_hired", xp: 50, totalXp: 50, level: 2, previousLevel: 1, title: "newcomer", unlocked: ["trail_footprints"] }, 1000);
    useProgression.getState().apply({ seq: 3, subjectId: "agent:scout", kind: "agent", source: "task_done", xp: 40, totalXp: 40, level: 2, previousLevel: 1, title: "rookie", unlocked: [] }, 1000);
    const s = useProgression.getState();
    expect(s.banners.map((b) => b.subjectId)).toEqual(["person"]);
    expect(s.toasts.map((b) => b.subjectId)).toEqual(["agent:scout"]);
    expect(s.bursts).toHaveLength(2);
    expect(s.cheerUntil).toBeGreaterThan(1000);
  });

  it("never rolls a subject back to a read older than a push", () => {
    useProgression.getState().apply({ seq: 9, subjectId: "person", kind: "person", source: "agent_hired", xp: 50, totalXp: 50, level: 2, previousLevel: 1, title: "newcomer", unlocked: [] }, 0);
    useProgression.getState().hydrate(snapshot({ subjects: [{ subjectId: "person", kind: "person", xp: 25, level: 1, xpIntoLevel: 25, xpForNext: 40, title: "newcomer" }] }), []);
    expect(useProgression.getState().subjects.person.xp).toBe(50);
  });

  it("ignores a late duplicate push", () => {
    const award = { seq: 4, subjectId: "person", kind: "person" as const, source: "chat_turn", xp: 5, totalXp: 5, level: 1, previousLevel: 1, title: "newcomer", unlocked: [] };
    useProgression.getState().apply(award, 1000);
    useProgression.getState().apply(award, 1000);
    expect(useProgression.getState().popups).toHaveLength(1);
  });

  it("expires popups and bursts on the scene clock", () => {
    useProgression.getState().apply({ seq: 5, subjectId: "person", kind: "person", source: "agent_hired", xp: 50, totalXp: 50, level: 2, previousLevel: 1, title: "newcomer", unlocked: [] }, 0);
    useProgression.getState().expire(100_000);
    expect(useProgression.getState().popups).toHaveLength(0);
    expect(useProgression.getState().bursts).toHaveLength(0);
  });

  it("tells each subject's missed level-ups once, and nothing on a first visit", () => {
    const missed = snapshot({
      subjects: [{ subjectId: "agent:scout", kind: "agent", xp: 200, level: 4, xpIntoLevel: 5, xpForNext: 115, title: "rookie" }],
      recent: [
        { seq: 3, subjectId: "agent:scout", kind: "agent", source: "task_done", xp: 40, levelBefore: 1, levelAfter: 2, tsMs: 0 },
        { seq: 4, subjectId: "agent:scout", kind: "agent", source: "quest_done", xp: 60, levelBefore: 2, levelAfter: 4, tsMs: 0 },
        { seq: 5, subjectId: "person", kind: "person", source: "chat_turn", xp: 5, levelBefore: 1, levelAfter: 1, tsMs: 0 },
      ],
    });
    const away = awayCelebrations(missed, 2);
    expect(away).toHaveLength(1);
    expect(away[0]).toMatchObject({ subjectId: "agent:scout", level: 4, previousLevel: 1, away: true, unlocked: ["frame_bronze"] });
    expect(awayCelebrations(missed, null)).toEqual([]);
    expect(awayCelebrations(missed, 4)).toEqual([]);
  });
});

describe("trail model", () => {
  it("samples a ribbon by distance, caps it and restarts after a teleport", () => {
    const samples: TrailSample[] = [];
    expect(pushSample(samples, 0, 0, 0)).toBe(true);
    expect(pushSample(samples, 0.05, 0, 0.1)).toBe(false);
    for (let i = 1; i <= 40; i++) pushSample(samples, i * 0.2, 0, i);
    expect(samples).toHaveLength(RIBBON_MAX_SAMPLES);
    pushSample(samples, 100, 100, 50);
    expect(samples).toHaveLength(1);
  });

  it("ages samples out", () => {
    const samples: TrailSample[] = [{ x: 0, z: 0, t: 0 }, { x: 1, z: 0, t: 1 }];
    pruneSamples(samples, 1.5, 1);
    expect(samples).toEqual([{ x: 1, z: 0, t: 1 }]);
  });

  it("writes a strip that is widest and brightest at the head", () => {
    const samples: TrailSample[] = [{ x: 0, z: 0, t: 1 }, { x: 0, z: 1, t: 1 }, { x: 0, z: 2, t: 1 }];
    const positions = new Float32Array(samples.length * 6);
    const bright = writeRibbon(samples, 1, 1, 0.4, 0.02, positions);
    expect(bright[0]).toBe(0);
    expect(bright[2]).toBe(1);
    const width = (i: number) => Math.hypot(positions[i * 6] - positions[i * 6 + 3], positions[i * 6 + 2] - positions[i * 6 + 5]);
    expect(width(2)).toBeCloseTo(0.4);
    expect(width(0)).toBeCloseTo(0);
    expect(positions[1]).toBeCloseTo(0.02);
  });

  it("places footprints left and right of the heading", () => {
    const left = footprintAt(0, 0, 0, true);
    const right = footprintAt(0, 0, 0, false);
    expect(left.x).toBeGreaterThan(0);
    expect(right.x).toBeLessThan(0);
    expect(rainbowHue(0.5, 0)).toBeGreaterThanOrEqual(0);
  });
});

describe("level catalog", () => {
  it("names every reward after its slot", () => {
    for (const id of REWARD_IDS) expect(["frame", "trail", "aura", "gadget"]).toContain(slotOf(id));
  });
});
