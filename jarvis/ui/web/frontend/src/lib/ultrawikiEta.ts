/**
 * Turning a measured backlog duration into a sentence a person can act on.
 *
 * Why this exists (2026-07-27). The overview stated a 236 000-item backlog and
 * followed it with "you do not have to wait". Both halves came from real
 * numbers; neither said how long, and the answer was four days for the
 * embedding queue alone. A duration is the difference between a detail you can
 * ignore and a decision you need to make, so it is now shown wherever the
 * backlog is.
 *
 * The rules are the same as the backend's (jarvis/ultrawiki/throughput.py) and
 * they matter more than the formatting:
 *
 * - `null` seconds is NOT zero and not infinity. It means the rate was not
 *   measurable, and the caller says so instead of printing a number.
 * - A stalled lane gets no duration at all. "Never at this rate" is not a
 *   date, and rendering one implies the queue is moving.
 * - The precision is deliberately coarse. Nobody decides differently between
 *   "4.1 days" and "4 days 3 hours", and a spuriously precise estimate over a
 *   queue this size reads as a promise it cannot keep.
 */
import type { UltraWikiThroughputLane } from "@/lib/ultrawikiApi";

type Translate = (key: string) => string;

/**
 * A duration as localized prose, or `""` when there is nothing honest to say.
 *
 * Buckets rather than a unit conversion, because each bucket is a different
 * sentence in a language with plural rules — a single "{0} {unit}" template
 * cannot survive translation into all of them.
 */
export function formatEta(seconds: number | null | undefined, t: Translate): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "";
  }
  const total = Math.max(0, seconds);
  if (total < 90) return t("ultrawiki.eta.under_minute");
  const minutes = total / 60;
  if (minutes < 90) return t("ultrawiki.eta.minutes").replace("{0}", String(Math.round(minutes)));
  const hours = minutes / 60;
  if (hours < 36) return t("ultrawiki.eta.hours").replace("{0}", String(Math.round(hours)));
  const days = hours / 24;
  if (days < 14) {
    // One decimal below two weeks: the difference between 4 and 5 days is a
    // different weekend, and rounding it away hides that.
    const shown = days < 10 ? days.toFixed(1).replace(/\.0$/, "") : String(Math.round(days));
    return t("ultrawiki.eta.days").replace("{0}", shown);
  }
  const weeks = days / 7;
  if (weeks < 9) return t("ultrawiki.eta.weeks").replace("{0}", String(Math.round(weeks)));
  return t("ultrawiki.eta.months").replace("{0}", String(Math.round(days / 30)));
}

/** What a lane can honestly be said to be doing right now. */
export type PaceKind = "measuring" | "stalled" | "paused" | "moving" | "done";

export interface Pace {
  kind: PaceKind;
  /** Localized duration, `""` unless `kind === "moving"`. */
  eta: string;
  /** Whole items an hour, rounded; `null` unless measured. */
  perHour: number | null;
  /** The backend's own sentence for a parked lane, `""` otherwise. */
  pausedReason: string;
}

/**
 * Classify one lane. The order is the point: a lane that is parked on purpose
 * must never be reported as stalled, and a lane still being measured must
 * never be reported as moving.
 */
export function paceOf(lane: UltraWikiThroughputLane | undefined, t: Translate): Pace {
  const empty: Pace = { kind: "measuring", eta: "", perHour: null, pausedReason: "" };
  if (!lane) return empty;
  const pausedReason = lane.paused_reason || "";
  if (lane.backlog === 0) {
    return { kind: "done", eta: "", perHour: lane.rate_per_hour, pausedReason };
  }
  if (pausedReason) {
    return { kind: "paused", eta: "", perHour: lane.rate_per_hour, pausedReason };
  }
  if (lane.rate_per_hour === null || lane.rate_per_hour === undefined) return empty;
  if (lane.stalled) {
    return { kind: "stalled", eta: "", perHour: 0, pausedReason };
  }
  return {
    kind: "moving",
    eta: formatEta(lane.eta_seconds, t),
    perHour: Math.round(lane.rate_per_hour),
    pausedReason,
  };
}
