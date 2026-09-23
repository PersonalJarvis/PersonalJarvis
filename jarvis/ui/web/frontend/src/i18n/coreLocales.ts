/** Core translations are cached individually instead of all riding in the entry chunk. */
export const CORE_LANGUAGES = ["en", "de", "es"] as const;
export type CoreLanguage = (typeof CORE_LANGUAGES)[number];

export const CORE_RESOURCES: Record<CoreLanguage, Record<string, unknown>> = { en: {}, de: {}, es: {} };
const loaded = new Set<CoreLanguage>();
const pending: Partial<Record<CoreLanguage, Promise<void>>> = {};
const loaders = {
  en: () => import("./locales/en.json"),
  de: () => import("./locales/de.json"),
  es: () => import("./locales/es.json"),
};

export function isCoreLocaleLoaded(language: CoreLanguage): boolean {
  return loaded.has(language);
}

export function loadCoreLocale(language: CoreLanguage): Promise<void> {
  const existing = pending[language];
  if (existing) return existing;
  const task = loaders[language]().then((module) => {
    CORE_RESOURCES[language] = module.default;
    loaded.add(language);
  }).catch((error: unknown) => {
    delete pending[language];
    throw error;
  });
  pending[language] = task;
  return task;
}

export const LOCALE_BOOT_ERROR = {
  en: { message: "The interface could not be loaded. Please try again.", retry: "Reload" },
  de: { message: "Die Oberfläche konnte nicht geladen werden. Bitte erneut versuchen.", retry: "Neu laden" },
  es: { message: "No se pudo cargar la interfaz. Vuelve a intentarlo.", retry: "Recargar" },
} satisfies Record<CoreLanguage, { message: string; retry: string }>;
