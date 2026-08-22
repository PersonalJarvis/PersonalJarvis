import { describe, expect, it } from "vitest";
import {
  CSV_HEADER,
  boardCsv,
  boardCsvFilename,
  boardCsvRows,
  csvField,
} from "./boardCsv";
import type {
  BoardCategories,
  BoardHeatmap,
  BoardSummary,
} from "@/hooks/useBoard";

const summary: BoardSummary = {
  window_days: 30,
  totals: {
    tasks_completed: 12,
    tasks_failed: 1,
    voice_commands: 40,
    hours_saved: 2.5,
    activity_events: 90,
    conversation_hours: 3.25,
    user_words: 1200,
    jarvis_words: 3400,
    session_count: 8,
    active_days: 5,
    first_day: "2026-01-04",
  },
  window: {
    tasks_completed: 3,
    tasks_failed: 0,
    voice_commands: 10,
    hours_saved: 0.5,
    activity_events: 20,
    conversation_hours: 1.5,
    user_words: 300,
    jarvis_words: 800,
    session_count: 2,
    voice_first_try_rate: null,
    unique_tools: 4,
  },
  streak_days: 3,
  longest_streak: 9,
};

const heatmap: BoardHeatmap = {
  start: "2026-08-21",
  end: "2026-08-22",
  days: 2,
  cells: [
    {
      date: "2026-08-21",
      tasks_completed: 1,
      tasks_failed: 0,
      activity_events: 5,
      conversation_hours: 0.5,
      user_words: 100,
      jarvis_words: 200,
    },
  ],
};

const categories: BoardCategories = {
  window_days: 30,
  total: 3,
  categories: [
    { category: "music", count: 2 },
    { category: 'weather, "local"', count: 1 },
  ],
};

/** Minimal RFC 4180 reader, so the quoting assertions test what a reader sees. */
function parseCsvLine(line: string): string[] {
  const fields: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          field += '"';
          i++;
        } else inQuotes = false;
      } else field += ch;
    } else if (ch === '"') inQuotes = true;
    else if (ch === ",") {
      fields.push(field);
      field = "";
    } else field += ch;
  }
  fields.push(field);
  return fields;
}

describe("csvField", () => {
  it("leaves a plain value alone", () => {
    expect(csvField("music")).toBe("music");
    expect(csvField(42)).toBe("42");
  });

  it("quotes a field containing a comma", () => {
    expect(csvField("weather, local")).toBe('"weather, local"');
  });

  it("doubles inner quotes", () => {
    expect(csvField('say "hi"')).toBe('"say ""hi"""');
  });

  it("quotes newlines and edge whitespace so a reader cannot silently trim", () => {
    expect(csvField("a\nb")).toBe('"a\nb"');
    expect(csvField(" padded ")).toBe('" padded "');
  });

  it("writes null and undefined as an empty field, never the string 'null'", () => {
    expect(csvField(null)).toBe("");
    expect(csvField(undefined)).toBe("");
  });
});

describe("boardCsvRows", () => {
  it("returns nothing when the view holds nothing", () => {
    expect(boardCsvRows({})).toEqual([]);
  });

  it("omits a section the view has not loaded instead of writing zeros", () => {
    const rows = boardCsvRows({ summary });
    expect(rows.some((r) => r[0] === "daily")).toBe(false);
    expect(rows.some((r) => r[0] === "categories")).toBe(false);
  });

  it("puts first_day in the date column, not the value column", () => {
    const row = boardCsvRows({ summary }).find((r) => r[2] === "first_day");
    expect(row).toEqual(["totals", "2026-01-04", "first_day", ""]);
  });

  it("writes a null rate as empty, so it cannot be read as zero", () => {
    const row = boardCsvRows({ summary }).find(
      (r) => r[0] === "window" && r[2] === "voice_first_try_rate",
    );
    expect(row?.[3]).toBe("");
  });

  it("emits one row per metric per day, keyed by the ISO date", () => {
    const rows = boardCsvRows({ heatmap }).filter((r) => r[0] === "daily");
    expect(rows).toHaveLength(6);
    expect(rows.every((r) => r[1] === "2026-08-21")).toBe(true);
  });

  it("keeps the streak values that live outside totals", () => {
    const rows = boardCsvRows({ summary });
    expect(rows).toContainEqual(["totals", "", "streak_days", 3]);
    expect(rows).toContainEqual(["totals", "", "longest_streak", 9]);
  });
});

describe("boardCsv", () => {
  it("starts with the fixed header after the BOM", () => {
    const text = boardCsv({ summary });
    expect(text.charCodeAt(0)).toBe(0xfeff);
    expect(text.slice(1).split("\r\n")[0]).toBe(CSV_HEADER.join(","));
  });

  it("quotes a category name containing a comma so columns do not shift", () => {
    const line = boardCsv({ categories })
      .split("\r\n")
      .find((l) => l.includes("weather"));
    expect(line).toBe('categories,,"weather, ""local""",1');
    // And a reader still sees exactly four fields, with the comma inside one.
    expect(parseCsvLine(line!)).toEqual([
      "categories",
      "",
      'weather, "local"',
      "1",
    ]);
  });

  it("uses CRLF and ends with a terminating newline", () => {
    const text = boardCsv({ summary });
    expect(text.endsWith("\r\n")).toBe(true);
    expect(text.includes("\r\n")).toBe(true);
  });

  it("carries every loaded section in one document", () => {
    const text = boardCsv({ summary, heatmap, categories });
    for (const section of ["totals", "window", "daily", "categories"]) {
      expect(text).toContain(`\r\n${section},`);
    }
  });
});

describe("boardCsvFilename", () => {
  it("names the file with an ISO date so files sort chronologically", () => {
    expect(boardCsvFilename(new Date("2026-08-22T18:04:00Z"))).toBe(
      "jarvis-board-2026-08-22.csv",
    );
  });
});
