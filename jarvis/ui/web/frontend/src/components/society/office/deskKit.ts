/**
 * What sits on (and under) every workstation, and the planter boxes at the
 * ends of each bench. The coding floor carries a developer's kit (laptops,
 * headphones, a walnut planter); the agents floor, where agents do office
 * work, an office kit (letter trays, documents, a tablet, a phone dock, pen
 * cups, a framed photo, takeaway coffee, a pale-oak planter). Pure: it says
 * which parts go where; `DeskDressing.tsx` draws them, one instanced mesh
 * per part.
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
  | "planterBody" | "planterPlinth" | "planterRail" | "planterSoil" | "foliage" | "blade"
  // The agents floor's office kit and its oak planters.
  | "letterTray" | "trayPost" | "paper" | "page" | "folder"
  | "tabletBody" | "tabletScreen" | "tabletStand" | "phoneBody" | "phoneScreen" | "dock"
  | "penCup" | "pencil" | "frame" | "photo" | "cup" | "cupLid" | "cupSleeve"
  | "oakPlanter" | "oakSlat" | "brassTrim";

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

// ---------------------------------------------------------------------------
// The agents floor: an office kit on every bench desk and pale-oak planters.
// Desk-local space is the same as above, but that floor's `DeskInstances`
// desk already carries a mug at (-0.52, 0.02) and a pedestal under the right
// side, so the kit keeps clear of the mug.
// ---------------------------------------------------------------------------

/** Scandinavian oak, sage, linen and brass: the agents floor's own palette. */
export const OFFICE_KIT_COLOURS = {
  tray: ["#c7a064", "#d8c7a6", "#8fa58f", "#2c2e33", "#ece6da"],
  paper: ["#f6f3ec", "#fbfaf6", "#efe8d9"],
  folder: ["#dcc497", "#9db09a", "#c98f6f", "#6f86a6", "#e6dccb"],
  lamp: ["#b8914f", "#ebe6da", "#8fa58f", "#2b2d31", "#c98f6f"],
  pot: ["#ece6da", "#b8836a", "#8fa58f", "#d8cbb4", "#3a3d42"],
  leaf: ["#6f9a64", "#86ad73", "#5b8757", "#9dbb86"],
  penCup: ["#b8914f", "#2b2d31", "#ebe6da", "#8fa58f", "#b8836a"],
  pencil: ["#e9b44c", "#c8553d", "#4f7cac", "#2b2d31", "#5e9c76", "#ebe6da"],
  frame: ["#c9a46a", "#d8c7a6", "#2b2d31", "#efe9de", "#8a6a4a"],
  /** Multiplies the one photo texture: warm, cool, faded and true prints. */
  photo: ["#ffffff", "#ffe6c8", "#dde8ff", "#efe2f5", "#e8f0dc"],
  cup: ["#f3efe7", "#c9a27a", "#e7ddd0", "#2f3136"],
  lid: ["#f6f3ec", "#2b2d31"],
  foliage: ["#6f9a64", "#557f52", "#8cb07a", "#7a9f6e", "#a3bf8e"],
  frond: ["#6c9a5e", "#86ad73", "#5a8a55", "#9cc08a"],
} as const;

/** The things one agents-floor desk carries; decided by the desk id alone (and whether someone sits there). */
export interface OfficeKit {
  /** Back-left corner. */
  corner: "tray" | "plant" | "books" | null;
  /** Back-right, beside the monitor. */
  side: "lamp" | "tablet" | "dock" | null;
  /** Front-left, beside the keyboard (the mug stays behind it). */
  front: "notebook" | "documents" | null;
  photo: boolean;
  penCup: boolean;
  /** A phone lying face up at the front right (never together with the dock, which holds it). */
  phone: boolean;
  cup: boolean;
  /** A printed page pinned to the felt screen, right of the monitor. */
  pinned: boolean;
  /** Sticky notes on the felt screen, 0–2. */
  notes: number;
  /** Colour seed shared by the kit's parts. */
  colour: number;
  /** Seeded jitter in [-1, 1) for yaw and placement. */
  jitter: [number, number, number];
}

/**
 * The office kit of one desk. As with `dressDesk`, every draw is taken in the
 * same order either way and the empty desk's choices are prefixes of the
 * occupied desk's thresholds, so an agent sitting down only adds things.
 */
