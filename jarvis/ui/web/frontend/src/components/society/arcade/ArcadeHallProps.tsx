/**
 * The arcade floor's machines (merged into PROP_RENDERERS): the playable
 * upright cabinets, the prize counter, the token changer, claw machines,
 * the air-hockey table, pinball machines and the snack-bar counter.
 *
 * Every piece is built in local space centred on the origin, front facing
 * +z, inside its FURNITURE_SIZE box. Matte parts of a piece are baked into
 * one vertex-coloured geometry and its lights into another (arcadeHallLook's
 * PartKit), built once per kind — per game for cabinets — and shared by every
 * copy; faces and screens are cached canvas materials (arcadeScreens). So a
 * cabinet costs four draw calls however much detail it has.
 */
import { BoxGeometry, PlaneGeometry, type BufferGeometry, type Material } from "three";
import type { Furniture, FurnitureKind } from "../office/officeLayout";
import { gameForCabinet, type ArcadeGameInfo } from "./arcadeGames";
import { HALL, HALL_MAT, lcg, PartKit, type Vec3 } from "./arcadeHallLook";
import { BLANK_LOOK, FACES, marqueeMaterial, screenMaterial } from "./arcadeScreens";

/** Where a cabinet's screen sits in the cabinet's own space (front +z): centre, size and backward lean. */
export const CABINET_SCREEN = { y: 1.38, z: 0.064, w: 0.5, h: 0.375, tilt: -0.12 } as const;
/** The lit marquee above the screen. */
const CABINET_MARQUEE = { y: 1.82, z: 0.062, w: 0.64, h: 0.16 } as const;

const GEO = {
  screen: new PlaneGeometry(CABINET_SCREEN.w, CABINET_SCREEN.h),
  marquee: new PlaneGeometry(CABINET_MARQUEE.w, CABINET_MARQUEE.h),
  box: new BoxGeometry(1, 1, 1),
  plane: new PlaneGeometry(1, 1),
};

/** A stable small hash of an id, to vary machines of the same kind. */
function variantOf(id: string, count: number): number {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return hash % count;
}

/** A self-lit or glass face: a unit plane scaled to `size`. */
function Face({ size, position, rotation, material }: {
  size: [number, number]; position: Vec3; rotation?: Vec3; material: Material;
}) {
  return <mesh geometry={GEO.plane} material={material} position={position} rotation={rotation} scale={[size[0], size[1], 1]} />;
}

/** A glass box: a unit cube scaled to `size`. */
function Glass({ size, position }: { size: Vec3; position: Vec3 }) {
  return <mesh geometry={GEO.box} material={HALL_MAT.glass} position={position} scale={size} />;
}

/** The two baked geometries of a piece: matte paint and lights. */
interface Baked { paint: BufferGeometry; glow: BufferGeometry }

function BakedPiece({ baked, gloss = false }: { baked: Baked; gloss?: boolean }) {
  return (
    <>
      <mesh geometry={baked.paint} material={gloss ? HALL_MAT.gloss : HALL_MAT.paint} castShadow receiveShadow />
      <mesh geometry={baked.glow} material={HALL_MAT.glow} />
    </>
  );
}

const bakedCache = new Map<string, Baked>();

/** Build a piece's two geometries once per key; every copy of the piece draws from the same buffers. */
function bake(key: string, build: (paint: PartKit, glow: PartKit) => void): Baked {
  let baked = bakedCache.get(key);
  if (!baked) {
    const paint = new PartKit(), glow = new PartKit();
    build(paint, glow);
    baked = { paint: paint.build(), glow: glow.build() };
    bakedCache.set(key, baked);
  }
  return baked;
}

/** Plush toys: a squashed body, a head and two round ears, each in one colour. */
function plush(kit: PartKit, x: number, y: number, z: number, r: number, colour: string): void {
  kit.sphere(r, [x, y + r * 0.9, z], colour, [1, 0.9, 0.9], 9);
  const head = y + r * 1.75;
  kit.sphere(r * 0.75, [x, head, z + r * 0.05], colour, undefined, 9);
  kit.sphere(r * 0.28, [x - r * 0.5, head + r * 0.6, z], colour, undefined, 6);
  kit.sphere(r * 0.28, [x + r * 0.5, head + r * 0.6, z], colour, undefined, 6);
}

