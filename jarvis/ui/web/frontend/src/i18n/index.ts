/**
 * Inhouse-i18n fuer die Desktop-App.
 *
 * Warum kein react-i18next: 3 Sprachen, ~50 Strings, kein Pluralization-Bedarf,
 * kein Backend-Lazy-Load. Eine Mini-Implementation auf Zustand spart 200 KB
 * Bundle und einen npm-install-Schritt.
 *
 * Usage:
 *   import { useT } from "@/i18n";
 *   const t = useT();
 *   <span>{t("nav.skills")}</span>
 *
 *   import { useUiLanguage, setUiLanguage } from "@/i18n";
 *   const lang = useUiLanguage();      // "en" | "de" | "es"
 *   setUiLanguage("de");                // sofort reactive
 *
 * STT recognition language (what Whisper transcribes the spoken voice INTO) is
 * its own setting, distinct from the UI and the reply language:
 *   useSttLanguage(), setSttLanguage("auto" | "en" | "de" | "es")
 */
import { create } from "zustand";
import enJson from "./locales/en.json";
import deJson from "./locales/de.json";
import esJson from "./locales/es.json";
import { useEventStore } from "@/store/events";

export type UiLanguage = "en" | "de" | "es";
// "auto" mirrors the user's input language; the rest hard-pin the reply language.
// Mirrors jarvis/brain/manager.py::SUPPORTED_REPLY_LANGUAGES (single source of truth).
export type ReplyLanguage = "auto" | "en" | "de" | "es";
// "auto" lets Whisper detect the spoken language per utterance (bilingual
// default); a concrete code forces what the recogniser transcribes into.
// Mirrors jarvis/ui/web/settings_routes.py::_STT_LANGUAGES.
export type SttLanguage = "auto" | "en" | "de" | "es";

const REPLY_LANGUAGE_ENDPOINT = "/api/settings/reply-language";
const UI_LANGUAGE_ENDPOINT = "/api/settings/ui-language";
const STT_LANGUAGE_ENDPOINT = "/api/settings/stt-language";
const REPLY_VALUES: readonly ReplyLanguage[] = ["auto", "en", "de", "es"];
const STT_VALUES: readonly SttLanguage[] = ["auto", "en", "de", "es"];

function isUiLanguage(v: unknown): v is UiLanguage {
  return v === "en" || v === "de" || v === "es";
}

function isReplyLanguage(v: unknown): v is ReplyLanguage {
  return typeof v === "string" && (REPLY_VALUES as readonly string[]).includes(v);
}

function isSttLanguage(v: unknown): v is SttLanguage {
  return typeof v === "string" && (STT_VALUES as readonly string[]).includes(v);
}

const RESOURCES: Record<UiLanguage, Record<string, unknown>> = {
  en: enJson as Record<string, unknown>,
  de: deJson as Record<string, unknown>,
  es: esJson as Record<string, unknown>,
};

const UI_KEY = "jarvis.ui.language";
const REPLY_KEY = "jarvis.reply.language";
const STT_KEY = "jarvis.stt.language";

function readUi(): UiLanguage {
  try {
    const raw = localStorage.getItem(UI_KEY);
    if (raw === "en" || raw === "de" || raw === "es") return raw;
  } catch {
    /* SSR / private mode */
  }
  return "en";
}

function readReply(): ReplyLanguage {
  try {
    const raw = localStorage.getItem(REPLY_KEY);
    if (isReplyLanguage(raw)) return raw;
  } catch {
    /* ignore */
  }
  return "auto";
}

function readStt(): SttLanguage {
  try {
    const raw = localStorage.getItem(STT_KEY);
    if (isSttLanguage(raw)) return raw;
  } catch {
    /* ignore */
  }
  return "auto";
}

/**
 * Push the reply language to the backend BrainManager. Fire-and-forget: the UI
 * stays responsive and a transient backend hiccup never blocks the click. The
 * backend is the runtime source of truth — without this call the choice would
 * die in localStorage (the original bug).
 */