export function dressOfficeDesk(deskId: string, occupied: boolean): OfficeKit {
  const rand = seeded(`office-desk:${deskId}`);
  const cornerRoll = rand(), sideRoll = rand(), frontRoll = rand();
  const photoRoll = rand(), penRoll = rand(), phoneRoll = rand(), cupRoll = rand(), pinRoll = rand();
  const notes = pick(3, rand());
  const colour = Math.floor(rand() * 64);
  const jitter: [number, number, number] = [rand() * 2 - 1, rand() * 2 - 1, rand() * 2 - 1];
  const corner = cornerRoll < 0.4 ? "tray" : cornerRoll < 0.72 ? "plant" : occupied && cornerRoll < 0.9 ? "books" : null;
  const side = sideRoll < 0.32 ? "lamp" : occupied && sideRoll < 0.64 ? "tablet" : occupied && sideRoll < 0.86 ? "dock" : null;
  if (!occupied) {
    return { corner, side, front: null, photo: false, penCup: penRoll < 0.3, phone: false, cup: false, pinned: false, notes: 0, colour, jitter };
  }
  return {
    corner, side,
    front: frontRoll < 0.45 ? "notebook" : frontRoll < 0.85 ? "documents" : null,
    photo: photoRoll < 0.55,
    penCup: penRoll < 0.65,
    phone: side !== "dock" && phoneRoll < 0.5,
    cup: cupRoll < 0.45,
    pinned: pinRoll < 0.5,
    notes, colour, jitter,
  };
}

/** A sheet's printed face, laid flat on top of a stack (or a folder) at height `y`. */
const printedFace = (x: number, y: number, z: number, yaw: number, w = 0.19, d = 0.26): Placement =>
  ({ part: "page", p: [x, y, z], r: [-Math.PI / 2, yaw, 0], s: [w, d, 1] });

/** A one- or two-tier letter tray, open towards the seat (+z), paper in each tier. */
function letterTray(colour: string, paper: string, tiers: number, jitter: number): Placement[] {
  const w = 0.24, d = 0.3, gap = 0.075, wall = 0.008;
  const out: Placement[] = [];
  for (let t = 0; t < tiers; t += 1) {
    const y0 = TOP + t * gap;
    const stack = 0.008 + ((t * 5 + Math.round(jitter * 7) + 7) % 4) * 0.006;
    const yaw = jitter * 0.05 * (t === 0 ? 1 : -1);
    out.push(
      { part: "letterTray", p: [0, y0 + 0.004, 0], s: [w, 0.008, d], c: colour },
      ...[-1, 1].map((side): Placement => ({ part: "letterTray", p: [side * (w / 2 - wall / 2), y0 + 0.02, 0], s: [wall, 0.032, d], c: colour })),
      { part: "letterTray", p: [0, y0 + 0.02, -d / 2 + wall / 2], s: [w, 0.032, wall], c: colour },
      { part: "paper", p: [0, y0 + 0.008 + stack / 2, 0.012], r: [0, yaw, 0], s: [0.205, stack, 0.28], c: paper },
      printedFace(0, y0 + 0.0085 + stack, 0.012, yaw, 0.2, 0.275),
    );
  }
  if (tiers > 1) {
    for (const sx of [-1, 1]) {
      for (const sz of [-1, 1]) {
        out.push({ part: "trayPost", p: [sx * (w / 2 - 0.012), TOP + 0.008 + gap / 2, sz * (d / 2 - 0.03)], s: [0.006, gap, 0.006] });
      }
    }
  }
  return out;
}

/** A manila folder with a few sheets in it and one loose page on top, a little askew. */
function documents(folder: string, paper: string, jitter: number): Placement[] {
  return [
    { part: "folder", p: [0, TOP + 0.003, 0], s: [0.22, 0.006, 0.29], c: folder },
    { part: "paper", p: [0.006, TOP + 0.009, -0.004], r: [0, 0.04, 0], s: [0.2, 0.006, 0.27], c: paper },
    { part: "paper", p: [0.03, TOP + 0.0131, 0.012], r: [0, -0.16 + jitter * 0.08, 0], s: [0.2, 0.0022, 0.27], c: paper },
    printedFace(0.03, TOP + 0.0145, 0.012, -0.16 + jitter * 0.08),
  ];
}

