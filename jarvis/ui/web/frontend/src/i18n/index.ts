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
 * Voice-Sprache (was Whisper erkennt) ist ein eigenes Setting:
 *   useVoiceLanguage(), setVoiceLanguage("auto" | "en" | "de" | "es")
 */
import { create } from "zustand";
import enJson from "./locales/en.json";
import deJson from "./locales/de.json";
import esJson from "./locales/es.json";

export type UiLanguage = "en" | "de" | "es";
// "auto" mirrors the user's input language; the rest hard-pin the reply language.
// Mirrors jarvis/brain/manager.py::SUPPORTED_REPLY_LANGUAGES (single source of truth).
export type ReplyLanguage = "auto" | "en" | "de" | "es";

const REPLY_LANGUAGE_ENDPOINT = "/api/settings/reply-language";
const REPLY_VALUES: readonly ReplyLanguage[] = ["auto", "en", "de", "es"];

function isReplyLanguage(v: unknown): v is ReplyLanguage {
  return typeof v === "string" && (REPLY_VALUES as readonly string[]).includes(v);
}

const RESOURCES: Record<UiLanguage, Record<string, unknown>> = {
  en: enJson as Record<string, unknown>,
  de: deJson as Record<string, unknown>,
  es: esJson as Record<string, unknown>,
};

const UI_KEY = "jarvis.ui.language";
const REPLY_KEY = "jarvis.reply.language";

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

interface I18nState {
  ui: UiLanguage;
  reply: ReplyLanguage;
  setUi: (lang: UiLanguage) => void;
  setReply: (lang: ReplyLanguage, opts?: { push?: boolean }) => void;
}

export const useI18nStore = create<I18nState>((set) => ({
  ui: readUi(),
  reply: readReply(),
  setUi: (lang) => {
    try {
      localStorage.setItem(UI_KEY, lang);
    } catch {
      /* ignore */
    }
    set({ ui: lang });
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

export function useT(): (key: string) => string {
  const lang = useI18nStore((s) => s.ui);
  return (key: string) => resolve(lang, key);
}

export function useUiLanguage(): UiLanguage {
  return useI18nStore((s) => s.ui);
}

export function useReplyLanguage(): ReplyLanguage {
  return useI18nStore((s) => s.reply);
}

export function setUiLanguage(lang: UiLanguage): void {
  useI18nStore.getState().setUi(lang);
}

export function setReplyLanguage(lang: ReplyLanguage): void {
  useI18nStore.getState().setReply(lang);
}
