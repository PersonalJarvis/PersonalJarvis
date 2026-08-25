/**
 * Paint-first snapshot of the Local models overview.
 *
 * The last `GET …/local-models/overview` payload is mirrored into
 * `localStorage` (one entry per provider id) so the next open paints the
 * tiles and the role checklist synchronously from it, while react-query
 * refetches in the background. The entry is versioned: a shape change bumps
 * `SNAPSHOT_VERSION` and older entries are simply ignored. Entries above
 * ~200 KB are not written (a huge inventory would otherwise crowd the
 * origin's storage quota out for the other caches).
 *
 * The payload type is imported from the hook module as a type only — this
 * file stays free of React and react-query so the idle prefetch can read it.
 */
import type { OverviewResponse } from "@/hooks/useLocalModels";

/** Bump when `OverviewResponse` changes shape in a way old paints would lie. */
export const SNAPSHOT_VERSION = 1;
/** Largest JSON we are willing to keep per provider (bytes, roughly). */
export const SNAPSHOT_MAX_BYTES = 200_000;

const KEY_PREFIX = "jarvis.localModels.overview.";

interface Envelope {
  v: number;
  data: OverviewResponse;
}

export function snapshotKey(providerId: string): string {
  return `${KEY_PREFIX}${providerId}`;
}

/**
 * Synchronously read the last overview for `providerId`; `null` when there
 * is none, the version does not match, the JSON is broken or storage is
 * unavailable. The returned payload carries `source: "cache"` so a panel can
 * tell a painted snapshot from server truth.
 */
export function readOverviewSnapshot(
  providerId: string,
): OverviewResponse | null {
  try {
    const raw = window.localStorage.getItem(snapshotKey(providerId));
    if (!raw) return null;
    const env = JSON.parse(raw) as Partial<Envelope> | null;
    if (!env || env.v !== SNAPSHOT_VERSION || !env.data) return null;
    const data = env.data;
    if (
      typeof data.fetched_at !== "number" ||
      !data.server ||
      !data.roles ||
      !data.inventory ||
      !data.recommended
    )
      return null;
    return { ...data, source: "cache" };
  } catch {
    return null;
  }
}

/**
 * Persist an overview for the next paint. Oversized payloads and blocked
 * storage are silent no-ops — the section still works, it just paints a
 * beat later next time.
 */
export function writeOverviewSnapshot(
  providerId: string,
  data: OverviewResponse,
): boolean {
  try {
    const env: Envelope = { v: SNAPSHOT_VERSION, data };
    const json = JSON.stringify(env);
    if (json.length > SNAPSHOT_MAX_BYTES) return false;
    window.localStorage.setItem(snapshotKey(providerId), json);
    return true;
  } catch {
    return false;
  }
}

export function clearOverviewSnapshot(providerId: string): void {
  try {
    window.localStorage.removeItem(snapshotKey(providerId));
  } catch {
    // Nothing to recover.
  }
}
