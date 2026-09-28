/**
 * The office minimap as pure drawing code: a north-up plan of the floor with
 * the agents, the person's character and (optionally) the camera's view.
 *
 * Map space is CSS pixels on the minimap canvas: +x right (east), +y down
 * (south). The 3D camera looks from the south-east, but the map stays
 * north-up so it reads like a floor plan. Nothing here touches the DOM, so
 * the transform and hit-testing are unit-tested and the drawing takes any
 * 2D context (the component's offscreen base canvas, or a test recorder).
 */
import { DEPARTMENT_TINTS, ROOM_FLOOR_COLOURS, CHECKPOINT_GOLD } from "./officePalette";
import { DESK_SIZE, allDesks, roomAt, type CheckpointKind, type OfficeLayout, type Point, type Rect, type RoomKind } from "./officeLayout";

export type MinimapAgentState = "idle" | "working" | "waiting" | "paused";

export interface MinimapTransform {
  /** Map pixels per metre. */
  scale: number;
  /** Map position of the world origin. */
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
  bounds: Rect;
}

export interface MapPoint { x: number; y: number }

export interface MinimapColours {
  /** The slab outside the walkable floor (railing edge). */
  slab: string;
  floor: string;
  outline: string;
  wall: string;
  desk: string;
  /** Room fills per kind, drawn at `roomAlpha` over the floor. */
  rooms: Record<RoomKind, string>;
  roomAlpha: number;
  /** Department carpet tints, indexed by `Department.tint` (wraps). */
  carpets: readonly string[];
  carpetAlpha: number;
  checkpoint: string;
  checkpointRim: string;
  working: string;
  waiting: string;
  idle: string;
  paused: string;
  selected: string;
  player: string;
  playerRim: string;
  camera: string;
}

export interface MinimapAgentDot extends Point { id: string; state: MinimapAgentState; selected: boolean }

/**
 * The camera's view on the floor: its ground position, the heading it looks
 * along (same convention as the player: direction = (sin yaw, cos yaw), so
 * 0 looks south) and the half-angle of the view in radians.
 */
export interface MinimapCamera extends Point { yaw: number; halfWidth: number }

export interface MinimapDynamic {
  agents: readonly MinimapAgentDot[];
  player: Point & { heading: number };
  camera: MinimapCamera | null;
  colours: MinimapColours;
  /** Clear the canvas first (default). Pass false to paint over a base already blitted into the same canvas. */
  clear?: boolean;
}

/** Fit `bounds` into a widthPx × heightPx map with `padding` on every side, keeping the aspect and centring. */
export function minimapTransform(bounds: Rect, widthPx: number, heightPx: number, padding = 6): MinimapTransform {
  const bw = Math.max(1e-6, bounds.maxX - bounds.minX);
  const bh = Math.max(1e-6, bounds.maxZ - bounds.minZ);
  const innerW = Math.max(1, widthPx - padding * 2);
  const innerH = Math.max(1, heightPx - padding * 2);
  const scale = Math.min(innerW / bw, innerH / bh);
  const offsetX = (widthPx - bw * scale) / 2 - bounds.minX * scale;
  const offsetY = (heightPx - bh * scale) / 2 - bounds.minZ * scale;
  return { scale, offsetX, offsetY, width: widthPx, height: heightPx, bounds };
}

/** Map height (CSS px) for a given width that keeps the floor's aspect, clamped to [minPx, maxPx]. */
export function minimapHeightFor(bounds: Rect, widthPx: number, padding = 6, minPx = 110, maxPx = 300): number {
  const bw = Math.max(1e-6, bounds.maxX - bounds.minX);
  const bh = Math.max(1e-6, bounds.maxZ - bounds.minZ);
  const natural = (widthPx - padding * 2) * (bh / bw) + padding * 2;
  return Math.round(Math.min(maxPx, Math.max(minPx, natural)));
}

export function worldToMap(t: MinimapTransform, p: Point): MapPoint {
  return { x: t.offsetX + p.x * t.scale, y: t.offsetY + p.z * t.scale };
}

export function mapToWorld(t: MinimapTransform, p: MapPoint): Point {
  return { x: (p.x - t.offsetX) / t.scale, z: (p.y - t.offsetY) / t.scale };
}

/** Clamp a world point onto the walkable floor (a click on the railing still means "over there"). */
export function clampToRect(p: Point, r: Rect): Point {
  return { x: Math.min(r.maxX, Math.max(r.minX, p.x)), z: Math.min(r.maxZ, Math.max(r.minZ, p.z)) };
}