/** A tablet leaning back in a brass stand, its calendar facing +z. */
function tablet(): Placement[] {
  const tilt = -0.35, half = 0.085, foot = TOP + 0.014;
  const up = Math.cos(tilt) * half, back = Math.sin(tilt) * half;
  return [
    { part: "tabletStand", p: [0, TOP + 0.004, -0.02], s: [0.13, 0.008, 0.11] },
    { part: "tabletStand", p: [0, TOP + 0.014, 0.008], s: [0.13, 0.014, 0.01] },
    { part: "tabletStand", p: [0, TOP + 0.045, -0.052], r: [0.5, 0, 0], s: [0.05, 0.08, 0.008] },
    { part: "tabletBody", p: [0, foot + up, back], r: [tilt, 0, 0] },
    { part: "tabletScreen", p: [0, foot + up - Math.sin(tilt) * 0.0045, back + Math.cos(tilt) * 0.0045], r: [tilt, 0, 0] },
  ];
}

/** A phone standing in a charging dock, its screen facing +z. */
function phoneDock(): Placement[] {
  const tilt = -0.3, half = 0.075, foot = TOP + 0.012;
  const up = Math.cos(tilt) * half, back = Math.sin(tilt) * half;
  return [
    { part: "dock", p: [0, TOP + 0.006, -0.005], s: [0.085, 0.012, 0.075] },
    { part: "dock", p: [0, TOP + 0.04, -0.034], r: [0.3, 0, 0], s: [0.06, 0.07, 0.01] },
    { part: "phoneBody", p: [0, foot + up, back], r: [tilt, 0, 0] },
    { part: "phoneScreen", p: [0, foot + up - Math.sin(tilt) * 0.0045, back + Math.cos(tilt) * 0.0045], r: [tilt, 0, 0] },
  ];
}

/** A phone lying face up. */
function phoneFlat(): Placement[] {
  return [
    { part: "phoneBody", p: [0, TOP + 0.004, 0], r: [-Math.PI / 2, 0, 0] },
    { part: "phoneScreen", p: [0, TOP + 0.0085, 0], r: [-Math.PI / 2, 0, 0] },
  ];
}

/** A pen cup with a few pencils leaning out of it. */
function penCup(colour: number): Placement[] {
  const leans: Vec3[] = [[0.16, 0, 0.08], [-0.1, 0, -0.14], [0.02, 0, 0.2]];
  return [
    { part: "penCup", p: [0, TOP + 0.045, 0], s: [0.032, 0.09, 0.032], c: at(OFFICE_KIT_COLOURS.penCup, colour) },
    ...leans.map(([rx, , rz], i): Placement => ({
      part: "pencil", p: [-rz * 0.03, TOP + 0.1, rx * 0.03], r: [rx, 0, rz], s: [0.0045, 0.15, 0.0045], c: at(OFFICE_KIT_COLOURS.pencil, colour + i * 2),
    })),
  ];
}

/** A small framed photo leaning back on its strut, facing +z. */
function photoFrame(frame: string, print: string): Placement[] {
  const tilt = -0.2, half = 0.07;
  const up = Math.cos(tilt) * half, back = Math.sin(tilt) * half;
  return [
    { part: "frame", p: [0, TOP + up, back], r: [tilt, 0, 0], s: [0.11, 0.14, 0.012], c: frame },
    { part: "photo", p: [0, TOP + up - Math.sin(tilt) * 0.0065, back + Math.cos(tilt) * 0.0065], r: [tilt, 0, 0], s: [0.086, 0.114, 1], c: print },
    { part: "frame", p: [0, TOP + 0.048, -0.045], r: [0.38, 0, 0], s: [0.02, 0.1, 0.006], c: frame },
  ];
}

/** A takeaway coffee cup: tapered body, kraft sleeve, lid. */
function takeawayCup(colour: number): Placement[] {
  return [
    { part: "cup", p: [0, TOP + 0.055, 0], s: [0.042, 0.11, 0.042], c: at(OFFICE_KIT_COLOURS.cup, colour) },
    { part: "cupSleeve", p: [0, TOP + 0.06, 0], s: [0.0445, 0.042, 0.0445] },
    { part: "cupLid", p: [0, TOP + 0.115, 0], s: [0.0445, 0.012, 0.0445], c: at(OFFICE_KIT_COLOURS.lid, colour) },
  ];
}

