/**
 * The office's own settings, kept per viewer in browser storage. Reception is
 * their home: every setting the map gains (and, later, rebindable keys) lands
 * in this one store and in the reception's Settings tab.
 */
import { create } from "zustand";

export interface OfficeSettings {
  /** Walk at sprint pace by default; Shift then slows to a walk. */
  alwaysRun: boolean;
  /** Show the one-line controls reminder at the bottom of the map. */
  showHintBar: boolean;
}

export const DEFAULT_OFFICE_SETTINGS: OfficeSettings = { alwaysRun: false, showHintBar: true };

const STORAGE_KEY = "jarvis.office.settings.v1";

export function loadOfficeSettings(): OfficeSettings {
  try {
    const raw = typeof localStorage === "undefined" ? null : localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_OFFICE_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<OfficeSettings>;
    return {
      alwaysRun: typeof parsed.alwaysRun === "boolean" ? parsed.alwaysRun : DEFAULT_OFFICE_SETTINGS.alwaysRun,
      showHintBar: typeof parsed.showHintBar === "boolean" ? parsed.showHintBar : DEFAULT_OFFICE_SETTINGS.showHintBar,
    };
  } catch (error) {
    // Blocked or corrupt storage: the defaults are a complete, working set.
    console.warn("Office settings unreadable, using defaults", error);
    return { ...DEFAULT_OFFICE_SETTINGS };
  }
}

function saveOfficeSettings(settings: OfficeSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch (error) {
    // The setting still applies for this visit; it just will not survive a reload.
    console.warn("Office settings not saved", error);
  }
}

interface OfficeSettingsState extends OfficeSettings {
  set: (patch: Partial<OfficeSettings>) => void;
  reset: () => void;
}

export const useOfficeSettings = create<OfficeSettingsState>((set, get) => ({
  ...loadOfficeSettings(),
  set: (patch) => {
    set(patch);
    const { alwaysRun, showHintBar } = get();
    saveOfficeSettings({ alwaysRun, showHintBar });
  },
  reset: () => {
    set({ ...DEFAULT_OFFICE_SETTINGS });
    saveOfficeSettings({ ...DEFAULT_OFFICE_SETTINGS });
  },
}));

/** Sprint when exactly one of "Shift held" and "always run" is on. */
export function isRunning(shiftHeld: boolean, alwaysRun: boolean): boolean {
  return shiftHeld !== alwaysRun;
}

/** The reception's tabs; H opens reception on the controls tab from anywhere. */
export type ReceptionTab = "welcome" | "controls" | "settings";

export const useReceptionTab = create<{ tab: ReceptionTab; setTab: (tab: ReceptionTab) => void }>((set) => ({
  tab: "welcome",
  setTab: (tab) => set({ tab }),
}));