/** The agent whose dot is closest to `at` (map px) within `radiusPx`, or null. */
export function agentAt<T extends Point & { id: string }>(t: MinimapTransform, agents: readonly T[], at: MapPoint, radiusPx = 8): T | null {
  let best: T | null = null;
  let bestD = radiusPx * radiusPx;
  for (const agent of agents) {
    const m = worldToMap(t, agent);
    const d = (m.x - at.x) ** 2 + (m.y - at.y) ** 2;
    if (d <= bestD) { best = agent; bestD = d; }
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

/** Theme colours for the map. `token(name)` returns a CSS custom property's raw value ("H S% L%"), or "". */
export function resolveMinimapColours(token: (name: string) => string): MinimapColours {
  const hsl = (name: string, fallback: string, alpha?: number): string => {
    const raw = token(name).trim();
    if (!raw) return fallback;
    return alpha === undefined ? `hsl(${raw})` : `hsl(${raw} / ${alpha})`;
  };
  return {
    slab: hsl("--muted", "#e5e7eb"),
    floor: hsl("--card", "#ffffff"),
    outline: hsl("--border", "#d4d4d8"),
    wall: hsl("--foreground", "#171717", 0.7),
    desk: hsl("--muted-foreground", "#737373", 0.75),
    rooms: {
      lead: ROOM_FLOOR_COLOURS.lead.base,
      team: ROOM_FLOOR_COLOURS.team.base,
      wardrobe: ROOM_FLOOR_COLOURS.wardrobe.base,
      reception: ROOM_FLOOR_COLOURS.reception.base,
      break: ROOM_FLOOR_COLOURS.break.base,
    },
    roomAlpha: 0.45,
    carpets: DEPARTMENT_TINTS,
    carpetAlpha: 0.35,
    checkpoint: CHECKPOINT_GOLD.face,
    checkpointRim: CHECKPOINT_GOLD.faceDeep,
    working: hsl("--success", "#16a34a"),
    waiting: hsl("--warning", "#d97706"),
    idle: hsl("--muted-foreground", "#737373"),
    paused: hsl("--muted-foreground", "#737373"),
    selected: hsl("--accent", "#2563eb"),
    player: CHECKPOINT_GOLD.ring,
    playerRim: hsl("--background", "#ffffff"),
    camera: hsl("--accent", "#2563eb", 0.16),
  };
}

function fillRect(ctx: CanvasRenderingContext2D, t: MinimapTransform, r: Rect): void {
  const a = worldToMap(t, { x: r.minX, z: r.minZ });
  ctx.fillRect(a.x, a.y, (r.maxX - r.minX) * t.scale, (r.maxZ - r.minZ) * t.scale);
}

function hexagon(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number): void {
  ctx.beginPath();
  for (let i = 0; i < 6; i += 1) {
    const a = (Math.PI / 3) * i;
    const x = cx + Math.cos(a) * r;
    const y = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

/** The static floor: slab, floor, room fills, department carpets, desks, walls (with door gaps) and checkpoints. */
export function drawMinimapBase(ctx: CanvasRenderingContext2D, layout: OfficeLayout, t: MinimapTransform, colours: MinimapColours): void {
  ctx.clearRect(0, 0, t.width, t.height);
  ctx.globalAlpha = 1;
  ctx.fillStyle = colours.slab;
  fillRect(ctx, t, layout.bounds);
  ctx.fillStyle = colours.floor;
  fillRect(ctx, t, layout.floor);

  ctx.globalAlpha = colours.roomAlpha;
  for (const room of layout.rooms) {
    ctx.fillStyle = colours.rooms[room.kind];
    fillRect(ctx, t, room);
  }
  ctx.globalAlpha = colours.carpetAlpha;
  for (const dept of layout.departments) {
    ctx.fillStyle = colours.carpets[dept.tint % Math.max(1, colours.carpets.length)] ?? colours.slab;
    fillRect(ctx, t, dept);
  }
  ctx.globalAlpha = 1;

  ctx.fillStyle = colours.desk;
  for (const desk of allDesks(layout)) {
    fillRect(ctx, t, { minX: desk.x - DESK_SIZE.w / 2, maxX: desk.x + DESK_SIZE.w / 2, minZ: desk.z - DESK_SIZE.d / 2, maxZ: desk.z + DESK_SIZE.d / 2 });
  }

  ctx.strokeStyle = colours.outline;
  ctx.lineWidth = 1;
  const f = worldToMap(t, { x: layout.floor.minX, z: layout.floor.minZ });
  ctx.strokeRect(f.x + 0.5, f.y + 0.5, (layout.floor.maxX - layout.floor.minX) * t.scale - 1, (layout.floor.maxZ - layout.floor.minZ) * t.scale - 1);

  // Walls come pre-split around their doors, so the gaps are simply not drawn.
  ctx.strokeStyle = colours.wall;
  ctx.lineWidth = Math.max(1, Math.min(2, t.scale * 0.2));
  ctx.lineCap = "butt";
  ctx.beginPath();
  for (const w of layout.walls) {
    const a = worldToMap(t, { x: w.x1, z: w.z1 });
    const b = worldToMap(t, { x: w.x2, z: w.z2 });
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
  }
  ctx.stroke();

  const hexR = Math.max(3, Math.min(5, t.scale * 0.55));
  ctx.lineWidth = 1;
  for (const cp of layout.checkpoints) {
    const m = worldToMap(t, cp);
    hexagon(ctx, m.x, m.y, hexR);
    ctx.fillStyle = colours.checkpoint;
    ctx.fill();
    ctx.strokeStyle = colours.checkpointRim;
    ctx.stroke();
  }
}

/** Radius (map px) of an agent dot. */
export const AGENT_DOT_PX = 3.2;

/** The moving layer: camera wedge (under everything), agent dots, the selection ring and the player's arrow. */
export function drawMinimapDynamic(ctx: CanvasRenderingContext2D, t: MinimapTransform, scene: MinimapDynamic): void {
  const { colours } = scene;
  if (scene.clear !== false) ctx.clearRect(0, 0, t.width, t.height);
  ctx.globalAlpha = 1;

  if (scene.camera) {
    const c = scene.camera;
    const origin = worldToMap(t, c);
    const reach = Math.hypot(t.width, t.height) * 1.5;
    const half = Math.max(0.05, Math.min(Math.PI / 2 - 0.05, c.halfWidth));
    // World heading (sin, cos) maps onto map (x, y) directly: +x east → right, +z south → down.
    const edge = (angle: number): MapPoint => ({ x: origin.x + Math.sin(angle) * reach, y: origin.y + Math.cos(angle) * reach });
    const left = edge(c.yaw - half);
    const right = edge(c.yaw + half);
    ctx.save();
    const b = worldToMap(t, { x: t.bounds.minX, z: t.bounds.minZ });
    ctx.beginPath();
    ctx.rect(b.x, b.y, (t.bounds.maxX - t.bounds.minX) * t.scale, (t.bounds.maxZ - t.bounds.minZ) * t.scale);
    ctx.clip();
    ctx.beginPath();
    ctx.moveTo(origin.x, origin.y);
    ctx.lineTo(left.x, left.y);
    ctx.lineTo(right.x, right.y);
    ctx.closePath();
    ctx.fillStyle = colours.camera;
    ctx.fill();
    ctx.restore();
  }

  ctx.lineWidth = 1.5;
  let selected: MapPoint | null = null;
  for (const agent of scene.agents) {
    const m = worldToMap(t, agent);
    ctx.beginPath();
    ctx.arc(m.x, m.y, AGENT_DOT_PX, 0, Math.PI * 2);
    if (agent.state === "paused") {
      ctx.strokeStyle = colours.paused;
      ctx.stroke();
    } else {
      ctx.fillStyle = agent.state === "working" ? colours.working : agent.state === "waiting" ? colours.waiting : colours.idle;
      ctx.fill();
    }
    if (agent.selected) selected = m;
  }
  if (selected) {
    ctx.beginPath();
    ctx.arc(selected.x, selected.y, AGENT_DOT_PX + 3, 0, Math.PI * 2);
    ctx.strokeStyle = colours.selected;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  // The player: a gold arrow pointing along its heading.
  const p = worldToMap(t, scene.player);
  const dx = Math.sin(scene.player.heading);
  const dy = Math.cos(scene.player.heading);
  const len = 7;
  const wing = 4.5;
  ctx.beginPath();
  ctx.moveTo(p.x + dx * len, p.y + dy * len);
  ctx.lineTo(p.x - dx * len * 0.5 + dy * wing, p.y - dy * len * 0.5 - dx * wing);
  ctx.lineTo(p.x - dx * len * 0.15, p.y - dy * len * 0.15);
  ctx.lineTo(p.x - dx * len * 0.5 - dy * wing, p.y - dy * len * 0.5 + dx * wing);
  ctx.closePath();
  ctx.fillStyle = colours.player;
  ctx.fill();
  ctx.strokeStyle = colours.playerRim;
  ctx.lineWidth = 1;
  ctx.stroke();
}
