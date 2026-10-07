/**
 * The in-app chords of ../lib/appChords: which keys the terminal text size
 * steps, the shortcut overview and the Agentic IDE key menu use on this device.
 *
 * Stored in the browser's own storage, like the quick switcher and the app
 * zoom: these keys live entirely inside the window and the backend never needs
 * them. Every window shares one origin, so a chord changed in one window
 * reaches the others through the `storage` event.
 *
 * An unreadable value falls back to the platform defaults.
 */
import { create } from "zustand";
import {
  APP_CHORD_IDS,
  defaultAppChords,
  type AppChordBindings,
  type AppChordId,
} from "@/lib/appChords";

export const APP_CHORD_STORAGE_KEY = "jarvis.appChords.v1";

export function readAppChordSettings(): { bindings: AppChordBindings } {
  const bindings = defaultAppChords();
  try {
    const raw = window.localStorage.getItem(APP_CHORD_STORAGE_KEY);
    if (!raw) return { bindings };
    const parsed = JSON.parse(raw) as { bindings?: Partial<Record<AppChordId, unknown>> };
    for (const id of APP_CHORD_IDS) {
      const value = parsed.bindings?.[id];
      // "" is kept: it means the user removed the shortcut on purpose.
      if (typeof value === "string") bindings[id] = value;
    }
  } catch {
    /* unreadable — the defaults above stand */
  }
  return { bindings };
}

interface AppChordSettingsState {
  bindings: AppChordBindings;
  setBinding: (id: AppChordId, combo: string) => void;
}

export const useAppChordSettings = create<AppChordSettingsState>((set, get) => ({
  ...readAppChordSettings(),
  setBinding: (id, combo) => {
    const bindings = { ...get().bindings, [id]: combo.trim().toLowerCase() };
    try {
      window.localStorage.setItem(APP_CHORD_STORAGE_KEY, JSON.stringify({ bindings }));
    } catch {
      /* storage unavailable — the choice holds for this session only */
    }
    set({ bindings });
  },
}));

/** The terminal text size steps, shaped for ../components/agentic/terminalZoom. */
export function terminalZoomBindings(bindings: AppChordBindings = useAppChordSettings.getState().bindings) {
  return { in: bindings.terminal_zoom_in, out: bindings.terminal_zoom_out, reset: bindings.terminal_zoom_reset };
}

/** The current chord for `id`, for listeners outside React. "" when removed. */
export function appChord(id: AppChordId): string {
  return useAppChordSettings.getState().bindings[id];
}

// Another window changed it: take the new value over.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === APP_CHORD_STORAGE_KEY) {
      useAppChordSettings.setState(readAppChordSettings());
    }
  });
}
