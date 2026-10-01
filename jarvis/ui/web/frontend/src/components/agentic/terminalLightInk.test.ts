import { describe, expect, it } from "vitest";

import {
  createLightPaneInk,
  inkForLightPane,
  lightGroundFor,
  lightInkFor,
  rewriteLightInkSgr,
} from "./terminalLightInk";

function lum([r, g, b]: readonly number[]): number {
  const ch = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}

function contrastOnPaper(rgb: readonly number[]): number {
  const paper = lum([252, 251, 248]);
  return (paper + 0.05) / (lum(rgb) + 0.05);
}

describe("lightInkFor", () => {
  it("turns dark-theme white body text into near-black ink", () => {
    const ink = lightInkFor([255, 255, 255]);
    expect(contrastOnPaper(ink)).toBeGreaterThan(12);
  });

  it("keeps the CLI's hierarchy: a dim hint stays lighter than body text", () => {
    const body = lightInkFor([255, 255, 255]);
    const hint = lightInkFor([153, 153, 153]);
    expect(lum(hint)).toBeGreaterThan(lum(body));
    expect(contrastOnPaper(hint)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps a pale link's hue but makes it readable", () => {
    const link = lightInkFor([177, 185, 249]);
    expect(link[2]).toBeGreaterThan(link[0]);
    expect(contrastOnPaper(link)).toBeGreaterThanOrEqual(4.5);
  });

  it("leaves ink that already reads on paper alone", () => {
    expect(lightInkFor([43, 43, 51])).toEqual([43, 43, 51]);
  });
});

describe("lightGroundFor", () => {
  it("turns Claude Code's dark prompt bar into a pale grey bar", () => {
    const bar = lightGroundFor([55, 55, 55]);
    expect(lum(bar)).toBeGreaterThan(0.75);
    expect(bar[0]).toBe(bar[2]);
  });

  it("keeps a dark diff row's hue as a pale tint", () => {
    const [r, g, b] = lightGroundFor([34, 92, 43]);
    expect(g).toBeGreaterThan(r);
    expect(g).toBeGreaterThan(b);
    expect(lum([r, g, b])).toBeGreaterThan(0.6);
  });

  it("leaves a pale ground alone", () => {
    expect(lightGroundFor([240, 240, 240])).toEqual([240, 240, 240]);
  });
});

describe("rewriteLightInkSgr", () => {
  it("rewrites truecolor foreground and background in one list", () => {
    const out = rewriteLightInkSgr("1;38;2;255;255;255;48;2;55;55;55");
    expect(out.startsWith("1;38;2;")).toBe(true);
    expect(out).not.toContain("255;255;255");
    expect(out).not.toContain("55;55;55");
  });

  it("maps 256-colour greys but leaves the 16 palette slots to the theme", () => {
    expect(rewriteLightInkSgr("38;5;255")).toMatch(/^38;2;\d+;\d+;\d+$/);
    expect(rewriteLightInkSgr("38;5;7")).toBe("38;5;7");
    expect(rewriteLightInkSgr("31")).toBe("31");
  });

  it("handles the colon form", () => {
    expect(rewriteLightInkSgr("48:2::30:30:30")).toMatch(/^48;2;\d+;\d+;\d+$/);
  });

  it("never reads an underline colour's channels as a setter", () => {
    expect(rewriteLightInkSgr("58;2;48;2;1")).toBe("58;2;48;2;1");
  });
});

describe("inkForLightPane", () => {
  it("passes text without colour setters through untouched", () => {
    const text = "plain \x1b[1mbold\x1b[0m \x1b[31mred\x1b[0m";
    expect(inkForLightPane(text)).toBe(text);
  });

  it("rewrites only complete SGR sequences", () => {
    const out = inkForLightPane("\x1b[38;2;255;255;255mhi\x1b[0m");
    expect(out).toMatch(/^\x1b\[38;2;\d+;\d+;\d+mhi\x1b\[0m$/);
    expect(out).not.toContain("255;255;255");
  });
});

describe("faint text on a light pane", () => {
  it("draws faint default-colour text in a readable grey instead of half opacity", () => {
    const ink = createLightPaneInk();
    expect(ink("\x1b[2mv0.159.3\x1b[22m done")).toBe(
      "\x1b[38;2;110;110;114mv0.159.3\x1b[22;39m done",
    );
  });

  it("takes the grey back on a full reset", () => {
    const ink = createLightPaneInk();
    expect(ink("\x1b[2mhint\x1b[0m")).toBe("\x1b[38;2;110;110;114mhint\x1b[0m");
  });

  it("keeps the state across chunks", () => {
    const ink = createLightPaneInk();
    expect(ink("\x1b[2mhi")).toBe("\x1b[38;2;110;110;114mhi");
    expect(ink("nt\x1b[22m")).toBe("nt\x1b[22;39m");
  });

  it("leaves a CLI's own colour alone while faint is on", () => {
    const ink = createLightPaneInk();
    expect(ink("\x1b[2;31mred\x1b[22;39m")).toBe("\x1b[31mred\x1b[22;39m");
  });

  it("re-applies the grey when the CLI drops its colour but stays faint", () => {
    const ink = createLightPaneInk();
    ink("\x1b[2;31mred");
    expect(ink("\x1b[39mgrey")).toBe("\x1b[39;38;2;110;110;114mgrey");
  });

  it("drops a repeated faint switch instead of emitting a reset", () => {
    const ink = createLightPaneInk();
    ink("\x1b[2ma");
    expect(ink("\x1b[2mb")).toBe("b");
  });

  it("passes plain resets through", () => {
    const ink = createLightPaneInk();
    expect(ink("\x1b[mx\x1b[1my")).toBe("\x1b[mx\x1b[1my");
  });
});
