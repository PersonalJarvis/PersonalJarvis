/**
 * The rank insignia as vector art: one source for every place a rank is
 * drawn — the SVG icons in the HUD, the banner and the hall screen, the
 * canvas of the Level Wall, and the extruded 3D pieces on a figure's sleeve
 * or shoulder and in the hall's display cases. Because every surface reads
 * these polygons, the insignia in a display case IS the one on the figure.
 *
 * Coordinates: a box 100 units wide, y pointing down (SVG convention); the
 * height depends on the insignia. Enlisted ranks are gold chevrons, rockers
 * and devices on a cut-to-shape navy backing worn on the upper sleeve;
 * officers wear metal pins (bars, oak leaves, the eagle) and general officers
 * silver stars on the shoulder. Private (E-1) wears no insignia.
 *
 * Pure geometry: no DOM, no three.js.
 */
import type { RankId } from "../levelCatalog";

export type Pt = readonly [number, number];

/** What a part is made of: embroidered gold, polished silver, or the navy cloth it is sewn onto. */
export type Finish = "gold" | "silver" | "cloth";

export interface InsigniaPart {
  pts: Pt[];
  finish: Finish;
  /** Raised detail (a leaf's veins, an eagle's feathers): drawn in a darker tone, extruded higher. */
  detail?: boolean;
}

export interface InsigniaArt {
  w: number;
  h: number;
  parts: InsigniaPart[];
  /** Where a figure wears it. */
  mount: "sleeve" | "shoulder" | "none";
}

// ---------------------------------------------------------------------------
// Primitives

/** Chevron geometry: each band rises RISE over a half-span of SPAN, BAND thick, a new band every STEP. */
const SPAN = 46;
const RISE = 30;
const BAND = 9.5;
const STEP = 13;
const TOP = 6;
/** How far a rocker sags at its centre. */
const SAG = 15;
/** The navy backing's margin round the gold. */
const MARGIN = 3.2;

function chevron(y0: number): Pt[] {
  return [
    [50, y0], [50 + SPAN, y0 + RISE], [50 + SPAN, y0 + RISE + BAND],
    [50, y0 + BAND], [50 - SPAN, y0 + RISE + BAND], [50 - SPAN, y0 + RISE],
  ];
}

/** A rocker: a band that hangs between the two leg ends, sagging SAG at the middle. */
function rockerEdge(yEnds: number, sag: number, steps = 16): Pt[] {
  const out: Pt[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const u = -1 + (2 * i) / steps;
    out.push([50 + u * SPAN, yEnds + sag * (1 - u * u)]);
  }
  return out;
}

function rocker(yEnds: number): Pt[] {
  const top = rockerEdge(yEnds, SAG);
  const bottom = rockerEdge(yEnds + BAND, SAG).reverse();
  return [...top, ...bottom];
}

export function star(cx: number, cy: number, r: number, rotation = 0): Pt[] {
  const out: Pt[] = [];
  for (let i = 0; i < 10; i += 1) {
    const a = rotation - Math.PI / 2 + (i * Math.PI) / 5;
    const rr = i % 2 === 0 ? r : r * 0.4;
    out.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]);
  }
  return out;
}

export function ellipse(cx: number, cy: number, rx: number, ry: number, steps = 24, rotation = 0): Pt[] {
  const out: Pt[] = [];
  const c = Math.cos(rotation), s = Math.sin(rotation);
  for (let i = 0; i < steps; i += 1) {
    const a = (i / steps) * Math.PI * 2;
    const x = Math.cos(a) * rx, y = Math.sin(a) * ry;
    out.push([cx + x * c - y * s, cy + x * s + y * c]);
  }
  return out;
}

