/**
 * The agents floor's wardrobe as a small boutique atelier: a light-oak
 * wardrobe wall with open hanging rails, folded knits, shoes and hats in the
 * real outfit colourways, a tall dressing mirror framed in warm bulbs, a
 * tailor's dummy wearing a half-finished jacket, a tufted sage bench, a brass
 * coat stand, a round wool rug and a brass ring pendant over it.
 *
 * Same rules as OfficeProps: every furniture piece is built in local space
 * centred on the origin, front facing +z, inside its `FURNITURE_SIZE` box.
 * The garments, folded clothes, shoes and hats of the wardrobe wall are merged
 * into one vertex-coloured geometry (one draw call); the oak carcass, the
 * brass and the light strips are one merged geometry each.
 */
import { memo } from "react";
import {
  BoxGeometry, BufferGeometry, Color, CylinderGeometry, Float32BufferAttribute, LatheGeometry, MeshStandardMaterial,
  SphereGeometry, TorusGeometry, Vector2,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { RoundedBoxGeometry } from "three-stdlib";
import { canvasMaterial } from "./canvasMaterials";
import { Box, matte, Rounded } from "./OfficeFurniture";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind } from "./officeLayout";
import { OUTFITS } from "./outfitCatalog";

type Vec3 = [number, number, number];

// ---------------------------------------------------------------------------
// Materials: pale Scandinavian oak, sage, linen and brass under warm light.
// ---------------------------------------------------------------------------

const C = {
  oak: "#d2b187",
  oakDark: "#a88660",
  backPanel: "#dfe3d6",
  linen: "#e8dfcf",
  sage: "#8d9f86",
  velvet: "#7e9378",
  brass: "#c9a25a",
  led: "#ffe2bd",
  bulb: "#fff0d2",
} as const;

const WM = {
  oak: matte(C.oak, { roughness: 0.6 }),
  oakDark: matte(C.oakDark, { roughness: 0.6 }),
  backPanel: matte(C.backPanel, { roughness: 0.95 }),
  linen: matte(C.linen, { roughness: 0.9 }),
  velvet: matte(C.velvet, { roughness: 1 }),
  brass: matte(C.brass, { roughness: 0.3, metalness: 0.65 }),
  // Everything textile on the wardrobe wall carries its own colour per vertex.
  textile: new MeshStandardMaterial({ color: "#ffffff", vertexColors: true, roughness: 0.9 }),
  // Light strips and bulbs: lit from within, so they read as light in any scene lighting.
  led: new MeshStandardMaterial({ color: C.led, emissive: C.led, emissiveIntensity: 1.2, toneMapped: false }),
  bulb: new MeshStandardMaterial({ color: C.bulb, emissive: C.bulb, emissiveIntensity: 1.6, toneMapped: false }),
  glass: new MeshStandardMaterial({ color: "#f3e6d0", transparent: true, opacity: 0.45, roughness: 0.05, depthWrite: false }),
  tape: matte("#e8c24a", { roughness: 0.7 }),
  leather: matte("#5c3622", { roughness: 0.55 }),
  scarf: matte("#b56a4c", { roughness: 1 }),
};

// ---------------------------------------------------------------------------
// Canvas faces
// ---------------------------------------------------------------------------

type Ctx = CanvasRenderingContext2D;

/** Deterministic pseudo-random numbers, so drawn patterns never change between loads. */
function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/** Mirror glass: a warm soft reflection of the room (an oak rail, a lit doorway) under two diagonal highlights. */
function drawMirror(ctx: Ctx, w: number, h: number): void {
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#d9e1e2");
  grad.addColorStop(0.55, "#b9c4c6");
  grad.addColorStop(1, "#a39d90");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
  // The reflected room, blurred: a doorway of warm light and an oak rail with garments.
  ctx.filter = "blur(6px)";
  ctx.fillStyle = "rgba(255,226,180,0.55)";
  ctx.fillRect(w * 0.55, h * 0.18, w * 0.3, h * 0.55);
  ctx.fillStyle = "rgba(168,134,96,0.45)";
  ctx.fillRect(0, h * 0.3, w * 0.45, h * 0.035);
  ["#3a3d44", "#5c3622", "#23304f", "#b08a5a", "#2f4a3a"].forEach((colour, i) => {
    ctx.fillStyle = colour;
    ctx.globalAlpha = 0.35;
    ctx.fillRect(i * w * 0.085, h * 0.34, w * 0.07, h * (0.2 + (i % 2) * 0.06));
  });
  ctx.globalAlpha = 1;
  ctx.filter = "none";
  ctx.fillStyle = "rgba(255,255,255,0.22)";
  ctx.beginPath();
  ctx.moveTo(w * 0.1, h); ctx.lineTo(w * 0.42, 0); ctx.lineTo(w * 0.56, 0); ctx.lineTo(w * 0.24, h);
  ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.12)";
  ctx.beginPath();
  ctx.moveTo(w * 0.34, h); ctx.lineTo(w * 0.66, 0); ctx.lineTo(w * 0.72, 0); ctx.lineTo(w * 0.4, h);
  ctx.fill();
}

