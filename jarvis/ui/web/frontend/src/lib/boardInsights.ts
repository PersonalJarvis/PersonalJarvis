/**
 * Pure maths behind the Board page. No React, no fetch — every figure the
 * page draws is derived here from the `/api/board/insights` answer, so the
 * derivations are testable on their own and named in one place.
 */
import type { BoardInsights, InsightsDay } from "@/hooks/useBoardInsights";
import { prettyProviderName } from "@/lib/prettyProviderName";

/** Things the user started on a day: each dictation, voice session, chat
 * message and coding-agent session counts once. */
export function actionsOf(day: InsightsDay): number {
  return day.dictations + day.voice_sessions + day.chat_messages + day.agent_sessions;
}

/** Words the user gave Jarvis on a day — dictated plus said in voice sessions. */
export function wordsOf(day: InsightsDay): number {
  return day.dictation_words + day.voice_words;
}

/**
 * Intensity level 0..4 per value. 0 is "nothing"; 1..4 split the non-zero
 * values at their quartiles, so one record day does not flatten every other
 * day into the palest shade.
 */
export function intensityLevels(values: readonly number[]): (v: number) => 0 | 1 | 2 | 3 | 4 {
  const sorted = values.filter((v) => v > 0).sort((a, b) => a - b);
  if (sorted.length === 0) return () => 0;
  const q = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  const [q1, q2, q3] = [q(0.25), q(0.5), q(0.75)];
  return (v) => {
    if (v <= 0) return 0;
    if (v <= q1) return 1;
    if (v <= q2) return 2;
    if (v <= q3) return 3;
    return 4;
  };
}

export interface CalendarWeek {
  /** Seven slots, Monday first; `null` pads days outside the series. */
  days: (InsightsDay | null)[];
  /** Set on the first week of a month: the month index 0..11. */
  monthStart: number | null;
}

function parseDay(iso: string): Date {
  return new Date(`${iso}T00:00:00`);
}

/** Lay the daily series into Monday-first week columns. */
export function calendarWeeks(days: readonly InsightsDay[]): CalendarWeek[] {
  const weeks: CalendarWeek[] = [];
  let current: (InsightsDay | null)[] = [];
  let lastMonth = -1;
  let monthStart: number | null = null;
  for (const day of days) {
    const date = parseDay(day.date);
    const slot = (date.getDay() + 6) % 7;
    if (current.length === 0 && slot > 0) {
      current = Array(slot).fill(null);
    }
    if (date.getMonth() !== lastMonth) {
      lastMonth = date.getMonth();
      monthStart = lastMonth;
    }
    current.push(day);
    if (current.length === 7) {
      weeks.push({ days: current, monthStart });
      current = [];
      monthStart = null;
    }
  }
  if (current.length > 0) {
    while (current.length < 7) current.push(null);
    weeks.push({ days: current, monthStart });
  }
  return weeks;
}

/** Minutes saved by dictating instead of typing at `typingWpm`. Never negative. */
export function minutesSaved(words: number, seconds: number, typingWpm: number): number {
  if (typingWpm <= 0) return 0;
  return Math.max(0, words / typingWpm - seconds / 60);
}

export function wordsPerMinute(words: number, seconds: number): number {
  return seconds > 0 ? words / (seconds / 60) : 0;
}

/** Percent change, or `null` when there is nothing to compare against. */
export function trendPercent(current: number, previous: number): number | null {
  if (previous <= 0) return null;
  return Math.round(((current - previous) / previous) * 100);
}

export interface MixRow {
  key: "dictations" | "agent_sessions" | "chat_messages" | "voice_sessions";
  count: number;
  share: number;
}

/** How the user reaches Jarvis — every row a thing they started, by share. */
export function usageMix(data: BoardInsights): MixRow[] {
  const rows: Omit<MixRow, "share">[] = [
    { key: "dictations", count: data.dictation.dictations },
    { key: "agent_sessions", count: data.agents.sessions },
    { key: "chat_messages", count: data.chats.messages },
    { key: "voice_sessions", count: data.voice.sessions },
  ];
  const total = rows.reduce((sum, r) => sum + r.count, 0);
  return rows
    .map((r) => ({ ...r, share: total ? r.count / total : 0 }))
    .sort((a, b) => b.count - a.count);
}

