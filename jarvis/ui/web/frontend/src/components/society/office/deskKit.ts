/**
 * What sits on (and under) every workstation of the coding floor, and the
 * planter boxes at the ends of each bench. Pure: it says which parts go
 * where; `DeskDressing.tsx` draws them, one instanced mesh per part.
 *
 * A desk's kit is seeded by its id, so the same desk always carries the same
 * things and nothing jumps when the roster refetches. A desk nobody sits at
 * is a clean hot desk (mat, maybe a plant or a lamp); an occupied one is
 * lived in (a laptop on a stand, a notebook, headphones on a hook, sticky
 * notes on the felt screen).
 *
 * Desk-local space matches `DeskInstances`: origin under the desk centre, the
 * seated agent at +z looking north (-z) at its monitor, the top at 0.77 m.
 * Nothing here reaches the monitor (|x| ≤ 0.36, above 0.96 m), so the live
 * terminal screens stay readable and clickable.
 */
import { hashString } from "./toyFigureModel";
import type { Rect } from "./officeLayout";

export type Vec3 = [number, number, number];

/** Every part type drawn; each is one instanced mesh across the whole floor. */
export type DressingPart =
  | "mat" | "standPlate" | "standLeg" | "laptopBase" | "laptopLid" | "laptopScreen"
  | "lampBase" | "lampArm" | "lampShade" | "lampBulb"
  | "pot" | "soil" | "succulent" | "book" | "bottle" | "bottleCap"
  | "notebook" | "notebookBand" | "pen" | "hook" | "headband" | "earCup" | "note"
  | "tray" | "snake" | "puck"
  | "planterBody" | "planterPlinth" | "planterRail" | "planterSoil" | "foliage" | "blade";

/**
 * One part in place. `r` is an Euler rotation applied in YXZ order (a yaw,
 * then a tilt), `s` the scale (the size, for the unit box and cylinder), `c`
 * the instance colour of a tinted part.
 */
export interface Placement { part: DressingPart; p: Vec3; r?: Vec3; s?: Vec3; c?: string }

export const DRESSING_COLOURS = {
  mat: ["#2e3136", "#454a52", "#7f9a86", "#c98f6f", "#d9d0c1", "#3d4b63"],
  lamp: ["#1f2226", "#f1efe9", "#7f9a86", "#c96f4a"],
  pot: ["#c56f4f", "#eeeae3", "#34373d", "#9fb4a0"],
  leaf: ["#5f8f5a", "#78a86a", "#8fb89a", "#4d7a52"],
  book: ["#c8553d", "#e9b44c", "#4f7cac", "#5e9c76", "#8d6cab", "#e7e2d8", "#2f3a4f"],
  bottle: ["#c9ced6", "#8fae96", "#e27d60", "#34507a", "#f3f1ec"],
  notebook: ["#1f2226", "#b08a5f", "#34507a", "#8a3b34", "#5e7d63"],
  headphones: ["#1f2226", "#eeeae3", "#7f9a86", "#c9b79c"],
  note: ["#f7d65a", "#f29fb5", "#9fe0c3", "#9cc8f2"],
  foliage: ["#4f8f52", "#2f6b3f", "#77ad64", "#3e7d4a", "#5f9a58"],
  blade: ["#2f6b3f", "#3f7d45", "#5a8f4c"],
} as const;

/** The things one desk carries; every field is decided by the desk id alone (and whether someone sits there). */
export interface DeskKit {
  /** Desk mat colour index. */
  mat: number;
  /** Back-left corner. */
  corner: "succulent" | "books" | "bottle" | null;
  /** Back-right, beside the monitor. */
  side: "laptop" | "lamp" | null;
  /** Colour index per part, and small turns so no two desks are laid out alike. */
  cornerColour: number;
  sideColour: number;
  notebook: number | null;
  headphones: number | null;
  /** Sticky notes on the felt screen, 0–3. */
  notes: number;
  /** Seeded jitter in [-1, 1) for yaw and placement. */
  jitter: [number, number, number];
}

/** A small deterministic generator (mulberry32) seeded from a string. */
export function seeded(key: string): () => number {
  let state = hashString(key);
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 0x1_0000_0000;
  };
}

const pick = (n: number, r: number) => Math.min(n - 1, Math.floor(r * n));

/**
 * The kit of one desk. Every random draw is taken in the same order whether
 * or not the desk is occupied, so a desk that gains an agent keeps its mat,
 * plant and lamp and only gains the personal things.
 */