/** Round wool rug: a cream field, a broad sage ring, a fine brass line and a soft inner medallion. */
function drawRoundRug(ctx: Ctx, w: number, h: number): void {
  const rand = lcg(2718);
  const cx = w / 2, cy = h / 2, r = w / 2;
  ctx.fillStyle = "#efe8da";
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 7000; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.18)" : "rgba(120,100,70,0.08)";
    ctx.fillRect(rand() * w, rand() * h, 2, 2);
  }
  const ring = (radius: number, width: number, colour: string) => {
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.stroke();
  };
  ring(r * 0.86, r * 0.1, C.sage);
  ring(r * 0.76, 3, C.brass);
  ring(r * 0.95, 3, "#b9ab92");
  // A medallion of eight soft petals round the centre.
  ctx.fillStyle = "rgba(141,159,134,0.28)";
  for (let i = 0; i < 8; i += 1) {
    const a = (i / 8) * Math.PI * 2;
    ctx.beginPath();
    ctx.ellipse(cx + Math.cos(a) * r * 0.22, cy + Math.sin(a) * r * 0.22, r * 0.14, r * 0.06, a, 0, Math.PI * 2);
    ctx.fill();
  }
  ring(r * 0.4, 2, "rgba(201,162,90,0.8)");
}

const faces = {
  mirror: () => canvasMaterial("wardrobe:mirror", 160, 384, drawMirror, { glow: 0.2, fallback: "#b9c4c6", roughness: 0.08 }),
  rug: () => canvasMaterial("wardrobe:round-rug", 512, 512, drawRoundRug, { fallback: "#efe8da", roughness: 1 }),
};

// ---------------------------------------------------------------------------
// Shared geometry
// ---------------------------------------------------------------------------

/** The dress form's torso as a lathe profile (radius, height): shoulders, bust, waist, hips. */
const TORSO_PROFILE = [
  [0, 0.84], [0.12, 0.85], [0.155, 0.93], [0.15, 1.02], [0.125, 1.12], [0.13, 1.2], [0.16, 1.3], [0.168, 1.38],
  [0.158, 1.46], [0.11, 1.52], [0.05, 1.55], [0.042, 1.6], [0.04, 1.64], [0, 1.65],
] as const;

const GEO = {
  cyl: new CylinderGeometry(1, 1, 1, 20),
  torso: new LatheGeometry(TORSO_PROFILE.map(([r, y]) => new Vector2(r, y)), 32).scale(1, 1, 0.72),
  // A half-made jacket: the upper torso a little wider, covering the front and one side.
  jacket: new LatheGeometry(TORSO_PROFILE.slice(4, 11).map(([r, y]) => new Vector2(r + 0.012, y)), 24, -0.2, Math.PI * 1.15).scale(1, 1, 0.74),
  watch: new TorusGeometry(0.028, 0.006, 8, 20),
  ring: new TorusGeometry(0.42, 0.016, 10, 64),
  ringLight: new TorusGeometry(0.42, 0.007, 8, 64),
  rug: new CylinderGeometry(0.5, 0.5, 1, 72),
  button: new SphereGeometry(1, 8, 6),
};

// ---------------------------------------------------------------------------
// Merged geometry helpers
// ---------------------------------------------------------------------------

