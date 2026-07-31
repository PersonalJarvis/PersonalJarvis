/**
 * Guards the terminal grid against the font it is actually drawn with.
 *
 * The bug these pin (browser panes, 2026-07-28): xterm measured the fallback
 * font, built a 7px cell grid from it, and then drew JetBrains Mono's 7.80px
 * glyphs into that grid — text smeared a full column out of place within nine
 * characters, and each pane told its pty ~8 columns more than it had.
 *
 * The two traps have their own tests below, because both LOOK fixed while
 * doing nothing: waiting on `document.fonts.ready` (which resolves before a
 * never-yet-rendered font is even requested) and re-fitting (which re-reads the
 * cached, wrong cell width instead of measuring again).
 */
import { describe, it, expect, vi } from "vitest";
import {
  TERMINAL_FONT_STACK,
  alignTerminalCells,
  syncTerminalFont,
} from "./terminalFont";

/** A stand-in for the xterm options this module is allowed to touch. */
function fakeTerm(fontSize = 13) {
  const options: {
    fontFamily: string;
    fontSize: number;
    letterSpacing?: number;
  } = { fontFamily: TERMINAL_FONT_STACK, fontSize };
  return { options };
}

/**
 * A FontFaceSet whose `load()` and `ready` resolve only when the test says so —
 * the whole point is what happens BETWEEN mount and the font arriving.
 */
function fakeFonts() {
  const listeners = new Set<() => void>();
  let releaseLoad: () => void = () => {};
  const loaded = new Promise<void>((resolve) => {
    releaseLoad = resolve;
  });
  return {
    set: {
      load: vi.fn(() => loaded),
      ready: loaded,
      addEventListener: (type: string, fn: () => void) => {
        if (type === "loadingdone") listeners.add(fn);
      },
      removeEventListener: (_type: string, fn: () => void) => {
        listeners.delete(fn);
      },
    } as unknown as FontFaceSet,
    /** The display font finished downloading. */
    arrive: async () => {
      releaseLoad();
      for (const fn of listeners) fn();
      await Promise.resolve();
      await Promise.resolve();
    },
    listenerCount: () => listeners.size,
  };
}

/** Consolas and JetBrains Mono at 13px, as measured in Chrome on the live app. */
const FALLBACK_ADVANCE = 7.147;
const DISPLAY_ADVANCE = 7.8;

describe("alignTerminalCells", () => {
  it("gives back the fraction of a pixel the canvas renderer floors away", () => {
    const term = fakeTerm();

    // JetBrains Mono at 13px advances 7.80px; the canvas renderer floors that to
    // a 7px cell and then draws the full 7.80px glyph into it, so every
    // character overhangs its neighbour by 0.8px. That is the smear.
    const changed = alignTerminalCells(term, {
      measureAdvance: () => DISPLAY_ADVANCE,
      devicePixelRatio: 1,
    });

    expect(changed).toBe(true);
    expect(term.options.letterSpacing).toBe(1);
  });

  it("leaves a font whose advance is already a whole pixel alone", () => {
    const term = fakeTerm();

    const changed = alignTerminalCells(term, {
      measureAdvance: () => 8,
      devicePixelRatio: 1,
    });

    // Nothing is floored away, so spacing would only push the glyphs apart.
    expect(changed).toBe(false);
    expect(term.options.letterSpacing ?? 0).toBe(0);
  });

  it("measures the overhang in DEVICE pixels, not css pixels", () => {
    // At dpr 2 a 7.5px advance is 15 device px — a whole number, nothing floored.
    const retina = fakeTerm();
    expect(
      alignTerminalCells(retina, {
        measureAdvance: () => 7.5,
        devicePixelRatio: 2,
      }),
    ).toBe(false);

    // The same advance at dpr 1 does lose a half pixel per cell.
    const standard = fakeTerm();
    expect(
      alignTerminalCells(standard, {
        measureAdvance: () => 7.5,
        devicePixelRatio: 1,
      }),
    ).toBe(true);
  });

  it("reports no change on a second call, so callers do not re-fit forever", () => {
    const term = fakeTerm();
    const deps = { measureAdvance: () => DISPLAY_ADVANCE, devicePixelRatio: 1 };

    expect(alignTerminalCells(term, deps)).toBe(true);
    expect(alignTerminalCells(term, deps)).toBe(false);
  });

  it("survives a terminal that exposes no options at all", () => {
    // This runs inside a mount effect: a throw here does not misalign a grid,
    // it takes the whole pane down. Stubs and older engines both hit this.
    const stub = {} as unknown as Parameters<typeof alignTerminalCells>[0];

    expect(() =>
      alignTerminalCells(stub, {
        measureAdvance: () => DISPLAY_ADVANCE,
        devicePixelRatio: 1,
      }),
    ).not.toThrow();
    expect(() =>
      syncTerminalFont(stub, vi.fn(), {
        fonts: fakeFonts().set,
        measureAdvance: () => DISPLAY_ADVANCE,
        devicePixelRatio: 1,
      })(),
    ).not.toThrow();
  });

  it("does not touch a terminal it cannot measure", () => {
    const term = fakeTerm();

    const changed = alignTerminalCells(term, {
      measureAdvance: () => null,
      devicePixelRatio: 1,
    });

    expect(changed).toBe(false);
    expect(term.options.letterSpacing).toBeUndefined();
  });
});