function roundRect(x0: number, y0: number, x1: number, y1: number, r: number): Pt[] {
  const out: Pt[] = [];
  const corners: [number, number, number][] = [[x1 - r, y0 + r, -Math.PI / 2], [x1 - r, y1 - r, 0], [x0 + r, y1 - r, Math.PI / 2], [x0 + r, y0 + r, Math.PI]];
  for (const [cx, cy, start] of corners) {
    for (let i = 0; i <= 4; i += 1) {
      const a = start + (i / 4) * (Math.PI / 2);
      out.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]);
    }
  }
  return out;
}

function diamond(cx: number, cy: number, rx: number, ry: number): Pt[] {
  return [[cx, cy - ry], [cx + rx, cy], [cx, cy + ry], [cx - rx, cy]];
}

function transform(pts: readonly Pt[], cx: number, cy: number, s: number, flipX = false): Pt[] {
  return pts.map(([x, y]) => [cx + (flipX ? -x : x) * s, cy + y * s] as Pt);
}

/**
 * A heraldic eagle with spread wings, its head turned to the viewer's left,
 * in a unit box (about -1..1). Wings are fans of feathers; a tail fan below.
 */
function eagleUnit(): { body: Pt[][]; detail: Pt[][] } {
  // Each wing is a fan of primary feathers raised from the shoulder, longest at the top.
  const pivot: Pt = [-0.14, -0.04];
  const feathers = 7;
  const wing: Pt[] = [[-0.06, -0.2]];
  for (let k = 0; k < feathers; k += 1) {
    const t = k / (feathers - 1);
    const angle = (-100 - t * 92) * (Math.PI / 180);
    const reach = 0.96 - t * 0.42;
    const tip: Pt = [pivot[0] + Math.cos(angle) * reach, pivot[1] + Math.sin(angle) * reach];
    // A feather has a rounded tip: two points either side of the axis.
    const across = angle + Math.PI / 2;
    wing.push([tip[0] + Math.cos(across) * 0.045, tip[1] + Math.sin(across) * 0.045]);
    wing.push([tip[0] + Math.cos(angle) * 0.03, tip[1] + Math.sin(angle) * 0.03]);
    wing.push([tip[0] - Math.cos(across) * 0.045, tip[1] - Math.sin(across) * 0.045]);
    if (k < feathers - 1) {
      const mid = angle - (92 / (feathers - 1) / 2) * (Math.PI / 180);
      const notch = reach * 0.8;
      wing.push([pivot[0] + Math.cos(mid) * notch, pivot[1] + Math.sin(mid) * notch]);
    }
  }
  wing.push([-0.12, 0.16]);
  const rightWing = wing.map(([x, y]) => [-x, y] as Pt).reverse();
  const body = ellipse(0, 0.06, 0.17, 0.34, 20);
  const head = ellipse(-0.03, -0.36, 0.12, 0.12, 16);
  const beak: Pt[] = [[-0.1, -0.4], [-0.27, -0.34], [-0.11, -0.29]];
  const tail: Pt[] = [[-0.12, 0.3], [-0.24, 0.72], [-0.1, 0.64], [0, 0.8], [0.1, 0.64], [0.24, 0.72], [0.12, 0.3]];
  // Raised feather lines on each wing and a breast shield.
  const detail: Pt[][] = [];
  for (const side of [-1, 1]) {
    for (let k = 0; k < 3; k += 1) {
      const angle = (-118 - k * 26) * (Math.PI / 180);
      const x0 = side * -(pivot[0] + Math.cos(angle) * 0.18), y0 = pivot[1] + Math.sin(angle) * 0.18;
      const x1 = side * -(pivot[0] + Math.cos(angle) * (0.66 - k * 0.08)), y1 = pivot[1] + Math.sin(angle) * (0.66 - k * 0.08);
      const nx = -(y1 - y0), ny = x1 - x0;
      const len = Math.hypot(nx, ny) || 1;
      const w = 0.016;
      detail.push([[x0 + (nx / len) * w, y0 + (ny / len) * w], [x1 + (nx / len) * w, y1 + (ny / len) * w],
        [x1 - (nx / len) * w, y1 - (ny / len) * w], [x0 - (nx / len) * w, y0 - (ny / len) * w]]);
    }
  }
  detail.push([[-0.1, -0.06], [0.1, -0.06], [0.1, 0.12], [0, 0.24], [-0.1, 0.12]]);
  return { body: [wing, rightWing, body, head, beak, tail], detail };
}

