/**
 * The Verse's copy of the level system's ids (jarvis/progression/rules.py):
 * subject kinds, cosmetic slots, reward ids, title ids and XP sources. The
 * server sends levels and amounts; this file only knows what each id LOOKS
 * like. tests/unit/progression/test_frontend_parity.py pins every list here
 * to the Python rulebook (AP-4): add an id in Python first, then here.
 */

export const SUBJECT_KINDS = ["person", "agent", "pet"] as const;
export type SubjectKind = (typeof SUBJECT_KINDS)[number];

export const SLOTS = ["uniform", "headwear", "decoration"] as const;
export type Slot = (typeof SLOTS)[number];

export const REWARD_IDS = [
  "uniform_service_shirt", "uniform_field_jacket", "uniform_service_greens", "uniform_dress_blues", "uniform_mess_dress",
  "headwear_patrol_cap", "headwear_garrison_cap", "headwear_beret", "headwear_service_cap",
  "decoration_ribbon_bar", "decoration_ribbon_rack", "decoration_aiguillette", "decoration_medals",
] as const;
export type RewardId = (typeof REWARD_IDS)[number];

/** The rank ladder, lowest first: every subject kind wears it as its title. */
export const TITLE_IDS = [
  "private", "private_second_class", "private_first_class", "specialist", "corporal", "sergeant", "staff_sergeant",
  "sergeant_first_class", "master_sergeant", "first_sergeant", "sergeant_major", "command_sergeant_major",
  "sergeant_major_of_the_army", "second_lieutenant", "first_lieutenant", "captain", "major", "lieutenant_colonel",
  "colonel", "brigadier_general", "major_general", "lieutenant_general", "general", "general_of_the_army",
] as const;
export type RankId = (typeof TITLE_IDS)[number];

/** Where a rank sits: enlisted, non-commissioned officer, officer or general officer. */
export type RankTier = "enlisted" | "nco" | "officer" | "general";

/** Each rank's pay grade and tier. The level it starts at comes from the server (`titles`). */
export const RANK_INFO: Record<RankId, { grade: string; tier: RankTier }> = {
  private: { grade: "E-1", tier: "enlisted" },
  private_second_class: { grade: "E-2", tier: "enlisted" },
  private_first_class: { grade: "E-3", tier: "enlisted" },
  specialist: { grade: "E-4", tier: "enlisted" },
  corporal: { grade: "E-4", tier: "nco" },
  sergeant: { grade: "E-5", tier: "nco" },
  staff_sergeant: { grade: "E-6", tier: "nco" },
  sergeant_first_class: { grade: "E-7", tier: "nco" },
  master_sergeant: { grade: "E-8", tier: "nco" },
  first_sergeant: { grade: "E-8", tier: "nco" },
  sergeant_major: { grade: "E-9", tier: "nco" },
  command_sergeant_major: { grade: "E-9", tier: "nco" },
  sergeant_major_of_the_army: { grade: "E-9", tier: "nco" },
  second_lieutenant: { grade: "O-1", tier: "officer" },
  first_lieutenant: { grade: "O-2", tier: "officer" },
  captain: { grade: "O-3", tier: "officer" },
  major: { grade: "O-4", tier: "officer" },
  lieutenant_colonel: { grade: "O-5", tier: "officer" },
  colonel: { grade: "O-6", tier: "officer" },
  brigadier_general: { grade: "O-7", tier: "general" },
  major_general: { grade: "O-8", tier: "general" },
  lieutenant_general: { grade: "O-9", tier: "general" },
  general: { grade: "O-10", tier: "general" },
  general_of_the_army: { grade: "O-11", tier: "general" },
};

export function isRankId(value: unknown): value is RankId {
  return typeof value === "string" && value in RANK_INFO;
}

/** The rank held at `level`, from the server's bands (lowest first); private before the snapshot arrives. */
export function rankAt(bands: readonly { level: number; title: string }[] | undefined, level: number): RankId {
  let rank: RankId = "private";
  for (const band of bands ?? []) if (level >= band.level && isRankId(band.title)) rank = band.title;
  return rank;
}

export const XP_SOURCES = [
  "chat_turn", "voice_turn", "quest_posted", "quest_completed", "agent_hired", "mission_completed",
  "daily_visit", "floor_discovered", "dog_petted", "dog_treat", "arcade_round", "arcade_record", "team_meeting",
  "task_done", "task_blocked", "quest_done", "answered_teammate",
  "jarvis_answered", "delegated", "walk_together",
] as const;
export type XpSource = (typeof XP_SOURCES)[number];

/** The world actions the Verse itself reports (`trigger: "world"` in the rulebook). */
export type WorldAction = "daily_visit" | "floor_discovered" | "dog_petted" | "dog_treat" | "arcade_round"
  | "arcade_record" | "team_meeting" | "walk_together";

export function slotOf(reward: RewardId): Slot {
  return reward.slice(0, reward.indexOf("_")) as Slot;
}

export function isRewardId(value: unknown): value is RewardId {
  return typeof value === "string" && (REWARD_IDS as readonly string[]).includes(value);
}
