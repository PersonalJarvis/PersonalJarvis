import { beforeEach, describe, expect, it } from "vitest";
import { equippedFor, levelFraction, progressFor, unlockedFor } from "./cosmetics";
import { parseAwardEvent, parseSnapshot, type ProgressionSnapshot, type RewardRow } from "./progressionApi";
import { awayCelebrations, useProgression } from "./progressionStore";
import { RANK_INFO, rankAt, REWARD_IDS, slotOf, TITLE_IDS } from "./levelCatalog";
import { rankArt } from "./insignia/rankArt";
import { rackRows, ribbonsFor } from "./regalia/decorations3d";
import { dressedLook, regaliaFor, uniformStyle } from "./regalia/dress";
import type { ToyLook } from "../office/toyFigureModel";

const REWARDS: RewardRow[] = [
  { rewardId: "decoration_ribbon_bar", slot: "decoration", levels: { person: 3, agent: 3, pet: null } },
  { rewardId: "decoration_ribbon_rack", slot: "decoration", levels: { person: 10, agent: 10, pet: null } },
  { rewardId: "uniform_service_shirt", slot: "uniform", levels: { person: 2, agent: null, pet: null } },
  { rewardId: "uniform_field_jacket", slot: "uniform", levels: { person: 5, agent: null, pet: null } },
  { rewardId: "headwear_patrol_cap", slot: "headwear", levels: { person: 7, agent: null, pet: null } },
];

const BANDS = [{ level: 1, title: "private" }, { level: 2, title: "private_second_class" }, { level: 4, title: "private_first_class" }];

/** Total XP at which each level starts, the server's `level_xp` for the first few levels. */
const CURVE = [0, 40, 105, 195, 310, 450];

function snapshot(overrides: Partial<ProgressionSnapshot> = {}): ProgressionSnapshot {
  return {
    subjects: [], petId: "ember", recent: [], latestSeq: 0, maxLevel: 50, levelXp: CURVE, rules: [], rewards: REWARDS,
    titles: { person: BANDS, agent: BANDS, pet: BANDS },
    ...overrides,
  };
}