export function dressDesk(deskId: string, occupied: boolean): DeskKit {
  const rand = seeded(`desk-dressing:${deskId}`);
  const mat = pick(DRESSING_COLOURS.mat.length, rand());
  const cornerRoll = rand(), sideRoll = rand();
  const cornerColour = Math.floor(rand() * 64), sideColour = Math.floor(rand() * 64);
  const notebookRoll = rand(), notebookColour = pick(DRESSING_COLOURS.notebook.length, rand());
  const headRoll = rand(), headColour = pick(DRESSING_COLOURS.headphones.length, rand());
  const notes = pick(4, rand());
  const jitter: [number, number, number] = [rand() * 2 - 1, rand() * 2 - 1, rand() * 2 - 1];
  if (!occupied) {
    return {
      mat, corner: cornerRoll < 0.45 ? "succulent" : null, side: sideRoll < 0.35 ? "lamp" : null,
      cornerColour, sideColour, notebook: null, headphones: null, notes: 0, jitter,
    };
  }
  return {
    mat,
    corner: cornerRoll < 0.45 ? "succulent" : cornerRoll < 0.7 ? "books" : cornerRoll < 0.92 ? "bottle" : null,
    side: sideRoll < 0.35 ? "lamp" : sideRoll < 0.88 ? "laptop" : null,
    cornerColour, sideColour,
    notebook: notebookRoll < 0.6 ? notebookColour : null,
    headphones: headRoll < 0.5 ? headColour : null,
    notes, jitter,
  };
}

/** Places a sub-assembly's parts: rotate by `yaw` about the assembly origin, then move it to (x, z). */
function assembly(x: number, z: number, yaw: number, parts: Placement[]): Placement[] {
  const cos = Math.cos(yaw), sin = Math.sin(yaw);
  return parts.map((part) => {
    const [px, py, pz] = part.p;
    const [rx, ry, rz] = part.r ?? [0, 0, 0];
    return { ...part, p: [x + px * cos + pz * sin, py, z - px * sin + pz * cos], r: [rx, ry + yaw, rz] };
  });
}

const TOP = 0.77;
const at = <T>(list: readonly T[], i: number): T => list[((i % list.length) + list.length) % list.length];

/** A laptop open on an aluminium stand, its front at +z. */
function laptop(): Placement[] {
  const tilt = 0.3, lid = -0.25;
  return [
    { part: "standPlate", p: [0, 0.815, 0], r: [tilt, 0, 0], s: [0.24, 0.008, 0.22] },
    { part: "standLeg", p: [0, 0.807, -0.1], s: [0.2, 0.075, 0.012] },
    { part: "laptopBase", p: [0, 0.826, 0.002], r: [tilt, 0, 0] },
    { part: "laptopLid", p: [0, 0.954, -0.125], r: [lid, 0, 0] },
    { part: "laptopScreen", p: [0, 0.9552, -0.1206], r: [lid, 0, 0] },
  ];
}

/** An anglepoise desk lamp; its head reaches out along +z. */
function lamp(colour: string): Placement[] {
  return [
    { part: "lampBase", p: [0, TOP + 0.0075, 0], s: [0.06, 0.015, 0.06], c: colour },
    { part: "lampArm", p: [0, 0.9225, -0.03], r: [-0.215, 0, 0], s: [0.008, 0.2815, 0.008], c: colour },
    { part: "lampArm", p: [0, 1.04, 0.07], r: [1.723, 0, 0], s: [0.008, 0.263, 0.008], c: colour },
    { part: "lampShade", p: [0, 0.995, 0.2], s: [0.055, 0.07, 0.055], c: colour },
    { part: "lampBulb", p: [0, 0.957, 0.2], s: [0.042, 0.004, 0.042] },
  ];
}

/** A succulent in a small ceramic pot: a rosette of fleshy leaves round a heart. */
function succulent(colour: number, turn: number): Placement[] {
  const potH = 0.07;
  const leaf = (i: number) => at(DRESSING_COLOURS.leaf, colour + i);
  const ring = Array.from({ length: 5 }, (_, i): Placement => {
    const a = turn + (i * 2 * Math.PI) / 5;
    return { part: "succulent", p: [Math.sin(a) * 0.03, TOP + potH + 0.012, Math.cos(a) * 0.03], r: [0, a, 0], s: [0.022, 0.014, 0.034], c: leaf(i % 2) };
  });
  return [
    { part: "pot", p: [0, TOP + potH / 2, 0], s: [0.048, potH, 0.048], c: at(DRESSING_COLOURS.pot, colour) },
    { part: "soil", p: [0, TOP + potH + 0.001, 0], s: [0.043, 0.004, 0.043] },
    ...ring,
    { part: "succulent", p: [0, TOP + potH + 0.024, 0], s: [0.02, 0.022, 0.02], c: leaf(2) },
  ];
}

