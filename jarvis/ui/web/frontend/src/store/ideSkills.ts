import { create } from "zustand";
import {
  createIdeSkill,
  deleteIdeSkill,
  listIdeSkills,
  markIdeSkillUsed,
  reorderIdeSkills,
  updateIdeSkill,
  type IdeSkill,
  type IdeSkillDraft,
} from "@/lib/ideSkillsApi";

/**
 * The Agentic IDE's skill library, shared by the Skills tab and the panes.
 *
 * The tab lists and edits the skills; a pane that receives one (dropped or
 * sent with "Paste into …") reports the landing here, so the card that was
 * carried can say where it went and the use count moves at once. The pane a
 * button press pastes into is the grid's selected pane, which only the IDE
 * view knows — it publishes it as `target`.
 */

export interface SkillLanding {
  skillId: string;
  pane: string;
  at: number;
}

/** The skill being carried right now, for the overlays that cannot read the drag yet. */
export interface SkillInFlight {
  id: string;
  title: string;
  hue: string;
  icon: string;
  /** Spans lines: only a terminal in bracketed-paste mode may take it. */
  multiline: boolean;
}

export interface SkillPasteTarget {
  workspaceId: string;
  pane: string;
}

type LoadState = "idle" | "loading" | "ready" | "error";

interface IdeSkillsState {
  skills: IdeSkill[];
  status: LoadState;
  error: string | null;
  load: (force?: boolean) => Promise<void>;
  create: (draft: IdeSkillDraft) => Promise<IdeSkill>;
  update: (id: string, patch: Partial<IdeSkillDraft>) => Promise<IdeSkill>;
  remove: (id: string) => Promise<void>;
  /** Put the skill with `id` where `beforeId` stands (null = at the end). */
  move: (id: string, beforeId: string | null) => Promise<void>;
  landing: SkillLanding | null;
  /** A pane took a skill: count it and remember where it went. */
  recordUse: (skillId: string, pane: string) => void;
  dragging: SkillInFlight | null;
  setDragging: (skill: SkillInFlight | null) => void;
  target: SkillPasteTarget | null;
  setTarget: (target: SkillPasteTarget | null) => void;
}

let inflight: Promise<void> | null = null;

export const useIdeSkillsStore = create<IdeSkillsState>((set, get) => ({
  skills: [],
  status: "idle",
  error: null,
  load: async (force = false) => {
    if (inflight) return inflight;
    if (!force && get().status === "ready") return;
    set((state) => ({ status: state.status === "ready" ? "ready" : "loading", error: null }));
    inflight = listIdeSkills()
      .then((skills) => set({ skills, status: "ready", error: null }))
      .catch((error: unknown) => set({ status: "error", error: error instanceof Error ? error.message : String(error) }))
      .finally(() => { inflight = null; });
    return inflight;
  },
  create: async (draft) => {
    const skill = await createIdeSkill(draft);
    set((state) => ({ skills: [skill, ...state.skills.filter((entry) => entry.id !== skill.id)] }));
    return skill;
  },
  update: async (id, patch) => {
    const skill = await updateIdeSkill(id, patch);
    set((state) => ({ skills: state.skills.map((entry) => (entry.id === id ? skill : entry)) }));
    return skill;
  },
  remove: async (id) => {
    const before = get().skills;
    set({ skills: before.filter((entry) => entry.id !== id) });
    try {
      await deleteIdeSkill(id);
    } catch (error) {
      set({ skills: before });
      throw error;
    }
  },
  move: async (id, beforeId) => {
    const before = get().skills;
    const moving = before.find((entry) => entry.id === id);
    if (!moving || id === beforeId) return;
    const rest = before.filter((entry) => entry.id !== id);
    const at = beforeId ? rest.findIndex((entry) => entry.id === beforeId) : rest.length;
    const next = [...rest.slice(0, at < 0 ? rest.length : at), moving, ...rest.slice(at < 0 ? rest.length : at)];
    set({ skills: next });
    try {
      set({ skills: await reorderIdeSkills(next.map((entry) => entry.id)) });
    } catch (error) {
      set({ skills: before });
      throw error;
    }
  },
  landing: null,
  recordUse: (skillId, pane) => {
    const now = new Date().toISOString();
    set((state) => ({
      landing: { skillId, pane, at: Date.now() },
      skills: state.skills.map((entry) =>
        entry.id === skillId ? { ...entry, use_count: entry.use_count + 1, last_used_at: now } : entry,
      ),
    }));
    markIdeSkillUsed(skillId)
      .then((skill) => set((state) => ({ skills: state.skills.map((entry) => (entry.id === skillId ? skill : entry)) })))
      .catch((error: unknown) => {
        // The paste itself already happened; only the counter is behind.
        console.warn("Could not count a skill paste", error);
      });
  },
  dragging: null,
  setDragging: (dragging) => set({ dragging }),
  target: null,
  setTarget: (target) => {
    const current = get().target;
    if (current?.pane === target?.pane && current?.workspaceId === target?.workspaceId) return;
    set({ target });
  },
}));
