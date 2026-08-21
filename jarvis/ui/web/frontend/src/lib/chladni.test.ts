import { describe, expect, it } from "vitest";

import {
  createField,
  driveFor,
  excitedModes,
  fillField,
  grainCountFor,
  grainThrow,
  modeEigenvalue,
  modeShape,
  plateModes,
  reflect,
  resonance,
  resonancePhase,
  sampleField,
  THROW_THRESHOLD,
  type ExcitedMode,
} from "@/lib/chladni";

/** One mode at full strength, for checking the field on its own terms. */
function only(n: number, m: number): ExcitedMode[] {
  return [{ mode: { n, m, lambda: modeEigenvalue(n, m, 1, 1) }, weight: 1 }];
}

/** grainThrow writes into a caller's buffer to keep the hot loop allocation
    free; the tests want the pair back, so they unpack it here. */
const OUT = new Float32Array(2);
function thrown(
  w: number,
  gx: number,
  gy: number,
  step: number,
  rndX: number,
  rndY: number,
): { dx: number; dy: number; moved: boolean } {
  const moved = grainThrow(w, gx, gy, step, rndX, rndY, OUT);
  return { dx: OUT[0], dy: OUT[1], moved };
}

describe("the plate's modes", () => {
  it("is symmetric only while it is square — which is why the square shortcut breaks", () => {
    // On a square plate (n,m) and (m,n) ring at the same frequency, and that
    // degeneracy is the ONLY reason the classic sin·sin − sin·sin figure is a
    // solution at all. Make the plate 16:9 and the pair splits apart, so that
    // formula stops describing anything on a widescreen window.
    expect(modeEigenvalue(3, 2, 1, 1)).toBeCloseTo(modeEigenvalue(2, 3, 1, 1), 12);
    const a = 16 / 9;
    expect(modeEigenvalue(3, 2, a, 1)).not.toBeCloseTo(modeEigenvalue(2, 3, a, 1), 3);
  });

  it("lists every mode, cheapest first", () => {
    const modes = plateModes(16 / 9, 1, 6);
    expect(modes).toHaveLength(36);
    for (let i = 1; i < modes.length; i++) {
      expect(modes[i].lambda).toBeGreaterThanOrEqual(modes[i - 1].lambda);
    }
  });
});

describe("how a mode answers the drive", () => {
  it("answers fully on resonance and falls away off it", () => {
    expect(resonance(40, 40, 20)).toBeCloseTo(1, 12);
    // Two bandwidths off — Q·detune = 2 — is R = 1/(1 + 2²) = 0.2 exactly.
    expect(resonance(44, 40, 20)).toBeCloseTo(0.2, 9);
    // A drive at twice the mode's frequency barely reaches it: 1/401.
    expect(resonance(80, 40, 20)).toBeCloseTo(1 / 401, 9);
  });

  it("is half as strong exactly one bandwidth off, so Q really is the width", () => {
    // R = 1/(1 + Q²·detune²) is 1/2 when Q·detune = 1.
    const q = 25;
    const f = 60;
    expect(resonance(f * (1 + 1 / q), f, q)).toBeCloseTo(0.5, 6);
  });

  it("gets narrower as the plate gets less damped", () => {
    const loose = resonance(45, 40, 8);
    const tight = resonance(45, 40, 40);
    expect(loose).toBeGreaterThan(tight);
  });

  it("flips phase through resonance", () => {
    // Below its own frequency a mode pushes with the driver, above it pushes
    // against. Without the flip neighbouring modes only ever add up, and the
    // figure fills in instead of cancelling into lines.
    expect(resonancePhase(30, 40)).toBe(1);
    expect(resonancePhase(50, 40)).toBe(-1);
  });

  it("never answers a drive of nothing", () => {
    expect(resonance(40, 0, 20)).toBe(0);
  });
});

