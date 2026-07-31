/**
 * One number/locale policy for the whole Run Inspector.
 *
 * Bare `toLocaleString()` follows the BROWSER's locale, not the app's UI
 * language. On a German-locale machine running the English UI that rendered
 * "3.613" for 3613 tokens — which an English reader parses as three-point-six.
 * Worse, the same run showed the value three different ways (raw "3613", German
 * "3.613", and a locale-aware "3,613") depending on which panel drew it.
 * Everything here routes through the UI language instead.
 */
import { useUiLanguage } from "@/i18n";

export function localeForUiLanguage(language: string): string {
  if (language === "de") return "de-DE";
  if (language === "es") return "es-ES";
  return "en-US";
}

/** The BCP-47 locale matching the app's current UI language. */
export function useRunLocale(): string {
  return localeForUiLanguage(useUiLanguage());
}

/** Thousands-grouped integer in the UI language. */
export function fmtInt(n: number, locale: string): string {
  return n.toLocaleString(locale);
}

/** Compact duration: sub-second in ms, otherwise seconds with one decimal. */
export function fmtMs(ms: number): string {
  if (ms <= 0) return "0ms";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}
