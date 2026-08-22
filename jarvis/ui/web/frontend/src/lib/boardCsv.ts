/**
 * Serialize the Board's aggregates as CSV.
 *
 * The Board holds three shapes at once — lifetime/window totals, a day-by-day
 * series, and a category breakdown. Flattening them into one wide table would
 * need a column per metric per shape and leave most cells empty, so this emits
 * TIDY (long) rows instead: one observation per line, four fixed columns.
 *
 *   section,date,metric,value
 *   totals,,user_words,124503
 *   daily,2026-08-22,user_words,1204
 *   categories,,music,42
 *
 * That is the shape pandas pivots in one call and Excel filters natively, and
 * adding a fourth aggregate later costs a `section` value, not a schema change.
 *
 * Dates are ISO 8601 (`YYYY-MM-DD`), never the localized display format —
 * `08/22/2026` and `22.08.2026` sort as text and are read differently on every
 * machine that opens the file. The backend already speaks ISO here, so the
 * values pass through untouched.
 */
import type {
  BoardCategories,
  BoardHeatmap,
  BoardSummary,
} from "@/hooks/useBoard";

/** The fixed header. Exported so a test pins it rather than restating it. */
export const CSV_HEADER = ["section", "date", "metric", "value"] as const;

/**
 * Quote one field per RFC 4180.
 *
 * A field is quoted when it contains a comma, a quote, a newline, or edge
 * whitespace that a reader would otherwise silently trim; inner quotes double.
 * Category names are free text and reach this function, so this is the
 * difference between a usable file and one that shifts every column after the
 * first category with a comma in it.
 */
export function csvField(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  const needsQuotes =
    text.includes(",") ||
    text.includes('"') ||
    text.includes("\n") ||
    text.includes("\r") ||
    text !== text.trim();
  if (!needsQuotes) return text;
  return `"${text.replace(/"/g, '""')}"`;
}

export interface BoardCsvInput {
  summary?: BoardSummary;
  heatmap?: BoardHeatmap;
  categories?: BoardCategories;
}

type Row = [string, string, string, string | number];

/**
 * Build the rows for whatever the view actually holds.
 *
 * Sections the view has not loaded are omitted rather than written as zeros —
 * an empty `daily` section says "not loaded", a run of zeros would claim "no
 * activity", and those are different facts.
 */
export function boardCsvRows(input: BoardCsvInput): Row[] {
  const rows: Row[] = [];

  if (input.summary) {
    const { totals, window, streak_days, longest_streak, window_days } =
      input.summary;
    for (const [metric, value] of Object.entries(totals)) {
      // first_day is a date, not a measurement — it belongs in the date column.
      if (metric === "first_day") {
        if (value) rows.push(["totals", String(value), "first_day", ""]);
        continue;
      }
      rows.push(["totals", "", metric, value as number]);
    }
    rows.push(["totals", "", "streak_days", streak_days]);
    rows.push(["totals", "", "longest_streak", longest_streak]);

    rows.push(["window", "", "window_days", window_days]);
    for (const [metric, value] of Object.entries(window)) {
      // A null rate means "not enough data to compute", which an empty cell
      // carries correctly and a 0 would not.
      rows.push(["window", "", metric, value === null ? "" : (value as number)]);
    }
  }

  if (input.heatmap) {
    for (const cell of input.heatmap.cells) {
      const { date, ...metrics } = cell;
      for (const [metric, value] of Object.entries(metrics)) {
        rows.push(["daily", date, metric, value as number]);
      }
    }
  }

  if (input.categories) {
    for (const entry of input.categories.categories) {
      rows.push(["categories", "", entry.category, entry.count]);
    }
  }

  return rows;
}

/**
 * Render the full document.
 *
 * CRLF line endings are what RFC 4180 specifies and what Excel expects; every
 * other reader accepts them. The leading BOM is there so a double-clicked file
 * opens with correct umlauts in Excel — pandas strips it, so the file stays
 * usable in both places.
 */
export function boardCsv(input: BoardCsvInput): string {
  const lines = [
    CSV_HEADER.join(","),
    ...boardCsvRows(input).map((row) => row.map(csvField).join(",")),
  ];
  return "﻿" + lines.join("\r\n") + "\r\n";
}

/** `jarvis-board-2026-08-22.csv` — ISO in the name too, so files sort by date. */
export function boardCsvFilename(now: Date = new Date()): string {
  const iso = now.toISOString().slice(0, 10);
  return `jarvis-board-${iso}.csv`;
}