function pushReply(lang: ReplyLanguage): void {
  try {
    void fetch(REPLY_LANGUAGE_ENDPOINT, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: lang }),
    }).catch(() => {
      /* offline / headless — localStorage re-syncs on next mount */
    });
  } catch {
    /* fetch unavailable (SSR / tests without stub) */
  }
}

/**
 * Pull the persisted reply language from the backend and reflect it in the
 * store, so the UI shows the real boot default after a restart. Call once on
 * app mount. On failure the localStorage value (already in the store) stands.
 */
export async function hydrateReplyLanguage(): Promise<void> {
  try {
    const res = await fetch(REPLY_LANGUAGE_ENDPOINT);
    if (!res.ok) return;
    const body = (await res.json()) as { language?: unknown };
    if (isReplyLanguage(body.language)) {
      useI18nStore.getState().setReply(body.language, { push: false });
    }
  } catch {
    /* keep the local value */
  }
}

/**
 * Push the STT recognition language to the backend. Fire-and-forget. Persists to
 * jarvis.toml [stt].language; applies on the next voice restart (the STT provider
 * is built once at voice bootstrap), so the UI hints at a restart after a change.
 */
function pushStt(lang: SttLanguage): void {
  try {
    void fetch(STT_LANGUAGE_ENDPOINT, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: lang }),
    }).catch(() => {
      /* offline / headless — localStorage re-syncs on next mount */
    });
  } catch {
    /* fetch unavailable (SSR / tests without stub) */
  }
}

/**
 * Pull the persisted STT recognition language from the backend and reflect it so
 * the UI shows the real boot default. Call once on the Languages view mount. On
 * failure the localStorage value (already in the store) stands.
 */
export async function hydrateSttLanguage(): Promise<void> {
  try {
    const res = await fetch(STT_LANGUAGE_ENDPOINT);
    if (!res.ok) return;
    const body = (await res.json()) as { language?: unknown };
    if (isSttLanguage(body.language)) {
      useI18nStore.getState().setStt(body.language, { push: false });
    }
  } catch {
    /* keep the local value */
  }
}

/**
 * Push the interface (display) language to the backend. The UI language is now
 * backend-backed (not just localStorage) so a voice command / the Control API
 * can change it and every open client switches live. Fire-and-forget.
 */
function pushUi(lang: UiLanguage): void {
  try {
    void fetch(UI_LANGUAGE_ENDPOINT, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: lang }),
    }).catch(() => {
      /* offline / headless — localStorage re-syncs on next mount */
    });
  } catch {
    /* fetch unavailable (SSR / tests without stub) */
  }
}

/**
 * Pull the persisted interface language from the backend and reflect it (so a
 * voice/Control-API change made while the app was closed, or on another client,
 * shows up). Called on mount and when a ConfigReloaded for ui.language arrives.
 */
export async function hydrateUiLanguage(): Promise<void> {
  try {
    const res = await fetch(UI_LANGUAGE_ENDPOINT);
    if (!res.ok) return;
    const body = (await res.json()) as { language?: unknown };
    if (isUiLanguage(body.language)) {
      useI18nStore.getState().setUi(body.language, { push: false });
    }
  } catch {
    /* keep the local value */
  }
}

interface I18nState {
  ui: UiLanguage;
  reply: ReplyLanguage;
  stt: SttLanguage;
  setUi: (lang: UiLanguage, opts?: { push?: boolean }) => void;
  setReply: (lang: ReplyLanguage, opts?: { push?: boolean }) => void;
  setStt: (lang: SttLanguage, opts?: { push?: boolean }) => void;
}