export interface PeakSlot {
  weekday: number; // 0 = Monday
  hour: number;
  value: number;
}

export function peakSlot(punch: readonly (readonly number[])[]): PeakSlot | null {
  let best: PeakSlot | null = null;
  punch.forEach((row, weekday) =>
    row.forEach((value, hour) => {
      if (value > 0 && (!best || value > best.value)) best = { weekday, hour, value };
    }),
  );
  return best;
}

export function hourTotals(punch: readonly (readonly number[])[]): number[] {
  return Array.from({ length: 24 }, (_, h) => punch.reduce((s, row) => s + (row[h] ?? 0), 0));
}

export function weekdayTotals(punch: readonly (readonly number[])[]): number[] {
  return punch.map((row) => row.reduce((s, v) => s + v, 0));
}

export interface WeekTotal {
  start: string;
  words: number;
  agentSessions: number;
}

/**
 * The last `count` Monday-first weeks of the series, oldest first. Leading
 * empty weeks are dropped (down to `minimum`), so a new user's chart starts at
 * their first week instead of a row of blanks.
 */
export function weeklyTotals(
  days: readonly InsightsDay[],
  count: number,
  minimum = count,
): WeekTotal[] {
  const totals = calendarWeeks(days).slice(-count).map((week) => {
    const present = week.days.filter((d): d is InsightsDay => d !== null);
    return {
      start: present[0]?.date ?? "",
      words: present.reduce((s, d) => s + wordsOf(d), 0),
      agentSessions: present.reduce((s, d) => s + d.agent_sessions, 0),
    };
  });
  const first = totals.findIndex((w) => w.words > 0);
  const cut = first < 0 ? 0 : Math.min(first, Math.max(0, totals.length - minimum));
  return totals.slice(cut);
}

const MILESTONES = [
  1_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_500_000,
  5_000_000, 10_000_000,
];

/** The milestone just passed and the next one, for the words progress bar. */
export function milestoneProgress(words: number): { previous: number; next: number } {
  const next = MILESTONES.find((m) => m > words) ?? Math.ceil((words + 1) / 10_000_000) * 10_000_000;
  const previous = [...MILESTONES].reverse().find((m) => m <= words) ?? 0;
  return { previous, next };
}

const AGENT_LABELS: Record<string, string> = {
  "claude-cli": "Claude Code",
  "codex-cli": "Codex",
  "agy-cli": "Antigravity",
  "grok-cli": "Grok CLI",
  "opencode-cli": "OpenCode",
  "kimi-cli": "Kimi CLI",
  "glm-cli": "GLM CLI",
  "cursor-cli": "Cursor CLI",
};

export function agentLabel(id: string): string {
  return AGENT_LABELS[id] ?? id.replace(/-cli$/, "");
}

const CHAT_PROVIDER_LABELS: Record<string, string> = {
  "claude-api": "Claude",
  "openai-codex": "Codex",
  antigravity: "Antigravity",
  "grok-build": "Grok Build",
};

export function chatProviderLabel(id: string): string {
  return CHAT_PROVIDER_LABELS[id] ?? prettyProviderName(id);
}

/** Short form for big counts, in the same locale as `toLocaleString()`. */
export function compactNumber(n: number): string {
  return new Intl.NumberFormat(undefined, {
    notation: n >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: n >= 10_000 ? 1 : 0,
  }).format(n);
}

/** `{0}` substitution plus the `_one` plural key convention of the locales. */
export function plural(t: (k: string) => string, baseKey: string, n: number): string {
  return t(n === 1 ? `${baseKey}_one` : baseKey).replace("{0}", n.toLocaleString());
}

/** A local day as "3 Oct 2026" in the UI language. */
export function formatDay(iso: string, lang?: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
}
