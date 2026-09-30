/**
 * The office maps as pure math and drawing code: the player-centred minimap,
 * the compass bar and the full map share one world → map transform and one
 * painted art style (a game-style map: warm floor, coloured rooms with soft
 * outlines, carpets, tiny desks, crisp walls, gold checkpoint icons).
 *
 * Map space is CSS pixels on a canvas: +x right (east), +y down (south), so
 * every map is north-up. World north is −z. Compass bearings are degrees
 * clockwise from north (east = 90). Nothing here touches the DOM, so the
 * transforms, clamping, bearings, grid labels and hit-testing are unit-tested
 * and the drawing takes any 2D context (an offscreen layer or a test recorder).
 */
import { CHECKPOINT_GOLD, DEPARTMENT_TINTS, ROOM_FLOOR_COLOURS } from "./officePalette";
import {
  FURNITURE_SIZE, allDesks, deskRect, footprint, roomAt,
  type CheckpointKind, type FurnitureKind, type OfficeLayout, type Point, type Rect, type RoomKind,
} from "./officeLayout";

export type MinimapAgentState = "idle" | "working" | "waiting" | "paused";

export interface MapTransform {
  /** Map pixels per metre. */
  scale: number;
  /** Map position of the world origin. */
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
}

export interface MapPoint { x: number; y: number }

// ---------------------------------------------------------------- transforms

/** Fit `bounds` into a widthPx × heightPx map with `padding` on every side, keeping the aspect and centring. */
export function fitTransform(bounds: Rect, widthPx: number, heightPx: number, padding = 6): MapTransform {
  const bw = Math.max(1e-6, bounds.maxX - bounds.minX);
  const bh = Math.max(1e-6, bounds.maxZ - bounds.minZ);
  const innerW = Math.max(1, widthPx - padding * 2);
  const innerH = Math.max(1, heightPx - padding * 2);
  const scale = Math.min(innerW / bw, innerH / bh);
  const offsetX = (widthPx - bw * scale) / 2 - bounds.minX * scale;
  const offsetY = (heightPx - bh * scale) / 2 - bounds.minZ * scale;
  return { scale, offsetX, offsetY, width: widthPx, height: heightPx };
}

/** A north-up map `metresAcross` wide (along its shorter side) with `centre` in the middle of the canvas. */
export function centredTransform(centre: Point, metresAcross: number, widthPx: number, heightPx: number): MapTransform {
  const scale = Math.min(widthPx, heightPx) / Math.max(1e-6, metresAcross);
  return { scale, offsetX: widthPx / 2 - centre.x * scale, offsetY: heightPx / 2 - centre.z * scale, width: widthPx, height: heightPx };
}

export function worldToMap(t: MapTransform, p: Point): MapPoint {
  return { x: t.offsetX + p.x * t.scale, y: t.offsetY + p.z * t.scale };
}

export function mapToWorld(t: MapTransform, p: MapPoint): Point {
  return { x: (p.x - t.offsetX) / t.scale, z: (p.y - t.offsetY) / t.scale };
}

/** Clamp a world point onto the walkable floor (a click on the railing still means "over there"). */
export function clampToRect(p: Point, r: Rect): Point {
  return { x: Math.min(r.maxX, Math.max(r.minX, p.x)), z: Math.min(r.maxZ, Math.max(r.minZ, p.z)) };
}

// ---------------------------------------------------------------------- zoom

/** Minimap zoom: metres across the card. */
export const ZOOM_MIN_M = 16;
export const ZOOM_MAX_M = 60;
export const ZOOM_DEFAULT_M = 26;

export function clampZoom(metres: number): number {
  if (!Number.isFinite(metres)) return ZOOM_DEFAULT_M;
  return Math.min(ZOOM_MAX_M, Math.max(ZOOM_MIN_M, metres));
}

/** One zoom step: `direction` > 0 zooms in (fewer metres), < 0 zooms out. */
export function zoomStep(metres: number, direction: number): number {
  if (direction === 0) return clampZoom(metres);
  return clampZoom(Math.round(metres * (direction > 0 ? 1 / 1.25 : 1.25)));
}

// ------------------------------------------------------------- edge clamping

