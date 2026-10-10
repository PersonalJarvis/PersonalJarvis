/**
 * Small, pure formatting helpers for the SOUL.md page — dates the way a
 * person says them ("today", "3 days ago") and the ledger's timestamps.
 */
import type { UiLanguage } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";

const DAY_MS = 86_400_000;

/**
 * The learning ledger writes `2026-10-02T10:00:10+0200` (strftime `%z`, no
 * colon), which not every engine parses; this adds the colon.
 */
export function parseLedgerTime(ts: string): number | null {
  if (!ts) return null;
  const normalized = ts.replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
  const ms = Date.parse(normalized);
  return Number.isNaN(ms) ? null : ms;
}

/** "today", "yesterday", "3 days ago"; a plain date past a month. */
export function relativeDay(ms: number | null, lang: UiLanguage, now: number = Date.now()): string | null {
  if (ms === null) return null;
  const startOf = (t: number) => {
    const d = new Date(t);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  };
  const days = Math.round((startOf(now) - startOf(ms)) / DAY_MS);
  if (days >= 0 && days <= 30) {
    return new Intl.RelativeTimeFormat(localeForUiLanguage(lang), { numeric: "auto" }).format(-days, "day");
  }
  return new Date(ms).toLocaleDateString(localeForUiLanguage(lang), { day: "numeric", month: "short", year: "numeric" });
}

/** A time of day for an entry from today, else the relative day. */
export function whenShort(ms: number | null, lang: UiLanguage, now: number = Date.now()): string | null {
  if (ms === null) return null;
  const sameDay = new Date(ms).toDateString() === new Date(now).toDateString();
  if (sameDay) {
    return new Date(ms).toLocaleTimeString(localeForUiLanguage(lang), { hour: "2-digit", minute: "2-digit" });
  }
  return relativeDay(ms, lang, now);
}

/** The newest of several file times; `null` when none is known. */
export function newest(times: readonly (number | null | undefined)[]): number | null {
  const known = times.filter((t): t is number => typeof t === "number");
  return known.length ? Math.max(...known) : null;
}

/** `key_one` for exactly one, `key_other` otherwise, with `{0}` filled in. */
export function plural(t: (key: string) => string, key: string, count: number, lang?: UiLanguage): string {
  const n = lang ? count.toLocaleString(localeForUiLanguage(lang)) : String(count);
  return t(`${key}_${count === 1 ? "one" : "other"}`).replace("{0}", n);
}