export const useI18nStore = create<I18nState>((set) => ({
  ui: readUi(),
  reply: readReply(),
  stt: readStt(),
  setUi: (lang, opts) => {
    try {
      localStorage.setItem(UI_KEY, lang);
    } catch {
      /* ignore */
    }
    set({ ui: lang });
    // Default: propagate to the backend (the new source of truth). The WS
    // handler and hydrate pass push:false to avoid a GET/PUT echo loop.
    if (opts?.push !== false) {
      pushUi(lang);
    }
  },
  setReply: (lang, opts) => {
    try {
      localStorage.setItem(REPLY_KEY, lang);
    } catch {
      /* ignore */
    }
    set({ reply: lang });
    // Default: propagate to the backend. Hydrate passes push:false to avoid a
    // GET→PUT echo loop.
    if (opts?.push !== false) {
      pushReply(lang);
    }
  },
  setStt: (lang, opts) => {
    try {
      localStorage.setItem(STT_KEY, lang);
    } catch {
      /* ignore */
    }
    set({ stt: lang });
    // Default: propagate to the backend. Hydrate passes push:false to avoid a
    // GET→PUT echo loop.
    if (opts?.push !== false) {
      pushStt(lang);
    }
  },
}));

/**
 * Resolve "nav.skills" zu dem String aus der aktiven Sprache.
 * Fallback-Kette:
 *   1. aktive Sprache
 *   2. Englisch (Default)
 *   3. der Key selbst (damit man nie "undefined" sieht)
 */
function resolve(lang: UiLanguage, key: string): string {
  const parts = key.split(".");
  const tryLookup = (root: Record<string, unknown>): string | null => {
    let cur: unknown = root;
    for (const p of parts) {
      if (cur && typeof cur === "object" && p in (cur as Record<string, unknown>)) {
        cur = (cur as Record<string, unknown>)[p];
      } else {
        return null;
      }
    }
    return typeof cur === "string" ? cur : null;
  };
  return tryLookup(RESOURCES[lang]) ?? tryLookup(RESOURCES.en) ?? key;
}

// The assistant-name token. Any locale value referring to the assistant by name
// uses `{name}` instead of a hardcoded "Jarvis", so a rename (the configurable
// assistant identity) propagates to EVERY translated string — headings, profile
// copy, onboarding, etc. — through one substitution. Non-collision verified:
// no other locale value contains a literal "{name}" (numeric placeholders use
// the `{0}` form). The substitution is a no-op for strings without the token.
const NAME_TOKEN = /\{name\}/g;

export function interpolateName(text: string, name: string): string {
  if (!name || !text.includes("{name}")) return text;
  return text.replace(NAME_TOKEN, name);
}

/**
 * Imperative, non-hook translate accessor.
 *
 * `useT` is a React hook and is illegal outside a component body (class
 * components, module-level helpers). This reads the current UI language from
 * the store directly — the same pattern the codebase already uses via
 * `useEventStore.getState()`. It interpolates the assistant name too, so the
 * `{name}` token works the same as in `useT`.
 */
export function translate(key: string): string {
  const lang = useI18nStore.getState().ui;
  const name = useEventStore.getState().assistantName;
  return interpolateName(resolve(lang, key), name);
}

export function useT(): (key: string) => string {
  const lang = useI18nStore((s) => s.ui);
  // Reactive: every t() consumer re-renders when the assistant name changes,
  // so a Settings rename live-updates the whole UI. Selector-scoped, so other
  // store mutations (voiceState, transcript, …) do NOT trigger a re-render.
  const assistantName = useEventStore((s) => s.assistantName);
  return (key: string) => interpolateName(resolve(lang, key), assistantName);
}

export function useUiLanguage(): UiLanguage {
  return useI18nStore((s) => s.ui);
}

export function useReplyLanguage(): ReplyLanguage {
  return useI18nStore((s) => s.reply);
}

export function useSttLanguage(): SttLanguage {
  return useI18nStore((s) => s.stt);
}

export function setUiLanguage(lang: UiLanguage): void {
  useI18nStore.getState().setUi(lang);
}

export function setReplyLanguage(lang: ReplyLanguage): void {
  useI18nStore.getState().setReply(lang);
}

export function setSttLanguage(lang: SttLanguage): void {
  useI18nStore.getState().setStt(lang);
}
