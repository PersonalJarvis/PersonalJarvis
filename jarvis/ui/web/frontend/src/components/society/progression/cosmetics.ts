/**
 * Which cosmetics a subject wears. Pure: the level comes from the server,
 * the unlock levels from the rulebook, and the person's own choice (for
 * themself and their pet) from browser storage. Agents always wear the best
 * they have unlocked; so does anyone who never chose.
 */
import type { RewardRow, SubjectLevel } from "./progressionApi";
import { SLOTS, type RewardId, type Slot, type SubjectKind } from "./levelCatalog";

/** A slot left on "automatic" wears the latest unlock; "none" wears nothing. */
export type SlotChoice = RewardId | "none" | "auto";
export type Loadout = Partial<Record<Slot, RewardId>>;

export function unlockedFor(rewards: readonly RewardRow[], kind: SubjectKind, level: number): RewardRow[] {
  return rewards.filter((r) => { const at = r.levels[kind]; return at !== null && level >= at; });
}

export function equippedFor(rewards: readonly RewardRow[], kind: SubjectKind, level: number,
  choices: Partial<Record<Slot, SlotChoice>> = {}): Loadout {
  const open = unlockedFor(rewards, kind, level);
  const loadout: Loadout = {};
  for (const slot of SLOTS) {
    const choice = choices[slot] ?? "auto";
    if (choice === "none") continue;
    const inSlot = open.filter((r) => r.slot === slot);
    const picked = choice !== "auto" ? inSlot.find((r) => r.rewardId === choice) : undefined;
    const best = inSlot.reduce<RewardRow | undefined>((top, r) => (!top || (r.levels[kind] ?? 0) >= (top.levels[kind] ?? 0) ? r : top), undefined);
    const wear = picked ?? best;
    if (wear) loadout[slot] = wear.rewardId;
  }
  return loadout;
}

/** XP gathered into the current level and the level's size, from the curve the server sent. */
export function progressFor(levelXp: readonly number[], totalXp: number, level: number): { into: number; size: number } {
  const start = levelXp[level - 1] ?? 0;
  const next = levelXp[level];
  return { into: Math.max(0, totalXp - start), size: next === undefined ? 0 : Math.max(1, next - start) };
}

/** A subject's place on its level bar, 0..1 (1 at the cap). */
export function levelFraction(subject: Pick<SubjectLevel, "xpIntoLevel" | "xpForNext"> | undefined): number {
  if (!subject) return 0;
  if (subject.xpForNext <= 0) return 1;
  return Math.max(0, Math.min(1, subject.xpIntoLevel / subject.xpForNext));
}

const CHOICES_KEY = "jarvis.verse.cosmetics.v1";

export type ChoiceBook = Partial<Record<"person" | "pet", Partial<Record<Slot, SlotChoice>>>>;

export function loadChoices(): ChoiceBook {
  try {
    const raw = typeof localStorage === "undefined" ? null : localStorage.getItem(CHOICES_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    return parsed && typeof parsed === "object" ? (parsed as ChoiceBook) : {};
  } catch (error) {
    // Blocked storage: everyone wears their best unlocks, which is the default anyway.
    console.debug("Verse cosmetics choices unavailable", error);
    return {};
  }
}

export function saveChoices(book: ChoiceBook): void {
  try {
    localStorage.setItem(CHOICES_KEY, JSON.stringify(book));
  } catch (error) {
    console.debug("Verse cosmetics choices not saved", error);
  }
}
