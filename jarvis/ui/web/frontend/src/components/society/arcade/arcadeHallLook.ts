/**
 * The arcade floor's look: its palette, the blacklight carpet and the room
 * floors, and a small kit that bakes many coloured primitives into ONE
 * geometry with vertex colours.
 *
 * The kit is why ten cabinets, a pinball row and shelves full of plush cost
 * a handful of draw calls: every matte part of a prop shares one material
 * (`HALL_MAT.paint`), every glowing part another (`HALL_MAT.glow`), and the
 * colour of each part lives in the geometry. Kits are built once per prop
 * kind (or per game for cabinets) and shared by every copy.
 */
import {
  BoxGeometry, BufferGeometry, Color, CylinderGeometry, DoubleSide, Euler, Float32BufferAttribute, Matrix4, MeshBasicMaterial,
  MeshStandardMaterial, Quaternion, SphereGeometry, Vector3,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

export type Vec3 = [number, number, number];

/** The arcade floor's colours. Neon on midnight: magenta, cyan, yellow, violet and green. */
export const HALL = {
  /** The night round the floating floor, and its fog. */
  space: "#0b0718",
  /** Hemisphere light: a violet sky and a dark plum bounce off the carpet. */
  sky: "#c9b8ff",
  ground: "#2a1638",
  sun: "#ece6ff",
  slabEdge: "#130d22",
  /** The glowing rim under the slab's edge. */
  rim: "#ff3fa4",
  /** The back walls: midnight panels with a faint pinstripe. */
  wall: { base: "#160f2c", panel: "#1c1438", groove: "#0d0920" },
  neon: { magenta: "#ff3fa4", cyan: "#2de2e6", yellow: "#ffd23f", violet: "#8b5cff", green: "#39ff88", orange: "#ff8a3d" },
  /** The blacklight carpet: near-black ground and its neon squiggles. */
  carpet: { base: "#0d0a1c", shapes: ["#ff3fa4", "#2de2e6", "#ffd23f", "#8b5cff", "#39ff88"] },
  /** The prize corner's plum carpet with gold stars, and the snack bar's diner checker. */
  prizes: { base: "#3a1838", star: "#f5c84a", dot: "#5a2656" },
  snack: { red: "#7a1f26", cream: "#e9e2d6" },
  /** Plush prizes on the shelves and in the claw machines. */
  plush: ["#ff8fc7", "#7fe3ff", "#ffe066", "#b99cff", "#ff9f5a", "#8ff0a4", "#f4f1ea", "#ff6b6b"],
  chrome: "#cfd3dc",
  steel: "#2a2a33",
  black: "#0a0a10",
} as const;

/** Every vertex-coloured matte part of the hall's props. */
export const HALL_MAT = {
  paint: new MeshStandardMaterial({ vertexColors: true, roughness: 0.62, metalness: 0.05 }),
  /** Chrome, steel and lacquer: the same colours, a shinier finish (barely metallic: the scene has no environment map to reflect). */
  gloss: new MeshStandardMaterial({ vertexColors: true, roughness: 0.3, metalness: 0.15 }),
  /** Neon tubes, bulbs and buttons: self-lit, never dimmed by the tone mapper. */
  glow: new MeshBasicMaterial({ vertexColors: true, toneMapped: false }),
  glass: new MeshStandardMaterial({
    color: "#d8ecff", transparent: true, opacity: 0.16, roughness: 0.05, metalness: 0.1, side: DoubleSide, depthWrite: false,
  }),
};

/**
 * Collects primitives, each placed, turned and painted, and merges them into
 * one geometry with a `color` attribute. Call `build()` once; it frees the parts.
 */
export class PartKit {
  private readonly parts: BufferGeometry[] = [];

  box(size: Vec3, at: Vec3, colour: string, rotation?: Vec3): this {
    return this.add(new BoxGeometry(size[0], size[1], size[2]), at, colour, rotation);
  }

  /** An upright cylinder (or cone when `radiusTop` differs) of `height`, centred on `at`. */
  cylinder(radius: number, height: number, at: Vec3, colour: string, { rotation, radiusTop = radius, segments = 12 }: {
    rotation?: Vec3; radiusTop?: number; segments?: number;
  } = {}): this {
    return this.add(new CylinderGeometry(radiusTop, radius, height, segments), at, colour, rotation);
  }

  /** A sphere of `radius`, optionally squashed by `scale`. */
  sphere(radius: number, at: Vec3, colour: string, scale?: Vec3, segments = 10): this {
    return this.add(new SphereGeometry(radius, segments, Math.max(4, Math.round(segments * 0.7))), at, colour, undefined, scale);
  }

  add(geometry: BufferGeometry, at: Vec3, colour: string, rotation?: Vec3, scale?: Vec3): this {
    const turn = new Quaternion().setFromEuler(new Euler(rotation?.[0] ?? 0, rotation?.[1] ?? 0, rotation?.[2] ?? 0));
    geometry.applyMatrix4(new Matrix4().compose(new Vector3(...at), turn, new Vector3(...(scale ?? [1, 1, 1]))));
    const c = new Color(colour);
    const count = geometry.getAttribute("position").count;
    const colours = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      colours[i * 3] = c.r;
      colours[i * 3 + 1] = c.g;
      colours[i * 3 + 2] = c.b;
    }
    geometry.setAttribute("color", new Float32BufferAttribute(colours, 3));
    this.parts.push(geometry);
    return this;
  }

  get size(): number {
    return this.parts.length;
  }

  build(): BufferGeometry {
    const merged = this.parts.length > 0 ? mergeGeometries(this.parts, false) : null;
    for (const part of this.parts) part.dispose();
    this.parts.length = 0;
    const out = merged ?? new BufferGeometry();
    out.computeBoundingBox();
    out.computeBoundingSphere();
    return out;
  }
}

