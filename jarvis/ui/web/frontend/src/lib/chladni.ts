import type { VoiceState } from "@/store/events";

/**
 * A driven rectangular plate, and the sand lying on it.
 *
 * This is the real thing, not the square-plate shortcut. That shortcut —
 * `sin(nπx)sin(mπy) − sin(mπx)sin(nπy)` — is only a solution when the plate
 * is SQUARE, because it leans on (n,m) and (m,n) being degenerate, which they
 * stop being the moment the plate is 16:9. Run it on a wide window and the
 * figure is not a Chladni figure, it is decoration shaped like one.
 *
 * What actually happens on a plate:
 *
 *  1. A simply supported rectangular plate a×b has mode shapes
 *         φ(x,y) = sin(nπx/a)·sin(mπy/b)
 *     with eigenvalue λ = (n/a)² + (m/b)², and ω ∝ λ.
 *
 *  2. You drive it at ONE frequency f, at ONE point. Two things decide how
 *     much each mode answers:
 *       · RESONANCE — a damped oscillator driven off its own frequency
 *         answers along a Lorentzian of width f/Q, and its phase flips by π
 *         as the drive passes through it. Below resonance a mode pushes with
 *         the driver, above it pushes against — which is what makes modes
 *         cancel into clean lines instead of piling into mush.
 *       · COUPLING — a mode is only driven at all to the extent it MOVES at
 *         the point you are hitting: c ∝ φ(x₀,y₀). Strike the dead centre of
 *         a plate and every even mode stays silent, because the centre is a
 *         node for all of them. This is also what settles the degenerate
 *         pairs on a square plate without anyone hand-picking a combination:
 *         (n,m) and (m,n) couple differently unless you strike the diagonal.
 *
 *  3. The sand does not diffuse. A grain is thrown when the plate's
 *     acceleration under it beats what holds it down — |w| above a threshold
 *     — and while it is airborne it lands preferentially DOWN the slope of
 *     |w|, toward the quiet. On a node line the plate is still, the grain is
 *     never thrown, and it stays put forever. That is why real figures have
 *     sharp lines, and why this needs no artificial jitter floor to keep
 *     re-forming: retune the plate and the node moves out from under the
 *     grain, which is thrown again all by itself.
 *
 * Everything here is a pure function over numbers so the physics can be
 * checked without a canvas — see `chladni.test.ts`. The field is evaluated on
 * a grid and the grains read it, rather than every grain evaluating every
 * mode: the mode shapes are SEPARABLE, so one table of sin(nπx/a) per column
 * and one of sin(mπy/b) per row turn the whole field into multiplications.
 */

export interface PlateMode {
  n: number;
  m: number;
  /** (n/a)² + (m/b)² — proportional to the mode's own frequency. */
  lambda: number;
}

export interface ExcitedMode {
  mode: PlateMode;
  /** Signed amplitude: Lorentzian response × coupling at the striking point. */
  weight: number;
}

/** The plate's grid: displacement and the slope of its magnitude. */
export interface PlateField {
  cols: number;
  rows: number;
  /** Displacement w at each grid point. */
  w: Float32Array;
  /** ∂|w|/∂x and ∂|w|/∂y — the slope a thrown grain drifts down. */
  gx: Float32Array;
  gy: Float32Array;
  /** sin(nπx/a) per mode index per column, and the same down the rows. */
  sx: Float32Array;
  sy: Float32Array;
  /** How many modes the tables were built for. */
  tabled: number;
}

/** λ = (n/a)² + (m/b)², the eigenvalue of mode (n, m) on a plate a×b. */
export function modeEigenvalue(n: number, m: number, a: number, b: number): number {
  const u = n / a;
  const v = m / b;
  return u * u + v * v;
}

/**
 * Every mode up to `maxIndex` in each direction, cheapest first. A plate has
 * infinitely many; the ones far above the drive answer with nothing, so the
 * list is cut where it stops mattering.
 */
export function plateModes(a: number, b: number, maxIndex = 9): PlateMode[] {
  const out: PlateMode[] = [];
  for (let n = 1; n <= maxIndex; n++) {
    for (let m = 1; m <= maxIndex; m++) {
      out.push({ n, m, lambda: modeEigenvalue(n, m, a, b) });
    }
  }
  out.sort((p, q) => p.lambda - q.lambda);
  return out;
}

/**
 * How hard a mode answers a drive at `f`. A damped oscillator's response is
 * Lorentzian around its own frequency, with width f/Q: 1 on resonance, and
 * falling away as the drive moves off it. Q is the plate's quality — a high Q
 * is a bell that answers one frequency, a low Q a damped sheet that answers a
 * band of them at once.
 */