describe("where the plate is struck", () => {
  it("leaves every even mode silent when struck dead centre", () => {
    // The centre is a node for every even mode — sin(nπ/2) = 0 — so hitting
    // it cannot drive them, however close they sit to the drive frequency.
    // This is the check that coupling is real and not decoration.
    const modes = plateModes(1, 1, 8);
    const excited = excitedModes(modes, 25, 6, 0.5, 0.5, 40);
    expect(excited.length).toBeGreaterThan(0);
    for (const e of excited) {
      expect(e.mode.n % 2).toBe(1);
      expect(e.mode.m % 2).toBe(1);
    }
  });

  it("wakes modes the centre cannot, once the stick moves off it", () => {
    const modes = plateModes(1, 1, 8);
    const centre = excitedModes(modes, 25, 6, 0.5, 0.5, 40);
    const offset = excitedModes(modes, 25, 6, 0.31, 0.42, 40);
    const evenSomewhere = offset.some((e) => e.mode.n % 2 === 0 || e.mode.m % 2 === 0);
    expect(evenSomewhere).toBe(true);
    expect(offset.length).toBeGreaterThan(centre.length);
  });

  it("hands back a normalised set, so the plate's shape never runs away", () => {
    const modes = plateModes(16 / 9, 1, 10);
    for (const f of [12, 40, 95]) {
      const excited = excitedModes(modes, f, 14, 0.37, 0.44, 8);
      const total = excited.reduce((s, e) => s + Math.abs(e.weight), 0);
      expect(total).toBeCloseTo(1, 6);
      expect(excited.length).toBeLessThanOrEqual(8);
    }
  });
});

describe("the field on the grid", () => {
  it("stands still on a single mode's node lines", () => {
    const field = createField(101, 101, 4);
    fillField(field, only(2, 1));
    // w = sin(2πx)·sin(πy): x = 0.5 is a node line for every y.
    for (const y of [0.2, 0.5, 0.8]) {
      expect(Math.abs(sampleField(field.w, 101, 101, 0.5, y))).toBeLessThan(1e-5);
    }
    // And it is emphatically not standing still between them.
    expect(Math.abs(sampleField(field.w, 101, 101, 0.25, 0.5))).toBeGreaterThan(0.9);
  });

  it("scales the shape to a peak of one, whatever it is driven at", () => {
    for (const [n, m] of [
      [1, 1],
      [3, 2],
      [5, 4],
    ]) {
      const field = createField(101, 101, 4);
      fillField(field, only(n, m));
      let peak = 0;
      for (const v of field.w) peak = Math.max(peak, Math.abs(v));
      expect(peak).toBeCloseTo(1, 4);
    }
  });

  it("slopes away from the node lines, which is the way the sand comes back", () => {
    const field = createField(101, 101, 4);
    fillField(field, only(2, 1));
    // Just left of the node at x = 0.5, |w| is falling as x grows, so the
    // downhill direction — where a thrown grain lands — points at the node.
    const gx = sampleField(field.gx, 101, 101, 0.42, 0.5);
    expect(gx).toBeLessThan(0);
    // Mirrored on the other side.
    expect(sampleField(field.gx, 101, 101, 0.58, 0.5)).toBeGreaterThan(0);
  });

  it("reads a grid point back exactly, and interpolates between them", () => {
    const field = createField(11, 11, 2);
    fillField(field, only(1, 1));
    const direct = field.w[5 * 11 + 5];
    expect(sampleField(field.w, 11, 11, 0.5, 0.5)).toBeCloseTo(direct, 6);
    const between = sampleField(field.w, 11, 11, 0.55, 0.5);
    expect(between).toBeLessThan(direct);
    expect(between).toBeGreaterThan(0);
  });
});

