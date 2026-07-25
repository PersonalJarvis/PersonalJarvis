/**
 * "last sync 4 min ago" instead of "2026-07-25T14:03:11Z".
 *
 * The raw ISO stamp is what the Sources card used to print, and nobody reads a
 * timestamp as "recently". The formatter returns an i18n KEY plus its one
 * placeholder value rather than a finished sentence, so every locale keeps its
 * own wording (and its own plural rules) instead of an English string being
 * assembled here.
 */

/** An i18n key under `ultrawiki.time.*` plus the value for its `{0}`. */
export interface RelativeTimeParts {
  key: string;
  value: string;
}

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/**
 * `null` for an absent or unparseable stamp — the caller then says "never"
 * rather than inventing a moment. A stamp in the future (clock skew between a
 * server and this browser) reads as "just now", never as a negative age.
 */
export function relativeTimeParts(
  stamp: string | number | null | undefined,
  now: number = Date.now(),
): RelativeTimeParts | null {
  if (stamp === null || stamp === undefined || stamp === "") return null;
  const parsed =
    typeof stamp === "number" ? stamp * 1000 : Date.parse(String(stamp));
  if (!Number.isFinite(parsed)) return null;
  const age = Math.max(0, now - parsed);
  if (age < MINUTE_MS) return { key: "ultrawiki.time.just_now", value: "" };
  if (age < HOUR_MS) {
    return {
      key: "ultrawiki.time.minutes",
      value: String(Math.floor(age / MINUTE_MS)),
    };
  }
  if (age < DAY_MS) {
    return {
      key: "ultrawiki.time.hours",
      value: String(Math.floor(age / HOUR_MS)),
    };
  }
  return { key: "ultrawiki.time.days", value: String(Math.floor(age / DAY_MS)) };
}

/** Convenience wrapper: resolve the parts through a translate function. */
export function formatRelativeTime(
  stamp: string | number | null | undefined,
  t: (key: string) => string,
  now: number = Date.now(),
): string {
  const parts = relativeTimeParts(stamp, now);
  return parts === null ? "" : t(parts.key).replace("{0}", parts.value);
}