/** A unit-free box placed at (x, y, z), optionally turned about y. */
function box(w: number, h: number, d: number, x: number, y: number, z: number, rotY = 0): BufferGeometry {
  const geometry = new BoxGeometry(w, h, d);
  if (rotY) geometry.rotateY(rotY);
  return geometry.translate(x, y, z);
}

function rounded(w: number, h: number, d: number, r: number, x: number, y: number, z: number): BufferGeometry {
  return new RoundedBoxGeometry(w, h, d, 2, r).translate(x, y, z);
}

function cyl(rTop: number, rBottom: number, h: number, x: number, y: number, z: number, segments = 20): BufferGeometry {
  return new CylinderGeometry(rTop, rBottom, h, segments).translate(x, y, z);
}

/** Merge parts into one geometry; indexed and non-indexed parts may mix. */
function merge(parts: BufferGeometry[]): BufferGeometry {
  const flat = parts.map((p) => (p.index ? p.toNonIndexed() : p));
  const merged = mergeGeometries(flat, false);
  parts.forEach((p) => p.dispose());
  flat.forEach((p) => p.dispose());
  return merged;
}

/** Merge parts that each carry one colour into a single vertex-coloured geometry. */
function mergePainted(parts: [BufferGeometry, string][]): BufferGeometry {
  const colour = new Color();
  return merge(parts.map(([part, hex]) => {
    const geometry = part.index ? part.toNonIndexed() : part;
    if (geometry !== part) part.dispose();
    colour.set(hex).convertSRGBToLinear();
    const count = geometry.getAttribute("position").count;
    const colours = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) colours.set([colour.r, colour.g, colour.b], i * 3);
    geometry.setAttribute("color", new Float32BufferAttribute(colours, 3));
    return geometry;
  }));
}

// ---------------------------------------------------------------------------
// The wardrobe wall
// ---------------------------------------------------------------------------

const WALL = FURNITURE_SIZE.wardrobeWall;
const UPRIGHT = 0.03;
/** Interior widths of the four bays, west to east: long rail, shelves, double rail, drawers + shoe display. */
const BAY_WIDTHS = [1.25, 0.86, 1.25, 0.85] as const;
const BAYS = BAY_WIDTHS.reduce<{ minX: number; maxX: number; cx: number }[]>((acc, width) => {
  const minX = (acc.length ? acc[acc.length - 1].maxX : -WALL.w / 2) + UPRIGHT;
  acc.push({ minX, maxX: minX + width, cx: minX + width / 2 });
  return acc;
}, []);
const CARCASS = { back: -0.28, front: 0.29, plinth: 0.08, top: 1.97 } as const;
/** Shelf tops in the shelving bay and in the display bay. */
const SHELVES_B = [0.46, 0.86, 1.26, 1.66];
const SHELVES_D = [0.74, 1.06, 1.38, 1.7];

const colourways = (id: string) => OUTFITS.find((o) => o.id === id)?.colourways ?? [];
const unique = (list: string[]) => [...new Set(list.map((c) => c.toLowerCase()))];

/**
 * Order items so light and dark colours alternate along a rail: the catalogue
 * lists each outfit's dark colourways together, which reads as a black band.
 */
function byLightness<T>(items: T[], colour: (item: T) => string): T[] {
  const tone = new Color();
  const lum = (item: T) => {
    tone.set(colour(item));
    return tone.r * 0.3 + tone.g * 0.59 + tone.b * 0.11;
  };
  const sorted = [...items].sort((a, b) => lum(b) - lum(a));
  const out: T[] = [];
  for (let lo = 0, hi = sorted.length - 1; lo <= hi; lo += 1, hi -= 1) {
    out.push(sorted[lo]);
    if (hi !== lo) out.push(sorted[hi]);
  }
  return out;
}