describe("the sand", () => {
  it("is never moved where the plate stands still", () => {
    // The reason a node line is a LINE and not a smear: a grain sitting on
    // one is never thrown, so it stays exactly where it is, forever.
    const still = thrown(THROW_THRESHOLD * 0.5, -1, -1, 0.02, 0.9, 0.1);
    expect(still.moved).toBe(false);
    expect(still.dx).toBe(0);
    expect(still.dy).toBe(0);
  });

  it("is thrown down the slope, toward the quiet", () => {
    // Randoms at 0.5 cancel the scatter, leaving the drift alone — the whole
    // reason the randoms are arguments and not calls to Math.random.
    const { dx, dy, moved } = thrown(0.9, 4, 0, 0.02, 0.5, 0.5);
    expect(moved).toBe(true);
    expect(dx).toBeLessThan(0); // slope rises to the right → grain goes left
    expect(dy).toBeCloseTo(0, 12);
  });

  it("is thrown further the harder the plate moves", () => {
    const gentle = thrown(THROW_THRESHOLD + 0.02, 1, 0, 0.02, 0.5, 0.5).dx;
    const violent = thrown(1, 1, 0, 0.02, 0.5, 0.5).dx;
    expect(Math.abs(violent)).toBeGreaterThan(Math.abs(gentle));
  });

  it("does not care which way the plate bends, only how hard", () => {
    const up = thrown(0.8, 3, 0, 0.02, 0.5, 0.5).dx;
    const down = thrown(-0.8, 3, 0, 0.02, 0.5, 0.5).dx;
    expect(down).toBeCloseTo(up, 12);
  });

  it("scatters when the randoms say so", () => {
    const straight = thrown(0.9, 0, 0, 0.02, 0.5, 0.5);
    const scattered = thrown(0.9, 0, 0, 0.02, 0.95, 0.05);
    expect(straight.dx).toBeCloseTo(0, 12);
    expect(scattered.dx).toBeGreaterThan(0);
    expect(scattered.dy).toBeLessThan(0);
  });

  it("walks a grain that left the plate back onto it", () => {
    expect(reflect(-0.2)).toBeCloseTo(0.2, 12);
    expect(reflect(1.3)).toBeCloseTo(0.7, 12);
    for (const v of [-0.9, -0.01, 0, 0.5, 1, 1.05, 1.9]) {
      const back = reflect(v);
      expect(back).toBeGreaterThanOrEqual(0);
      expect(back).toBeLessThanOrEqual(1);
    }
  });

  it("settles onto the node line when the simulation is actually run", () => {
    // The end-to-end claim: start with sand everywhere, run the real loop,
    // and the sand should be sitting on x = 0.5 and nowhere else.
    const field = createField(101, 101, 4);
    fillField(field, only(2, 1));
    const N = 400;
    const xs = new Float32Array(N);
    const ys = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      xs[i] = (i % 20) / 19;
      ys[i] = Math.floor(i / 20) / 19;
    }
    for (let step = 0; step < 900; step++) {
      for (let i = 0; i < N; i++) {
        const wv = sampleField(field.w, 101, 101, xs[i], ys[i]);
        const gx = sampleField(field.gx, 101, 101, xs[i], ys[i]);
        const gy = sampleField(field.gy, 101, 101, xs[i], ys[i]);
        const { dx, dy } = thrown(wv, gx, gy, 0.02, Math.random(), Math.random());
        xs[i] = reflect(xs[i] + dx);
        ys[i] = reflect(ys[i] + dy);
      }
    }
    // Node lines of sin(2πx)sin(πy): x = 0 , 0.5 , 1 and y = 0 , 1.
    let onNode = 0;
    for (let i = 0; i < N; i++) {
      const nearX = Math.min(xs[i], Math.abs(xs[i] - 0.5), 1 - xs[i]);
      const nearY = Math.min(ys[i], 1 - ys[i]);
      if (Math.min(nearX, nearY) < 0.06) onNode++;
    }
    expect(onNode / N).toBeGreaterThan(0.85);
  });
});

describe("what the conversation does to the plate", () => {
  it("drives it higher the more is going on", () => {
    expect(driveFor("speaking", 0).f).toBeGreaterThan(driveFor("listening", 0).f);
    expect(driveFor("listening", 0).f).toBeGreaterThan(driveFor("idle", 0).f);
  });

  it("drives it higher the louder the voice", () => {
    expect(driveFor("listening", 1).f).toBeGreaterThan(driveFor("listening", 0).f);
  });

  it("loosens the plate while the assistant talks, so several modes ring at once", () => {
    expect(driveFor("speaking", 0).q).toBeLessThan(driveFor("idle", 0).q);
  });
});

describe("the grain budget", () => {
  it("asks a small window for less sand than a big one, and caps both", () => {
    const laptop = grainCountFor(1536, 864);
    expect(grainCountFor(320, 240)).toBeLessThan(laptop);
    // A 4K window is nine times the area but must not be nine times the
    // frame: past the cap the extra sand lands on lines already drawn.
    expect(grainCountFor(7680, 4320)).toBeLessThan(laptop * 2);
    // Even a sliver of a window gets enough sand to draw a figure at all.
    expect(grainCountFor(40, 40)).toBeGreaterThanOrEqual(2000);
  });
});

describe("a mode's shape", () => {
  it("is zero on the plate's edges, whichever mode it is", () => {
    for (const [n, m] of [
      [1, 1],
      [4, 3],
    ]) {
      const mode = { n, m, lambda: modeEigenvalue(n, m, 1, 1) };
      expect(modeShape(mode, 0, 0.5)).toBeCloseTo(0, 12);
      expect(modeShape(mode, 1, 0.5)).toBeCloseTo(0, 12);
      expect(modeShape(mode, 0.5, 0)).toBeCloseTo(0, 12);
      expect(modeShape(mode, 0.5, 1)).toBeCloseTo(0, 12);
    }
  });
});