/** Two or three hardbacks stacked flat, each a little askew. */
function books(colour: number, jitter: number): Placement[] {
  const count = 2 + (colour % 2);
  const sizes: Vec3[] = [[0.2, 0.032, 0.26], [0.18, 0.026, 0.235], [0.16, 0.022, 0.21]];
  let y = TOP;
  return sizes.slice(0, count).map((s, i) => {
    const placed: Placement = { part: "book", p: [0, y + s[1] / 2, 0], r: [0, (i - 1) * 0.18 + jitter * 0.1, 0], s, c: at(DRESSING_COLOURS.book, colour + i * 3) };
    y += s[1];
    return placed;
  });
}

function bottle(colour: number): Placement[] {
  return [
    { part: "bottle", p: [0, TOP + 0.11, 0], s: [0.034, 0.22, 0.034], c: at(DRESSING_COLOURS.bottle, colour) },
    { part: "bottleCap", p: [0, TOP + 0.235, 0], s: [0.027, 0.03, 0.027] },
  ];
}

/** A closed notebook with an elastic band, a pen lying on it. */
function notebook(colour: number): Placement[] {
  return [
    { part: "notebook", p: [0, TOP + 0.007, 0], s: [0.15, 0.014, 0.21], c: at(DRESSING_COLOURS.notebook, colour) },
    { part: "notebookBand", p: [0.055, TOP + 0.0075, 0], s: [0.008, 0.0155, 0.212] },
    { part: "pen", p: [-0.01, TOP + 0.019, 0.01], r: [Math.PI / 2, 0.35, 0], s: [0.005, 0.14, 0.005] },
  ];
}

/** Over-ear headphones hanging from a hook under the desk's front edge; the band parallel to the edge. */
function headphones(colour: string): Placement[] {
  const z = 0.43, top = 0.708, radius = 0.08;
  return [
    { part: "hook", p: [0, 0.726, 0.42], s: [0.026, 0.012, 0.06] },
    { part: "hook", p: [0, 0.713, 0.449], s: [0.026, 0.03, 0.008] },
    { part: "headband", p: [0, top - radius, z], c: colour },
    ...[-1, 1].map((side): Placement => ({
      part: "earCup", p: [side * (radius + 0.004), top - radius - 0.035, z], r: [0, 0, Math.PI / 2], s: [0.046, 0.034, 0.046], c: colour,
    })),
  ];
}

/** Every part of one desk's kit, in desk-local space. */
export function kitPlacements(kit: DeskKit): Placement[] {
  const [j0, j1, j2] = kit.jitter;
  const out: Placement[] = [
    // Felt desk mat under the keyboard and mouse.
    { part: "mat", p: [0.06, TOP + 0.002, 0.22], c: DRESSING_COLOURS.mat[kit.mat] },
    // Cable tray under the back of the desk and the snake that feeds it from a floor box.
    { part: "tray", p: [0, 0.64, -0.3], s: [1.2, 0.05, 0.14] },
    { part: "snake", p: [-0.25, 0.3075, -0.34], s: [0.022, 0.615, 0.022] },
    { part: "puck", p: [-0.25, 0.012, -0.34], s: [0.13, 0.024, 0.13] },
  ];
  if (kit.side === "laptop") out.push(...assembly(0.56 + j0 * 0.02, -0.1, -0.45 + j1 * 0.08, laptop()));
  if (kit.side === "lamp") out.push(...assembly(0.62, -0.27, -1.0 + j1 * 0.15, lamp(at(DRESSING_COLOURS.lamp, kit.sideColour))));
  const cornerX = -0.6 + j2 * 0.03, cornerZ = -0.24;
  if (kit.corner === "succulent") out.push(...assembly(cornerX, cornerZ, 0, succulent(kit.cornerColour, j0 * Math.PI)));
  if (kit.corner === "books") out.push(...assembly(cornerX + 0.02, cornerZ + 0.02, 0.2 + j0 * 0.2, books(kit.cornerColour, j1)));
  if (kit.corner === "bottle") out.push(...assembly(cornerX - 0.02, cornerZ + 0.02, 0, bottle(kit.cornerColour)));
  if (kit.notebook !== null) out.push(...assembly(-0.48, 0.26, 0.12 + j2 * 0.22, notebook(kit.notebook)));
  if (kit.headphones !== null) out.push(...assembly(-0.52, 0, 0, headphones(DRESSING_COLOURS.headphones[kit.headphones])));
  for (let i = 0; i < kit.notes; i += 1) {
    // Pinned to the felt screen left of the monitor, a hair in front of its face.
    out.push({
      part: "note", p: [-0.44 - i * 0.085, 1.02 + ((i * 37 + Math.round(j1 * 10)) % 5) * 0.012, -0.3935],
      r: [0, 0, (i % 2 === 0 ? 1 : -1) * 0.08 + j0 * 0.05], c: at(DRESSING_COLOURS.note, kit.cornerColour + i),
    });
  }
  return out;
}

