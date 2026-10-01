/**
 * The quick switcher's two settings: whether it is on, and which chord opens it.
 *
 * Stored in the browser's own storage rather than in `jarvis.toml`: the
 * switcher lives entirely inside the window and the backend never needs to know
 * about it — the voice keybinds are a different thing, global OS hotkeys the
 * backend registers. Every window of the app shares one origin, so they all
 * read the same value, and a change made in one window reaches the others
 * through the `storage` event.
 *
 * An unreadable or missing value falls back to "on, platform default" — a
 * broken preference must never leave someone without the switcher and without
 * a clue why.
 */
import { create } from "zustand";
import { defaultQuickSwitchCombo } from "@/lib/quickSwitchChord";

export const QUICK_SWITCH_STORAGE_KEY = "jarvis.quickSwitch.v1";

interface StoredQuickSwitch {
  enabled: boolean;
  /** "" means deliberately unassigned (the row's clear button). */
  combo: string;
}

function defaults(): StoredQuickSwitch {
  return { enabled: true, combo: defaultQuickSwitchCombo() };
}

export function readQuickSwitchSettings(): StoredQuickSwitch {
  try {
    const raw = window.localStorage.getItem(QUICK_SWITCH_STORAGE_KEY);
    if (!raw) return defaults();
    const parsed = JSON.parse(raw) as Partial<StoredQuickSwitch>;
    return {
      enabled: typeof parsed.enabled === "boolean" ? parsed.enabled : true,
      combo: typeof parsed.combo === "string" ? parsed.combo : defaultQuickSwitchCombo(),
    };
  } catch {
    return defaults();
  }
}

function write(value: StoredQuickSwitch): void {
  try {
    window.localStorage.setItem(QUICK_SWITCH_STORAGE_KEY, JSON.stringify(value));
  } catch {
    /* storage unavailable — the choice holds for this session only */
  }
}

interface QuickSwitchSettingsState extends StoredQuickSwitch {
  setEnabled: (enabled: boolean) => void;
  setCombo: (combo: string) => void;
}

export const useQuickSwitchSettings = create<QuickSwitchSettingsState>((set, get) => ({
  ...readQuickSwitchSettings(),
  setEnabled: (enabled) => {
    write({ enabled, combo: get().combo });
    set({ enabled });
  },
  setCombo: (combo) => {
    const next = combo.trim().toLowerCase();
    write({ enabled: get().enabled, combo: next });
    set({ combo: next });
  },
}));

// Another window changed it: take the new value over.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === QUICK_SWITCH_STORAGE_KEY) {
      useQuickSwitchSettings.setState(readQuickSwitchSettings());
    }
  });
}