export interface EdgeMarker extends MapPoint {
  /** True when the point lay outside the inset area and was pulled onto its edge. */
  clamped: boolean;
  /** Direction from the map centre to the real point, radians, canvas convention (0 = right, π/2 = down). */
  angle: number;
}

/**
 * Keep a marker inside a width × height map with `inset` px of margin. A point
 * outside is pulled towards the centre onto the inset edge (like an off-screen
 * teammate on a game minimap), keeping its direction.
 */
export function clampToEdge(p: MapPoint, width: number, height: number, inset: number): EdgeMarker {
  const cx = width / 2;
  const cy = height / 2;
  const dx = p.x - cx;
  const dy = p.y - cy;
  const angle = Math.atan2(dy, dx);
  const hw = Math.max(0, cx - inset);
  const hh = Math.max(0, cy - inset);
  if (Math.abs(dx) <= hw && Math.abs(dy) <= hh) return { x: p.x, y: p.y, clamped: false, angle };
  const k = Math.min(Math.abs(dx) > 1e-9 ? hw / Math.abs(dx) : Infinity, Math.abs(dy) > 1e-9 ? hh / Math.abs(dy) : Infinity);
  return { x: cx + dx * k, y: cy + dy * k, clamped: true, angle };
}

// ------------------------------------------------------------------- compass

function wrap360(deg: number): number {
  const d = deg % 360;
  return d < 0 ? d + 360 : d;
}

/** Compass bearing (degrees clockwise from north, [0, 360)) of a world direction; north is −z. */
export function bearingOf(dx: number, dz: number): number {
  if (Math.abs(dx) < 1e-12 && Math.abs(dz) < 1e-12) return 0;
  return wrap360((Math.atan2(dx, -dz) * 180) / Math.PI);
}

/** Bearing of a yaw/heading (direction = (sin yaw, cos yaw), so yaw 0 looks south = 180°). */
export function yawToBearing(yaw: number): number {
  return bearingOf(Math.sin(yaw), Math.cos(yaw));
}

/** Signed angle from `heading` to `bearing`, in [-180, 180): negative = to the left. */
export function relativeBearing(bearing: number, heading: number): number {
  return wrap360(bearing - heading + 180) - 180;
}

/** Compass point keys (8 winds) in bearing order; labels come from i18n. */
export const COMPASS_POINTS = ["n", "ne", "e", "se", "s", "sw", "w", "nw"] as const;
export type CompassPoint = (typeof COMPASS_POINTS)[number];

export interface CompassTick {
  /** Bearing of the tick, [0, 360). */
  deg: number;
  /** Horizontal position as a fraction of the bar width, 0 = left edge, 1 = right edge. */
  at: number;
  /** Set on the eight winds (every 45°). */
  point?: CompassPoint;
  major: boolean;
}

/** Ticks every `stepDeg` visible on a bar that spans `spanDeg` centred on `heading`. */
export function compassTicks(heading: number, spanDeg: number, stepDeg = 15): CompassTick[] {
  const out: CompassTick[] = [];
  const half = spanDeg / 2;
  const first = Math.ceil((heading - half) / stepDeg) * stepDeg;
  for (let raw = first; raw <= heading + half + 1e-9; raw += stepDeg) {
    const deg = wrap360(raw);
    const rounded = Math.round(deg * 1000) / 1000;
    const isWind = Math.abs(rounded % 45) < 1e-6;
    out.push({
      deg: rounded,
      at: (raw - (heading - half)) / spanDeg,
      point: isWind ? COMPASS_POINTS[Math.round(rounded / 45) % 8] : undefined,
      major: isWind,
    });
  }
  return out;
}

/** Where a bearing sits on the bar (fraction of the width), or null when it lies outside the span. */
export function compassPosition(bearing: number, heading: number, spanDeg: number): number | null {
  const rel = relativeBearing(bearing, heading);
  if (Math.abs(rel) > spanDeg / 2) return null;
  return 0.5 + rel / spanDeg;
}

// ---------------------------------------------------------------------- grid

export interface MapGrid {
  columns: number;
  rows: number;
  /** Cell edge in metres (square cells). */
  cell: number;
  originX: number;
  originZ: number;
}

