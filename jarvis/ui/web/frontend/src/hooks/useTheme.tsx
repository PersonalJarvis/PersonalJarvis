import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/** What the user picks. `system` is an intent that follows the OS live. */
export type ThemePreference = "dark" | "light" | "system";
/** What actually gets painted. */
export type Theme = "dark" | "light";

interface ThemeCtx {
  /** The resolved colour currently on screen. */
  theme: Theme;
  /** The user's choice, which may be `system`. */
  preference: ThemePreference;
  /** Persist a choice (backend + local cache) and repaint. */
  setPreference: (p: ThemePreference) => void;
  /** Flip between the two concrete themes. Resolves `system` first. */
  toggle: () => void;
}

const Ctx = createContext<ThemeCtx | undefined>(undefined);

/**
 * Cache key. Shared verbatim with the inline boot script in `index.html`,
 * which reads it before the first paint — keep the key AND the resolution
 * order in lockstep with that script, or the app will flash on launch.
 */
const STORAGE_KEY = "jarvis.theme";
const ENDPOINT = "/api/settings/appearance";
const DEFAULT_PREFERENCE: ThemePreference = "dark";

function isPreference(v: unknown): v is ThemePreference {
  return v === "dark" || v === "light" || v === "system";
}

/** The OS preference, or the product default where it cannot be read. */
function systemTheme(): Theme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return DEFAULT_PREFERENCE === "dark" ? "dark" : "light";
  }
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function resolveTheme(preference: ThemePreference): Theme {
  return preference === "system" ? systemTheme() : preference;
}

/**
 * The theme the app is about to paint, answered synchronously from the cache.
 *
 * For code that needs the answer at module-init time, before the provider (or
 * even React) exists — e.g. the wallpaper store filing a stored pick under the
 * mode it belongs to. Reads the same cache the boot script uses, so the three
 * readers of `jarvis.theme` cannot disagree about what the first frame wears.
 */
export function cachedTheme(): Theme {
  return resolveTheme(readCachedPreference());
}

/**
 * The cached preference, read synchronously.
 *
 * The authoritative value lives in `[ui] theme` on the backend, but it arrives
 * over HTTP — too late to paint the first frame with. So the cache is what the
 * app starts from, and `hydrate()` reconciles it a moment later. In the normal
 * case the two already agree and nothing visibly changes.
 */
function readCachedPreference(): ThemePreference {
  if (typeof window === "undefined") return DEFAULT_PREFERENCE;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isPreference(stored)) return stored;
  } catch {
    /* storage blocked (private mode) — fall through to the default */
  }
  return DEFAULT_PREFERENCE;
}

function applyToDocument(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  // Native widgets — dropdowns, the caret, scrollbar internals CSS cannot
  // reach — follow this rather than the class.
  root.style.colorScheme = theme;
}

/**
 * Re-read the theme from the backend and repaint.
 *
 * Called when a `ConfigReloaded` names `ui.theme` — i.e. something wrote
 * jarvis.toml directly (a voice command, the self-mod pipeline, an edit on
 * another machine) without going through the appearance endpoint, so no
 * `UiThemeChanged` was ever published. Routed through the same custom event as
 * the broadcast, so ThemeProvider stays the only writer.
 */
export async function hydrateUiTheme(): Promise<void> {
  try {
    const res = await fetch(ENDPOINT, { credentials: "same-origin" });
    if (!res.ok) return;
    const data: unknown = await res.json();
    const theme = (data as { theme?: unknown }).theme;
    if (isPreference(theme)) {
      window.dispatchEvent(new CustomEvent("jarvis:theme-changed", { detail: { theme } }));
    }
  } catch (err) {
    if (import.meta.env?.DEV) console.debug("appearance re-hydrate skipped", err);
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readCachedPreference);
  const [systemValue, setSystemValue] = useState<Theme>(systemTheme);

  const theme: Theme = preference === "system" ? systemValue : preference;

  // Follow the OS while the preference is `system`. Registered unconditionally
  // so switching TO `system` picks up the current OS value without a reload.
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setSystemValue(mq.matches ? "light" : "dark");
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Paint, and mirror the choice into the cache the boot script reads.
  useEffect(() => {
    applyToDocument(theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, preference);
    } catch {
      /* storage blocked — the backend value still survives a restart */
    }
  }, [theme, preference]);

  // Reconcile with the backend once, on mount. A value written by the Control
  // API (`jarvis api settings put-appearance`) or carried over from another
  // machine's config wins over a stale local cache.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(ENDPOINT, { credentials: "same-origin" });
        if (!res.ok) return;
        const data: unknown = await res.json();
        const remote = (data as { theme?: unknown }).theme;
        if (!cancelled && isPreference(remote)) setPreferenceState(remote);
      } catch (err) {
        // Offline or the route is not mounted yet — the cached preference is a
        // perfectly good answer, so this is a non-event rather than a failure.
        if (import.meta.env?.DEV) console.debug("appearance hydrate skipped", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Another client / the Control API changed it: repaint without a reload.
  useEffect(() => {
    const onRemote = (e: Event) => {
      const next = (e as CustomEvent<{ theme?: unknown }>).detail?.theme;
      if (isPreference(next)) setPreferenceState(next);
    };
    window.addEventListener("jarvis:theme-changed", onRemote);
    return () => window.removeEventListener("jarvis:theme-changed", onRemote);
  }, []);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    void fetch(ENDPOINT, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme: next, persist: true }),
    }).catch((err) => {
      // The visible switch already happened and the local cache holds it, so a
      // failed PUT costs the user nothing this session — it only means the
      // choice may not survive a config reload elsewhere.
      if (import.meta.env?.DEV) console.debug("appearance persist failed", err);
    });
  }, []);

  const value = useMemo<ThemeCtx>(
    () => ({
      theme,
      preference,
      setPreference,
      toggle: () => setPreference(theme === "dark" ? "light" : "dark"),
    }),
    [theme, preference, setPreference],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}

/**
 * The current theme for components that merely want to MATCH it.
 *
 * `useTheme` throws without a provider, which is right for anything that
 * switches the theme — that is a wiring bug worth failing loudly on. But a
 * component that only tints itself to fit should not take the view down when it
 * is rendered outside the provider (a unit test, a storybook, an isolated
 * mount). Those read the document instead, which is where the answer actually
 * is: the class on <html> is set before React mounts and stays authoritative.
 */
export function useThemeValue(): Theme {
  const ctx = useContext(Ctx);
  const [fallback, setFallback] = useState<Theme>(() =>
    typeof document !== "undefined" && document.documentElement.classList.contains("dark")
      ? "dark"
      : resolveTheme(readCachedPreference()),
  );

  // Outside a provider, follow the class on <html> so an isolated component
  // still repaints when the theme changes around it.
  useEffect(() => {
    if (ctx || typeof document === "undefined") return;
    const root = document.documentElement;
    const sync = () => setFallback(root.classList.contains("dark") ? "dark" : "light");
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, [ctx]);

  return ctx?.theme ?? fallback;
}