/** Hanging garments on a rail at `railY`, seen edge-on: body, a lapel of the shirt underneath, a hanger. */
function railGarments(
  bay: { cx: number }, railY: number, garments: { colour: string; inner?: string; length: number }[],
): [BufferGeometry, string][] {
  const pitch = 0.108;
  const start = bay.cx - ((garments.length - 1) * pitch) / 2;
  return garments.flatMap(({ colour, inner, length }, i) => {
    const top = railY - 0.05;
    const parts: [BufferGeometry, string][] = [
      [rounded(0.06, length, 0.44, 0.025, 0, top - length / 2, 0), colour],
      // Shoulders: a slightly wider band at the top, so each piece reads as a garment and not a board.
      [rounded(0.07, 0.08, 0.46, 0.03, 0, top - 0.05, 0), colour],
      [box(0.012, 0.02, 0.4, 0, railY - 0.035, 0), C.oakDark],
      [box(0.008, 0.05, 0.008, 0, railY - 0.005, 0), C.brass],
    ];
    if (inner) parts.push([box(0.062, 0.22, 0.02, 0, top - 0.2, 0.215), inner]);
    // Each piece hangs a little askew, as on a rail people actually browse.
    const turn = (((i * 37) % 5) - 2) * 0.07;
    for (const [geometry] of parts) geometry.rotateY(turn).translate(start + i * pitch, 0, 0);
    return parts;
  });
}

/** A stack of folded clothes, bottom first, on a shelf top at `y`. */
function foldedStack(x: number, y: number, z: number, colours: string[]): [BufferGeometry, string][] {
  return colours.map((colour, i) => [rounded(0.3 - (i % 2) * 0.01, 0.05, 0.27, 0.018, x + ((i * 7) % 3 - 1) * 0.006, y + 0.026 + i * 0.052, z), colour]);
}

/** A pair of shoes side by side, toes to +z, on a surface at `y`. */
function shoePair(x: number, y: number, z: number, colour: string, sole = "#2a2522"): [BufferGeometry, string][] {
  return [-0.05, 0.05].flatMap((dx) => [
    [rounded(0.085, 0.07, 0.24, 0.03, x + dx, y + 0.045, z), colour],
    [box(0.09, 0.014, 0.25, x + dx, y + 0.007, z), sole],
  ] satisfies [BufferGeometry, string][]);
}

/** A fedora: a crown with a band on a flat brim. */
function hat(x: number, y: number, z: number, colour: string, band: string): [BufferGeometry, string][] {
  return [
    [cyl(0.15, 0.15, 0.012, x, y + 0.006, z, 28), colour],
    [cyl(0.075, 0.09, 0.1, x, y + 0.062, z), colour],
    [cyl(0.092, 0.092, 0.022, x, y + 0.024, z), band],
  ];
}

