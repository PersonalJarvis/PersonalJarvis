import { useCallback, useSyncExternalStore } from "react";

export type ModelAccess = "subscription" | "api" | "local";
type Preferences = Partial<Record<string, ModelAccess>>;

export const MODEL_ACCESS_KEY = "jarvis.modelAccess.v1";
const listeners = new Set<() => void>();
let cached: { raw: string | null; value: Preferences } = { raw: null, value: {} };
const EMPTY: Preferences = {};

/** Presentation families shared by every model picker. Routing keeps the catalog id. */
export function modelAccessFamily(family: string): string {
  return family === "antigravity" ? "gemini" : family;
}

function read(): Preferences {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(MODEL_ACCESS_KEY);
  } catch {
    // Storage unavailable: explicit choices still last for this window.
    return cached.value;
  }
  if (raw === cached.raw) return cached.value;
  let value: Preferences = {};
  try {
    const parsed: unknown = JSON.parse(raw ?? "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      value = Object.fromEntries(Object.entries(parsed).filter(([, kind]) =>
        kind === "subscription" || kind === "api" || kind === "local"));
    }
  } catch {
    // Corrupt preferences restore subscription-first browsing.
  }
  cached = { raw, value };
  return value;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const storage = (event: StorageEvent) => {
    if (event.key === MODEL_ACCESS_KEY || event.key === null) listener();
  };
  window.addEventListener("storage", storage);
  return () => { listeners.delete(listener); window.removeEventListener("storage", storage); };
}

/** A browsing preference, never permission to switch an existing agent or bill a key. */
export function useModelAccess(): [Preferences, (family: string, kind: ModelAccess) => void] {
  const preferences = useSyncExternalStore(subscribe, read, () => EMPTY);
  const remember = useCallback((family: string, kind: ModelAccess) => {
    const value = { ...read(), [modelAccessFamily(family)]: kind };
    let raw = cached.raw;
    try {
      const serialized = JSON.stringify(value);
      window.localStorage.setItem(MODEL_ACCESS_KEY, serialized);
      raw = serialized;
    } catch {
      // The UI remains usable when the browser refuses persistence.
    }
    cached = { raw, value };
    listeners.forEach((listener) => listener());
  }, []);
  return [preferences, remember];
}

/** Use only offered access. An absent saved choice is retained for reconnection. */
export function preferredModelAccess<T extends { kind: ModelAccess; disabled?: boolean }>(
  options: T[], preferred?: ModelAccess,
): T | undefined {
  const available = options.filter((option) => !option.disabled);
  return available.find((option) => option.kind === preferred)
    ?? available.find((option) => option.kind === "subscription")
    ?? available[0] ?? options[0];
}
