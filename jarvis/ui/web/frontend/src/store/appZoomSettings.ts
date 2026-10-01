/**
 * Whole-app zoom: whether the keys are on, which chords they are, and the
 * current level.
 *
 * Stored in the browser's own storage, like the quick switcher: zoom lives
 * entirely inside the window and the backend never needs to know it. Every
 * window of the app shares one origin, so a level set in one window reaches
 * the others through the `storage` event — the same way a browser zooms every
 * tab of one site together.
 *
 * An unreadable value falls back to "on, platform default chords, 100 %".
 */
import { create } from "zustand";
import {
  APP_ZOOM_INTENTS,
  defaultAppZoomBindings,
  snapAppZoom,
  type AppZoomBindings,
  type AppZoomIntent,
} from "@/lib/appZoom";

export const APP_ZOOM_STORAGE_KEY = "jarvis.appZoom.v1";

interface StoredAppZoom {
  enabled: boolean;
  /** "" means deliberately unassigned (the row's clear button). */
  bindings: AppZoomBindings;
  level: number;
}

function defaults(): StoredAppZoom {
  return { enabled: true, bindings: defaultAppZoomBindings(), level: 1 };
}

export function readAppZoomSettings(): StoredAppZoom {
  try {
    const raw = window.localStorage.getItem(APP_ZOOM_STORAGE_KEY);
    if (!raw) return defaults();
    const parsed = JSON.parse(raw) as Partial<StoredAppZoom>;
    const fallback = defaultAppZoomBindings();
    const bindings = { ...fallback };
    for (const intent of APP_ZOOM_INTENTS) {
      const value = parsed.bindings?.[intent];
      if (typeof value === "string") bindings[intent] = value;
    }
    return {
      enabled: typeof parsed.enabled === "boolean" ? parsed.enabled : true,
      bindings,
      level: typeof parsed.level === "number" ? snapAppZoom(parsed.level) : 1,
    };
  } catch {
    return defaults();
  }
}

function write(value: StoredAppZoom): void {
  try {
    window.localStorage.setItem(APP_ZOOM_STORAGE_KEY, JSON.stringify(value));
  } catch {
    /* storage unavailable — the choice holds for this session only */
  }
}

interface AppZoomSettingsState extends StoredAppZoom {
  setEnabled: (enabled: boolean) => void;
  setBinding: (intent: AppZoomIntent, combo: string) => void;
  setLevel: (level: number) => void;
}

export const useAppZoomSettings = create<AppZoomSettingsState>((set, get) => {
  const persist = (patch: Partial<StoredAppZoom>) => {
    const { enabled, bindings, level } = { ...get(), ...patch };
    write({ enabled, bindings, level });
    set(patch);
  };
  return {
    ...readAppZoomSettings(),
    setEnabled: (enabled) => persist({ enabled }),
    setBinding: (intent, combo) =>
      persist({ bindings: { ...get().bindings, [intent]: combo.trim().toLowerCase() } }),
    setLevel: (level) => persist({ level: snapAppZoom(level) }),
  };
});

// Another window changed it: take the new value over.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === APP_ZOOM_STORAGE_KEY) {
      useAppZoomSettings.setState(readAppZoomSettings());
    }
  });
}
