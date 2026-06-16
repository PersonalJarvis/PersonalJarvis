import { create } from "zustand";

/**
 * Shared "a mission card is being dragged" flag.
 *
 * The drag source (`SessionRow` in `OutputsView`) and the drop surface
 * (`JarvisDock`, far away in the tree) need to agree that a drag is in flight so
 * the dock can bloom into a big, forgiving target and mount its full-window
 * catch layer. A tiny zustand store is the lightest way to bridge them — same
 * pattern as `useEventStore` / `useI18nStore`.
 *
 * `begin()` is called from the source `onDragStart`; `end()` from its
 * `onDragEnd` and from every drop handler, so the bloom always tears down
 * whether the user drops on target, tosses near it, or cancels with Esc.
 */
interface MissionDragState {
  dragging: boolean;
  begin: () => void;
  end: () => void;
}

export const useMissionDrag = create<MissionDragState>((set) => ({
  dragging: false,
  begin: () => set({ dragging: true }),
  end: () => set({ dragging: false }),
}));
