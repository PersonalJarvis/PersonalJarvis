import { afterEach, describe, expect, it } from "vitest";
import {
  EDGE, MIN_H, MIN_W, defaultRect, fitRect, loadStoredWindow, moveRect, placeStored, resizeRect, saveStoredWindow, toStored,
  type WindowRect,
} from "./paneWindow";

const IDE = { width: 1795, height: 1030 };
const inside = (rect: WindowRect, stage: { width: number; height: number }) =>
  rect.x >= EDGE && rect.y >= EDGE && rect.x + rect.w <= stage.width - EDGE && rect.y + rect.h <= stage.height - EDGE;

describe("pane window geometry", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("opens as a window, not a strip: about half the stage wide, bottom-right, clear of the HUD", () => {
    const rect = defaultRect(IDE, true);
    expect(rect.w).toBeLessThanOrEqual(860);
    expect(rect.w).toBeGreaterThanOrEqual(460);
    expect(rect.w).toBeLessThan(IDE.width * 0.6);
    expect(rect.h).toBeGreaterThanOrEqual(280);
    expect(rect.h).toBeLessThanOrEqual(480);
    expect(rect.x + rect.w).toBe(IDE.width - EDGE);
    // The compact stage's buttons run along its bottom edge.
    expect(IDE.height - (rect.y + rect.h)).toBe(48);
  });

  it("fits a small stage without leaving it", () => {
    const stage = { width: 420, height: 300 };
    const rect = defaultRect(stage, true);
    expect(inside(rect, stage)).toBe(true);
    expect(rect.w).toBe(stage.width - 2 * EDGE);
  });

  it("stops a move at the stage edges", () => {
    const start = defaultRect(IDE, true);
    const far = moveRect(start, 5000, 5000, IDE);
    expect(inside(far, IDE)).toBe(true);
    expect(far.w).toBe(start.w);
    const back = moveRect(start, -5000, -5000, IDE);
    expect(back.x).toBe(EDGE);
    expect(back.y).toBe(EDGE);
  });

  it("resizes from the top-left corner with the bottom-right corner held", () => {
    const start = defaultRect(IDE, true);
    const grown = resizeRect(start, "nw", -100, -60, IDE);
    expect(grown.w).toBe(start.w + 100);
    expect(grown.h).toBe(start.h + 60);
    expect(grown.x + grown.w).toBe(start.x + start.w);
    expect(grown.y + grown.h).toBe(start.y + start.h);
  });

  it("never resizes below the minimum or past the stage", () => {
    const start = defaultRect(IDE, true);
    const tiny = resizeRect(start, "se", -5000, -5000, IDE);
    expect(tiny.w).toBe(MIN_W);
    expect(tiny.h).toBe(MIN_H);
    const huge = resizeRect(start, "nw", -5000, -5000, IDE);
    expect(inside(huge, IDE)).toBe(true);
    expect(huge.x).toBe(EDGE);
    expect(huge.y).toBe(EDGE);
  });

  it("keeps the window's distance from the bottom-right corner when the stage changes", () => {
    const rect = fitRect({ x: 900, y: 500, w: 600, h: 400 }, IDE);
    const stored = toStored(rect, IDE);
    const wider = { width: IDE.width + 200, height: IDE.height };
    expect(placeStored(stored, wider)).toEqual({ ...rect, x: rect.x + 200 });
    // A stage too small for it shrinks the window rather than pushing it off.
    const narrow = { width: 500, height: 400 };
    expect(inside(placeStored(stored, narrow), narrow)).toBe(true);
  });

  it("remembers the window per stage kind and forgets it on request", () => {
    const stored = { r: 10, b: 50, w: 700, h: 420 };
    saveStoredWindow(true, stored);
    expect(loadStoredWindow(true)).toEqual(stored);
    expect(loadStoredWindow(false)).toBeNull();
    saveStoredWindow(true, null);
    expect(loadStoredWindow(true)).toBeNull();
  });

  it("ignores a stored value that is not a window", () => {
    window.localStorage.setItem("jarvis.office.paneWindow.compact", JSON.stringify({ r: "x", b: 1 }));
    expect(loadStoredWindow(true)).toBeNull();
    window.localStorage.setItem("jarvis.office.paneWindow.compact", "{not json");
    expect(loadStoredWindow(true)).toBeNull();
  });
});