/** A leafy plant in a ceramic pot, a few leaves trailing over the rim. */
function leafyPlant(colour: number, turn: number): Placement[] {
  const potH = 0.09;
  const leaf = (i: number) => at(OFFICE_KIT_COLOURS.leaf, colour + i);
  const crown = Array.from({ length: 4 }, (_, i): Placement => {
    const a = turn + (i * 2 * Math.PI) / 4;
    const size = 0.034 + (i % 2) * 0.01;
    return { part: "foliage", p: [Math.sin(a) * 0.03, TOP + potH + 0.035 + (i % 2) * 0.02, Math.cos(a) * 0.03], r: [0, a, 0], s: [size, size * 0.85, size], c: leaf(i) };
  });
  const trail = Array.from({ length: 3 }, (_, i): Placement => ({
    part: "succulent", p: [Math.sin(turn) * 0.056, TOP + potH - 0.012 - i * 0.03, Math.cos(turn) * 0.056 + (i - 1) * 0.008],
    r: [0.4, turn + i, 0.3], s: [0.018, 0.008, 0.024], c: leaf(i + 1),
  }));
  return [
    { part: "pot", p: [0, TOP + potH / 2, 0], s: [0.055, potH, 0.055], c: at(OFFICE_KIT_COLOURS.pot, colour) },
    { part: "soil", p: [0, TOP + potH + 0.001, 0], s: [0.05, 0.004, 0.05] },
    ...crown,
    { part: "foliage", p: [0, TOP + potH + 0.07, 0], s: [0.03, 0.032, 0.03], c: leaf(2) },
    ...trail,
  ];
}

/** Every part of one agents-floor desk's kit, in desk-local space. */
export function officeKitPlacements(kit: OfficeKit): Placement[] {
  const [j0, j1, j2] = kit.jitter;
  const c = kit.colour;
  const paper = at(OFFICE_KIT_COLOURS.paper, c);
  const out: Placement[] = [];
  const cornerX = -0.6 + j2 * 0.02, cornerZ = -0.225;
  if (kit.corner === "tray") out.push(...assembly(cornerX, cornerZ, j0 * 0.06, letterTray(at(OFFICE_KIT_COLOURS.tray, c), paper, 1 + (c % 2), j1)));
  if (kit.corner === "plant") out.push(...assembly(cornerX + 0.02, cornerZ - 0.02, 0, leafyPlant(c, j0 * Math.PI)));
  if (kit.corner === "books") out.push(...assembly(cornerX + 0.02, cornerZ + 0.02, 0.2 + j0 * 0.2, books(c, j1)));
  if (kit.photo) out.push(...assembly(-0.39, -0.29, 0.22 + j1 * 0.12, photoFrame(at(OFFICE_KIT_COLOURS.frame, c + 1), at(OFFICE_KIT_COLOURS.photo, c + 3))));
  if (kit.side === "lamp") out.push(...assembly(0.62, -0.27, -1.0 + j1 * 0.15, lamp(at(OFFICE_KIT_COLOURS.lamp, c))));
  if (kit.side === "tablet") out.push(...assembly(0.56 + j0 * 0.015, -0.15, -0.6 + j1 * 0.08, tablet()));
  if (kit.side === "dock") out.push(...assembly(0.6, -0.24, -0.45 + j1 * 0.1, phoneDock()));
  if (kit.penCup) out.push(...assembly(kit.side === "dock" ? 0.47 : 0.43, -0.31, j2, penCup(c + 1)));
  if (kit.front === "notebook") out.push(...assembly(-0.47, 0.25, 0.12 + j2 * 0.2, notebook(c)));
  if (kit.front === "documents") out.push(...assembly(-0.47, 0.25, 0.1 + j2 * 0.12, documents(at(OFFICE_KIT_COLOURS.folder, c + 2), paper, j0)));
  if (kit.phone) out.push(...assembly(0.53, 0.27, -0.25 + j0 * 0.3, phoneFlat()));
  if (kit.cup) out.push(...assembly(0.64, 0.07 + j1 * 0.02, 0, takeawayCup(c + 2)));
  if (kit.pinned) {
    // A printed page pinned to the felt screen right of the monitor, a hair in front of its face.
    out.push({ part: "page", p: [0.5 + j0 * 0.03, 1.02, -0.3935], r: [0, 0, j1 * 0.06], s: [0.1, 0.135, 1] });
  }
  for (let i = 0; i < kit.notes; i += 1) {
    out.push({
      part: "note", p: [-0.46 - i * 0.085, 1.03 + ((i * 37 + Math.round(j1 * 10)) % 5) * 0.012, -0.3935],
      r: [0, 0, (i % 2 === 0 ? 1 : -1) * 0.08 + j0 * 0.05], c: at(DRESSING_COLOURS.note, c + i),
    });
  }
  return out;
}

/** The agents floor's planter: taller than the coding floor's box, so it also screens the bench end. */
export const OAK_PLANTER_H = 0.52;

/**
 * A pale-oak planter on short feet, clad in vertical slats and rimmed in
 * brass, full of sage-green foliage and arching fronds, filling `rect` (world
 * space, long side along z). Seeded by `key`.
 */
