/**
 * The toy-figure look and its procedural animation, as pure data.
 *
 * The office characters are chunky avatar-app toys: a very big round head,
 * a short rounded torso, stubby arms and short legs in chunky sneakers. This
 * module holds everything that is not rendering — the colour look derived from
 * a figure recipe, the rig dimensions, the per-mode pose and a tiny forward
 * kinematics helper — so `ToyFigure.tsx` only applies numbers to joints and the
 * tests can check the poses without a WebGL context.
 *
 * Conventions (figure-local, metres, designed at TOY_HEIGHT = 1.3 m):
 * - Origin on the floor, the figure faces local +z, its right side is local -x.
 * - Every joint angle is a raw three.js Euler value (order XYZ), exactly what the
 *   component writes into `rotation`. For a limb hanging along -y a positive
 *   x-rotation swings it backwards (-z), a negative one forwards (+z).
 * - `hipY` is the bottom of the pelvis (the point that rests on a seat);
 *   the hip joints sit HIP_UP above it.
 */

import { recipeKey, type FigureRecipe, type Palette } from "../figures/figureRecipe";
import type { FigureMode } from "../figures/FigureRig";
import { OUTFITS, type Colourway } from "./outfitCatalog";

// ---------------------------------------------------------------------------
// Look

export type HairStyle =
  | "short" | "spiky" | "bun" | "curly" | "long" | "beanie" | "cap" | "bald"
  | "slick" | "sidepart" | "buzz" | "ponytail";

/**
 * What the figure wears on its upper body. "tee" is the original casual look
 * and the default for every recipe that names no outfit.
 */
export type OutfitId = "tee" | "hoodie" | "suit" | "leather" | "vest" | "turtleneck" | "blazer" | "quarterzip";

export const OUTFIT_IDS: readonly OutfitId[] = ["tee", "hoodie", "suit", "leather", "vest", "turtleneck", "blazer", "quarterzip"];

export type Eyewear = "none" | "glasses" | "shades";

export const EYEWEAR: readonly Eyewear[] = ["none", "glasses", "shades"];

export interface ToyLook {
  skin: string;
  hair: string;
  hairStyle: HairStyle;
  /** The main upper-body garment: tee, hoodie, jacket, vest or knit. */
  shirt: string;
  /** Emblem, tie, drawstrings or pocket square, depending on the outfit. */
  shirtAccent: string;
  /** The shirt worn under a jacket or vest (collar, V-opening, sleeves under a vest). */
  inner: string;
  pants: string;
  shoes: string;
  blush: boolean;
  outfit: OutfitId;
  eyewear: Eyewear;
}

/** Natural skin tones, light to dark; the fallback and the snap target for fantasy skins. */
export const SKIN_TONES = ["#f6d3b8", "#f1c4a0", "#e9b48e", "#d8a37c", "#c68a62", "#a8704c", "#8a5a3c", "#6b4430"] as const;
const HAIR_COLOURS = ["#2a1d15", "#3b2a20", "#6b4226", "#9e5d47", "#d9b36c", "#c9c9c9", "#1d1d24"] as const;
const SHIRT_COLOURS = ["#6fbf5a", "#3f7fd6", "#e5674f", "#f2c14e", "#8a63d2", "#2fb3a6", "#ef8fb3", "#2b2b33"] as const;
const PANTS_COLOURS = ["#2f4a7a", "#3b3f4a", "#23304f", "#5a4636", "#46505e"] as const;
const DEFAULT_SHOES = "#f4f4f2";

/** Weighted so plain styles dominate and a bald figure stays rare. */
const HAIR_STYLE_WHEEL: readonly HairStyle[] = [
  "short", "short", "short", "spiky", "spiky", "bun", "curly", "curly", "long", "long", "beanie", "cap", "cap", "bald",
];

