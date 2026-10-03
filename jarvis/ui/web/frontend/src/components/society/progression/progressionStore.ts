/**
 * The Verse's level state, shared by the HUD (DOM) and the effects (canvas).
 *
 * The first read hydrates it; every `ProgressionAwarded` push applies one
 * award, queues a floating "+XP" over whoever earned it and, on a level-up,
 * a celebration: the big banner for the person and their pet, a toast for an
 * agent, and a burst of light in the world for all three.
 */
import { create } from "zustand";
import type { AwardEvent, ProgressionSnapshot, SubjectLevel } from "./progressionApi";
import { PERSON_SUBJECT } from "./progressionApi";
import type { RewardId, Slot, SubjectKind } from "./levelCatalog";
import { loadChoices, progressFor, saveChoices, type ChoiceBook, type SlotChoice } from "./cosmetics";

export interface Celebration {
  id: number;
  subjectId: string;
  kind: SubjectKind;
  level: number;
  previousLevel: number;
  title: string;
  unlocked: RewardId[];
  /** Earned while the Verse was closed: told, not staged in the world. */
  away: boolean;
}

export interface XpPopup { id: number; subjectId: string; xp: number; bornMs: number }
export interface Burst { id: number; subjectId: string; kind: SubjectKind; level: number; bornMs: number }

/** How long a floating "+XP" and a level-up burst live, in ms. */
export const POPUP_MS = 1600;
export const BURST_MS = 2600;
/** How long the person's figure cheers after their own level-up. */
export const CHEER_MS = 2200;
const MAX_POPUPS = 12;
const MAX_TOASTS = 4;

/**
 * The Level Hall screen's pages: where you stand, the Upgrade Studio (what you
 * and your pet wear, with a live preview), the reward road, how XP is earned,
 * and the team ranking. The hall's checkpoints, the level card and `L` open it.
 */
export type HallTab = "overview" | "studio" | "rewards" | "guide" | "team";
export const HALL_TABS: readonly HallTab[] = ["overview", "studio", "rewards", "guide", "team"];
/** Whose level the screen shows: the person's own, or their pet's (the pet IS Jarvis in the Verse). */
export type HallSubject = "person" | "pet";

interface ProgressionState {
  snapshot: ProgressionSnapshot | null;
  subjects: Record<string, SubjectLevel>;
  petId: string;
  /** Person and pet level-ups waiting for the banner, oldest first. */
  banners: Celebration[];
  /** Agent level-ups, newest last. */
  toasts: Celebration[];
  popups: XpPopup[];
  bursts: Burst[];
  cheerUntil: number;
  panel: HallTab | null;
  hallSubject: HallSubject;
  /** A reward to show on the reward road (a pedestal in the hall was clicked); null = the next unlock. */
  focusReward: RewardId | null;
  choices: ChoiceBook;
  hydrate: (snapshot: ProgressionSnapshot, away: Celebration[]) => void;
  apply: (award: AwardEvent, nowMs?: number) => void;
  setPetId: (petId: string) => void;
  dismissBanner: () => void;
  dismissToast: (id: number) => void;
  expire: (nowMs: number) => void;
  /** Open the Level Hall screen on a page (null closes it), optionally for a subject and a reward. */
  openPanel: (tab: HallTab | null, options?: { subject?: HallSubject; reward?: RewardId | null }) => void;
  setHallSubject: (subject: HallSubject) => void;
  choose: (who: "person" | "pet", slot: Slot, choice: SlotChoice) => void;
}

let nextId = 1;

export function celebrationOf(award: Pick<AwardEvent, "subjectId" | "kind" | "level" | "previousLevel" | "title" | "unlocked">, away = false): Celebration {
  return {
    id: nextId++, subjectId: award.subjectId, kind: award.kind, level: award.level, previousLevel: award.previousLevel,
    title: award.title, unlocked: award.unlocked, away,
  };
}

