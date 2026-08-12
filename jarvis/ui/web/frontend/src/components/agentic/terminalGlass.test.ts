import { describe, expect, it } from "vitest";
import { clearTuiCanvasFill, rewriteCanvasFillSgr } from "./terminalGlass";

describe("clearTuiCanvasFill", () => {
  it("turns GrokNight's canvas fill into the terminal default background", () => {
    expect(clearTuiCanvasFill("\x1b[48;2;20;20;20m   \x1b[0m")).toBe(
      "\x1b[49m   \x1b[0m",
    );
  });

  it("keeps a combined SGR's other attributes when the fill is the canvas", () => {
    expect(
      clearTuiCanvasFill("\x1b[0;48;2;20;20;20;38;2;225;225;225mhi"),
    ).toBe("\x1b[0;49;38;2;225;225;225mhi");
  });

  it("does not rewrite a foreground that happens to be the canvas RGB", () => {
    const text = "\x1b[38;2;20;20;20mdim\x1b[0m";
    expect(clearTuiCanvasFill(text)).toBe(text);
  });

  it("leaves a highlight / prompt-box background painted", () => {
    // GrokNight bg_light is #242424 — chrome, not the canvas.
    const text = "\x1b[48;2;36;36;36mselected\x1b[0m";
    expect(clearTuiCanvasFill(text)).toBe(text);
  });

  it("clears every bundled Grok theme's bg_base, and the pane's own ground", () => {
    const fills: [number, number, number][] = [
      [20, 20, 20],
      [238, 238, 238],
      [36, 40, 59],
      [35, 33, 54],
      [3, 3, 4],
      [18, 20, 26],
      [252, 251, 248],
    ];
    for (const [r, g, b] of fills) {
      expect(clearTuiCanvasFill(`\x1b[48;2;${r};${g};${b}m`)).toBe("\x1b[49m");
    }
  });

  it("understands the colon-form RGB background", () => {
    expect(clearTuiCanvasFill("\x1b[48:2::20:20:20m")).toBe("\x1b[49m");
    expect(clearTuiCanvasFill("\x1b[48:2:0:20:20:20m")).toBe("\x1b[49m");
    expect(clearTuiCanvasFill("\x1b[48:2:20:20:20m")).toBe("\x1b[49m");
  });

  it("leaves a chunk with no SGR background alone, including empty text", () => {
    expect(clearTuiCanvasFill("hello")).toBe("hello");
    expect(clearTuiCanvasFill("")).toBe("");
    expect(clearTuiCanvasFill("\x1b[31mred\x1b[0m")).toBe("\x1b[31mred\x1b[0m");
  });

  it("does not invent a match from a split CSI", () => {
    // The closing `m` is in the next read. Rewriting here would corrupt both.
    expect(clearTuiCanvasFill("\x1b[48;2;20;20;20")).toBe("\x1b[48;2;20;20;20");
  });

  it("rewrites a canvas fill that follows a truecolor foreground", () => {
    expect(
      clearTuiCanvasFill("\x1b[38;2;225;225;225;48;2;20;20;20m"),
    ).toBe("\x1b[38;2;225;225;225;49m");
  });

  it("does not treat a colour-channel 48 as a background setter", () => {
    // Indexed fg 48, then a stray 2 — not `48;2;r;g;b`.
    const text = "\x1b[38;5;48;2m";
    expect(clearTuiCanvasFill(text)).toBe(text);
  });

  it("leaves a colon-form highlight background painted", () => {
    const text = "\x1b[48:2:36:36:36m";
    expect(clearTuiCanvasFill(text)).toBe(text);
  });
});

describe("rewriteCanvasFillSgr", () => {
  it("is a no-op when 48 is not a background setter", () => {
    expect(rewriteCanvasFillSgr("1;4")).toBe("1;4");
    expect(rewriteCanvasFillSgr("38;2;20;20;20")).toBe("38;2;20;20;20");
  });
});