/** A square grid over `bounds`: `columns` letters across, as many numbered rows as the depth needs. */
export function mapGrid(bounds: Rect, columns = 8): MapGrid {
  const width = Math.max(1e-6, bounds.maxX - bounds.minX);
  const depth = Math.max(1e-6, bounds.maxZ - bounds.minZ);
  const cell = width / Math.max(1, columns);
  return { columns: Math.max(1, columns), rows: Math.max(1, Math.ceil(depth / cell - 1e-6)), cell, originX: bounds.minX, originZ: bounds.minZ };
}

/** Column letters A, B, … Z, AA, AB, … */
export function columnLabel(index: number): string {
  let n = Math.max(0, Math.floor(index));
  let out = "";
  do {
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return out;
}

/** The grid cell of a world point, like "C4", clamped onto the grid. */
export function gridLabel(grid: MapGrid, p: Point): string {
  const col = Math.min(grid.columns - 1, Math.max(0, Math.floor((p.x - grid.originX) / grid.cell)));
  const row = Math.min(grid.rows - 1, Math.max(0, Math.floor((p.z - grid.originZ) / grid.cell)));
  return `${columnLabel(col)}${row + 1}`;
}

// --------------------------------------------------------------- hit testing

/** The item whose map point is closest to `at` within `radiusPx`, or null. */
export function pickNearest<T extends MapPoint>(items: readonly T[], at: MapPoint, radiusPx: number): T | null {
  let best: T | null = null;
  let bestD = radiusPx * radiusPx;
  for (const item of items) {
    const d = (item.x - at.x) ** 2 + (item.y - at.y) ** 2;
    if (d <= bestD) { best = item; bestD = d; }
  }
  return best;
}

export type MinimapPlace =
  | { kind: "checkpoint"; id: CheckpointKind }
  | { kind: "room"; id: RoomKind }
  | { kind: "department"; label: string };

/** What lies under a world point: a checkpoint (within its radius) beats a room, a room beats a department. */
export function placeAt(layout: OfficeLayout, p: Point): MinimapPlace | null {
  for (const cp of layout.checkpoints) {
    if ((cp.x - p.x) ** 2 + (cp.z - p.z) ** 2 <= cp.radius * cp.radius) return { kind: "checkpoint", id: cp.id };
  }
  const room = roomAt(layout, p);
  if (room) return { kind: "room", id: room.kind };
  const dept = layout.departments.find((d) => p.x >= d.minX && p.x <= d.maxX && p.z >= d.minZ && p.z <= d.maxZ);
  return dept ? { kind: "department", label: dept.label } : null;
}

// -------------------------------------------------------------------- colour

function parseHex(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1];
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/** Lighten (amount > 0) or darken (< 0) a hex colour by mixing with white or black. Non-hex passes through. */
export function shade(hex: string, amount: number): string {
  const rgb = parseHex(hex);
  if (!rgb) return hex;
  const target = amount >= 0 ? 255 : 0;
  const k = Math.min(1, Math.abs(amount));
  const out = rgb.map((c) => Math.round(c + (target - c) * k));
  return `#${out.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** Dark or white ink for text on a hex background (white for anything that is not hex). */
export function inkOn(hex: string): string {
  const rgb = parseHex(hex);
  if (!rgb) return "#ffffff";
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 ? "#1b1f2a" : "#ffffff";
}

/** First letter of a name for a portrait token ("?" when empty). */
export function initialOf(name: string): string {
  const ch = name.trim().charAt(0);
  return ch ? ch.toLocaleUpperCase() : "?";
}

// ------------------------------------------------------------ painted palette

/**
 * The map art's own colours. Like the 3D diorama the painted map looks the
 * same in light and dark mode; only the chrome around it reads theme tokens.
 */
export const MAP_PAINT = {
  space: "#121833",
  star: "#e8ecff",
  slab: "#474d5c",
  slabEdge: "#2c303b",
  railing: "#c9ced6",
  floor: "#e9d3ad",
  plank: "#b98a5e",
  deskTop: "#fbf3e2",
  deskEdge: "#9c7b58",
  monitor: "#2a2d35",
  wall: "#2b2f3a",
  glass: "#d9f0ff",
  furniture: "#8d7a66",
  plant: "#5fa35a",
  rug: "#d8c8ae",
  checkpoint: CHECKPOINT_GOLD.face,
  checkpointDeep: CHECKPOINT_GOLD.faceDeep,
  checkpointIcon: CHECKPOINT_GOLD.icon,
  working: "#4ade80",
  waiting: "#fbbf24",
  idle: "#aab3c2",
  paused: "#aab3c2",
  tokenRim: "#1b1f2a",
  player: "#ffd23f",
  playerRim: "#1b1f2a",
  label: "#ffffff",
  labelHalo: "rgba(20,24,40,0.85)",
} as const;

const FURNITURE_PAINT: Partial<Record<FurnitureKind, string>> = {
  couch: "#4a5366", coffeeTable: "#c89f74", meetingTable: "#c89f74", teamBoard: "#f4f4f0", receptionDesk: "#f2eee6",
  lockers: "#5fa8a0", mirror: "#d6ecf8", coffeeBar: "#8a6240", waterCooler: "#6fb7ea", arcade: "#6a58b0",
  beanbag: "#e27d60", bookshelf: "#8a6240", plant: MAP_PAINT.plant, rug: MAP_PAINT.rug, elevator: "#c9ced6",
  breakSofa: "#d8cdb9", breakSofaCorner: "#d8cdb9", breakTable: "#d6b98f", breakRug: "#e2c9a8", breakBookcase: "#a88660",
  readingNook: "#93a58a", foosball: "#3f7d4a", breakPlant: MAP_PAINT.plant, arcadeMat: "#2a2548",
  leadWall: "#3a2416", executiveRug: "#26335a", guestChair: "#a9683c", chesterfield: "#7a2c22", loungeTable: "#d8ae52",
  executiveBar: "#5b3a25", globe: "#5f8f96", floorLamp: "#d8ae52", commandWall: "#3a2a20", commandDesk: "#c9b79c",
  serverRack: "#23272e", coldAisle: "#b3bac3", nocConsole: "#2b3038", statusWall: "#1a1d22", ups: "#2b3038", fireSuppression: "#c8262b",
  teamWall: "#6a4631", credenza: "#6a4631", designerPlant: MAP_PAINT.plant, teamRug: "#e6dccb",
  wardrobeWall: "#d2b187", dressingMirror: "#e9edf0", tailorDummy: "#e8dfcf", dressingBench: "#7e9378", coatStand: "#a88660",
  roundRug: "#e6ddca",
  brandWall: "#d2b286", agentTotem: "#2b2f33", lobbySofa: "#efe9de", lobbyArmchair: "#a8653a", lobbyTable: "#e7ddcc",
  sideTable: "#f1eeea", lobbyLamp: "#c7a15e", oliveTree: MAP_PAINT.plant, awardCase: "#d2b286", entranceMat: "#3b3a37", lobbyRug: "#e8dfcf",
  spawnPad: "#d8c08c", spawnTerminal: "#2b2f33",
};

export const STATE_RING: Record<MinimapAgentState, string> = {
  working: MAP_PAINT.working, waiting: MAP_PAINT.waiting, idle: MAP_PAINT.idle, paused: MAP_PAINT.paused,
};

/** Checkpoint icon per checkpoint kind; the same pictures as the 3D tokens (CheckpointMarker). */
export const CHECKPOINT_ICON_KEYS: Record<CheckpointKind, "spawn" | "plus" | "list" | "team" | "shirt" | "star" | "coffee" | "elevator" | "target"> = {
  spawn: "spawn", launch: "spawn", create: "plus", manage: "list", team: "team", wardrobe: "shirt", lead: "star", break: "coffee", elevator: "elevator", mission: "target",
};

/** Stroke-only icons in a 24 × 24 box; kept in sync with CheckpointMarker's paths. */
const ICON_PATHS: Record<(typeof CHECKPOINT_ICON_KEYS)[CheckpointKind], string> = {
  spawn: "M12 8.5v7M8.5 12h7M5.2 7.5A8 8 0 0 1 18.8 7.5M18.8 16.5A8 8 0 0 1 5.2 16.5",
  plus: "M12 5v14M5 12h14",
  list: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  team: "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16.5 11a2.5 2.5 0 1 0 0-5M18 14.5c2 .6 3.5 2.4 3.5 5",
  shirt: "M8 3L3 6l2 4 2.5-1v12h9V9l2.5 1 2-4-5-3c-.5 1.5-2 2.5-4 2.5S8.5 4.5 8 3z",
  star: "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z",
  coffee: "M4 9h12v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V9zM16 10h1.5a2.5 2.5 0 0 1 0 5H16M8 3.5c0 1 1 1 1 2M12 3.5c0 1 1 1 1 2",
  elevator: "M5 3h14v18H5zM9 10l3-3 3 3M9 14l3 3 3-3",
  target: "M12 3a9 9 0 1 1 0 18a9 9 0 1 1 0-18zM12 8a4 4 0 1 1 0 8a4 4 0 1 1 0-8zM12 1v4M12 19v4M1 12h4M19 12h4",
};

// ------------------------------------------------------------------ drawing

type Ctx = CanvasRenderingContext2D;

function roundRectPath(ctx: Ctx, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.arcTo(x + w, y, x + w, y + rr, rr);
  ctx.lineTo(x + w, y + h - rr);
  ctx.arcTo(x + w, y + h, x + w - rr, y + h, rr);
  ctx.lineTo(x + rr, y + h);
  ctx.arcTo(x, y + h, x, y + h - rr, rr);
  ctx.lineTo(x, y + rr);
  ctx.arcTo(x, y, x + rr, y, rr);
  ctx.closePath();
}

function rectPath(ctx: Ctx, t: MapTransform, r: Rect, radiusM = 0): { x: number; y: number; w: number; h: number } {
  const a = worldToMap(t, { x: r.minX, z: r.minZ });
  const w = (r.maxX - r.minX) * t.scale;
  const h = (r.maxZ - r.minZ) * t.scale;
  roundRectPath(ctx, a.x, a.y, w, h, radiusM * t.scale);
  return { x: a.x, y: a.y, w, h };
}

/** Small deterministic pseudo-random sequence (stars must not flicker between rebuilds). */
function seeded(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

/**
 * The painted floor: night sky with stars, slab and railing, warm planks,
 * department carpets, rooms, furniture, desks and crisp walls. `area` is the
 * world rectangle to cover (the layout bounds plus any margin the caller wants).
 */
export function drawFloorArt(ctx: Ctx, layout: OfficeLayout, t: MapTransform, area: Rect): void {
  ctx.globalAlpha = 1;
  ctx.fillStyle = MAP_PAINT.space;
  const sky = worldToMap(t, { x: area.minX, z: area.minZ });
  ctx.fillRect(sky.x, sky.y, (area.maxX - area.minX) * t.scale, (area.maxZ - area.minZ) * t.scale);

  // Stars: about one per 14 m², deterministic per layout size.
  const rand = seeded(Math.round((area.maxX - area.minX) * 131 + (area.maxZ - area.minZ) * 17));
  const count = Math.min(900, Math.round(((area.maxX - area.minX) * (area.maxZ - area.minZ)) / 14));
  ctx.fillStyle = MAP_PAINT.star;
  for (let i = 0; i < count; i += 1) {
    const p = worldToMap(t, { x: area.minX + rand() * (area.maxX - area.minX), z: area.minZ + rand() * (area.maxZ - area.minZ) });
    ctx.globalAlpha = 0.25 + rand() * 0.55;
    const r = 0.4 + rand() * 0.8;
    ctx.fillRect(p.x - r / 2, p.y - r / 2, r, r);
  }
  ctx.globalAlpha = 1;

  // Slab with a soft shadow, then the railing line and the warm plank floor.
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.45)";
  ctx.shadowBlur = Math.max(4, t.scale * 0.8);
  ctx.fillStyle = MAP_PAINT.slab;
  rectPath(ctx, t, layout.bounds, 0.8);
  ctx.fill();
  ctx.restore();
  ctx.strokeStyle = MAP_PAINT.slabEdge;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.fillStyle = MAP_PAINT.floor;
  rectPath(ctx, t, layout.floor, 0.4);
  ctx.fill();
  ctx.strokeStyle = MAP_PAINT.railing;
  ctx.lineWidth = 1;
  ctx.stroke();

  if (t.scale >= 4) {
    // Faint plank seams, every 0.9 m, running east-west.
    ctx.save();
    rectPath(ctx, t, layout.floor, 0.4);
    ctx.clip();
    ctx.strokeStyle = MAP_PAINT.plank;
    ctx.globalAlpha = 0.14;
    ctx.lineWidth = 1;
    ctx.beginPath();
    const left = worldToMap(t, { x: layout.floor.minX, z: 0 }).x;
    const right = worldToMap(t, { x: layout.floor.maxX, z: 0 }).x;
    for (let z = layout.floor.minZ; z <= layout.floor.maxZ; z += 0.9) {
      const y = Math.round(worldToMap(t, { x: 0, z }).y) + 0.5;
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
    }
    ctx.stroke();
    ctx.restore();
    ctx.globalAlpha = 1;
  }

  // Department carpets and rooms: painted areas with soft darker outlines.
  ctx.lineWidth = Math.max(1, Math.min(2, t.scale * 0.12));
  for (const dept of layout.departments) {
    const tint = DEPARTMENT_TINTS[dept.tint % DEPARTMENT_TINTS.length];
    rectPath(ctx, t, dept, 0.5);
    ctx.globalAlpha = 0.85;
    ctx.fillStyle = tint;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = shade(tint, -0.3);
    ctx.stroke();
  }
  for (const room of layout.rooms) {
    const base = ROOM_FLOOR_COLOURS[room.kind].base;
    rectPath(ctx, t, room, room.walled ? 0.15 : 0.5);
    ctx.globalAlpha = 0.92;
    ctx.fillStyle = base;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = shade(base, -0.32);
    ctx.stroke();
  }

  // Furniture: rugs first, then solid pieces; plants and beanbags are round.
  for (const pass of ["rug", "solid"] as const) {
    for (const item of layout.furniture) {
      const isRug = item.kind === "rug" || item.kind === "executiveRug" || item.kind === "teamRug"
        || item.kind === "breakRug" || item.kind === "arcadeMat" || item.kind === "lobbyRug"
        || item.kind === "entranceMat" || item.kind === "roundRug" || item.kind === "spawnPad";
      if ((pass === "rug") !== isRug) continue;
      const colour = FURNITURE_PAINT[item.kind] ?? MAP_PAINT.furniture;
      const fp = footprint(item);
      if (item.kind === "plant" || item.kind === "designerPlant" || item.kind === "breakPlant" || item.kind === "beanbag" || item.kind === "spawnPad") {
        const c = worldToMap(t, item);
        ctx.beginPath();
        ctx.arc(c.x, c.y, Math.max(1.2, (FURNITURE_SIZE[item.kind].w / 2) * t.scale * 0.85), 0, Math.PI * 2);
      } else {
        rectPath(ctx, t, fp, isRug ? 0.3 : 0.08);
      }
      ctx.fillStyle = colour;
      ctx.fill();
      if (!isRug) {
        ctx.strokeStyle = shade(colour, -0.35);
        ctx.lineWidth = 0.8;
        ctx.stroke();
      }
    }
  }

  // Desks: tiny light blocks with the monitor on the far side from the chair.
  for (const desk of allDesks(layout)) {
    const c = worldToMap(t, desk);
    const rect = deskRect(desk);
    const deskW = (rect.maxX - rect.minX) * t.scale;
    const deskD = (rect.maxZ - rect.minZ) * t.scale;
    roundRectPath(ctx, c.x - deskW / 2, c.y - deskD / 2, deskW, deskD, Math.min(2, deskD * 0.2));
    ctx.fillStyle = MAP_PAINT.deskTop;
    ctx.fill();
    ctx.strokeStyle = MAP_PAINT.deskEdge;
    ctx.lineWidth = 0.8;
    ctx.stroke();
    if (t.scale >= 5) {
      const mh = Math.max(1, deskD * 0.18);
      // A north-facing agent looks at a monitor on the desk's north edge.
      const my = desk.facing === "north" ? c.y - deskD / 2 + mh * 0.6 : c.y + deskD / 2 - mh * 1.6;
      ctx.fillStyle = MAP_PAINT.monitor;
      ctx.fillRect(c.x - deskW * 0.28, my, deskW * 0.56, mh);
    }
  }

  // Walls: crisp dark lines with a thin glass highlight; door gaps are simply not drawn.
  const wallW = Math.max(1.5, Math.min(3.5, t.scale * 0.2));
  ctx.lineCap = "round";
  ctx.beginPath();
  for (const w of layout.walls) {
    const a = worldToMap(t, { x: w.x1, z: w.z1 });
    const b = worldToMap(t, { x: w.x2, z: w.z2 });
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
  }
  ctx.strokeStyle = MAP_PAINT.wall;
  ctx.lineWidth = wallW;
  ctx.stroke();
  ctx.strokeStyle = MAP_PAINT.glass;
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = Math.max(0.5, wallW * 0.3);
  ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.lineCap = "butt";
}

/** A gold checkpoint badge (hexagon) with its white icon, `size` px across. */
export function drawCheckpointBadge(ctx: Ctx, x: number, y: number, size: number, kind: CheckpointKind): void {
  const r = size / 2;
  ctx.beginPath();
  for (let i = 0; i < 6; i += 1) {
    const a = (Math.PI / 3) * i;
    const px = x + Math.cos(a) * r;
    const py = y + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fillStyle = MAP_PAINT.checkpoint;
  ctx.fill();
  ctx.strokeStyle = MAP_PAINT.checkpointDeep;
  ctx.lineWidth = 1.2;
  ctx.stroke();
  if (typeof Path2D === "undefined") return;
  const path = new Path2D(ICON_PATHS[CHECKPOINT_ICON_KEYS[kind]]);
  const k = (size * 0.62) / 24;
  ctx.save();
  ctx.translate(x - 12 * k, y - 12 * k);
  ctx.scale(k, k);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = MAP_PAINT.checkpointIcon;
  ctx.lineWidth = 2.6;
  ctx.stroke(path);
  ctx.restore();
}

/** Every checkpoint of the layout as a badge. */
export function drawCheckpoints(ctx: Ctx, layout: OfficeLayout, t: MapTransform, size: number): void {
  for (const cp of layout.checkpoints) {
    const m = worldToMap(t, cp);
    drawCheckpointBadge(ctx, m.x, m.y, size, cp.id);
  }
}

/** The camera's view as a soft white cone from `origin`, fading out towards `reach` px. */
export function drawViewCone(ctx: Ctx, origin: MapPoint, yaw: number, halfWidth: number, reach: number): void {
  const half = Math.max(0.12, Math.min(Math.PI / 2 - 0.05, halfWidth));
  // World heading (sin, cos) maps straight onto map (x, y): +x east → right, +z south → down.
  const centre = Math.atan2(Math.cos(yaw), Math.sin(yaw));
  ctx.save();
  const grad = typeof ctx.createRadialGradient === "function" ? ctx.createRadialGradient(origin.x, origin.y, 0, origin.x, origin.y, reach) : null;
  if (grad) {
    grad.addColorStop(0, "rgba(255,255,255,0.55)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = grad;
  } else {
    ctx.fillStyle = "rgba(255,255,255,0.3)";
  }
  ctx.beginPath();
  ctx.moveTo(origin.x, origin.y);
  ctx.arc(origin.x, origin.y, reach, centre - half, centre + half);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

/** The person: a bright arrow pointing along `heading` (same convention as the player body). */
export function drawPlayerArrow(ctx: Ctx, x: number, y: number, heading: number, size = 9): void {
  const dx = Math.sin(heading);
  const dy = Math.cos(heading);
  const len = size;
  const wing = size * 0.68;
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.45)";
  ctx.shadowBlur = 3;
  ctx.beginPath();
  ctx.moveTo(x + dx * len, y + dy * len);
  ctx.lineTo(x - dx * len * 0.62 + dy * wing, y - dy * len * 0.62 - dx * wing);
  ctx.lineTo(x - dx * len * 0.22, y - dy * len * 0.22);
  ctx.lineTo(x - dx * len * 0.62 - dy * wing, y - dy * len * 0.62 + dx * wing);
  ctx.closePath();
  ctx.fillStyle = MAP_PAINT.player;
  ctx.fill();
  ctx.restore();
  ctx.strokeStyle = MAP_PAINT.playerRim;
  ctx.lineWidth = 1.4;
  ctx.lineJoin = "round";
  ctx.stroke();
}

export interface AgentToken extends MapPoint {
  id: string;
  name: string;
  state: MinimapAgentState;
  /** The agent's palette colour (token fill). */
  colour: string;
  selected: boolean;
}

/**
 * A round portrait-like token: the agent's initial on its palette colour
 * inside a status ring (working green, waiting amber, idle grey, paused a
 * hollow dashed ring). `pulse` in [0, 1) animates the selected agent's halo.
 */
export function drawAgentToken(ctx: Ctx, token: AgentToken, radius: number, pulse: number | null): void {
  const { x, y } = token;
  const r = token.selected ? radius * 1.3 : radius;
  if (token.selected) {
    const phase = pulse ?? 0.35;
    ctx.beginPath();
    ctx.arc(x, y, r + 3 + phase * 6, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(255,255,255,${(0.9 * (1 - phase)).toFixed(3)})`;
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  // Dark rim for contrast on any floor colour.
  ctx.beginPath();
  ctx.arc(x, y, r + 2.6, 0, Math.PI * 2);
  ctx.fillStyle = MAP_PAINT.tokenRim;
  ctx.fill();
  const ring = STATE_RING[token.state];
  ctx.beginPath();
  ctx.arc(x, y, r + 1.4, 0, Math.PI * 2);
  if (token.state === "paused") {
    ctx.setLineDash?.([2, 2]);
    ctx.strokeStyle = ring;
    ctx.lineWidth = 1.4;
    ctx.stroke();
    ctx.setLineDash?.([]);
  } else {
    ctx.strokeStyle = ring;
    ctx.lineWidth = 2.2;
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.arc(x, y, r - 0.2, 0, Math.PI * 2);
  ctx.globalAlpha = token.state === "paused" ? 0.55 : 1;
  ctx.fillStyle = token.colour;
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.fillStyle = inkOn(token.colour);
  ctx.font = `800 ${Math.round(r * 1.25)}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(initialOf(token.name), x, y + 0.5);
}

/** An off-screen agent on the card edge: a small arrow in its status colour pointing towards it. */
export function drawEdgeArrow(ctx: Ctx, x: number, y: number, angle: number, token: Pick<AgentToken, "colour" | "state" | "selected">): void {
  const size = token.selected ? 8 : 6;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const pt = (along: number, across: number): MapPoint => ({ x: x + cos * along - sin * across, y: y + sin * along + cos * across });
  const tip = pt(size, 0);
  const l = pt(-size * 0.7, size * 0.75);
  const back = pt(-size * 0.25, 0);
  const r = pt(-size * 0.7, -size * 0.75);
  ctx.beginPath();
  ctx.moveTo(tip.x, tip.y);
  ctx.lineTo(l.x, l.y);
  ctx.lineTo(back.x, back.y);
  ctx.lineTo(r.x, r.y);
  ctx.closePath();
  ctx.fillStyle = token.colour;
  ctx.fill();
  ctx.strokeStyle = STATE_RING[token.state];
  ctx.lineWidth = token.selected ? 2 : 1.5;
  ctx.lineJoin = "round";
  ctx.stroke();
}

/** A game-map label: white text with a dark halo, centred on (x, y). */
export function drawMapLabel(ctx: Ctx, text: string, x: number, y: number, px: number, weight = 700): void {
  if (!text) return;
  ctx.font = `${weight} ${px}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.strokeStyle = MAP_PAINT.labelHalo;
  ctx.lineWidth = Math.max(2.5, px * 0.32);
  ctx.strokeText(text, x, y);
  ctx.fillStyle = MAP_PAINT.label;
  ctx.fillText(text, x, y);
}