export function resonance(lambda: number, f: number, q: number): number {
  if (f <= 0) return 0;
  const detune = (lambda - f) / f;
  return 1 / (1 + q * q * detune * detune);
}

/**
 * The phase of that answer. A driven oscillator moves WITH the driver below
 * its own frequency and AGAINST it above — the π flip through resonance.
 * Without the flip, neighbouring modes only ever add, and the figure fills in
 * instead of cancelling into lines.
 */
export function resonancePhase(lambda: number, f: number): number {
  return lambda <= f ? 1 : -1;
}

/** A mode's shape at a point, both coordinates 0…1 across the plate. */
export function modeShape(mode: PlateMode, x: number, y: number): number {
  return Math.sin(mode.n * Math.PI * x) * Math.sin(mode.m * Math.PI * y);
}

/**
 * Which modes are actually ringing, and how strongly, for a plate struck at
 * (strikeX, strikeY) and driven at f.
 *
 * The weight carries all three effects at once: Lorentzian magnitude, the π
 * phase flip, and the coupling to the striking point. Modes that answer with
 * almost nothing are dropped — carrying forty modes that contribute 0.1 %
 * each costs the frame and changes no pixel. The result is normalised, so the
 * plate's displacement stays in a fixed range whatever it is driven at.
 */
export function excitedModes(
  modes: PlateMode[],
  f: number,
  q: number,
  strikeX: number,
  strikeY: number,
  limit = 8,
): ExcitedMode[] {
  const scored: ExcitedMode[] = [];
  for (const mode of modes) {
    const r = resonance(mode.lambda, f, q);
    if (r < 0.02) continue;
    const coupling = modeShape(mode, strikeX, strikeY);
    const weight = r * resonancePhase(mode.lambda, f) * coupling;
    if (Math.abs(weight) < 0.01) continue;
    scored.push({ mode, weight });
  }
  scored.sort((p, o) => Math.abs(o.weight) - Math.abs(p.weight));
  const kept = scored.slice(0, limit);
  const peak = kept.reduce((s, e) => s + Math.abs(e.weight), 0);
  if (peak <= 0) return kept;
  return kept.map((e) => ({ mode: e.mode, weight: e.weight / peak }));
}

export function createField(cols: number, rows: number, maxModes = 8): PlateField {
  return {
    cols,
    rows,
    w: new Float32Array(cols * rows),
    gx: new Float32Array(cols * rows),
    gy: new Float32Array(cols * rows),
    sx: new Float32Array(maxModes * cols),
    sy: new Float32Array(maxModes * rows),
    tabled: maxModes,
  };
}

/**
 * Evaluate the plate on its grid, then the slope of |w|.
 *
 * The mode shapes are separable — sin(nπx)·sin(mπy) — so each mode's sine is
 * tabulated once per column and once per row, and the field itself is a
 * multiply-add over those tables. A 160×90 grid across eight modes is 115k
 * multiplications and not one call to Math.sin.
 */
export function fillField(field: PlateField, excited: ExcitedMode[]): void {
  const { cols, rows, w, gx, gy, sx, sy } = field;
  const count = Math.min(excited.length, field.tabled);

  for (let e = 0; e < count; e++) {
    const { n, m } = excited[e].mode;
    const rowOff = e * cols;
    for (let i = 0; i < cols; i++) {
      sx[rowOff + i] = Math.sin(n * Math.PI * (i / (cols - 1)));
    }
    const colOff = e * rows;
    for (let j = 0; j < rows; j++) {
      sy[colOff + j] = Math.sin(m * Math.PI * (j / (rows - 1)));
    }
  }

  w.fill(0);
  for (let e = 0; e < count; e++) {
    const weight = excited[e].weight;
    const xOff = e * cols;
    const yOff = e * rows;
    for (let j = 0; j < rows; j++) {
      const wy = weight * sy[yOff + j];
      if (wy === 0) continue;
      const row = j * cols;
      for (let i = 0; i < cols; i++) {
        w[row + i] += wy * sx[xOff + i];
      }
    }
  }

  // Scale the shape to a peak of 1. The plate's SHAPE is what the modes
  // decide; how hard it is being hit is a separate number the caller owns
  // (the throw step). Without this the threshold below would mean something
  // different at every drive frequency, and the figure would dissolve
  // whenever the modes happened to cancel.
  let peak = 0;
  for (let i = 0; i < w.length; i++) {
    const v = w[i] < 0 ? -w[i] : w[i];
    if (v > peak) peak = v;
  }
  if (peak > 1e-6) {
    const scale = 1 / peak;
    for (let i = 0; i < w.length; i++) w[i] *= scale;
  }

  // Central differences of |w|. The grain drifts down this, so it is the
  // slope of the MAGNITUDE — a grain does not care which way the plate is
  // bending, only how hard it is being thrown.
  for (let j = 0; j < rows; j++) {
    const row = j * cols;
    const up = (j > 0 ? j - 1 : j) * cols;
    const down = (j < rows - 1 ? j + 1 : j) * cols;
    for (let i = 0; i < cols; i++) {
      const left = i > 0 ? i - 1 : i;
      const right = i < cols - 1 ? i + 1 : i;
      gx[row + i] = Math.abs(w[row + right]) - Math.abs(w[row + left]);
      gy[row + i] = Math.abs(w[down + i]) - Math.abs(w[up + i]);
    }
  }
}

