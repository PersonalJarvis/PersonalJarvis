/**
 * "Not now" for the app-wide macOS permissions banner.
 *
 * The banner used to be a permanent strip over every view until every row was
 * granted — including rows for features the person never uses. Other Mac apps
 * ask once, then let you decline and get on with it. This remembers which rows
 * were put off, in `localStorage` (a per-WebView convenience, nothing the
 * backend needs):
 *
 * - it is per ROW, so granting one permission never brings the others back, and
 *   a row that turns up later (a feature switched on afterwards) still shows;
 * - it expires after a week, so a voice assistant whose microphone was declined
 *   in a hurry reminds you again instead of staying quietly dead for good.
 *
 * A storage that cannot be read or written (private mode, a locked-down WebView)
 * only costs the convenience: the banner is simply not dismissible there.
 */

/** localStorage key for the dismissed-row record. */
export const PERMISSIONS_BANNER_DISMISSED_KEY = "jarvis.permissions.banner.dismissed.v1";

/** How long a "Not now" holds before the banner may come back. */
export const PERMISSIONS_BANNER_DISMISS_TTL_MS = 7 * 24 * 60 * 60 * 1000;

interface DismissedRecord {
  at: number;
  ids: string[];
}

function parseRecord(raw: string | null, now: number): DismissedRecord | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<DismissedRecord> | null;
    if (!value || typeof value.at !== "number" || !Array.isArray(value.ids)) return null;
    if (now - value.at >= PERMISSIONS_BANNER_DISMISS_TTL_MS || now < value.at) return null;
    return { at: value.at, ids: value.ids.filter((id): id is string => typeof id === "string") };
  } catch {
    // A record that is not JSON is as good as none: the banner shows again.
    return null;
  }
}

/** The rows currently put off; empty when nothing was, or the week has passed. */
export function readDismissedPermissionIds(now: number = Date.now()): ReadonlySet<string> {
  try {
    const record = parseRecord(window.localStorage.getItem(PERMISSIONS_BANNER_DISMISSED_KEY), now);
    return new Set(record?.ids ?? []);
  } catch {
    // Storage unavailable: nothing can have been dismissed here.
    return new Set();
  }
}

/**
 * Put `ids` off (on top of the rows already put off) and restart the week.
 * Returns what is dismissed afterwards, whether or not it could be stored.
 */
export function dismissPermissionIds(
  ids: readonly string[],
  now: number = Date.now(),
): ReadonlySet<string> {
  const merged = new Set([...readDismissedPermissionIds(now), ...ids]);
  try {
    const record: DismissedRecord = { at: now, ids: [...merged] };
    window.localStorage.setItem(PERMISSIONS_BANNER_DISMISSED_KEY, JSON.stringify(record));
  } catch {
    // Not stored: this session still honours the click, the next one asks again.
  }
  return merged;
}
