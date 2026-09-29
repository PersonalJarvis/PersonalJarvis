import { describe, expect, it } from "vitest";
import type { PaneScreen } from "@/lib/paneScreensApi";
import {
  drawTerminalScreen, paneDot, screenChanged, terminalLayout, truncateRow, visibleRows,
} from "./terminalScreen";

/** A 2D context that records what was drawn. */
function recordingContext() {
  const texts: { text: string; x: number; y: number; fill: string }[] = [];
  const rects: { x: number; y: number; w: number; h: number; fill: string }[] = [];
  const ctx = {
    fillStyle: "", font: "", textAlign: "start", textBaseline: "alphabetic",
    fillRect(x: number, y: number, w: number, h: number) { rects.push({ x, y, w, h, fill: String(this.fillStyle) }); },
    fillText(text: string, x: number, y: number) { texts.push({ text, x, y, fill: String(this.fillStyle) }); },
    beginPath() {}, arc() {}, fill() {},
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
  it("shrinks the font for wide terminals within bounds", () => {
    const narrow = terminalLayout(40), wide = terminalLayout(200);
    expect(narrow.font).toBeGreaterThan(wide.font);
    expect(wide.font).toBeGreaterThanOrEqual(9);
    expect(narrow.font).toBeLessThanOrEqual(15);
    expect(wide.fit).toBeGreaterThan(narrow.fit);
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

describe("terminal monitor drawing", () => {
  it("draws the title, the last rows and a cursor block", () => {
    const { ctx, texts, rects } = recordingContext();
    const lines = Array.from({ length: 40 }, (_, i) => `line ${i}`);
    drawTerminalScreen(ctx, { name: "Refactor auth", dot: "working" }, screen({ lines, cursor: [39, 7] }));
    expect(texts[0].text).toBe("Refactor auth");
    const body = texts.slice(1).map((t) => t.text);
    expect(body.at(-1)).toBe("line 39");
    expect(body).not.toContain("line 0");
    // Background, title bar, cursor block.
    expect(rects).toHaveLength(3);
    const cursor = rects[2];
    expect(cursor.y).toBeGreaterThan(texts.at(-2)!.y);
  });
  it("truncates rows wider than the monitor", () => {
    const { ctx, texts } = recordingContext();
    drawTerminalScreen(ctx, { name: "x", dot: "idle" }, screen({ cols: 240, lines: ["y".repeat(240)] }));
    expect(texts[1].text.endsWith("…")).toBe(true);
    expect(texts[1].text.length).toBe(terminalLayout(240).cols);
  });
  it("shows a dim screensaver when no screen has arrived", () => {
    const { ctx, texts, rects } = recordingContext();
    drawTerminalScreen(ctx, { name: "Quiet pane", dot: "idle" }, undefined);
    expect(rects).toHaveLength(1);
    expect(texts.map((t) => t.text)).toEqual(["Quiet pane", ">_"]);
    expect(ctx.textAlign).toBe("start");
  });
});
