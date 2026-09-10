/** Seconds as the coarsest unit that stays a whole number: "6 h", "30 min". */
function humanEvery(seconds: number): string {
  if (seconds % 86_400 === 0) return `${seconds / 86_400} d`;
  if (seconds % 3_600 === 0) return `${seconds / 3_600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${Math.round(seconds)} s`;
}

/** Clock time from an ISO timestamp, or empty when it is not a real date. */
function clockFromIso(value: unknown): string {
  if (typeof value !== "string") return "";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/**
 * The raw trigger object into one readable phrase.
 *
 * The scheduler stores a `TaskSpec` trigger (`jarvis/society/routines.py`),
 * not a sentence, and there is no server-side rendering of it — so the shapes
 * are mapped here, and an unknown kind falls back to its own name rather than
 * to an invented schedule. `t` is the locale function so the phrase follows
 * the UI language; tests pass the identity `(k) => k` and assert on keys.
 */
export function describeTrigger(trigger: unknown, t: (key: string) => string = (key) => key): string {
  if (!trigger || typeof trigger !== "object") return "";
  const raw = trigger as Record<string, unknown>;
  const kind = String(raw.kind ?? raw.type ?? "");
  if (kind === "source") {
    const source = raw.source as { kind: string; topic?: string; path?: string; upstream_id?: string };
    return [t("society.triggers.kinds." + source.kind), source.topic || source.path || source.upstream_id].filter(Boolean).join(" · ");
  }
  if (kind === "cron") return `${String(raw.expression)} · ${String(raw.timezone)}`;
  if (kind === "webhook" || kind === "event_hook") {
    const label = kind === "webhook" ? (raw.provider && raw.provider !== "generic" ? String(raw.provider) : "Webhook") : t("society.hooks.event_hook") + " · " + String(raw.event_name ?? "");
    const conditions = raw.conditions && typeof raw.conditions === "object" ? Object.entries(raw.conditions).map(([field, value]) => `${field} = ${JSON.stringify(value)}`).join(", ") : "";
    return conditions ? `${label} · ${conditions}` : label;
  }
  if (kind === "calendar") {
    const time = String(raw.local_time ?? "");
    const zone = String(raw.timezone ?? "");
    const weekdays = Array.isArray(raw.weekdays) ? raw.weekdays.map((day: number) =>
      new Date(Date.UTC(2024, 0, 1 + day)).toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" })).join(", ") : "";
    const filters = [weekdays, Array.isArray(raw.month_days) && raw.month_days.length ? `[${raw.month_days.join(", ")}]` : "",
      Array.isArray(raw.months) && raw.months.length ? raw.months.map((month: number) =>
        new Date(Date.UTC(2024, month - 1, 1)).toLocaleDateString(undefined, { month: "short", timeZone: "UTC" })).join(", ") : ""].filter(Boolean).join(" · ");
    return `${filters ? `${filters} · ${time}` : t("society.card.sched_every_day_at").replace("{0}", time)} · ${zone}`;
  }
  if (kind === "every" && typeof raw.interval_seconds === "number") {
    const seconds = raw.interval_seconds;
    const time = clockFromIso(raw.start_at);
    if (seconds % 86_400 === 0) {
      const days = seconds / 86_400;
      if (days === 1 && time) return t("society.card.sched_every_day_at").replace("{0}", time);
      if (days === 1) return t("society.card.sched_every_day");
      return t("society.card.sched_every_days").replace("{0}", String(days));
    }
    if (seconds % 3_600 === 0) {
      const hours = seconds / 3_600;
      if (hours === 1) return t("society.card.sched_every_hour");
      return t("society.card.sched_every_hours").replace("{0}", String(hours));
    }
    if (seconds % 60 === 0) {
      const minutes = seconds / 60;
      if (minutes === 1) return t("society.card.sched_every_minute");
      return t("society.card.sched_every_minutes").replace("{0}", String(minutes));
    }
    return t("society.card.sched_every_seconds").replace("{0}", String(Math.round(seconds)));
  }
  if (kind === "at_time" && typeof raw.iso_timestamp === "string") {
    const clock = clockFromIso(raw.iso_timestamp);
    return clock ? t("society.card.sched_at").replace("{0}", clock) : t("society.card.sched_at_fixed");
  }
  if (kind === "after_delay" && typeof raw.delay_seconds === "number") {
    return t("society.card.sched_once_in").replace("{0}", humanEvery(raw.delay_seconds));
  }
  if (kind === "on_event" && typeof raw.event_name === "string") {
    return t("society.card.sched_on").replace("{0}", raw.event_name);
  }
  return kind;
}