// ---------------------------------------------------------------------------
// Retro cabinet
// ---------------------------------------------------------------------------

/**
 * A classic upright: side panels in the game's body colour with two racing
 * stripes, neon T-molding along their front edges, a slanted control panel
 * with a joystick and two buttons, a coin door with lit slots, the tube in a
 * black bezel and the lit marquee on top.
 */
function cabinetParts(game: ArcadeGameInfo | null): Baked {
  const look = game?.look ?? BLANK_LOOK;
  return bake(`arcade:cabinet:${game?.id ?? "blank"}`, (paint, glow) => {
    const tilt = 0.22;
    for (const side of [-1, 1]) {
      const x = side * 0.3575;
      paint.box([0.035, 0.98, 0.69], [x, 0.49, -0.045], look.body);
      paint.box([0.035, 0.94, 0.51], [x, 1.45, -0.135], look.body);
      const sx = side * 0.378;
      paint.box([0.006, 0.07, 0.62], [sx, 0.55, -0.05], look.accent, [0.55, 0, 0]);
      paint.box([0.006, 0.07, 0.62], [sx, 0.75, -0.05], look.marqueeText, [0.55, 0, 0]);
      paint.box([0.006, 0.06, 0.4], [sx, 1.5, -0.14], look.accent, [0.55, 0, 0]);
      glow.box([0.04, 0.98, 0.012], [x, 0.49, 0.306], look.accent);
      glow.box([0.04, 0.94, 0.012], [x, 1.45, 0.126], look.accent);
      glow.box([0.04, 0.012, 0.19], [x, 0.986, 0.215], look.accent);
    }
    paint.box([0.68, 0.98, 0.64], [0, 0.49, -0.07], look.trim);
    paint.box([0.6, 0.1, 0.02], [0, 0.06, 0.26], HALL.black);
    paint.box([0.24, 0.3, 0.02], [0, 0.5, 0.26], HALL.steel);
    glow.box([0.035, 0.05, 0.008], [-0.055, 0.56, 0.274], "#ff4b2b");
    glow.box([0.035, 0.05, 0.008], [0.055, 0.56, 0.274], "#ff4b2b");
    // Control panel, its front edge tipped down towards the player.
    paint.box([0.68, 0.06, 0.3], [0, 0.99, 0.17], look.trim, [tilt, 0, 0]);
    paint.cylinder(0.009, 0.07, [-0.16, 1.045, 0.2], HALL.black, { segments: 8 });
    paint.sphere(0.03, [-0.16, 1.09, 0.2], look.accent, undefined, 12);
    glow.cylinder(0.025, 0.016, [0.04, 1.02, 0.2], look.accent, { rotation: [tilt, 0, 0], segments: 14 });
    glow.cylinder(0.025, 0.016, [0.13, 1.02, 0.2], look.marqueeText, { rotation: [tilt, 0, 0], segments: 14 });
    // Upper cabinet: the tube's housing, the bezel, the marquee box and the top cap.
    paint.box([0.68, 0.76, 0.42], [0, 1.42, -0.17], look.trim);
    paint.box([0.62, 0.52, 0.02], [0, CABINET_SCREEN.y, 0.05], HALL.black, [CABINET_SCREEN.tilt, 0, 0]);
    paint.box([0.7, 0.2, 0.4], [0, CABINET_MARQUEE.y, -0.14], look.body);
    paint.box([0.72, 0.03, 0.46], [0, 1.935, -0.15], look.trim);
  });
}