/** Every textile on the wall, coloured from the outfit catalogue, as one geometry. */
const TEXTILE_GEOMETRY = (() => {
  const [bayA, bayB, bayC, bayD] = BAYS;
  const parts: [BufferGeometry, string][] = [];
  // Bay A: jackets, suits and leather in their real colourways, lapels showing the shirt beneath.
  const long = byLightness([...colourways("suit"), ...colourways("blazer"), ...colourways("leather")], (w) => w.primary).slice(0, 10);
  parts.push(...railGarments(bayA, 1.8, long.map((w, i) => ({ colour: w.primary, inner: w.inner, length: 0.78 + ((i * 5) % 4) * 0.07 }))));
  // Under the jackets, the shoes that go with them on a low oak rack.
  unique(long.map((w) => w.shoes)).slice(0, 4).forEach((colour, i) => {
    parts.push(...shoePair(bayA.minX + 0.18 + i * 0.3, 0.13, 0.06, colour));
  });
  // Bay B: folded knits and tees, trainers at the bottom, hats on the top shelf.
  const knits = [...colourways("turtleneck"), ...colourways("hoodie"), ...colourways("tee")].map((w) => w.primary);
  const stacks = [knits.slice(0, 4), knits.slice(4, 8), knits.slice(8, 11), [knits[1], knits[5], knits[9]]];
  [SHELVES_B[0], SHELVES_B[1]].forEach((y, row) => {
    parts.push(...foldedStack(bayB.cx - 0.2, y, 0.02, stacks[row * 2]));
    parts.push(...foldedStack(bayB.cx + 0.2, y, 0.02, stacks[row * 2 + 1]));
  });
  parts.push(...foldedStack(bayB.cx - 0.2, SHELVES_B[2], 0.02, colourways("quarterzip").map((w) => w.primary)));
  // A woven basket of rolled ties beside the quarter-zips.
  parts.push([rounded(0.28, 0.16, 0.26, 0.02, bayB.cx + 0.2, SHELVES_B[2] + 0.08, 0.02), "#c7a57a"]);
  unique(OUTFITS.flatMap((o) => o.colourways.map((w) => w.accent))).slice(0, 4).forEach((colour, i) => {
    parts.push([cyl(0.028, 0.028, 0.06, bayB.cx + 0.1 + i * 0.065, SHELVES_B[2] + 0.18, 0.02, 12), colour]);
  });
  parts.push(...shoePair(bayB.cx - 0.2, CARCASS.plinth, 0.04, "#f4f4f2", "#d9d6cf"));
  parts.push(...shoePair(bayB.cx + 0.2, CARCASS.plinth, 0.04, "#3b2418"));
  parts.push(...hat(bayB.cx - 0.2, SHELVES_B[3], 0.0, "#b08a5a", "#3b2418"));
  parts.push(...hat(bayB.cx + 0.2, SHELVES_B[3], 0.0, "#2b2f38", "#8b1e2d"));
  // Bay C: a double hang — shirts and quarter-zips above, trousers folded over hangers below.
  const tops = byLightness([
    ...colourways("vest").map((w) => ({ colour: w.primary, inner: w.inner })),
    ...colourways("quarterzip").map((w) => ({ colour: w.primary, inner: w.inner })),
    ...colourways("turtleneck").map((w) => ({ colour: w.primary })),
    ...unique(colourways("suit").map((w) => w.inner)).map((colour) => ({ colour })),
  ], (g) => g.colour).slice(0, 10).map((g, i) => ({ ...g, length: 0.6 + ((i * 3) % 3) * 0.04 }));
  parts.push(...railGarments(bayC, 1.86, tops));
  const trousers = byLightness(unique(OUTFITS.flatMap((o) => o.colourways.map((w) => w.secondary))), (c) => c).slice(0, 10);
  parts.push(...railGarments(bayC, 1.0, trousers.map((colour) => ({ colour, length: 0.42 }))));
  // Bay D: shoes displayed on lit shelves, folded scarves and a leather tote on top.
  const dress = unique(OUTFITS.flatMap((o) => o.colourways.map((w) => w.shoes)));
  [SHELVES_D[0], SHELVES_D[1], SHELVES_D[2]].forEach((y, row) => {
    parts.push(...shoePair(bayD.cx - 0.19, y, 0.03, dress[(row * 2) % dress.length]));
    parts.push(...shoePair(bayD.cx + 0.19, y, 0.03, dress[(row * 2 + 3) % dress.length]));
  });
  parts.push(...foldedStack(bayD.cx - 0.2, SHELVES_D[3], 0.02, [C.sage, "#b56a4c", C.linen]));
  parts.push([rounded(0.26, 0.2, 0.1, 0.02, bayD.cx + 0.2, SHELVES_D[3] + 0.1, 0.0), "#5c3622"]);
  return mergePainted(parts);
})();

/** The oak carcass: uprights, shelves, a plinth, a cornice and three drawer fronts. */
const OAK_GEOMETRY = (() => {
  const depth = CARCASS.front - CARCASS.back;
  const midZ = (CARCASS.front + CARCASS.back) / 2;
  const parts: BufferGeometry[] = [];
  const edges = [-WALL.w / 2 + UPRIGHT / 2, ...BAYS.map((b) => b.maxX + UPRIGHT / 2)];
  for (const x of edges) parts.push(box(UPRIGHT, CARCASS.top, depth, x, CARCASS.top / 2, midZ));
  parts.push(box(WALL.w, CARCASS.plinth, depth - 0.03, 0, CARCASS.plinth / 2, midZ - 0.015));
  parts.push(box(WALL.w, 0.05, depth + 0.02, 0, CARCASS.top + 0.025, midZ + 0.01));
  const [bayA, bayB, , bayD] = BAYS;
  for (const y of SHELVES_B) parts.push(box(BAY_WIDTHS[1], 0.022, depth - 0.02, bayB.cx, y - 0.011, midZ));
  for (const y of SHELVES_D) parts.push(box(BAY_WIDTHS[3], 0.022, depth - 0.02, bayD.cx, y - 0.011, midZ));
  // The low shoe rack under the jackets, tilted towards the room.
  parts.push(box(BAY_WIDTHS[0] - 0.04, 0.02, 0.3, bayA.cx, 0.12, 0.06));
  // Drawers in the display bay.
  for (let i = 0; i < 3; i += 1) parts.push(box(BAY_WIDTHS[3] - 0.02, 0.19, 0.02, bayD.cx, CARCASS.plinth + 0.105 + i * 0.205, CARCASS.front - 0.01));
  return merge(parts);
})();