/** Stable 32-bit FNV-1a hash of a string. */
export function hashString(value: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

function pick<T>(list: readonly T[], hash: number, salt: number): T {
  return list[(Math.imul(hash ^ salt, 0x9e3779b1) >>> 0) % list.length];
}

function parseHex(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function validColour(value: string | undefined): string | null {
  return value && parseHex(value) ? value : null;
}

/** True when a colour could be a human skin tone (warm hue, moderate saturation). */
export function isNaturalSkin(hex: string): boolean {
  const rgb = parseHex(hex);
  if (!rgb) return false;
  const [r, g, b] = rgb.map((c) => c / 255) as [number, number, number];
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return false;
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = 0;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h *= 60;
  if (h < 0) h += 360;
  return h >= 8 && h <= 48 && s >= 0.15 && s <= 0.85 && l >= 0.2 && l <= 0.9;
}

function luminance(hex: string): number {
  const rgb = parseHex(hex) ?? [200, 160, 130];
  return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255;
}

/** The natural tone closest in brightness to a (possibly fantasy) skin colour. */
function naturalSkinFor(hex: string): string {
  const target = luminance(hex);
  let best: string = SKIN_TONES[0];
  let bestDist = Infinity;
  for (const tone of SKIN_TONES) {
    const dist = Math.abs(luminance(tone) - target);
    if (dist < bestDist) { best = tone; bestDist = dist; }
  }
  return best;
}

/** Every hair style the figure can draw; the hash wheel above only picks from the original eight. */
export const HAIR_STYLES: readonly HairStyle[] = [
  "short", "slick", "sidepart", "buzz", "spiky", "curly", "long", "ponytail", "bun", "beanie", "cap", "bald",
];

/** The hair the recipe names explicitly, when it is one this figure can draw. */
function chosenHair(recipe: FigureRecipe | null): HairStyle | null {
  const value = recipe?.hairStyle;
  return typeof value === "string" && (HAIR_STYLES as readonly string[]).includes(value) ? (value as HairStyle) : null;
}

function chosenOutfit(recipe: FigureRecipe | null): OutfitId | null {
  const value = recipe?.outfit;
  return typeof value === "string" && (OUTFIT_IDS as readonly string[]).includes(value) ? (value as OutfitId) : null;
}

/** Office styles for a figure nobody dressed yet; weighted so a plain T-shirt stays rare. */
const OFFICE_OUTFIT_WHEEL: readonly OutfitId[] = [
  "suit", "suit", "blazer", "blazer", "vest", "vest", "quarterzip", "quarterzip", "turtleneck", "leather", "hoodie", "tee",
];

/** The default office look for an undressed recipe: an outfit and one of its colourways. */
function officeLook(seed: number): { outfit: OutfitId; way: Colourway } {
  const outfit = pick(OFFICE_OUTFIT_WHEEL, seed, 0x5e);
  const ways = OUTFITS.find((o) => o.id === outfit)!.colourways;
  return { outfit, way: pick(ways, seed, 0x6f) };
}

function chosenEyewear(recipe: FigureRecipe | null): Eyewear {
  const value = recipe?.eyewear;
  return typeof value === "string" && (EYEWEAR as readonly string[]).includes(value) ? (value as Eyewear) : "none";
}

/** The shirt under a jacket when the recipe names none: a crisp white. */
const DEFAULT_INNER = "#f2f2ee";

/**
 * The toy look for a stored figure. Hair and the default office outfit come
 * from the recipe itself (its own fields, else a hash of the recipe), never
 * from the agent id, so the creator's preview and the office always draw the
 * same person. A recipe that names an outfit wears it in its palette colours
 * (primary → garment, accent → tie / trim, secondary → trousers, shoes); one
 * that names none wears an office outfit in a curated colourway. `identity`
 * only fills blush and colours a missing recipe leaves open.
 */
export function toyLookFor(recipe: FigureRecipe | null, identity: string): ToyLook {
  const hash = hashString(identity || "toy");
  const palette: Partial<Palette> = recipe?.palette ?? {};
  const rawSkin = validColour(palette.skin);
  const skin = rawSkin ? (isNaturalSkin(rawSkin) ? rawSkin : naturalSkinFor(rawSkin)) : pick(SKIN_TONES, hash, 0x51);
  const seed = recipe ? hashString(recipeKey({ ...recipe, hairStyle: undefined, outfit: undefined, inner: undefined, eyewear: undefined })) : hash;
  const explicit = chosenOutfit(recipe);
  const office = explicit ? null : officeLook(seed);
  return {
    skin,
    hair: validColour(palette.hair) ?? pick(HAIR_COLOURS, hash, 0x7a),
    hairStyle: chosenHair(recipe) ?? pick(HAIR_STYLE_WHEEL, seed, 0x3c),
    shirt: office?.way.primary ?? validColour(palette.primary) ?? pick(SHIRT_COLOURS, hash, 0x19),
    shirtAccent: office?.way.accent ?? validColour(palette.accent) ?? pick(SHIRT_COLOURS, hash, 0x2d),
    pants: office?.way.secondary ?? validColour(palette.secondary) ?? pick(PANTS_COLOURS, hash, 0x44),
    inner: office?.way.inner ?? validColour(recipe?.inner) ?? DEFAULT_INNER,
    shoes: office?.way.shoes ?? validColour(palette.shoes) ?? DEFAULT_SHOES,
    blush: (hash & 0x3) !== 0,
    outfit: explicit ?? office!.outfit,
    eyewear: chosenEyewear(recipe),
  };
}

// ---------------------------------------------------------------------------
// Rig dimensions (metres at TOY_HEIGHT; the component scales uniformly)

export const TOY_HEIGHT = 1.3;
export const TOY = {
  /** Pelvis: bottom at hipY, hip joints HIP_UP above it. */
  pelvis: { w: 0.3, h: 0.14, d: 0.2 },
  hipUp: 0.06,
  hipX: 0.085,
  thigh: 0.18,
  shin: 0.14,
  /** Ankle joint to sole. */
  sole: 0.06,
  /** Shoe extent along z around the ankle (heel behind, toe in front). */
  heel: 0.06,
  toe: 0.12,
  legRadius: 0.062,
  /** Torso pivot = hip-joint level; chest block spans 0.02 … 0.28 above it. */
  torso: { w: 0.34, h: 0.28, d: 0.22 },
  neckY: 0.3,
  /** Shoulder joint: tucked into the chest's rounded edge so the arm grows out of the body. */
  shoulderX: 0.2,
  shoulderY: 0.225,
  upperArm: 0.14,
  /** Elbow to hand centre. */
  foreArm: 0.13,
  /** Upper-arm radius; the forearm tapers slightly below it. */
  armRadius: 0.054,
  handRadius: 0.056,
  /** Head ellipsoid radii; its centre sits `head.y` above the neck pivot. */
  head: { rx: 0.31, ry: 0.29, rz: 0.25, y: 0.3 },
  /** Hair or a hat adds at most this much behind the head. */
  hairBack: 0.035,
} as const;

/** Standing hip-joint height: leg straight down to the sole. */
export const TOY_LEG = TOY.thigh + TOY.shin + TOY.sole;

/**
 * Seat-top heights of the office seating (floor to where the pelvis rests), read
 * from the furniture: desk chair seat centre 0.48 + half its 0.08 thickness;
 * couch cushions centre 0.50 + half 0.12; meeting chair seat 0.46 + 0.04; the
 * beanbag's squashed sphere tops out at 0.52 but a body sinks in, so 0.42.
 */
export const SEAT_HEIGHT = { chair: 0.52, couch: 0.56, beanbag: 0.42, meeting: 0.5 } as const;

/** Keyboard position relative to the seat centre at a desk (figure-local). */
export const KEYBOARD = { y: 0.78, z: 0.38 } as const;

/** The deepest any body point may reach behind the seat centre (backrest front at ~0.2). */
export const SEAT_BACK_CLEARANCE = 0.16;

// ---------------------------------------------------------------------------
// Pose

export type ToyMode = FigureMode;
export type Vec3 = [number, number, number];

export interface ToyPose {
  /** Bottom of the pelvis above the floor (the seat contact point). */
  hipY: number;
  /** Pelvis offset forward (+z) from the origin. */
  hipZ: number;
  /** Extra lift of the upper body above the pelvis (breathing, walk bounce). */
  bob: number;
  torsoPitch: number;
  torsoYaw: number;
  torsoRoll: number;
  headPitch: number;
  headYaw: number;
  headRoll: number;
  leftShoulder: Vec3;
  rightShoulder: Vec3;
  leftElbow: number;
  rightElbow: number;
  leftHip: number;
  rightHip: number;
  leftKnee: number;
  rightKnee: number;
  leftAnkle: number;
  rightAnkle: number;
}

export function createPose(): ToyPose {
  return {
    hipY: TOY_LEG - TOY.hipUp, hipZ: 0, bob: 0,
    torsoPitch: 0, torsoYaw: 0, torsoRoll: 0,
    headPitch: 0, headYaw: 0, headRoll: 0,
    leftShoulder: [0, 0, 0.1], rightShoulder: [0, 0, -0.1],
    leftElbow: 0, rightElbow: 0,
    leftHip: 0, rightHip: 0, leftKnee: 0, rightKnee: 0, leftAnkle: 0, rightAnkle: 0,
  };
}

const SCALAR_KEYS = [
  "hipY", "hipZ", "bob", "torsoPitch", "torsoYaw", "torsoRoll", "headPitch", "headYaw", "headRoll",
  "leftElbow", "rightElbow", "leftHip", "rightHip", "leftKnee", "rightKnee", "leftAnkle", "rightAnkle",
] as const;

export function copyPose(src: ToyPose, out: ToyPose): ToyPose {
  for (const key of SCALAR_KEYS) out[key] = src[key];
  for (let i = 0; i < 3; i += 1) {
    out.leftShoulder[i] = src.leftShoulder[i];
    out.rightShoulder[i] = src.rightShoulder[i];
  }
  return out;
}

/** Linear blend a → b by k (0 = a, 1 = b). Writes into `out` (may alias a or b); allocates only without it. */
export function blendPose(a: ToyPose, b: ToyPose, k: number, out: ToyPose = createPose()): ToyPose {
  const j = 1 - k;
  for (const key of SCALAR_KEYS) out[key] = a[key] * j + b[key] * k;
  for (let i = 0; i < 3; i += 1) {
    out.leftShoulder[i] = a.leftShoulder[i] * j + b.leftShoulder[i] * k;
    out.rightShoulder[i] = a.rightShoulder[i] * j + b.rightShoulder[i] * k;
  }
  return out;
}

const TAU = Math.PI * 2;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const smooth = (e0: number, e1: number, v: number) => {
  const x = clamp((v - e0) / (e1 - e0), 0, 1);
  return x * x * (3 - 2 * x);
};

/** Walk cycles per second at a ground speed; the stride lengthens with speed. */
export function walkCadence(speed: number): number {
  const s = Math.max(0, speed);
  const stride = 0.45 + 0.1 * Math.min(s, 5);
  return clamp(s / stride, 1.2, 4.6);
}

/** Lowest sole point of one leg, relative to the hip joint (y is negative). */
function soleDrop(hip: number, knee: number, ankle: number): number {
  const a1 = hip;
  const a2 = hip + knee;
  const a3 = a2 + ankle;
  // A limb along -y rotated by a about x points to (y = -cos a, z = -sin a).
  const kneeY = -TOY.thigh * Math.cos(a1);
  const ankleY = kneeY - TOY.shin * Math.cos(a2);
  const c = Math.cos(a3);
  const s = Math.sin(a3);
  // Heel (z = -heel) and toe (z = +toe) at the sole, in the ankle frame, rotated by a3.
  // Rx(a) maps (0, y, z) to (0, y cos a - z sin a, …).
  const heelY = ankleY - TOY.sole * c + TOY.heel * s;
  const toeY = ankleY - TOY.sole * c - TOY.toe * s;
  return Math.min(heelY, toeY);
}

/** Put the lower of the two feet exactly on the floor. */
function ground(p: ToyPose): void {
  const drop = Math.min(soleDrop(p.leftHip, p.leftKnee, p.leftAnkle), soleDrop(p.rightHip, p.rightKnee, p.rightAnkle));
  p.hipY = -drop - TOY.hipUp;
}

function standBase(p: ToyPose): void {
  p.hipZ = 0; p.bob = 0;
  p.torsoPitch = 0; p.torsoYaw = 0; p.torsoRoll = 0;
  p.headPitch = 0; p.headYaw = 0; p.headRoll = 0;
  p.leftShoulder[0] = 0.05; p.leftShoulder[1] = 0; p.leftShoulder[2] = 0.1;
  p.rightShoulder[0] = 0.05; p.rightShoulder[1] = 0; p.rightShoulder[2] = -0.1;
  p.leftElbow = -0.15; p.rightElbow = -0.15;
  p.leftHip = 0; p.rightHip = 0; p.leftKnee = 0; p.rightKnee = 0; p.leftAnkle = 0; p.rightAnkle = 0;
}

/** Seated legs: thighs forward along +z, shins hanging straight down, feet level. */
function seatBase(p: ToyPose, seatHeight: number): void {
  standBase(p);
  p.hipY = seatHeight;
  p.hipZ = 0.05;
  p.torsoPitch = 0.14;
  p.leftHip = -Math.PI / 2 + 0.04; p.rightHip = -Math.PI / 2 + 0.04;
  p.leftKnee = Math.PI / 2 - 0.04; p.rightKnee = Math.PI / 2 - 0.04;
  // Hands resting on the thighs.
  p.leftShoulder[0] = -0.55; p.leftShoulder[2] = 0.12;
  p.rightShoulder[0] = -0.55; p.rightShoulder[2] = -0.12;
  p.leftElbow = -0.75; p.rightElbow = -0.75;
}

/**
 * Two-bone reach in the sagittal plane: shoulder pitch and elbow bend that put
 * the hand centre at (dy, dz) from the shoulder, in the torso's frame.
 */
const REACH = { shoulder: 0, elbow: 0 };
function reach(dy: number, dz: number): typeof REACH {
  const l1 = TOY.upperArm;
  const l2 = TOY.foreArm;
  const d = clamp(Math.hypot(dy, dz), Math.abs(l1 - l2) + 1e-3, l1 + l2 - 1e-4);
  const toward = Math.atan2(-dz, -dy);
  const atShoulder = Math.acos(clamp((l1 * l1 + d * d - l2 * l2) / (2 * l1 * d), -1, 1));
  const atElbow = Math.acos(clamp((l1 * l1 + l2 * l2 - d * d) / (2 * l1 * l2), -1, 1));
  REACH.shoulder = toward + atShoulder;
  REACH.elbow = -(Math.PI - atElbow);
  return REACH;
}

/** Inward roll of the typing arms (left arm rolls towards -x, right towards +x). */
const TYPE_ROLL = 0.1;
const TYPE_ROLL_COS = Math.cos(TYPE_ROLL);

/** Aim both forearms at the keyboard (work mode; the torso only pitches here). */
function typeAt(p: ToyPose, t: number): void {
  const cos = Math.cos(p.torsoPitch);
  const sin = Math.sin(p.torsoPitch);
  const shoulderY = p.hipY + TOY.hipUp + p.bob + TOY.shoulderY * cos;
  const shoulderZ = p.hipZ + TOY.shoulderY * sin;
  for (let side = 0; side < 2; side += 1) {
    const tap = 0.012 * Math.max(0, Math.sin(t * TAU * 3.1 + side * 2.1));
    const wy = KEYBOARD.y + 0.045 + tap - shoulderY;
    const wz = KEYBOARD.z - shoulderZ + 0.004 * Math.sin(t * TAU * 1.3 + side);
    // World -> torso frame (undo the torso pitch about x); the inward roll
    // (applied first, Euler XYZ) shortens the arm's vertical reach by cos(roll).
    const r = reach((wy * cos + wz * sin) / TYPE_ROLL_COS, -wy * sin + wz * cos);
    if (side === 0) { p.leftShoulder[0] = r.shoulder; p.leftShoulder[2] = -TYPE_ROLL; p.leftElbow = r.elbow; }
    else { p.rightShoulder[0] = r.shoulder; p.rightShoulder[2] = TYPE_ROLL; p.rightElbow = r.elbow; }
  }
}

export interface PoseOptions {
  /** Seat-top height for the seated modes (sit, work, sleep), in rig units. */
  seatHeight: number;
  /** Walk-cycle phase in cycles; when given it replaces `t * walkCadence(speed)` so cadence changes stay continuous. */
  phase?: number;
}

/**
 * The procedural pose for a mode at time `t` (seconds). Pure; writes into `out`
 * when given so a render loop can call it every frame without allocating.
 */
export function poseFor(mode: ToyMode, t: number, speed: number, opts: PoseOptions, out: ToyPose = createPose()): ToyPose {
  const p = out;
  const breathe = Math.sin(t * TAU * 0.28);
  switch (mode) {
    case "walk": {
      standBase(p);
      const s = Math.max(0, speed);
      const phase = (opts.phase ?? t * walkCadence(s)) * TAU;
      const move = smooth(0, 0.35, s);
      const run = smooth(2.6, 3.6, s);
      const swing = move * (0.32 + 0.1 * Math.min(s, 3) + 0.2 * run);
      const sL = Math.sin(phase);
      const sR = -sL;
      // Hip: negative = forward. The left leg leads when sL > 0.
      p.leftHip = -swing * sL;
      p.rightHip = -swing * sR;
      // The knee lifts while its leg swings through (the half cycle it moves forward).
      const liftL = Math.max(0, Math.cos(phase));
      const liftR = Math.max(0, -Math.cos(phase));
      const kneeAmp = move * (0.55 + 0.5 * run);
      p.leftKnee = 0.08 * move + kneeAmp * liftL;
      p.rightKnee = 0.08 * move + kneeAmp * liftR;
      // Keep the sole roughly level; toes lift slightly on the trailing foot.
      p.leftAnkle = -(p.leftHip + p.leftKnee) * 0.75;
      p.rightAnkle = -(p.rightHip + p.rightKnee) * 0.75;
      // Arms swing opposite to the legs, bent further into a run.
      const armSwing = move * (0.35 + 0.35 * run);
      p.leftShoulder[0] = armSwing * sL;
      p.rightShoulder[0] = armSwing * sR;
      p.leftElbow = -0.25 - 1.0 * run;
      p.rightElbow = -0.25 - 1.0 * run;
      p.torsoPitch = move * (0.05 + 0.02 * Math.min(s, 3)) + 0.16 * run;
      p.torsoYaw = 0.08 * move * sL;
      p.headPitch = -0.6 * p.torsoPitch * run;
      p.bob = move * (0.012 + 0.012 * run) * Math.abs(Math.cos(phase));
      ground(p);
      return p;
    }
    case "sit": {
      seatBase(p, opts.seatHeight);
      p.bob = 0.004 * breathe;
      p.headYaw = 0.18 * Math.sin(t * 0.37);
      p.headPitch = 0.04 + 0.03 * Math.sin(t * 0.23);
      return p;
    }
    case "work": {
      seatBase(p, opts.seatHeight);
      // Scoot forward and lean in so the short arms reach the keyboard.
      p.hipZ = 0.08;
      p.torsoPitch = 0.3;
      p.bob = 0.003 * breathe;
      p.headPitch = -0.14 + 0.02 * Math.sin(t * 0.5); // net ~0.16 rad down with the lean
      p.headYaw = 0.05 * Math.sin(t * 0.31);
      // Forearms forward to the keyboard; small alternating typing jitter.
      typeAt(p, t);
      return p;
    }
    case "sleep": {
      seatBase(p, opts.seatHeight);
      p.torsoPitch = 0.26 + 0.012 * Math.sin(t * TAU * 0.2);
      p.torsoRoll = 0.08;
      p.headPitch = 0.42 + 0.03 * Math.sin(t * TAU * 0.2);
      p.headRoll = 0.3;
      p.leftShoulder[0] = -0.3; p.rightShoulder[0] = -0.3;
      p.leftElbow = -0.6; p.rightElbow = -0.6;
      p.leftHip += 0.05; p.rightHip += 0.08;
      p.leftShoulder[2] = 0.14; p.rightShoulder[2] = -0.14;
      return p;
    }
    case "wave": {
      standBase(p);
      p.bob = 0.004 * breathe;
      // Right arm up and out to the figure's right (-x), waving side to side.
      p.rightShoulder[0] = -0.25;
      p.rightShoulder[2] = -2.45 + 0.28 * Math.sin(t * TAU * 2.2);
      p.rightElbow = -0.35;
      p.torsoRoll = 0.04;
      p.headRoll = -0.08;
      p.headPitch = -0.05;
      ground(p);
      return p;
    }
    case "talk": {
      standBase(p);
      p.bob = 0.004 * breathe;
      p.headPitch = 0.07 * Math.sin(t * TAU * 1.6) - 0.02;
      p.headYaw = 0.12 * Math.sin(t * 0.8);
      p.torsoYaw = 0.05 * Math.sin(t * 0.6);
      p.rightShoulder[0] = -0.45 + 0.15 * Math.sin(t * TAU * 0.9);
      p.rightElbow = -1.1 + 0.3 * Math.sin(t * TAU * 1.3);
      p.leftShoulder[0] = -0.2 + 0.1 * Math.sin(t * TAU * 0.7 + 1.2);
      p.leftElbow = -0.7 + 0.2 * Math.sin(t * TAU * 1.1 + 0.5);
      ground(p);
      return p;
    }
    case "celebrate": {
      standBase(p);
      const hop = Math.sin(t * TAU * 1.6);
      const air = Math.max(0, hop);
      const crouch = Math.max(0, -hop);
      p.leftHip = -0.35 * crouch; p.rightHip = -0.35 * crouch;
      p.leftKnee = 0.7 * crouch; p.rightKnee = 0.7 * crouch;
      p.leftAnkle = -0.35 * crouch; p.rightAnkle = -0.35 * crouch;
      p.leftShoulder[0] = -0.2; p.rightShoulder[0] = -0.2;
      p.leftShoulder[2] = 2.6 + 0.2 * Math.sin(t * TAU * 3.2);
      p.rightShoulder[2] = -2.6 - 0.2 * Math.sin(t * TAU * 3.2 + 1);
      p.leftElbow = -0.2; p.rightElbow = -0.2;
      p.headPitch = -0.15;
      ground(p);
      p.hipY += 0.11 * air;
      return p;
    }
    case "idle":
    default: {
      standBase(p);
      p.bob = 0.005 * breathe;
      p.torsoRoll = 0.025 * Math.sin(t * TAU * 0.18);
      p.headRoll = -0.5 * p.torsoRoll;
      p.headYaw = 0.25 * Math.sin(t * 0.29) * Math.sin(t * 0.11);
      p.leftShoulder[0] = 0.04 * breathe;
      p.rightShoulder[0] = 0.04 * breathe;
      p.leftHip = -0.02; p.rightHip = 0.02;
      ground(p);
      return p;
    }
  }
}

// ---------------------------------------------------------------------------
// Forward kinematics (tests and the work-mode reach)

type Mat3 = [number, number, number, number, number, number, number, number, number];

/** Rotation matrix of a three.js Euler in XYZ order (row-major). */
function eulerXYZ(x: number, y: number, z: number): Mat3 {
  const a = Math.cos(x), b = Math.sin(x);
  const c = Math.cos(y), d = Math.sin(y);
  const e = Math.cos(z), f = Math.sin(z);
  const ae = a * e, af = a * f, be = b * e, bf = b * f;
  return [
    c * e, -c * f, d,
    af + be * d, ae - bf * d, -b * c,
    bf - ae * d, be + af * d, a * c,
  ];
}

function mul(m: Mat3, n: Mat3): Mat3 {
  const r = new Array(9).fill(0) as Mat3;
  for (let i = 0; i < 3; i += 1)
    for (let j = 0; j < 3; j += 1)
      r[i * 3 + j] = m[i * 3] * n[j] + m[i * 3 + 1] * n[3 + j] + m[i * 3 + 2] * n[6 + j];
  return r;
}

function apply(m: Mat3, v: Vec3): Vec3 {
  return [
    m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
    m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
    m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
  ];
}

interface Frame { pos: Vec3; rot: Mat3 }

function child(parent: Frame, offset: Vec3, euler: Vec3): Frame {
  const o = apply(parent.rot, offset);
  return { pos: [parent.pos[0] + o[0], parent.pos[1] + o[1], parent.pos[2] + o[2]], rot: mul(parent.rot, eulerXYZ(euler[0], euler[1], euler[2])) };
}

function at(frame: Frame, local: Vec3): Vec3 {
  const o = apply(frame.rot, local);
  return [frame.pos[0] + o[0], frame.pos[1] + o[1], frame.pos[2] + o[2]];
}

export interface ToyPoint { name: string; p: Vec3 }

/**
 * Surface sample points of the posed figure in figure-local space: joints plus
 * the outer extents of pelvis, chest, head (incl. hair), limbs and shoes. The
 * joint layout mirrors `ToyFigure.tsx` exactly.
 */
export function toyPoints(pose: ToyPose): ToyPoint[] {
  const pts: ToyPoint[] = [];
  const push = (name: string, p: Vec3) => pts.push({ name, p });
  const pelvis: Frame = { pos: [0, pose.hipY, pose.hipZ], rot: eulerXYZ(0, 0, 0) };
  const { w, h, d } = TOY.pelvis;
  for (const sx of [-1, 1]) for (const sy of [0, 1]) for (const sz of [-1, 1])
    push("pelvis", at(pelvis, [sx * w / 2, sy * h, sz * d / 2]));

  const torso = child(pelvis, [0, TOY.hipUp + pose.bob, 0], [pose.torsoPitch, pose.torsoYaw, pose.torsoRoll]);
  const t = TOY.torso;
  for (const sx of [-1, 1]) for (const y of [0.02, 0.15, 0.28]) for (const sz of [-1, 1])
    push("torso", at(torso, [sx * t.w / 2, y, sz * t.d / 2]));

  const head = child(torso, [0, TOY.neckY, 0], [pose.headPitch, pose.headYaw, pose.headRoll]);
  const hd = TOY.head;
  const back = hd.rz + TOY.hairBack;
  for (let i = 0; i <= 8; i += 1) {
    const a = (i / 8) * Math.PI - Math.PI / 2; // −90° (bottom) … +90° (top)
    const y = hd.y + Math.sin(a) * hd.ry;
    const r = Math.cos(a);
    push("head", at(head, [0, y, -back * r]));
    push("head", at(head, [0, y, hd.rz * r]));
    push("head", at(head, [hd.rx * r, y, 0]));
    push("head", at(head, [-hd.rx * r, y, 0]));
  }

  const arms: Array<[string, number, Vec3, number]> = [
    ["left", 1, pose.leftShoulder, pose.leftElbow],
    ["right", -1, pose.rightShoulder, pose.rightElbow],
  ];
  for (const [side, sx, sh, el] of arms) {
    const shoulder = child(torso, [sx * TOY.shoulderX, TOY.shoulderY, 0], sh);
    push(`${side}Shoulder`, shoulder.pos);
    const elbow = child(shoulder, [0, -TOY.upperArm, 0], [el, 0, 0]);
    push(`${side}Elbow`, elbow.pos);
    push(`${side}ElbowBack`, at(elbow, [0, 0, -TOY.armRadius]));
    push(`${side}Hand`, at(elbow, [0, -TOY.foreArm, 0]));
  }

  const legs: Array<[string, number, number, number, number]> = [
    ["left", 1, pose.leftHip, pose.leftKnee, pose.leftAnkle],
    ["right", -1, pose.rightHip, pose.rightKnee, pose.rightAnkle],
  ];
  for (const [side, sx, hp, kn, an] of legs) {
    const hip = child(pelvis, [sx * TOY.hipX, TOY.hipUp, 0], [hp, 0, 0]);
    push(`${side}Hip`, hip.pos);
    push(`${side}ThighBack`, at(hip, [0, -TOY.thigh / 2, -TOY.legRadius]));
    const knee = child(hip, [0, -TOY.thigh, 0], [kn, 0, 0]);
    push(`${side}Knee`, knee.pos);
    const ankle = child(knee, [0, -TOY.shin, 0], [an, 0, 0]);
    push(`${side}Ankle`, ankle.pos);
    push(`${side}Heel`, at(ankle, [0, -TOY.sole, -TOY.heel]));
    push(`${side}Toe`, at(ankle, [0, -TOY.sole, TOY.toe]));
  }
  return pts;
}

/** The lowest shoe-sole point of the posed figure. */
export function lowestSoleY(pose: ToyPose): number {
  let min = Infinity;
  for (const { name, p } of toyPoints(pose)) if (name.endsWith("Heel") || name.endsWith("Toe")) min = Math.min(min, p[1]);
  return min;
}