function RetroCabinet({ item }: { item: Furniture }) {
  const game = gameForCabinet(item.id);
  return (
    <group>
      <BakedPiece baked={cabinetParts(game)} />
      <mesh geometry={GEO.screen} material={screenMaterial(game)} position={[0, CABINET_SCREEN.y, CABINET_SCREEN.z]}
        rotation={[CABINET_SCREEN.tilt, 0, 0]} />
      <mesh geometry={GEO.marquee} material={marqueeMaterial(game)} position={[0, CABINET_MARQUEE.y, CABINET_MARQUEE.z]} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Prize counter
// ---------------------------------------------------------------------------

/**
 * The redemption counter: a glass display case of small prizes with a lit
 * ticket strip on its base and a register on top, and behind it a plum shelf
 * wall of plush toys under neon shelf lights.
 */
function prizeParts(): Baked {
  return bake("arcade:prize-counter", (paint, glow) => {
    const plum = "#3b1d4a", deep = "#2a1240", cream = "#efe6f4";
    const rand = lcg(0x9e1ce);
    const colour = () => HALL.plush[Math.floor(rand() * HALL.plush.length)];
    paint.box([3.3, 0.35, 0.42], [0, 0.175, 0.21], plum);
    paint.box([3.26, 0.02, 0.38], [0, 0.36, 0.21], "#1b0b24");
    for (const x of [-1.635, 1.635]) for (const z of [0.03, 0.39]) paint.box([0.03, 0.64, 0.03], [x, 0.68, z], HALL.chrome);
    paint.box([3.36, 0.04, 0.44], [0, 1.02, 0.21], cream);
    // Small prizes in the case: balls, boxes and little plush.
    for (let i = 0; i < 11; i += 1) {
      const x = -1.45 + i * 0.29, kind = i % 3;
      if (kind === 0) paint.sphere(0.05, [x, 0.42, 0.2], colour(), undefined, 10);
      else if (kind === 1) paint.box([0.1, 0.1, 0.1], [x, 0.42, 0.22], colour(), [0, 0.4, 0]);
      else plush(paint, x, 0.37, 0.2, 0.045, colour());
    }
    // The register on the counter.
    paint.box([0.32, 0.16, 0.24], [1.25, 1.12, 0.2], HALL.steel);
    glow.box([0.2, 0.05, 0.008], [1.25, 1.15, 0.324], HALL.neon.green);
    // The shelf wall.
    paint.box([3.4, 2.15, 0.06], [0, 1.075, -0.42], deep);
    paint.box([3.3, 1.0, 0.3], [0, 0.5, -0.24], plum);
    for (const x of [-1.675, 1.675]) paint.box([0.05, 2.15, 0.36], [x, 1.075, -0.27], deep);
    for (const y of [1.28, 1.62, 1.96]) {
      paint.box([3.3, 0.03, 0.3], [0, y, -0.24], cream);
      glow.box([3.3, 0.012, 0.012], [0, y - 0.022, -0.085], HALL.neon.cyan);
      for (let i = 0; i < 8; i += 1) {
        plush(paint, -1.4 + i * 0.4 + (rand() - 0.5) * 0.06, y + 0.015, -0.24, 0.065 + rand() * 0.015, colour());
      }
    }
    // A big bear sits on the counter's end.
    plush(paint, -1.38, 1.04, 0.2, 0.1, HALL.plush[0]);
    glow.box([3.3, 0.025, 0.012], [0, 0.355, 0.426], HALL.neon.magenta);
  });
}

function PrizeCounter() {
  return (
    <group>
      <BakedPiece baked={prizeParts()} />
      <Glass size={[3.26, 0.62, 0.38]} position={[0, 0.68, 0.21]} />
      <Face size={[1.6, 0.3]} position={[0, 0.19, 0.4215]} material={FACES.prizeSign()} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Token changer
// ---------------------------------------------------------------------------

/** A golden change machine: a bill slot, a lit panel (bill → coins), a coin tray and a lit header. */
function tokenParts(): Baked {
  return bake("arcade:token-machine", (paint, glow) => {
    paint.box([0.58, 0.06, 0.46], [0, 0.03, 0], HALL.black);
    paint.box([0.56, 1.52, 0.44], [0, 0.82, -0.02], "#d9a521");
    paint.box([0.46, 0.86, 0.02], [0, 1.0, 0.21], "#1c1c24");
    paint.box([0.26, 0.1, 0.1], [0, 0.38, 0.2], HALL.steel);
    glow.box([0.58, 0.12, 0.46], [0, 1.64, -0.02], HALL.neon.yellow);
    glow.box([0.2, 0.02, 0.008], [0, 0.68, 0.224], HALL.neon.green);
  });
}

function TokenMachine() {
  return (
    <group>
      <BakedPiece baked={tokenParts()} gloss />
      <Face size={[0.4, 0.5]} position={[0, 1.08, 0.2215]} material={FACES.token()} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Claw machine
// ---------------------------------------------------------------------------

const CLAW_BODIES = ["#d23b6a", "#1fa4b8"] as const;

/** A claw crane: a coloured base with a prize chute, a glass box full of plush, the claw on its gantry, a bulb-lit header. */
function clawParts(variant: number): Baked {
  return bake(`arcade:claw:${variant}`, (paint, glow) => {
    const body = CLAW_BODIES[variant % CLAW_BODIES.length];
    const rand = lcg(0xc1a3 + variant * 97);
    paint.box([0.96, 0.85, 0.96], [0, 0.425, 0], body);
    paint.box([0.28, 0.26, 0.02], [0.24, 0.36, 0.49], HALL.black);
    paint.box([0.9, 0.06, 0.14], [0, 0.88, 0.42], HALL.steel);
    paint.cylinder(0.008, 0.06, [-0.22, 0.94, 0.42], HALL.black, { segments: 8 });
    paint.sphere(0.026, [-0.22, 0.98, 0.42], "#e0443e", undefined, 10);
    glow.cylinder(0.024, 0.014, [0.0, 0.917, 0.42], HALL.neon.yellow, { segments: 14 });
    for (const x of [-0.46, 0.46]) for (const z of [-0.46, 0.46]) paint.box([0.04, 0.9, 0.04], [x, 1.3, z], body);
    paint.box([0.96, 0.25, 0.96], [0, 1.875, 0], body);
    paint.box([0.9, 0.02, 0.9], [0, 0.86, 0], "#2a1240");
    // The drop chute in the front-right corner, and a pile of plush everywhere else.
    paint.box([0.26, 0.2, 0.26], [0.3, 0.97, 0.3], "#c9d3e6");
    for (let i = 0; i < 16; i += 1) {
      const x = -0.34 + rand() * 0.68, z = -0.34 + rand() * 0.68;
      if (x > 0.12 && z > 0.12) continue;
      plush(paint, x, 0.87 + rand() * 0.06, z, 0.055 + rand() * 0.02, HALL.plush[Math.floor(rand() * HALL.plush.length)]);
    }
    // The claw hanging from its gantry.
    paint.box([0.9, 0.025, 0.025], [0, 1.732, -0.05], HALL.chrome);
    paint.cylinder(0.006, 0.25, [-0.05, 1.62, -0.05], HALL.chrome, { segments: 6 });
    paint.sphere(0.035, [-0.05, 1.48, -0.05], HALL.chrome, undefined, 10);
    for (let k = 0; k < 3; k += 1) {
      const a = (k / 3) * Math.PI * 2;
      paint.box([0.012, 0.12, 0.012], [-0.05 + Math.cos(a) * 0.04, 1.42, -0.05 + Math.sin(a) * 0.04], HALL.chrome, [0, -a, 0.35]);
    }
    glow.box([0.9, 0.015, 0.9], [0, 1.742, 0], "#ffe6f2");
    for (let i = 0; i < 9; i += 1) glow.sphere(0.018, [-0.4 + i * 0.1, 1.765, 0.485], HALL.neon.yellow, undefined, 6);
  });
}

function ClawMachine({ item }: { item: Furniture }) {
  return (
    <group>
      <BakedPiece baked={clawParts(variantOf(item.id, CLAW_BODIES.length))} />
      <Glass size={[0.88, 0.86, 0.88]} position={[0, 1.31, 0]} />
      <Face size={[0.8, 0.2]} position={[0, 1.875, 0.481]} material={FACES.clawHeader()} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Air hockey
// ---------------------------------------------------------------------------

/** An air-hockey table: a blue cabinet with neon side strips, white rails, two mallets and a puck. */
function hockeyParts(): Baked {
  return bake("arcade:air-hockey", (paint, glow) => {
    paint.box([1.9, 0.56, 1.0], [0, 0.38, 0], "#1f3a8a");
    for (const x of [-0.85, 0.85]) for (const z of [-0.42, 0.42]) paint.box([0.12, 0.1, 0.12], [x, 0.05, z], HALL.black);
    paint.box([1.94, 0.1, 1.04], [0, 0.7, 0], "#16285e");
    for (const z of [-0.56, 0.56]) paint.box([2.1, 0.12, 0.08], [0, 0.74, z], "#e9edf5");
    for (const x of [-1.01, 1.01]) paint.box([0.08, 0.12, 1.04], [x, 0.74, 0], "#e9edf5");
    const mallet = (x: number, colour: string) => {
      paint.cylinder(0.06, 0.035, [x, 0.7675, 0], colour, { segments: 18 });
      paint.cylinder(0.022, 0.06, [x, 0.815, 0], colour, { segments: 12 });
    };
    mallet(-0.75, "#e0443e");
    mallet(0.75, "#3b82f6");
    paint.cylinder(0.04, 0.012, [0.18, 0.756, 0.12], "#ff3d7f", { segments: 16 });
    for (const z of [-0.505, 0.505]) glow.box([1.8, 0.03, 0.01], [0, 0.5, z], HALL.neon.cyan);
  });
}

function AirHockey() {
  return (
    <group>
      <BakedPiece baked={hockeyParts()} />
      <Face size={[1.94, 1.04]} position={[0, 0.751, 0]} rotation={[-Math.PI / 2, 0, 0]} material={FACES.airHockey()} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Pinball
// ---------------------------------------------------------------------------

const PINBALL_BODIES = ["#2a1050", "#0f3a4a", "#4a1022"] as const;
/** The playfield's tilt (back raised) and the tilted cabinet's centre. */
const PINBALL = { tilt: 0.07, y: 0.88, depth: 1.3 } as const;

/** The legs and the backbox; they stand straight. */
function pinballStand(variant: number): Baked {
  return bake(`arcade:pinball-stand:${variant}`, (paint, glow) => {
    const body = PINBALL_BODIES[variant % PINBALL_BODIES.length];
    for (const x of [-0.32, 0.32]) {
      paint.box([0.05, 0.78, 0.05], [x, 0.39, 0.6], HALL.chrome);
      paint.box([0.05, 0.86, 0.05], [x, 0.43, -0.55], HALL.chrome);
    }
    paint.box([0.7, 0.8, 0.16], [0, 1.45, -0.62], body);
    glow.box([0.66, 0.02, 0.012], [0, 1.84, -0.535], HALL.neon.magenta);
    glow.box([0.02, 0.66, 0.012], [-0.33, 1.47, -0.535], HALL.neon.cyan);
    glow.box([0.02, 0.66, 0.012], [0.33, 1.47, -0.535], HALL.neon.cyan);
  });
}

/** The tilted cabinet in its own frame (origin = the cabinet's centre): body, rails, bumpers, lights and buttons. */
function pinballTable(variant: number): Baked {
  return bake(`arcade:pinball-table:${variant}`, (paint, glow) => {
    const body = PINBALL_BODIES[variant % PINBALL_BODIES.length];
    paint.box([0.7, 0.24, PINBALL.depth], [0, 0, 0], body);
    for (const x of [-0.335, 0.335]) paint.box([0.03, 0.08, PINBALL.depth], [x, 0.15, 0], HALL.chrome);
    paint.box([0.22, 0.14, 0.01], [0, -0.03, 0.655], HALL.steel);
    // Bumpers where the playfield texture draws their rings (u, v from its top-left corner).
    const lights = [HALL.neon.magenta, HALL.neon.yellow, HALL.neon.cyan];
    [[0.32, 0.28], [0.62, 0.24], [0.48, 0.4]].forEach(([u, v], i) => {
      const x = (u - 0.5) * 0.62, z = (v - 0.5) * 1.22;
      paint.cylinder(0.045, 0.04, [x, 0.141, z], "#e9e2d6", { segments: 14 });
      glow.sphere(0.025, [x, 0.168, z], lights[i], [1, 0.6, 1], 10);
    });
    for (const x of [-0.36, 0.36]) glow.box([0.02, 0.04, 0.04], [x, 0.05, 0.55], HALL.neon.yellow);
    glow.box([0.035, 0.03, 0.006], [-0.05, 0.0, 0.661], "#ff4b2b");
    glow.box([0.035, 0.03, 0.006], [0.05, 0.0, 0.661], "#ff4b2b");
    paint.cylinder(0.012, 0.05, [0.25, 0.02, 0.665], HALL.chrome, { rotation: [Math.PI / 2, 0, 0], segments: 8 });
  });
}

function Pinball({ item }: { item: Furniture }) {
  const variant = variantOf(item.id, PINBALL_BODIES.length);
  return (
    <group>
      <BakedPiece baked={pinballStand(variant)} />
      <group position={[0, PINBALL.y, 0]} rotation={[PINBALL.tilt, 0, 0]}>
        <BakedPiece baked={pinballTable(variant)} />
        <Face size={[0.62, 1.22]} position={[0, 0.121, 0]} rotation={[-Math.PI / 2, 0, 0]} material={FACES.playfield()} />
        <Face size={[0.64, 1.24]} position={[0, 0.19, 0]} rotation={[-Math.PI / 2, 0, 0]} material={HALL_MAT.glass} />
      </group>
      <Face size={[0.62, 0.62]} position={[0, 1.47, -0.539]} material={FACES.backglass()} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Snack counter
// ---------------------------------------------------------------------------

/** A diner counter: red front with chrome bands and a lit menu strip, a cream top with cups, a tray and a bell. */
function snackParts(): Baked {
  return bake("arcade:snack-counter", (paint, glow) => {
    paint.box([2.5, 0.98, 0.62], [0, 0.51, 0.04], "#b8323a");
    paint.box([2.5, 0.1, 0.02], [0, 0.05, 0.36], HALL.chrome);
    paint.box([2.6, 0.05, 0.8], [0, 1.025, 0], HALL.snack.cream);
    const cups = ["#e0443e", "#2de2e6", "#ffd23f", "#e0443e"];
    cups.forEach((colour, i) => {
      const x = 0.15 + i * 0.12;
      paint.cylinder(0.035, 0.08, [x, 1.09, 0.12], colour, { radiusTop: 0.042, segments: 12 });
      paint.cylinder(0.043, 0.008, [x, 1.134, 0.12], "#f4f1ea", { segments: 12 });
      paint.cylinder(0.004, 0.012, [x + 0.01, 1.142, 0.12], "#ffffff", { segments: 5 });
    });
    paint.box([0.36, 0.015, 0.26], [-0.6, 1.058, 0.05], "#e0443e");
    paint.cylinder(0.06, 0.022, [-0.66, 1.077, 0.05], "#e0a24a", { segments: 14 });
    paint.cylinder(0.063, 0.018, [-0.66, 1.097, 0.05], "#6b3a1e", { segments: 14 });
    paint.sphere(0.06, [-0.66, 1.106, 0.05], "#e0a24a", [1, 0.55, 1], 12);
    paint.box([0.08, 0.07, 0.05], [-0.48, 1.1, 0.05], "#e0443e");
    for (let i = -2; i <= 2; i += 1) paint.box([0.008, 0.06, 0.008], [-0.48 + i * 0.013, 1.115, 0.05], HALL.neon.yellow);
    paint.box([0.12, 0.09, 0.08], [0.95, 1.095, -0.1], HALL.chrome);
    paint.sphere(0.04, [1.12, 1.05, 0.15], "#d9a521", [1, 0.7, 1], 12);
    glow.box([2.5, 0.025, 0.012], [0, 0.92, 0.356], HALL.neon.cyan);
    glow.box([2.5, 0.025, 0.012], [0, 0.15, 0.356], HALL.neon.magenta);
  });
}

function SnackCounter() {
  return (
    <group>
      <BakedPiece baked={snackParts()} gloss />
      <Face size={[2.2, 0.4]} position={[0, 0.58, 0.3505]} material={FACES.snackMenu()} />
    </group>
  );
}

export const ARCADE_RENDERERS = {
  retroCabinet: ({ item }) => <RetroCabinet item={item} />,
  prizeCounter: () => <PrizeCounter />,
  tokenMachine: () => <TokenMachine />,
  clawMachine: ({ item }) => <ClawMachine item={item} />,
  airHockey: () => <AirHockey />,
  pinball: ({ item }) => <Pinball item={item} />,
  snackCounter: () => <SnackCounter />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;