const EAGLE = eagleUnit();

export function eagle(cx: number, cy: number, s: number, finish: Finish): InsigniaPart[] {
  return [
    ...EAGLE.body.map((pts) => ({ pts: transform(pts, cx, cy, s), finish })),
    ...EAGLE.detail.map((pts) => ({ pts: transform(pts, cx, cy, s), finish, detail: true })),
  ];
}

/** A lobed oak leaf, stem down, centred on (50, cy). */
function oakLeaf(cy: number, finish: Finish): InsigniaPart[] {
  const length = 84, width = 30, top = cy - length / 2;
  const steps = 48;
  const right: Pt[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const body = Math.pow(Math.sin(Math.PI * Math.pow(t, 0.8)), 0.85);
    const lobes = 0.74 + 0.26 * Math.cos(t * Math.PI * 2 * 3.5 + 0.6);
    right.push([50 + (width / 2) * body * lobes, top + t * length * 0.9]);
  }
  const stem: Pt[] = [[52.2, top + length * 0.9], [52.2, top + length], [47.8, top + length], [47.8, top + length * 0.9]];
  const left = right.map(([x, y]) => [100 - x, y] as Pt).reverse();
  const outline = [...right, ...stem.slice(1, 3), ...left];
  const veins: Pt[][] = [[[49.2, top + 6], [50.8, top + 6], [50.8, top + length * 0.9], [49.2, top + length * 0.9]]];
  for (let k = 0; k < 3; k += 1) {
    const y = top + 20 + k * 18;
    for (const side of [-1, 1]) {
      veins.push([[50, y], [50 + side * 10, y - 6], [50 + side * 10.6, y - 4.6], [50, y + 2]]);
    }
  }
  return [{ pts: outline, finish }, ...veins.map((pts) => ({ pts, finish, detail: true }))];
}

// ---------------------------------------------------------------------------
// Enlisted sets

interface ChevronSet { chevrons: number; rockers: number; device?: "diamond" | "star" | "wreath" | "sma" }