describe("syncTerminalFont", () => {
  it("aligns the grid at mount, before any font arrives", () => {
    const term = fakeTerm();
    const onChanged = vi.fn();

    const dispose = syncTerminalFont(term, onChanged, {
      fonts: fakeFonts().set,
      measureAdvance: () => DISPLAY_ADVANCE,
      devicePixelRatio: 1,
    });

    // The overhang does not wait for a late font: a cell floored at mount is
    // already drawing glyphs over their neighbours on the first frame.
    expect(term.options.letterSpacing).toBe(1);
    expect(onChanged).toHaveBeenCalledTimes(1);

    dispose();
  });

  it("re-aligns the grid to the font that actually arrived", async () => {
    const term = fakeTerm();
    const fonts = fakeFonts();
    // A fallback that happens to land on a whole pixel needs no spacing...
    let advance = 7;
    const onChanged = vi.fn();

    const dispose = syncTerminalFont(term, onChanged, {
      fonts: fonts.set,
      measureAdvance: () => advance,
      devicePixelRatio: 1,
    });
    expect(term.options.letterSpacing ?? 0).toBe(0);

    // ...but the display font that replaces it does.
    advance = DISPLAY_ADVANCE;
    await fonts.arrive();

    expect(term.options.letterSpacing).toBe(1);
    expect(onChanged).toHaveBeenCalled();

    dispose();
  });

  it("re-measures and reports once the display font replaces the fallback", async () => {
    const term = fakeTerm();
    const fonts = fakeFonts();
    let advance = FALLBACK_ADVANCE;
    const onRemeasured = vi.fn();

    const dispose = syncTerminalFont(term, onRemeasured, {
      fonts: fonts.set,
      measureAdvance: () => advance,
      devicePixelRatio: 1,
    });

    // The mount-time alignment may already have reported once (7.147 overhangs
    // its floored cell too); what this test is about is the SECOND report, the
    // one the font swap owes.
    const beforeSwap = onRemeasured.mock.calls.length;

    const stackBefore = term.options.fontFamily;
    advance = DISPLAY_ADVANCE;
    await fonts.arrive();

    expect(onRemeasured.mock.calls.length).toBe(beforeSwap + 1);
    // xterm re-measures ONLY on a fontFamily/fontSize change and ignores a
    // write of the identical string, so a sync that left this untouched would
    // have reported a change xterm never acted on.
    expect(term.options.fontFamily).not.toBe(stackBefore);
    // ...and it must still resolve to the same font.
    expect(term.options.fontFamily).toContain("JetBrains Mono");

    dispose();
  });

  it("stays quiet when the measurement never changes (offline, no web font)", async () => {
    const term = fakeTerm();
    const fonts = fakeFonts();
    const onRemeasured = vi.fn();

    // An advance that lands on a whole device pixel, so the mount-time
    // alignment has nothing to give back and this test sees the font path alone.
    const dispose = syncTerminalFont(term, onRemeasured, {
      fonts: fonts.set,
      measureAdvance: () => 7,
      devicePixelRatio: 1,
    });
    await fonts.arrive();

    // The fallback IS the font being drawn: the grid was right all along.
    expect(onRemeasured).not.toHaveBeenCalled();
    expect(term.options.fontFamily).toBe(TERMINAL_FONT_STACK);

    dispose();
  });

  it("asks for the display font BY NAME rather than trusting fonts.ready", () => {
    const term = fakeTerm();
    const fonts = fakeFonts();

    const dispose = syncTerminalFont(term, vi.fn(), {
      fonts: fonts.set,
      measureAdvance: () => FALLBACK_ADVANCE,
    });

    // `fonts.ready` resolves as soon as nothing is PENDING, and a font nothing
    // has rendered yet was never requested — so it reports "ready" while the
    // terminal font is still absent. Only an explicit request starts it.
    const asked = (fonts.set.load as unknown as ReturnType<typeof vi.fn>).mock
      .calls;
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every((c) => String(c[0]).includes("JetBrains Mono"))).toBe(
      true,
    );

    dispose();
  });

  it("keeps watching after a late arrival, and stops on dispose", async () => {
    const term = fakeTerm();
    const fonts = fakeFonts();
    // Starts on a whole pixel so the mount-time alignment is silent and every
    // count below belongs to a font change.
    let advance = 7;
    const onRemeasured = vi.fn();

    const dispose = syncTerminalFont(term, onRemeasured, {
      fonts: fonts.set,
      measureAdvance: () => advance,
      devicePixelRatio: 1,
    });

    advance = DISPLAY_ADVANCE;
    await fonts.arrive();
    expect(onRemeasured).toHaveBeenCalledTimes(1);

    // A second genuine change (a font swapped in later) is still caught: the
    // sync must not burn itself out on the first signal, which is precisely how
    // the previous implementation lost its one chance to fix the grid.
    advance = 9.1;
    await fonts.arrive();
    expect(onRemeasured).toHaveBeenCalledTimes(2);

    dispose();
    expect(fonts.listenerCount()).toBe(0);
    advance = 12;
    await fonts.arrive();
    expect(onRemeasured).toHaveBeenCalledTimes(2);
  });

  it("is a no-op where nothing can be measured or no font API exists", async () => {
    const onRemeasured = vi.fn();

    // Headless/jsdom: no 2D context, so no advance can be measured. Reporting a
    // change here would reflow every pane on a number that means nothing.
    const unmeasurable = syncTerminalFont(fakeTerm(), onRemeasured, {
      fonts: fakeFonts().set,
      measureAdvance: () => null,
    });
    unmeasurable();

    // An engine without FontFaceSet: what was measured at mount is the font, and
    // on a whole-pixel advance there is nothing to align either.
    const noApi = syncTerminalFont(fakeTerm(), onRemeasured, {
      fonts: null,
      measureAdvance: () => 7,
      devicePixelRatio: 1,
    });
    noApi();

    await Promise.resolve();
    expect(onRemeasured).not.toHaveBeenCalled();
  });
});