/** Brass: hanging rails, drawer pulls and the thin edge on the cornice. */
const BRASS_GEOMETRY = (() => {
  const [bayA, , bayC, bayD] = BAYS;
  const rail = (bay: { cx: number }, y: number) => new CylinderGeometry(0.011, 0.011, BAY_WIDTHS[0], 12).rotateZ(Math.PI / 2).translate(bay.cx, y, 0);
  const parts: BufferGeometry[] = [rail(bayA, 1.8), rail(bayC, 1.86), rail(bayC, 1.0)];
  for (let i = 0; i < 3; i += 1) parts.push(box(0.16, 0.014, 0.018, bayD.cx, CARCASS.plinth + 0.15 + i * 0.205, CARCASS.front + 0.009));
  parts.push(box(WALL.w, 0.008, 0.008, 0, CARCASS.top + 0.004, CARCASS.front + 0.016));
  return merge(parts);
})();

/** Warm light strips under the cornice of every bay and under the display shelves. */
const LED_GEOMETRY = (() => {
  const parts = BAYS.map((b, i) => box(BAY_WIDTHS[i] - 0.06, 0.006, 0.02, b.cx, CARCASS.top - 0.004, CARCASS.front - 0.05));
  const bayD = BAYS[3];
  for (const y of SHELVES_D.slice(1)) parts.push(box(BAY_WIDTHS[3] - 0.06, 0.005, 0.015, bayD.cx, y - 0.025, CARCASS.front - 0.05));
  return merge(parts);
})();

/**
 * The north wall: an open light-oak wardrobe on a pale sage back panel. West
 * to east — a long rail of jackets and suits over a shoe rack; shelves of
 * folded knits with hats on top; a double rail of shirts over trousers;
 * drawers under a lit shoe display. Every colour is a real outfit colourway.
 */