export const useProgression = create<ProgressionState>((set, get) => ({
  snapshot: null,
  subjects: {},
  petId: "gigi",
  banners: [],
  toasts: [],
  popups: [],
  bursts: [],
  cheerUntil: 0,
  panel: null,
  hallSubject: "person",
  focusReward: null,
  choices: loadChoices(),
  hydrate: (snapshot, away) => set((s) => {
    // A push can land before the read that was already on its way: never roll a subject back.
    const subjects: Record<string, SubjectLevel> = { ...s.subjects };
    for (const row of snapshot.subjects) {
      const known = subjects[row.subjectId];
      if (!known || known.xp <= row.xp) subjects[row.subjectId] = row;
    }
    const banners = [...s.banners, ...away.filter((c) => c.kind !== "agent")];
    const toasts = [...s.toasts, ...away.filter((c) => c.kind === "agent")].slice(-MAX_TOASTS);
    return { snapshot, subjects, petId: snapshot.petId, banners, toasts };
  }),
  apply: (award, nowMs = performance.now()) => set((s) => {
    const known = s.subjects[award.subjectId];
    // A push older than what the subject already shows (a late duplicate) changes nothing.
    if (known && known.xp >= award.totalXp) return s;
    const curve = s.snapshot?.levelXp ?? [];
    const { into, size } = progressFor(curve, award.totalXp, award.level);
    const atCap = s.snapshot ? award.level >= s.snapshot.maxLevel : false;
    const subject: SubjectLevel = {
      subjectId: award.subjectId, kind: award.kind, xp: award.totalXp, level: award.level,
      xpIntoLevel: atCap ? 0 : into, xpForNext: atCap ? 0 : size, title: award.title,
    };
    const popups = [...s.popups, { id: nextId++, subjectId: award.subjectId, xp: award.xp, bornMs: nowMs }].slice(-MAX_POPUPS);
    if (award.level <= award.previousLevel) return { subjects: { ...s.subjects, [award.subjectId]: subject }, popups };
    const party = celebrationOf(award);
    const bursts = [...s.bursts, { id: nextId++, subjectId: award.subjectId, kind: award.kind, level: award.level, bornMs: nowMs }];
    return {
      subjects: { ...s.subjects, [award.subjectId]: subject },
      popups,
      bursts,
      banners: award.kind === "agent" ? s.banners : [...s.banners, party],
      toasts: award.kind === "agent" ? [...s.toasts, party].slice(-MAX_TOASTS) : s.toasts,
      cheerUntil: award.subjectId === PERSON_SUBJECT ? nowMs + CHEER_MS : s.cheerUntil,
    };
  }),
  setPetId: (petId) => set((s) => (s.petId === petId || !petId ? s : { petId })),
  dismissBanner: () => set((s) => ({ banners: s.banners.slice(1) })),
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  expire: (nowMs) => {
    const s = get();
    const popups = s.popups.filter((p) => nowMs - p.bornMs < POPUP_MS);
    const bursts = s.bursts.filter((b) => nowMs - b.bornMs < BURST_MS);
    if (popups.length !== s.popups.length || bursts.length !== s.bursts.length) set({ popups, bursts });
  },
  openPanel: (panel, options = {}) => set((s) => ({
    panel,
    hallSubject: options.subject ?? s.hallSubject,
    focusReward: options.reward !== undefined ? options.reward : panel === "rewards" ? s.focusReward : null,
  })),
  // A reward focused for one subject means nothing for the other: switching forgets it.
  setHallSubject: (hallSubject) => set((s) => (s.hallSubject === hallSubject ? s : { hallSubject, focusReward: null })),
  choose: (who, slot, choice) => set((s) => {
    const choices: ChoiceBook = { ...s.choices, [who]: { ...(s.choices[who] ?? {}), [slot]: choice } };
    saveChoices(choices);
    return { choices };
  }),
}));

const SEEN_KEY = "jarvis.verse.levels.seen.v1";

/** The last award this browser has shown; null on a first visit. */
export function readSeenSeq(): number | null {
  try {
    const raw = typeof localStorage === "undefined" ? null : localStorage.getItem(SEEN_KEY);
    const value = raw === null ? NaN : Number(raw);
    return Number.isFinite(value) ? value : null;
  } catch (error) {
    console.debug("Verse level marker unavailable", error);
    return null;
  }
}

export function writeSeenSeq(seq: number): void {
  try {
    localStorage.setItem(SEEN_KEY, String(seq));
  } catch (error) {
    console.debug("Verse level marker not saved", error);
  }
}

/**
 * Level-ups that happened while the Verse was closed: the latest one per
 * subject, so a busy agent that climbed three levels is told once.
 */
export function awayCelebrations(snapshot: ProgressionSnapshot, seenSeq: number | null): Celebration[] {
  if (seenSeq === null) return [];
  const latest = new Map<string, Celebration>();
  for (const row of snapshot.recent) {
    if (row.seq <= seenSeq || row.levelAfter <= row.levelBefore) continue;
    const prior = latest.get(row.subjectId);
    const subject = snapshot.subjects.find((s) => s.subjectId === row.subjectId);
    const unlocked = snapshot.rewards
      .filter((r) => { const at = r.levels[row.kind]; return at !== null && at > (prior?.previousLevel ?? row.levelBefore) && at <= row.levelAfter; })
      .map((r) => r.rewardId);
    latest.set(row.subjectId, celebrationOf({
      subjectId: row.subjectId, kind: row.kind, level: row.levelAfter, previousLevel: prior?.previousLevel ?? row.levelBefore,
      title: subject?.title ?? "", unlocked,
    }, true));
  }
  return [...latest.values()];
}