/** Bilinear read of one plane of the field at (x, y), both 0…1. */
export function sampleField(
  plane: Float32Array,
  cols: number,
  rows: number,
  x: number,
  y: number,
): number {
  const fx = Math.min(cols - 1.0001, Math.max(0, x * (cols - 1)));
  const fy = Math.min(rows - 1.0001, Math.max(0, y * (rows - 1)));
  const i = fx | 0;
  const j = fy | 0;
  const tx = fx - i;
  const ty = fy - j;
  const a = plane[j * cols + i];
  const b = plane[j * cols + i + 1];
  const c = plane[(j + 1) * cols + i];
  const d = plane[(j + 1) * cols + i + 1];
  return a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty;
}

/**
 * How much of the plate's motion it takes to throw a grain off it. Below
 * this the grain is never airborne and never moves — which is exactly why a
 * node line stays a line instead of blurring away.
 */
export const THROW_THRESHOLD = 0.055;

/**
 * Where a grain goes this frame.
 *
 * It is thrown only while |w| beats the threshold; the harder it is thrown,
 * the further it travels. Airborne it drifts DOWN the slope of |w| — toward
 * the quiet — with a random component, because a bouncing grain does not
 * choose its direction, it is merely biased. The randoms are passed in
 * rather than drawn here so the physics stays deterministic under test.
 *
 * The displacement is written into `out` (plate coordinates, 0…1 across the
 * plate) and the return says whether the grain moved at all. Returning a
 * fresh `[dx, dy]` cost 19,000 array allocations per frame at the maintainer's
 * window size, which the garbage collector then had to clean up sixty times a
 * second — measured at 22.9 ms per walk before this was changed.
 */
export function grainThrow(
  w: number,
  gx: number,
  gy: number,
  step: number,
  rndX: number,
  rndY: number,
  out: Float32Array,
): boolean {
  const mag = w < 0 ? -w : w;
  const excess = mag - THROW_THRESHOLD;
  if (excess <= 0) {
    out[0] = 0;
    out[1] = 0;
    return false;
  }
  const energy = (excess < 1 ? excess : 1) * step;
  // Downhill on |w|, normalised so a shallow slope still points somewhere.
  // Math.hypot is correct and slow; the magnitudes here cannot overflow, so
  // the plain square root is the right tool.
  const slope = Math.sqrt(gx * gx + gy * gy);
  const dirX = slope > 1e-6 ? -gx / slope : 0;
  const dirY = slope > 1e-6 ? -gy / slope : 0;
  out[0] = (dirX * 0.62 + (rndX - 0.5) * 1.5) * energy;
  out[1] = (dirY * 0.62 + (rndY - 0.5) * 1.5) * energy;
  return true;
}

/** The plate has edges: a grain that walks off one comes back on. */
export function reflect(v: number): number {
  if (v < 0) return -v;
  if (v > 1) return 2 - v;
  return v;
}

/**
 * Grains scale with the area, so a small window does less work than a big one
 * and a huge one does not run away with the frame.
 *
 * One grain per ~110 square pixels. Denser was tried (1 per 70) and measured:
 * it costs a third more per frame and the extra sand lands on node lines that
 * are already drawn, so it buys weight, not detail.
 */
export function grainCountFor(width: number, height: number): number {
  return Math.min(16000, Math.max(2500, Math.round((width * height) / 110)));
}

/**
 * What the conversation does to the plate.
 *
 * The voice sets the drive frequency, and the frequency picks the modes — the
 * figure is never chosen directly. Resting is a low drive and a coarse
 * figure; speaking drives it high and fine. `q` loosens while the assistant
 * talks so several modes ring at once and the figure churns rather than
 * holding one shape.
 */
export function driveFor(state: VoiceState, level: number): { f: number; q: number } {
  switch (state) {
    case "listening":
      return { f: 34 + level * 46, q: 15 };
    case "thinking":
      return { f: 52 + level * 30, q: 22 };
    case "speaking":
      return { f: 62 + level * 92, q: 9 };
    default:
      return { f: 19 + level * 8, q: 20 };
  }
}