describe("cosmetics", () => {
  it("unlocks by the subject kind's own level", () => {
    expect(unlockedFor(REWARDS, "person", 2).map((r) => r.rewardId)).toEqual(["uniform_service_shirt"]);
    expect(unlockedFor(REWARDS, "pet", 50)).toEqual([]);
    expect(unlockedFor(REWARDS, "agent", 4).map((r) => r.rewardId)).toEqual(["decoration_ribbon_bar"]);
  });

  it("wears the latest unlock per slot unless the person chose", () => {
    expect(equippedFor(REWARDS, "person", 7))
      .toEqual({ decoration: "decoration_ribbon_bar", uniform: "uniform_field_jacket", headwear: "headwear_patrol_cap" });
    expect(equippedFor(REWARDS, "person", 7, { uniform: "uniform_service_shirt", headwear: "none" }))
      .toEqual({ decoration: "decoration_ribbon_bar", uniform: "uniform_service_shirt" });
  });

  it("ignores a chosen piece that is not unlocked (any more)", () => {
    expect(equippedFor(REWARDS, "person", 4, { decoration: "decoration_ribbon_rack" }))
      .toEqual({ decoration: "decoration_ribbon_bar", uniform: "uniform_service_shirt" });
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
      rewards: [{ reward_id: "headwear_beret", slot: "headwear", levels: { person: 24, agent: null, pet: null } }, { reward_id: "trail_rainbow", slot: "trail", levels: {} }],
      titles: { person: [{ level: 1, title: "newcomer" }] },
    });
    expect(parsed.subjects[0]).toMatchObject({ subjectId: "person", level: 2, xpIntoLevel: 10, xpForNext: 65 });
    expect(parsed.petId).toBe("miso");
    expect(parsed.rewards.map((r) => r.rewardId)).toEqual(["headwear_beret"]);
    expect(parsed.rewards[0].levels.pet).toBeNull();
    expect(parsed.rules[0]).toMatchObject({ source: "chat_turn", dailyCap: 100 });
  });

  it("parses an award push and refuses one without a subject", () => {
    expect(parseAwardEvent({ subject_id: "agent:scout", subject_kind: "agent", xp: 40, total_xp: 108, level: 3, previous_level: 2, unlocked: ["decoration_ribbon_bar", "gadget_crown"] }))
      .toMatchObject({ subjectId: "agent:scout", kind: "agent", level: 3, unlocked: ["decoration_ribbon_bar"] });
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
    useProgression.getState().apply({ seq: 2, subjectId: "person", kind: "person", source: "agent_hired", xp: 50, totalXp: 50, level: 2, previousLevel: 1, title: "newcomer", unlocked: ["uniform_service_shirt"] }, 1000);
    useProgression.getState().apply({ seq: 3, subjectId: "agent:scout", kind: "agent", source: "task_done", xp: 40, totalXp: 40, level: 2, previousLevel: 1, title: "rookie", unlocked: [] }, 1000);
    const s = useProgression.getState();
    expect(s.banners.map((b) => b.subjectId)).toEqual(["person"]);
    expect(s.toasts.map((b) => b.subjectId)).toEqual(["agent:scout"]);
    expect(s.bursts).toHaveLength(2);
    // The burst carries the climb, so the world can tell a promotion from a level.
    expect(s.bursts[0]).toMatchObject({ level: 2, previousLevel: 1, title: "newcomer" });
    expect(s.cheerUntil).toBeGreaterThan(1000);
  });

  it("never rolls a subject back to a read older than a push", () => {
    useProgression.getState().apply({ seq: 9, subjectId: "person", kind: "person", source: "agent_hired", xp: 50, totalXp: 50, level: 2, previousLevel: 1, title: "newcomer", unlocked: [] }, 0);
    useProgression.getState().hydrate(snapshot({ subjects: [{ subjectId: "person", kind: "person", xp: 25, level: 1, xpIntoLevel: 25, xpForNext: 40, title: "newcomer" }] }), []);
    expect(useProgression.getState().subjects.person.xp).toBe(50);
  });

  it("measures a push that arrived before the first read once the curve is there", () => {
    useProgression.setState({ snapshot: null, subjects: {} });
    // No curve yet: the level is known, the distance to the next one is not.
    useProgression.getState().apply({ seq: 6, subjectId: "person", kind: "person", source: "daily_visit", xp: 25, totalXp: 130, level: 3, previousLevel: 3, title: "private_second_class", unlocked: [] }, 0);
    expect(useProgression.getState().subjects.person.xpForNext).toBe(0);
    // The read is older than the push (105 XP), so the push stays — measured on the curve now.
    useProgression.getState().hydrate(snapshot({ subjects: [{ subjectId: "person", kind: "person", xp: 105, level: 3, xpIntoLevel: 0, xpForNext: 90, title: "private_second_class" }] }), []);
    expect(useProgression.getState().subjects.person).toMatchObject({ xp: 130, xpIntoLevel: 25, xpForNext: 90 });
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
    expect(away[0]).toMatchObject({ subjectId: "agent:scout", level: 4, previousLevel: 1, away: true, unlocked: ["decoration_ribbon_bar"] });
    expect(awayCelebrations(missed, null)).toEqual([]);
    expect(awayCelebrations(missed, 4)).toEqual([]);
  });
});

