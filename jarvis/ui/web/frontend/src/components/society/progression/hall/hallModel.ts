/**
 * What the Level Hall screen computes from the server's numbers. Pure: the
 * curve, the rules and the reward levels all come from the snapshot; nothing
 * here re-derives the rulebook.
 */
import type { SubjectKind, XpSource } from "../levelCatalog";
import type { XpRuleRow } from "../progressionApi";

/** How rare a reward feels, by the level it unlocks at: the colour of its card. */
export type Rarity = "common" | "rare" | "epic" | "legendary";

export function rarityOf(unlockLevel: number): Rarity {
  if (unlockLevel <= 5) return "common";
  if (unlockLevel <= 15) return "rare";
  if (unlockLevel <= 30) return "epic";
  return "legendary";
}

/** XP still missing until `target` is reached (0 once it is); `levelXp[i]` is the total at which level i + 1 starts. */
export function xpToReach(levelXp: readonly number[], totalXp: number, target: number): number {
  const start = levelXp[target - 1];
  if (start === undefined) return 0;
  return Math.max(0, start - totalXp);
}

/** The size of each of the next `count` levels after `level`, for "every level takes a little more". */
export function upcomingLevelCosts(levelXp: readonly number[], level: number, count: number): { level: number; xp: number }[] {
  const out: { level: number; xp: number }[] = [];
  for (let next = level + 1; next <= level + count; next += 1) {
    const start = levelXp[next - 2], end = levelXp[next - 1];
    if (start === undefined || end === undefined) break;
    out.push({ level: next, xp: end - start });
  }
  return out;
}

/** The next title band above `level`, if any. */
export function nextTitle(bands: readonly { level: number; title: string }[], level: number): { level: number; title: string } | undefined {
  return [...bands].sort((a, b) => a.level - b.level).find((band) => band.level > level);
}

/** How the guide groups the ways to earn XP, per subject kind; a source the rulebook adds later lands in "other". */
export const RULE_GROUPS: Record<SubjectKind, readonly { id: string; sources: readonly XpSource[] }[]> = {
  person: [
    { id: "jarvis", sources: ["chat_turn", "voice_turn"] },
    { id: "team", sources: ["quest_posted", "quest_completed", "mission_completed", "agent_hired"] },
    { id: "verse", sources: ["daily_visit", "floor_discovered", "dog_petted", "dog_treat", "arcade_round", "arcade_record", "team_meeting"] },
  ],
  pet: [{ id: "pet", sources: ["jarvis_answered", "delegated", "walk_together"] }],
  agent: [{ id: "agent", sources: ["task_done", "quest_done", "answered_teammate", "task_blocked"] }],
};

/** A subject kind's rules sorted into the guide's groups, in the group's order; empty groups are dropped. */
export function groupRules(rules: readonly XpRuleRow[], kind: SubjectKind): { id: string; rules: XpRuleRow[] }[] {
  const mine = rules.filter((r) => r.kind === kind);
  const groups = RULE_GROUPS[kind].map((group) => ({
    id: group.id,
    rules: group.sources.map((source) => mine.find((r) => r.source === source)).filter((r): r is XpRuleRow => !!r),
  }));
  const known = new Set(RULE_GROUPS[kind].flatMap((g) => g.sources));
  const other = mine.filter((r) => !known.has(r.source));
  if (other.length > 0) groups.push({ id: "other", rules: other });
  return groups.filter((g) => g.rules.length > 0);
}

/** The quickest everyday ways to earn: the highest-paying repeatable rules first. */
export function quickWins(rules: readonly XpRuleRow[], kind: SubjectKind, count: number): XpRuleRow[] {
  return rules
    .filter((r) => r.kind === kind && r.source !== "agent_hired" && r.source !== "floor_discovered")
    .sort((a, b) => b.xp - a.xp || a.source.localeCompare(b.source))
    .slice(0, count);
}
