import { describe, expect, it } from "vitest";
import type { PaneScreen } from "@/lib/paneScreensApi";
import {
  drawTerminalScreen, monitorPalette, paneDot, rowTones, screenChanged, terminalLayout, truncateRow, visibleRows,
  type MonitorFace,
} from "./terminalScreen";

/** A 2D context that records what was drawn. */
function recordingContext() {
  const texts: { text: string; x: number; y: number; fill: string }[] = [];
  const rects: { x: number; y: number; w: number; h: number; fill: string }[] = [];
  const ctx = {
    fillStyle: "", font: "", textAlign: "start", textBaseline: "alphabetic",
    fillRect(x: number, y: number, w: number, h: number) { rects.push({ x, y, w, h, fill: String(this.fillStyle) }); },
    fillText(text: string, x: number, y: number) { texts.push({ text, x, y, fill: String(this.fillStyle) }); },
    beginPath() {}, arc() {}, fill() {}, drawImage() {},
    measureText(text: string) { return { width: text.length * 8 }; },
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, texts, rects };
}

const screen = (over: Partial<PaneScreen> = {}): PaneScreen => ({
  workspace_id: "ws", key: "T1", name: "T1", cols: 80, rows: 30, lines: [], cursor: null, at: 1, ...over,
});

describe("terminal monitor layout", () => {
  it("shows the bottom block ending at the last non-empty row", () => {
    const lines = ["a", "b", "c", "d", "e", "", ""];
    expect(visibleRows(lines, 3, null)).toEqual({ rows: ["c", "d", "e"], first: 2 });
    expect(visibleRows(lines, 10, null)).toEqual({ rows: ["a", "b", "c", "d", "e"], first: 0 });
  });
  it("keeps a cursor row below the text in view", () => {
    expect(visibleRows(["a", "b", "", ""], 2, [3, 0])).toEqual({ rows: ["", ""], first: 2 });
  });
  it("draws nothing but the cursor row for an empty screen", () => {
    expect(visibleRows(["", "  "], 5, null)).toEqual({ rows: [], first: 0 });
  });
  it("fits the font to the terminal's width, within bounds", () => {
    const narrow = terminalLayout(37), wide = terminalLayout(200);
    expect(narrow.font).toBeGreaterThan(wide.font);
    expect(wide.font).toBeGreaterThanOrEqual(12);
    expect(narrow.font).toBeLessThanOrEqual(40);
    expect(wide.fit).toBeGreaterThan(narrow.fit);
    // A narrow TUI fills the monitor instead of hugging its left edge.
    expect(narrow.cols).toBeLessThan(60);
  });
  it("tones the TUI's rules and the status bar under the composer", () => {
    const rows = ["answer", "────────────", "> prompt", "────────────", "status bar", "mode"];
    expect(rowTones(rows)).toEqual(["text", "rule", "text", "rule", "status", "status"]);
    // A separator high up in the output never mutes what follows it.
    expect(rowTones(["────────────", "a", "b", "c"])).toEqual(["rule", "text", "text", "text"]);
  });
  it("cuts long rows with an ellipsis", () => {
    expect(truncateRow("abcdef", 4)).toBe("abc…");
    expect(truncateRow("abc", 4)).toBe("abc");
  });
});

describe("terminal monitor state", () => {
  it("reads the title dot like the grid badge", () => {
    expect(paneDot({ status: "live", activity: "working" })).toBe("working");
    expect(paneDot({ status: "pending", activity: "" })).toBe("working");
    expect(paneDot({ status: "live", activity: "asking" })).toBe("asking");
    expect(paneDot({ status: "error", activity: "working" })).toBe("error");
    expect(paneDot({ status: "live", activity: "failed" })).toBe("error");
    expect(paneDot({ status: "live", activity: "waiting" })).toBe("idle");
    expect(paneDot({ status: "exited", activity: "exited" })).toBe("idle");
  });
  it("redraws only when the feed stamp or cursor moves", () => {
    const a = screen({ at: 1, cursor: [1, 2] });
    expect(screenChanged(undefined, a)).toBe(true);
    expect(screenChanged(a, undefined)).toBe(true);
    expect(screenChanged(undefined, undefined)).toBe(false);
    expect(screenChanged(a, { ...a, lines: ["new array, same stamp"] })).toBe(false);
    expect(screenChanged(a, { ...a, at: 2 })).toBe(true);
    expect(screenChanged(a, { ...a, cursor: [1, 3] })).toBe(true);
    expect(screenChanged(a, { ...a, cursor: null })).toBe(true);
  });
});

const face = (over: Partial<MonitorFace> = {}): MonitorFace => ({
  title: "Refactor auth", meta: "Working · 3 min", dot: "working", mark: null, palette: monitorPalette("dark"), ...over,
});

describe("terminal monitor drawing", () => {
  it("draws the IDE header, the last rows and a cursor block", () => {
    const { ctx, texts, rects } = recordingContext();
    const lines = Array.from({ length: 40 }, (_, i) => `line ${i}`);
    drawTerminalScreen(ctx, face(), screen({ lines, cursor: [39, 7] }));
    const drawn = texts.map((t) => t.text);
    expect(drawn).toContain("Refactor auth");
    expect(drawn).toContain("Working · 3 min");
    // No mark loaded yet: the terminal glyph stands in.
    expect(drawn).toContain(">_");
    const body = texts.filter((t) => t.text.startsWith("line ")).map((t) => t.text);
    expect(body.at(-1)).toBe("line 39");
    expect(body).not.toContain("line 0");
    const cursor = rects.at(-1)!;
    expect(cursor.fill).toBe(monitorPalette("dark").cursor);
    expect(cursor.y).toBeGreaterThan(texts.filter((t) => t.text.startsWith("line ")).at(-2)!.y);
  });
  it("draws the text in the pane's own ink for either appearance", () => {
    for (const appearance of ["light", "dark"] as const) {
      const { ctx, texts } = recordingContext();
      drawTerminalScreen(ctx, face({ palette: monitorPalette(appearance) }), screen({ lines: ["hello"] }));
      expect(texts.find((t) => t.text === "hello")?.fill).toBe(monitorPalette(appearance).text);
    }
    expect(monitorPalette("light").ground).not.toBe(monitorPalette("dark").ground);
  });
  it("truncates rows wider than the monitor", () => {
    const { ctx, texts } = recordingContext();
    drawTerminalScreen(ctx, face(), screen({ cols: 240, lines: ["y".repeat(240)] }));
    const row = texts.find((t) => t.text.startsWith("yyy"))!;
    expect(row.text.endsWith("…")).toBe(true);
    expect(row.text.length).toBe(terminalLayout(240).cols);
  });
  it("keeps the header over a quiet screen when no screen has arrived", () => {
    const { ctx, texts } = recordingContext();
    drawTerminalScreen(ctx, face({ title: "Quiet pane", dot: "idle" }), undefined);
    expect(texts.map((t) => t.text)).toContain("Quiet pane");
    expect(texts.at(-1)?.text).toBe(">_");
    expect(ctx.textAlign).toBe("start");
  });
});