describe("rank insignia", () => {
  it("climbs from no insignia to five stars, sleeve first, shoulder for officers", () => {
    expect(rankArt("private").parts).toEqual([]);
    expect(rankArt("private").mount).toBe("none");
    expect(rankArt("sergeant").mount).toBe("sleeve");
    expect(rankArt("second_lieutenant").mount).toBe("shoulder");
    expect(rankArt("general_of_the_army").mount).toBe("shoulder");
    // Chevrons and rockers count up: one chevron, then three, then three with three rockers.
    const gold = (rank: Parameters<typeof rankArt>[0]) => rankArt(rank).parts.filter((p) => p.finish === "gold" && !p.detail).length;
    expect(gold("private_second_class")).toBe(1);
    expect(gold("sergeant")).toBe(3);
    expect(gold("master_sergeant")).toBe(6);
    expect(gold("first_sergeant")).toBe(7);
    // Generals: one star per grade, five at the cap.
    expect(rankArt("brigadier_general").parts).toHaveLength(1);
    expect(rankArt("general").parts).toHaveLength(4);
    expect(rankArt("general_of_the_army").parts).toHaveLength(5);
  });

  it("draws every polygon inside its own box", () => {
    for (const rank of TITLE_IDS) {
      const art = rankArt(rank);
      for (const part of art.parts) {
        for (const [x, y] of part.pts) {
          expect(x).toBeGreaterThanOrEqual(-1);
          expect(x).toBeLessThanOrEqual(art.w + 1);
          expect(y).toBeGreaterThanOrEqual(-1);
          expect(y).toBeLessThanOrEqual(art.h + 1);
        }
      }
    }
  });

  it("reads the rank off the server's bands, private before any arrive", () => {
    expect(rankAt(BANDS, 3)).toBe("private_second_class");
    expect(rankAt(BANDS, 50)).toBe("private_first_class");
    expect(rankAt(undefined, 30)).toBe("private");
    expect(RANK_INFO.colonel).toEqual({ grade: "O-6", tier: "officer" });
  });
});

describe("regalia", () => {
  const look = { skin: "#f1c4a0", hair: "#2a1d15", hairStyle: "short", shirt: "#3f7fd6", shirtAccent: "#e5674f", inner: "#ffffff",
    pants: "#2f4a7a", shoes: "#f4f4f2", blush: false, outfit: "hoodie", eyewear: "none" } as ToyLook;

  it("dresses the figure in the uniform's colours and keeps its own face", () => {
    expect(dressedLook(look, undefined, "sergeant")).toBe(look);
    const blues = dressedLook(look, "uniform_dress_blues", "captain");
    expect(blues).toMatchObject({ skin: look.skin, hair: look.hair, shirt: "#1c2541", outfit: "suit" });
    // Officers wear gold braid on the cuffs; a private's blues have no trouser stripe.
    expect(blues.uniform?.cuffBraid).toBeTruthy();
    expect(uniformStyle("uniform_dress_blues", "private").stripe).toBeUndefined();
    expect(uniformStyle("uniform_mess_dress", "private").tieKind).toBe("bow");
  });

  it("wears enlisted rank on the sleeves, officer rank on the shoulders, and nothing else unworn", () => {
    expect(regaliaFor("sergeant", {})).toMatchObject({ shoulder: undefined, chest: undefined, hat: undefined });
    expect(regaliaFor("sergeant", {}).sleeve).toBeTruthy();
    expect(regaliaFor("colonel", {}).sleeve).toBeUndefined();
    expect(regaliaFor("colonel", {}).shoulder).toBeTruthy();
    expect(regaliaFor("private", {}).sleeve).toBeUndefined();
    const full = regaliaFor("major", { headwear: "headwear_service_cap", decoration: "decoration_medals" });
    expect(full.hat).toBeTruthy();
    expect(full.chest).toBeTruthy();
  });

  it("grows the ribbon rack with the decoration, rows of three", () => {
    expect(ribbonsFor("decoration_ribbon_bar")).toHaveLength(3);
    expect(ribbonsFor("decoration_ribbon_rack")).toHaveLength(9);
    expect(ribbonsFor("decoration_medals")).toHaveLength(5);
    expect(rackRows(9)).toEqual([3, 3, 3]);
    expect(rackRows(5)).toEqual([2, 3]);
  });
});

describe("level catalog", () => {
  it("names every reward after its slot", () => {
    for (const id of REWARD_IDS) expect(["uniform", "headwear", "decoration"]).toContain(slotOf(id));
  });
});