function chevronSet({ chevrons, rockers, device }: ChevronSet): InsigniaArt {
  const parts: InsigniaPart[] = [];
  const lastY = TOP + (chevrons - 1) * STEP;
  const legEnd = lastY + RISE + BAND;
  for (let i = 0; i < chevrons; i += 1) parts.push({ pts: chevron(TOP + i * STEP), finish: "gold" });
  const rockerEnds: number[] = [];
  for (let j = 0; j < rockers; j += 1) {
    const y = legEnd + 3.5 + j * STEP;
    rockerEnds.push(y);
    parts.push({ pts: rocker(y), finish: "gold" });
  }
  // The device sits in the space between the lowest chevron and the first rocker.
  const deviceY = (lastY + BAND + (rockerEnds[0] ?? legEnd) + SAG) / 2 + 2;
  if (device === "diamond") parts.push({ pts: diamond(50, deviceY, 8, 11.5), finish: "gold" });
  if (device === "star") parts.push({ pts: star(50, deviceY + 1, 12), finish: "gold" });
  if (device === "wreath") {
    parts.push({ pts: star(50, deviceY - 1, 9), finish: "gold" });
    for (const side of [-1, 1]) {
      for (let k = 0; k < 5; k += 1) {
        const a = Math.PI / 2 + side * (0.55 + k * 0.42);
        const r = 14;
        parts.push({ pts: ellipse(50 + Math.cos(a) * r, deviceY + 1 + Math.sin(a) * r, 3.6, 1.7, 10, a + Math.PI / 2), finish: "gold", detail: true });
      }
    }
  }
  if (device === "sma") {
    parts.push(...eagle(50, deviceY - 1, 11, "gold"));
    parts.push({ pts: star(33, deviceY + 5, 5.5), finish: "gold" }, { pts: star(67, deviceY + 5, 5.5), finish: "gold" });
  }
  // The navy backing: the gold outline grown by a margin, cut along the lowest band.
  const bottom = rockers > 0
    ? rockerEdge(rockerEnds[rockers - 1] + BAND + MARGIN, SAG + 0.6).reverse()
    : [[50 + SPAN + MARGIN, legEnd + MARGIN], [50, lastY + BAND + MARGIN * 1.6], [50 - SPAN - MARGIN, legEnd + MARGIN]] as Pt[];
  const sideBottom = rockers > 0 ? rockerEnds[rockers - 1] + BAND + MARGIN : legEnd + MARGIN;
  const backing: Pt[] = [
    [50, TOP - MARGIN * 1.7],
    [50 + SPAN + MARGIN, TOP + RISE - MARGIN * 0.3],
    [50 + SPAN + MARGIN, sideBottom],
    ...(rockers > 0 ? bottom : bottom.slice(1, 2)),
    [50 - SPAN - MARGIN, sideBottom],
    [50 - SPAN - MARGIN, TOP + RISE - MARGIN * 0.3],
  ];
  const h = Math.max(...backing.map(([, y]) => y)) + 2;
  return { w: 100, h, parts: [{ pts: backing, finish: "cloth" }, ...parts], mount: "sleeve" };
}

function specialist(): InsigniaArt {
  const shield = (inset: number): Pt[] => {
    const out: Pt[] = [];
    const r = 38 - inset;
    for (let i = 0; i <= 18; i += 1) {
      const a = Math.PI + (i / 18) * Math.PI;
      out.push([50 + Math.cos(a) * r, 44 + Math.sin(a) * r]);
    }
    out.push([88 - inset, 66 - inset * 0.3], [50, 98 - inset * 1.5], [12 + inset, 66 - inset * 0.3]);
    return out;
  };
  return {
    w: 100, h: 100, mount: "sleeve",
    parts: [{ pts: shield(0), finish: "gold" }, { pts: shield(5), finish: "cloth" }, ...eagle(50, 50, 30, "gold")],
  };
}

// ---------------------------------------------------------------------------
// Officers

function bar(x0: number, x1: number, finish: Finish): InsigniaPart[] {
  return [
    { pts: roundRect(x0, 10, x1, 90, 3), finish },
    // A raised rim inside the bar, the way a pin is struck.
    { pts: roundRect(x0 + 3.5, 14, x1 - 3.5, 86, 2), finish, detail: true },
  ];
}

function stars(count: number): InsigniaArt {
  const r = [0, 21, 17, 13.5, 11][count];
  const gap = (100 - 12) / count;
  const parts: InsigniaPart[] = [];
  for (let i = 0; i < count; i += 1) parts.push({ pts: star(50, 6 + gap * (i + 0.5), r), finish: "silver" });
  return { w: 100, h: 100, parts, mount: "shoulder" };
}

function fiveStars(): InsigniaArt {
  const parts: InsigniaPart[] = [];
  for (let i = 0; i < 5; i += 1) {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / 5;
    // Each star points out from the ring; their inner points meet in the middle.
    parts.push({ pts: star(50 + Math.cos(a) * 27, 50 + Math.sin(a) * 27, 17, a + Math.PI / 2), finish: "silver" });
  }
  return { w: 100, h: 100, parts, mount: "shoulder" };
}

// ---------------------------------------------------------------------------