/** Planter box: height, rail width, and how far the soil sits below the rim. */
export const PLANTER_H = 0.46;
const RAIL = 0.035;

/**
 * A low walnut planter box on a recessed black plinth, oak-rimmed, full of
 * mixed foliage and tall snake-plant blades, filling `rect` (world space, long
 * side along z). Seeded by `key`.
 */
export function planterPlacements(rect: Rect, key: string): Placement[] {
  const rand = seeded(`planter:${key}`);
  const cx = (rect.minX + rect.maxX) / 2, cz = (rect.minZ + rect.maxZ) / 2;
  const w = rect.maxX - rect.minX, d = rect.maxZ - rect.minZ;
  const plinth = 0.05;
  const out: Placement[] = [
    { part: "planterPlinth", p: [cx, plinth / 2, cz], s: [w - 0.06, plinth, d - 0.06] },
    { part: "planterBody", p: [cx, plinth + (PLANTER_H - plinth) / 2, cz], s: [w, PLANTER_H - plinth, d] },
    { part: "planterSoil", p: [cx, PLANTER_H - 0.03, cz], s: [w - RAIL * 2, 0.01, d - RAIL * 2] },
    { part: "planterRail", p: [rect.minX + RAIL / 2, PLANTER_H + 0.01, cz], s: [RAIL, 0.02, d + 0.01] },
    { part: "planterRail", p: [rect.maxX - RAIL / 2, PLANTER_H + 0.01, cz], s: [RAIL, 0.02, d + 0.01] },
    { part: "planterRail", p: [cx, PLANTER_H + 0.01, rect.minZ + RAIL / 2], s: [w - RAIL * 2, 0.02, RAIL] },
    { part: "planterRail", p: [cx, PLANTER_H + 0.01, rect.maxZ - RAIL / 2], s: [w - RAIL * 2, 0.02, RAIL] },
  ];
  // Foliage in two staggered rows of mixed sizes, so it reads as plants, not a clipped hedge.
  const clusters = Math.max(4, Math.round(d / 0.16));
  for (let i = 0; i < clusters; i += 1) {
    const z = rect.minZ + RAIL + 0.08 + (i + 0.5) * ((d - RAIL * 2 - 0.16) / clusters);
    const size = 0.06 + rand() * rand() * 0.16;
    out.push({
      part: "foliage", p: [cx + (i % 2 === 0 ? -1 : 1) * w * 0.14 + (rand() - 0.5) * 0.04, PLANTER_H + size * 0.5, z],
      r: [0, rand() * Math.PI, 0], s: [size * 1.1, size * (0.7 + rand() * 0.6), size], c: at(DRESSING_COLOURS.foliage, Math.floor(rand() * 16)),
    });
  }
  // Snake-plant blades rising out of the foliage, in a few tufts.
  const tufts = 2 + Math.floor(rand() * 2);
  for (let t = 0; t < tufts; t += 1) {
    const tz = rect.minZ + 0.2 + ((t + 0.5) / tufts) * (d - 0.4);
    const blades = 3 + Math.floor(rand() * 3);
    for (let i = 0; i < blades; i += 1) {
      const h = 0.3 + rand() * 0.28;
      out.push({
        part: "blade", p: [cx + (rand() - 0.5) * 0.08, PLANTER_H + h / 2 - 0.02, tz + (rand() - 0.5) * 0.1],
        r: [(rand() - 0.5) * 0.35, rand() * Math.PI, (rand() - 0.5) * 0.35], s: [0.032, h, 0.012], c: at(DRESSING_COLOURS.blade, Math.floor(rand() * 8)),
      });
    }
  }
  return out;
}