function WardrobeWall() {
  return (
    <group>
      <Box size={[WALL.w, CARCASS.top, 0.025]} position={[0, CARCASS.top / 2, CARCASS.back - 0.0125]} material={WM.backPanel} />
      <mesh geometry={OAK_GEOMETRY} material={WM.oak} castShadow receiveShadow />
      <mesh geometry={BRASS_GEOMETRY} material={WM.brass} castShadow />
      <mesh geometry={TEXTILE_GEOMETRY} material={WM.textile} castShadow receiveShadow />
      <mesh geometry={LED_GEOMETRY} material={WM.led} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Mirror, dummy, bench, coat stand, rug
// ---------------------------------------------------------------------------

const MIRROR = { frameW: 0.86, frameH: 1.84, y: 1.0, bulbX: 0.48 } as const;

/** Hollywood bulbs up both sides of the mirror and across its top, merged into one geometry. */
const BULB_GEOMETRY = (() => {
  const parts: BufferGeometry[] = [];
  for (let i = 0; i < 7; i += 1) {
    const y = 0.32 + i * 0.24;
    for (const x of [-MIRROR.bulbX, MIRROR.bulbX]) parts.push(new SphereGeometry(0.028, 12, 8).translate(x, y, -0.085));
  }
  for (let i = 0; i < 5; i += 1) parts.push(new SphereGeometry(0.028, 12, 8).translate(-0.36 + i * 0.18, 1.99, -0.085));
  return merge(parts);
})();

/**
 * A tall free-standing dressing mirror: an oak frame on two splayed feet, a
 * row of warm bulbs on brass strips round three sides, and a small ledge
 * with a perfume bottle and a watch; the glass faces +z.
 */
function DressingMirror() {
  return (
    <group>
      {[-0.4, 0.4].map((x) => <Box key={x} size={[0.06, 0.04, 0.28]} position={[x, 0.02, 0]} material={WM.oakDark} />)}
      <Rounded size={[MIRROR.frameW, MIRROR.frameH, 0.05]} radius={0.02} position={[0, MIRROR.y, -0.12]} material={WM.oak} />
      <mesh position={[0, MIRROR.y, -0.0945]} material={faces.mirror()}><planeGeometry args={[MIRROR.frameW - 0.1, MIRROR.frameH - 0.1]} /></mesh>
      {[-MIRROR.bulbX, MIRROR.bulbX].map((x) => (
        <Box key={x} size={[0.04, 1.72, 0.03]} position={[x, 1.06, -0.12]} material={WM.brass} />
      ))}
      <Box size={[0.96, 0.035, 0.03]} position={[0, 1.99, -0.12]} material={WM.brass} />
      <mesh geometry={BULB_GEOMETRY} material={WM.bulb} />
      {/* The ledge with a perfume bottle and a watch. */}
      <Box size={[0.62, 0.022, 0.13]} position={[0, 0.92, -0.035]} material={WM.oakDark} />
      <mesh geometry={GEO.cyl} material={WM.glass} position={[-0.18, 0.98, -0.04]} scale={[0.03, 0.1, 0.03]} />
      <mesh geometry={GEO.cyl} material={WM.brass} position={[-0.18, 1.04, -0.04]} scale={[0.012, 0.025, 0.012]} />
      <mesh geometry={GEO.watch} material={WM.leather} position={[0.14, 0.935, -0.03]} rotation={[Math.PI / 2, 0, 0]} />
      <mesh geometry={GEO.cyl} material={WM.brass} position={[0.14, 0.94, -0.03]} scale={[0.022, 0.008, 0.022]} />
    </group>
  );
}

/**
 * A tailor's dummy on an oak tripod: a linen torso on a brass pole, wearing
 * the front of a sage jacket in progress, a yellow tape measure round the
 * neck and a brass knob on top.
 */
function TailorDummy() {
  return (
    <group>
      {[0, 1, 2].map((i) => (
        <group key={i} rotation={[0, (i * Math.PI * 2) / 3 + Math.PI / 6, 0]}>
          <Box size={[0.035, 0.03, 0.24]} position={[0, 0.06, 0.11]} material={WM.oakDark} />
        </group>
      ))}
      <mesh geometry={GEO.cyl} material={WM.oakDark} position={[0, 0.1, 0]} scale={[0.04, 0.08, 0.04]} castShadow />
      <mesh geometry={GEO.cyl} material={WM.brass} position={[0, 0.48, 0]} scale={[0.012, 0.76, 0.012]} castShadow />
      <mesh geometry={GEO.torso} material={WM.linen} castShadow receiveShadow />
      <mesh geometry={GEO.jacket} material={WM.velvet} castShadow />
      <mesh geometry={GEO.button} material={WM.brass} position={[0, 1.7, 0]} scale={0.04} castShadow />
      {[-0.055, 0.055].map((x) => (
        <group key={x} position={[x, 1.4, 0.1]} rotation={[-0.2, 0, x > 0 ? -0.08 : 0.08]}>
          <Box size={[0.014, 0.3, 0.004]} position={[0, 0, 0]} material={WM.tape} cast={false} />
        </group>
      ))}
    </group>
  );
}

/** Tufting buttons on the bench cushion: two rows of five. */
const TUFT_GEOMETRY = merge(Array.from({ length: 10 }, (_, i) =>
  new SphereGeometry(0.014, 8, 6).translate(-0.48 + (i % 5) * 0.24, 0.44, i < 5 ? -0.1 : 0.1)));

/**
 * An upholstered bench: a tufted sage velvet cushion on an oak frame with a
 * slatted shoe shelf below; a folded sweater on top, loafers underneath.
 */
function DressingBench() {
  return (
    <group>
      {[-0.58, 0.58].flatMap((x) => [-0.19, 0.19].map((z) => (
        <Box key={`${x}:${z}`} size={[0.045, 0.3, 0.045]} position={[x, 0.15, z]} material={WM.oak} />
      )))}
      <Box size={[1.22, 0.04, 0.42]} position={[0, 0.28, 0]} material={WM.oak} />
      {[-0.12, 0, 0.12].map((z) => <Box key={z} size={[1.16, 0.015, 0.08]} position={[0, 0.09, z]} material={WM.oak} />)}
      <Rounded size={[1.28, 0.14, 0.48]} radius={0.05} position={[0, 0.37, 0]} material={WM.velvet} />
      <mesh geometry={TUFT_GEOMETRY} material={WM.velvet} />
      <Rounded size={[0.34, 0.05, 0.28]} radius={0.018} position={[0.36, 0.465, 0]} material={WM.linen} />
      {[-0.05, 0.05].map((x) => (
        <Rounded key={x} size={[0.085, 0.07, 0.24]} radius={0.03} position={[-0.3 + x, 0.14, 0]} material={WM.leather} />
      ))}
    </group>
  );
}

/** A brass-and-oak coat stand with a fedora on top, a rust scarf and a leather tote on its hooks. */
function CoatStand() {
  return (
    <group>
      <mesh geometry={GEO.cyl} material={WM.oakDark} position={[0, 0.015, 0]} scale={[0.2, 0.03, 0.2]} castShadow receiveShadow />
      <mesh geometry={GEO.cyl} material={WM.oak} position={[0, 0.88, 0]} scale={[0.02, 1.72, 0.02]} castShadow />
      {[0, 1, 2, 3].map((i) => (
        <group key={i} position={[0, 1.6, 0]} rotation={[0, (i * Math.PI) / 2 + Math.PI / 4, 0]}>
          <group rotation={[0.7, 0, 0]}><Box size={[0.012, 0.012, 0.14]} position={[0, 0, 0.07]} material={WM.brass} cast={false} /></group>
        </group>
      ))}
      <mesh geometry={GEO.cyl} material={WM.oakDark} position={[0, 1.755, 0]} scale={[0.15, 0.012, 0.15]} castShadow />
      <mesh geometry={GEO.cyl} material={WM.oakDark} position={[0, 1.81, 0]} scale={[0.085, 0.1, 0.085]} castShadow />
      <mesh geometry={GEO.cyl} material={WM.leather} position={[0, 1.775, 0]} scale={[0.088, 0.022, 0.088]} />
      <Box size={[0.07, 0.62, 0.02]} position={[0.12, 1.28, 0.06]} material={WM.scarf} />
      <Box size={[0.2, 0.24, 0.07]} position={[-0.12, 1.22, -0.05]} material={WM.leather} />
      <Box size={[0.012, 0.16, 0.012]} position={[-0.12, 1.42, -0.05]} material={WM.leather} cast={false} />
    </group>
  );
}

/** A round wool rug, sized per instance (w × d). */
function RoundRug({ w, d }: { w: number; d: number }) {
  return <mesh geometry={GEO.rug} material={faces.rug()} position={[0, 0.007, 0]} scale={[w, 0.014, d]} receiveShadow />;
}

/** Renderers of the wardrobe's furniture kinds; merged into OfficeProps' exhaustive table. */
export const WARDROBE_RENDERERS = {
  wardrobeWall: () => <WardrobeWall />,
  dressingMirror: () => <DressingMirror />,
  tailorDummy: () => <TailorDummy />,
  dressingBench: () => <DressingBench />,
  coatStand: () => <CoatStand />,
  roundRug: ({ item }: { item: Furniture }) => {
    const size = item.size ?? FURNITURE_SIZE.roundRug;
    return <RoundRug w={size.w} d={size.d} />;
  },
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;

/**
 * What hangs in the wardrobe rather than stands in it: a brass ring pendant
 * over the round rug with a warm light strip underneath and one warm light.
 */
export const WardrobeFittings = memo(function WardrobeFittings({ rug }: { rug: Pick<Furniture, "x" | "z"> }) {
  const at: Vec3 = [rug.x, 0, rug.z];
  return (
    <group position={at}>
      <mesh geometry={GEO.ring} material={WM.brass} position={[0, 2.02, 0]} rotation={[Math.PI / 2, 0, 0]} castShadow />
      <mesh geometry={GEO.ringLight} material={WM.led} position={[0, 2.0, 0]} rotation={[Math.PI / 2, 0, 0]} />
      {[0, 1, 2].map((i) => {
        const a = (i * Math.PI * 2) / 3;
        return <Box key={i} size={[0.005, 0.7, 0.005]} position={[Math.cos(a) * 0.42, 2.37, Math.sin(a) * 0.42]} material={WM.brass} cast={false} />;
      })}
      <pointLight position={[0, 1.85, 0]} color="#ffd9ad" intensity={4.5} distance={5} decay={2} />
    </group>
  );
});