const BUILD: Record<RankId, () => InsigniaArt> = {
  private: () => ({ w: 100, h: 100, parts: [], mount: "none" }),
  private_second_class: () => chevronSet({ chevrons: 1, rockers: 0 }),
  private_first_class: () => chevronSet({ chevrons: 1, rockers: 1 }),
  specialist,
  corporal: () => chevronSet({ chevrons: 2, rockers: 0 }),
  sergeant: () => chevronSet({ chevrons: 3, rockers: 0 }),
  staff_sergeant: () => chevronSet({ chevrons: 3, rockers: 1 }),
  sergeant_first_class: () => chevronSet({ chevrons: 3, rockers: 2 }),
  master_sergeant: () => chevronSet({ chevrons: 3, rockers: 3 }),
  first_sergeant: () => chevronSet({ chevrons: 3, rockers: 3, device: "diamond" }),
  sergeant_major: () => chevronSet({ chevrons: 3, rockers: 3, device: "star" }),
  command_sergeant_major: () => chevronSet({ chevrons: 3, rockers: 3, device: "wreath" }),
  sergeant_major_of_the_army: () => chevronSet({ chevrons: 3, rockers: 3, device: "sma" }),
  second_lieutenant: () => ({ w: 100, h: 100, parts: bar(38, 62, "gold"), mount: "shoulder" }),
  first_lieutenant: () => ({ w: 100, h: 100, parts: bar(38, 62, "silver"), mount: "shoulder" }),
  captain: () => ({
    w: 100, h: 100, mount: "shoulder",
    parts: [...bar(22, 43, "silver"), ...bar(57, 78, "silver"),
      { pts: roundRect(42, 20, 58, 25, 1), finish: "silver" }, { pts: roundRect(42, 75, 58, 80, 1), finish: "silver" }],
  }),
  major: () => ({ w: 100, h: 100, parts: oakLeaf(50, "gold"), mount: "shoulder" }),
  lieutenant_colonel: () => ({ w: 100, h: 100, parts: oakLeaf(50, "silver"), mount: "shoulder" }),
  colonel: () => ({ w: 100, h: 100, parts: eagle(50, 52, 46, "silver"), mount: "shoulder" }),
  brigadier_general: () => stars(1),
  major_general: () => stars(2),
  lieutenant_general: () => stars(3),
  general: () => stars(4),
  general_of_the_army: fiveStars,
};

const cache = new Map<RankId, InsigniaArt>();

/** The insignia of a rank, built once. */
export function rankArt(rank: RankId): InsigniaArt {
  let art = cache.get(rank);
  if (!art) {
    art = BUILD[rank]();
    cache.set(rank, art);
  }
  return art;
}

/** The officer's cap badge: the gold eagle. */
export const CAP_BADGE_ART: InsigniaArt = { w: 100, h: 100, parts: eagle(50, 52, 46, "gold"), mount: "none" };

/** A unit crest for enlisted headwear: a gold disc ringed in gold with a star. */
export const CREST_ART: InsigniaArt = {
  w: 100, h: 100, mount: "none",
  parts: [
    { pts: ellipse(50, 50, 46, 46, 36), finish: "gold" },
    { pts: ellipse(50, 50, 36, 36, 36), finish: "cloth" },
    { pts: star(50, 52, 28), finish: "gold" },
  ],
};

/** Polygon points as an SVG `points` attribute. */
export function svgPoints(pts: readonly Pt[]): string {
  return pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
}

/** The colours each finish is drawn in: a light, a mid and a dark stop, plus the edge line. */
export const FINISH_TONES: Record<Finish, { light: string; mid: string; dark: string; edge: string }> = {
  gold: { light: "#fbe7a1", mid: "#d8a93f", dark: "#8f6518", edge: "#5c3f0a" },
  silver: { light: "#ffffff", mid: "#c9ced6", dark: "#7c8592", edge: "#3d434d" },
  cloth: { light: "#2a3558", mid: "#1b2340", dark: "#111731", edge: "#0a0e1f" },
};