export function oakPlanterPlacements(rect: Rect, key: string): Placement[] {
  const rand = seeded(`oak-planter:${key}`);
  const cx = (rect.minX + rect.maxX) / 2, cz = (rect.minZ + rect.maxZ) / 2;
  const w = rect.maxX - rect.minX, d = rect.maxZ - rect.minZ;
  const H = OAK_PLANTER_H, feet = 0.06, slat = 0.03, pitch = 0.055, trim = 0.022;
  const inW = w - 0.02, inD = d - 0.02;
  const out: Placement[] = [
    { part: "oakPlanter", p: [cx, feet + (H - feet) / 2, cz], s: [inW - 0.012, H - feet, inD - 0.012] },
    { part: "planterSoil", p: [cx, H - 0.035, cz], s: [inW - trim * 2, 0.01, inD - trim * 2] },
  ];
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) out.push({ part: "oakSlat", p: [cx + sx * (inW / 2 - 0.04), feet / 2, cz + sz * (inD / 2 - 0.05)], s: [0.04, feet, 0.04] });
  }
  // Slats: along both long sides, then the two short ends.
  const along = Math.floor(inD / pitch);
  for (let i = 0; i < along; i += 1) {
    const z = cz - ((along - 1) * pitch) / 2 + i * pitch;
    for (const sx of [-1, 1]) out.push({ part: "oakSlat", p: [cx + sx * (inW / 2 - 0.006), feet + (H - feet) / 2, z], s: [0.012, H - feet, slat] });
  }
  const across = Math.max(2, Math.floor(inW / pitch));
  for (let i = 0; i < across; i += 1) {
    const x = cx - ((across - 1) * pitch) / 2 + i * pitch;
    for (const sz of [-1, 1]) out.push({ part: "oakSlat", p: [x, feet + (H - feet) / 2, cz + sz * (inD / 2 - 0.006)], s: [slat, H - feet, 0.012] });
  }
  // Brass rim, flush over the slats.
  out.push(
    { part: "brassTrim", p: [cx - inW / 2 + trim / 2, H + 0.006, cz], s: [trim, 0.012, inD + 0.004] },
    { part: "brassTrim", p: [cx + inW / 2 - trim / 2, H + 0.006, cz], s: [trim, 0.012, inD + 0.004] },
    { part: "brassTrim", p: [cx, H + 0.006, cz - inD / 2 + trim / 2], s: [inW - trim * 2, 0.012, trim] },
    { part: "brassTrim", p: [cx, H + 0.006, cz + inD / 2 - trim / 2], s: [inW - trim * 2, 0.012, trim] },
  );
  // Soft mounds of foliage in two staggered rows.
  const clusters = Math.max(4, Math.round(inD / 0.15));
  for (let i = 0; i < clusters; i += 1) {
    const z = rect.minZ + 0.1 + (i + 0.5) * ((d - 0.2) / clusters);
    const size = 0.07 + rand() * rand() * 0.12;
    out.push({
      part: "foliage", p: [cx + (i % 2 === 0 ? -1 : 1) * w * 0.12 + (rand() - 0.5) * 0.04, H + size * 0.45, z],
      r: [0, rand() * Math.PI, 0], s: [size * 1.15, size * (0.65 + rand() * 0.4), size], c: at(OFFICE_KIT_COLOURS.foliage, Math.floor(rand() * 16)),
    });
  }
  // Arching fronds fanning out of a few crowns, like ferns and soft grasses.
  const crowns = 3 + Math.floor(rand() * 2);
  for (let t = 0; t < crowns; t += 1) {
    const tz = rect.minZ + 0.18 + ((t + 0.5) / crowns) * (d - 0.36);
    const fronds = 5 + Math.floor(rand() * 3);
    for (let i = 0; i < fronds; i += 1) {
      const h = 0.2 + rand() * 0.2;
      const yaw = (i / fronds) * Math.PI * 2 + rand() * 0.5, lean = 0.45 + rand() * 0.45;
      // Tilt outwards about the frond's own base, then lift it so the base sits in the soil.
      const reach = Math.sin(lean) * h * 0.5;
      out.push({
        part: "blade", p: [cx + Math.sin(yaw) * reach, H - 0.02 + Math.cos(lean) * h * 0.5, tz + Math.cos(yaw) * reach],
        r: [lean, yaw, 0], s: [0.03, h, 0.01], c: at(OFFICE_KIT_COLOURS.frond, Math.floor(rand() * 8)),
      });
    }
  }
  return out;
}