/** Deterministic pseudo-random numbers, so the carpet and the prize shelves never change between loads. */
export function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

type Ctx = CanvasRenderingContext2D;

/** Metres covered by one repeat of the carpet tile and of the room floors. */
export const CARPET_METRES = 2.6;
export const ROOM_FLOOR_METRES = 1.6;

/** Draw `paint` at every wrapped copy of the tile, so a shape crossing an edge continues on the other side. */
function wrapped(w: number, h: number, paint: (dx: number, dy: number) => void): void {
  for (const dx of [-w, 0, w]) for (const dy of [-h, 0, h]) paint(dx, dy);
}

/**
 * The blacklight arcade carpet: a near-black ground scattered with neon
 * squiggles, rings, triangles, zigzags and specks. Seamless on both axes.
 */
export function drawCarpet(ctx: Ctx, w: number, h: number): void {
  const { base, shapes } = HALL.carpet;
  const rand = lcg(0xa2cade);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, w, h);
  // A faint woven speckle first, so the ground is not a flat black.
  for (let i = 0; i < 2600; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(70,60,120,0.35)" : "rgba(0,0,0,0.4)";
    ctx.fillRect(Math.floor(rand() * w), Math.floor(rand() * h), 2, 2);
  }
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const pick = () => shapes[Math.floor(rand() * shapes.length)];
  for (let i = 0; i < 64; i += 1) {
    const x = rand() * w, y = rand() * h, colour = pick(), size = 10 + rand() * 18, turn = rand() * Math.PI * 2;
    const kind = i % 5;
    ctx.strokeStyle = colour;
    ctx.fillStyle = colour;
    ctx.lineWidth = 3 + rand() * 2;
    wrapped(w, h, (dx, dy) => {
      ctx.save();
      ctx.translate(x + dx, y + dy);
      ctx.rotate(turn);
      ctx.beginPath();
      if (kind === 0) {
        // A squiggle: one wave of a sine.
        for (let s = 0; s <= 12; s += 1) {
          const px = -size * 1.4 + (s / 12) * size * 2.8, py = Math.sin((s / 12) * Math.PI * 2) * size * 0.35;
          if (s === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.stroke();
      } else if (kind === 1) {
        ctx.arc(0, 0, size * 0.55, 0, Math.PI * 2);
        ctx.stroke();
      } else if (kind === 2) {
        ctx.moveTo(0, -size * 0.7);
        ctx.lineTo(size * 0.62, size * 0.45);
        ctx.lineTo(-size * 0.62, size * 0.45);
        ctx.closePath();
        ctx.stroke();
      } else if (kind === 3) {
        // A zigzag bolt.
        ctx.moveTo(-size, 0);
        for (let s = 1; s <= 4; s += 1) ctx.lineTo(-size + s * size * 0.5, s % 2 === 1 ? -size * 0.35 : size * 0.35);
        ctx.stroke();
      } else {
        // A four-point sparkle.
        ctx.moveTo(0, -size * 0.6);
        ctx.quadraticCurveTo(0, 0, size * 0.6, 0);
        ctx.quadraticCurveTo(0, 0, 0, size * 0.6);
        ctx.quadraticCurveTo(0, 0, -size * 0.6, 0);
        ctx.quadraticCurveTo(0, 0, 0, -size * 0.6);
        ctx.fill();
      }
      ctx.restore();
    });
  }
  // Small neon specks between the shapes.
  for (let i = 0; i < 140; i += 1) {
    ctx.fillStyle = pick();
    const x = rand() * w, y = rand() * h, r = 1.5 + rand() * 2;
    wrapped(w, h, (dx, dy) => {
      ctx.beginPath();
      ctx.arc(x + dx, y + dy, r, 0, Math.PI * 2);
      ctx.fill();
    });
  }
}

/** The prize corner's plum carpet: a fine dot grid with small gold stars. Seamless. */
export function drawPrizeCarpet(ctx: Ctx, w: number, h: number): void {
  const { base, star, dot } = HALL.prizes;
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = dot;
  for (let y = 8; y < h; y += 16) for (let x = 8; x < w; x += 16) ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
  ctx.fillStyle = star;
  const cell = w / 4;
  for (let row = 0; row < 4; row += 1) {
    for (let col = 0; col < 4; col += 1) {
      const cx = col * cell + (row % 2 === 0 ? cell / 2 : 0), cy = row * cell + cell / 2;
      wrapped(w, h, (dx, dy) => {
        starPath(ctx, cx + dx, cy + dy, cell * 0.16, cell * 0.07);
        ctx.fill();
      });
    }
  }
}

/** The snack bar's diner floor: a red and cream checkerboard. Seamless (an even number of tiles). */
export function drawSnackFloor(ctx: Ctx, w: number, h: number): void {
  const tiles = 4, size = w / tiles;
  for (let row = 0; row < tiles; row += 1) {
    for (let col = 0; col < tiles; col += 1) {
      ctx.fillStyle = (row + col) % 2 === 0 ? HALL.snack.red : HALL.snack.cream;
      ctx.fillRect(col * size, row * size, size, size);
    }
  }
  ctx.fillStyle = "rgba(0,0,0,0.12)";
  for (let i = 0; i <= tiles; i += 1) {
    ctx.fillRect(i * size - 1, 0, 2, h);
    ctx.fillRect(0, i * size - 1, w, 2);
  }
}

/** Adds a five-point star to the current path (begins a new path). */
export function starPath(ctx: Ctx, cx: number, cy: number, outer: number, inner: number): void {
  ctx.beginPath();
  for (let i = 0; i < 10; i += 1) {
    const r = i % 2 === 0 ? outer : inner;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    if (i === 0) ctx.moveTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
    else ctx.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
  }
  ctx.closePath();
}

/** A soft white disc fading to transparent: the glow a cabinet throws on the carpet, tinted per instance. */
export function drawGlowSpot(ctx: Ctx, w: number, h: number): void {
  ctx.clearRect(0, 0, w, h);
  const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
  g.addColorStop(0, "rgba(255,255,255,0.55)");
  g.addColorStop(0.45, "rgba(255,255,255,0.22)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
}

/** The back walls: tall midnight panels with dark grooves and a faint violet sheen. Seamless horizontally. */
export function drawWallPanels(ctx: Ctx, w: number, h: number): void {
  const { base, panel, groove } = HALL.wall;
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, w, h);
  const panels = 4, pw = w / panels;
  for (let i = 0; i < panels; i += 1) {
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, panel);
    g.addColorStop(1, base);
    ctx.fillStyle = g;
    ctx.fillRect(i * pw + 3, 0, pw - 6, h);
    ctx.fillStyle = groove;
    ctx.fillRect(i * pw - 1.5, 0, 3, h);
  }
  // A skirting band at the foot.
  ctx.fillStyle = groove;
  ctx.fillRect(0, h - h * 0.04, w, h * 0.04);
}
