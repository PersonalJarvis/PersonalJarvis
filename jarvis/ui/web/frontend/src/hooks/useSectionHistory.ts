import { useEffect } from "react";
import { create } from "zustand";

import { useEventStore, type SectionId } from "@/store/events";

/**
 * How many visited sections the back stack keeps.
 *
 * A section change is cheap (one id), but an unbounded array in a long-lived
 * desktop session is not — 50 steps back is far past what anyone reaches for.
 */
const MAX_HISTORY = 50;

interface SectionNavHistoryState {
  /** The section the stacks were last reconciled with. Null until observed. */
  current: SectionId | null;
  /** Older sections, oldest first. The last entry is where "back" goes. */
  past: SectionId[];
  /** Sections undone by "back", oldest first. Cleared by any new visit. */
  future: SectionId[];
  /** True while a back/forward navigation is in flight to the store. */
  navigating: boolean;
  /** Reconcile the stacks with a newly observed section. */
  record: (section: SectionId) => void;
  /** Step one section back. Returns the target, or null when there is none. */
  goBack: () => SectionId | null;
  /** Re-apply one undone step. Returns the target, or null when there is none. */
  goForward: () => SectionId | null;
  /** Test-only reset to a pristine history. */
  reset: () => void;
}

export const useSectionNavHistory = create<SectionNavHistoryState>((set, get) => ({
  current: null,
  past: [],
  future: [],
  navigating: false,

  record: (section) => {
    const { current, past, navigating } = get();
    if (current === section) {
      // A back/forward navigation lands here through the store subscription:
      // the stacks were already moved by goBack/goForward, so only clear the
      // in-flight flag instead of recording a duplicate visit.
      if (navigating) set({ navigating: false });
      return;
    }
    if (navigating) {
      // Same flight, observed before goBack/goForward set `current` — adopt.
      set({ current: section, navigating: false });
      return;
    }
    if (current === null) {
      set({ current: section });
      return;
    }
    set({
      current: section,
      past: [...past, current].slice(-MAX_HISTORY),
      future: [],
    });
  },

  goBack: () => {
    const { current, past, future } = get();
    if (current === null || past.length === 0) return null;
    const target = past[past.length - 1];
    if (target === current) {
      set({ past: past.slice(0, -1) });
      return get().goBack();
    }
    set({
      current: target,
      past: past.slice(0, -1),
      future: [current, ...future].slice(0, MAX_HISTORY),
      navigating: true,
    });
    useEventStore.getState().setActiveSection(target);
    return target;
  },

  goForward: () => {
    const { current, past, future } = get();
    if (current === null || future.length === 0) return null;
    const [target, ...rest] = future;
    if (target === current) {
      set({ future: rest });
      return get().goForward();
    }
    set({
      current: target,
      past: [...past, current].slice(-MAX_HISTORY),
      future: rest,
      navigating: true,
    });
    useEventStore.getState().setActiveSection(target);
    return target;
  },

  reset: () => set({ current: null, past: [], future: [], navigating: false }),
}));

/** Clear the navigation history — tests only. */
export function resetSectionHistory(): void {
  useSectionNavHistory.getState().reset();
}

export interface SectionHistory {
  canGoBack: boolean;
  canGoForward: boolean;
  goBack: () => SectionId | null;
  goForward: () => SectionId | null;
}

/**
 * Browser-style section history for the caption back/forward buttons.
 *
 * Mounted once per window (the TopBar owns it): every section change flows
 * through `activeSection` — sidebar clicks, voice commands, deck jumps, staged
 * requests — so watching the value covers all of them, where hooking one click
 * path would cover one. "Back" returns to the previously visited section and
 * "forward" only re-applies an undone step: it stays disabled until a step was
 * undone, and any new visit clears the redo stack, because a section never
 * visited cannot be gone to.
 */
export function useSectionHistory(): SectionHistory {
  const activeSection = useEventStore((s) => s.activeSection);
  const record = useSectionNavHistory((s) => s.record);
  const past = useSectionNavHistory((s) => s.past);
  const future = useSectionNavHistory((s) => s.future);
  const goBack = useSectionNavHistory((s) => s.goBack);
  const goForward = useSectionNavHistory((s) => s.goForward);

  useEffect(() => {
    record(activeSection);
  }, [activeSection, record]);

  return {
    canGoBack: past.length > 0,
    canGoForward: future.length > 0,
    goBack,
    goForward,
  };
}
