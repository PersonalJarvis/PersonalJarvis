/**
 * The Verse's copy of the level system's ids (jarvis/progression/rules.py):
 * subject kinds, cosmetic slots, reward ids, title ids and XP sources. The
 * server sends levels and amounts; this file only knows what each id LOOKS
 * like. tests/unit/progression/test_frontend_parity.py pins every list here
 * to the Python rulebook (AP-4): add an id in Python first, then here.
 */

export const SUBJECT_KINDS = ["person", "agent", "pet"] as const;
export type SubjectKind = (typeof SUBJECT_KINDS)[number];

export const SLOTS = ["frame", "trail", "aura", "gadget"] as const;
export type Slot = (typeof SLOTS)[number];

export const REWARD_IDS = [
  "frame_bronze", "frame_silver", "frame_gold", "frame_diamond",
  "trail_footprints", "trail_sparkle", "trail_comet", "trail_neon", "trail_rainbow", "trail_stardust",
  "aura_glow", "aura_runes", "aura_storm", "aura_legend",
  "gadget_drone", "gadget_halo", "gadget_crown", "gadget_wings",
] as const;
export type RewardId = (typeof REWARD_IDS)[number];

export const TITLE_IDS = [
  "newcomer", "apprentice", "operator", "specialist", "strategist", "architect", "commander", "visionary", "legend",
  "rookie", "trainee", "associate", "professional", "expert", "veteran", "elite", "grandmaster",
  "hatchling", "buddy", "sidekick", "partner", "guardian", "champion", "mythic",
] as const;

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

/** Frame tiers colour the level chip on name plates and the HUD ring. */
export const FRAME_STYLE: Record<"frame_none" | Extract<RewardId, `frame_${string}`>, { ring: string; fill: string; ink: string }> = {
  frame_none: { ring: "#94a3b8", fill: "#334155", ink: "#f8fafc" },
  frame_bronze: { ring: "#d08a4f", fill: "#6b3b1c", ink: "#ffe8d3" },
  frame_silver: { ring: "#d7dee8", fill: "#4b5563", ink: "#ffffff" },
  frame_gold: { ring: "#ffd25e", fill: "#8a5a00", ink: "#fff7da" },
  frame_diamond: { ring: "#9be8ff", fill: "#1d4e89", ink: "#ffffff" },
};

/**
 * The colours of each effect in the diorama. The world keeps one palette in
 * light and dark mode (office-map.md §2), so these are fixed, not tokens.
 */
export const EFFECT_COLOURS: Record<Exclude<RewardId, `frame_${string}`>, readonly string[]> = {
  trail_footprints: ["#ffe2a8"],
  trail_sparkle: ["#fff1b8", "#ffd166"],
  trail_comet: ["#7dd3fc", "#ffffff"],
  trail_neon: ["#22d3ee", "#e879f9"],
  trail_rainbow: ["#ff5e5e", "#ffb347", "#fff275", "#6ee7a0", "#5ec8ff", "#b18cff"],
  trail_stardust: ["#c4b5fd", "#ffffff", "#93c5fd"],
  aura_glow: ["#ffd98a"],
  aura_runes: ["#7dd3fc"],
  aura_storm: ["#a78bfa", "#e0f2fe"],
  aura_legend: ["#ff8ad8", "#ffe36e", "#7af0ff"],
  gadget_drone: ["#e2e8f0", "#38bdf8"],
  gadget_halo: ["#ffe28a"],
  gadget_crown: ["#ffcf40", "#e11d48"],
  gadget_wings: ["#fbf8ef", "#f6dfa4"],
};
