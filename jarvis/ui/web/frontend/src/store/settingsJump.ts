/**
 * A Settings group someone asked to land on from OUTSIDE the Settings hub.
 *
 * The hub already scrolls to a group picked from its own search box, but that
 * target lives in the hub's local state, which does not exist yet while the hub
 * is closed. The quick switcher (Ctrl+Space) can pick "Keyboard" or "Wake word"
 * from anywhere, so the request is parked here, the hub opens, and it takes
 * the target over on mount and clears it — a one-shot hand-off, never a
 * standing preference.
 */
import { create } from "zustand";

interface SettingsJumpState {
  target: string | null;
  request: (target: string) => void;
  take: () => string | null;
}

export const useSettingsJump = create<SettingsJumpState>((set, get) => ({
  target: null,
  request: (target) => set({ target }),
  take: () => {
    const { target } = get();
    if (target !== null) set({ target: null });
    return target;
  },
}));
