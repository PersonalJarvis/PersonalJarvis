/**
 * Rooms of the walkable office: a floor overlay per room, glass walls along
 * the layout's wall segments, and a name sign above each room's door.
 * Pattern textures and sign textures are drawn once and cached.
 */
import { useEffect, useMemo } from "react";
import { MeshStandardMaterial, RepeatWrapping, type Texture } from "three";
import { Box, MAT, Rounded } from "./OfficeFurniture";
import { cachedCanvasTexture } from "./canvasMaterials";
import { ARCH, archPosts, type Door, type Room, type RoomKind, type WallSegment } from "./officeLayout";
import { LEAD_MAT } from "./LeadSuite";
import { OFFICE, ROOM_FLOOR_COLOURS } from "./officePalette";

// ---------------------------------------------------------------------------
// Floors
// ---------------------------------------------------------------------------

/** Deterministic pseudo-random numbers, so the patterns never shimmer between loads. */
function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/** Metres covered by one repeat of each floor pattern. */
const PATTERN_METRES: Record<RoomKind, number> = { lead: 2.6, team: 2, wardrobe: 2, reception: 2.4, break: 2 };

function drawFloor(kind: RoomKind, ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const { base, accents } = ROOM_FLOOR_COLOURS[kind];
  const rand = lcg(kind.length * 7919 + kind.charCodeAt(0));
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, w, h);
  if (kind === "lead") {
    // Dark walnut chevron: columns of slanted planks that alternate direction, so
    // the tile wraps seamlessly on both axes (64-px columns, 16-px planks).
    const col = 64, plank = 16, image = ctx.createImageData(w, h);
    const tones = accents.map((hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)));
    for (let y = 0; y < h; y += 1) {
      for (let x = 0; x < w; x += 1) {
        const c = Math.floor(x / col), xm = x % col;
        const yy = (y + (c % 2 === 0 ? xm : col - xm)) % h;
        const index = Math.floor(yy / plank);
        const [r, g, b] = tones[(index * 7 + c * 3) % tones.length];
        // Seams between planks and along the chevron's spine, plus a faint grain.
        const seam = yy % plank < 1.2 || xm < 1 ? 0.62 : 1;
        const grain = 0.93 + 0.07 * Math.sin((yy * 0.9 + xm * 0.35 + index * 11) * 0.8);
        const o = (y * w + x) * 4;
        image.data[o] = r * seam * grain;
        image.data[o + 1] = g * seam * grain;
        image.data[o + 2] = b * seam * grain;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, 0, 0);
  } else if (kind === "team" || kind === "break") {
    // Carpet grain: fine speckles in the accent tones, plus a faint weave.
    for (let i = 0; i < 2600; i += 1) {
      ctx.fillStyle = accents[Math.floor(rand() * accents.length)];
      ctx.fillRect(Math.floor(rand() * w), Math.floor(rand() * h), 2, 2);
    }
    ctx.fillStyle = "rgba(0,0,0,0.035)";
    for (let y = 0; y < h; y += 8) ctx.fillRect(0, y, w, 1);
  } else {
    // Tiles: wardrobe small square tiles, reception large stone slabs.
    const tiles = kind === "wardrobe" ? 4 : 2;
    const size = w / tiles;
    for (let ty = 0; ty < tiles; ty += 1) {
      for (let tx = 0; tx < tiles; tx += 1) {
        ctx.fillStyle = accents[Math.floor(rand() * accents.length)];
        ctx.fillRect(tx * size, ty * size, size, size);
        if (kind === "reception") {
          for (let i = 0; i < 90; i += 1) {
            ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.18)" : "rgba(120,110,95,0.12)";
            ctx.fillRect(tx * size + rand() * size, ty * size + rand() * size, 3, 3);
          }
        }
      }
    }
    ctx.fillStyle = kind === "wardrobe" ? "rgba(120,100,150,0.35)" : "rgba(140,130,115,0.45)";
    for (let i = 0; i < tiles; i += 1) {
      ctx.fillRect(i * size, 0, 3, h);
      ctx.fillRect(0, i * size, w, 3);
    }
  }
}

function floorTexture(kind: RoomKind): Texture | null {
  const texture = cachedCanvasTexture(`floor:${kind}`, 256, 256, (ctx, w, h) => drawFloor(kind, ctx, w, h));
  if (texture) {
    texture.wrapS = texture.wrapT = RepeatWrapping;
    texture.anisotropy = 8;
  }
  return texture;
}

function RoomFloor({ room }: { room: Room }) {
  const w = room.maxX - room.minX;
  const d = room.maxZ - room.minZ;
  const material = useMemo(() => {
    const base = floorTexture(room.kind);
    let map: Texture | null = null;
    if (base) {
      map = base.clone();
      map.repeat.set(w / PATTERN_METRES[room.kind], d / PATTERN_METRES[room.kind]);
      map.needsUpdate = true;
    }
    return new MeshStandardMaterial({ color: map ? "#ffffff" : ROOM_FLOOR_COLOURS[room.kind].base, map, roughness: 0.85 });
  }, [room.kind, w, d]);
  // The clone and its material belong to this room; the cached base texture stays.
  useEffect(() => () => { material.map?.dispose(); material.dispose(); }, [material]);
  return (
    <mesh position={[(room.minX + room.maxX) / 2, 0.004, (room.minZ + room.maxZ) / 2]} rotation={[-Math.PI / 2, 0, 0]}
      material={material} receiveShadow>
      <planeGeometry args={[w, d]} />
    </mesh>
  );
}

/** A floor overlay per room: walnut chevron (lead), teal carpet (team), lilac tile (wardrobe), stone (reception), coral carpet (break). */
export function RoomFloors({ rooms }: { rooms: Room[] }) {
  return <group>{rooms.map((room) => <RoomFloor key={room.id} room={room} />)}</group>;
}

// ---------------------------------------------------------------------------
// Walls
// ---------------------------------------------------------------------------

export const WALL_HEIGHT = 2.1;
const BASE_HEIGHT = 0.12;
const POST_SPACING = 1.5;

/** One glass wall run: white base strip, glass pane, metal posts and a top rail. */
function GlassWall({ wall }: { wall: WallSegment }) {
  // The lead office is framed in brass on a walnut base, behind warm-tinted glass.
  const lead = wall.room === "lead";
  const frame = lead ? LEAD_MAT.brass : MAT.railing;
  const base = lead ? LEAD_MAT.walnutDark : MAT.wall;
  const glass = lead ? LEAD_MAT.wallGlass : MAT.glass;
  const length = Math.hypot(wall.x2 - wall.x1, wall.z2 - wall.z1);
  if (length < 0.05) return null;
  const angle = Math.atan2(wall.z2 - wall.z1, wall.x2 - wall.x1);
  const posts = Math.max(2, Math.ceil(length / POST_SPACING) + 1);
  return (
    <group position={[(wall.x1 + wall.x2) / 2, 0, (wall.z1 + wall.z2) / 2]} rotation={[0, -angle, 0]}>
      <Box size={[length, BASE_HEIGHT, 0.1]} position={[0, BASE_HEIGHT / 2, 0]} material={base} />
      <Box size={[length, WALL_HEIGHT - BASE_HEIGHT - 0.04, 0.02]} position={[0, (WALL_HEIGHT + BASE_HEIGHT - 0.04) / 2, 0]}
        material={glass} cast={false} />
      <Box size={[length, 0.05, 0.07]} position={[0, WALL_HEIGHT - 0.025, 0]} material={frame} />
      {Array.from({ length: posts }, (_, i) => (
        <Box key={i} size={[0.05, WALL_HEIGHT, 0.07]} position={[-length / 2 + (length * i) / (posts - 1), WALL_HEIGHT / 2, 0]}
          material={frame} />
      ))}
    </group>
  );
}

/** Glass walls, 2.1 m high, along every wall segment of the layout. */
export function RoomWalls({ walls }: { walls: WallSegment[] }) {
  return (
    <group>
      {walls.map((wall) => <GlassWall key={`${wall.room}:${wall.x1}:${wall.z1}:${wall.x2}:${wall.z2}`} wall={wall} />)}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Signs
// ---------------------------------------------------------------------------

const SIGN_W = 1.5;
const SIGN_H = 0.36;
const SIGN_TEXT_W = 1.4;
const SIGN_CANVAS = { w: 512, h: 112 } as const;

const signMaterials = new Map<string, MeshStandardMaterial>();
/** A sign face; the lead office's is gold lettering on black between two brass rules. */
function signMaterial(label: string, lead = false): MeshStandardMaterial {
  const key = `${lead ? "lead" : "room"}:${label}`;
  let material = signMaterials.get(key);
  if (!material) {
    const map = cachedCanvasTexture(`room-sign:${key}`, SIGN_CANVAS.w, SIGN_CANVAS.h, (ctx, w, h) => {
      ctx.fillStyle = lead ? "#111217" : OFFICE.signBoard;
      ctx.fillRect(0, 0, w, h);
      if (lead) {
        ctx.fillStyle = "#d8ae52";
        ctx.fillRect(14, 10, w - 28, 3);
        ctx.fillRect(14, h - 13, w - 28, 3);
      }
      ctx.fillStyle = lead ? "#f5c55a" : OFFICE.signText;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      // Shrink long names (e.g. "Despacho del líder") to fit before cutting them off.
      const room = w - 32;
      let size = 56;
      const font = (px: number) => `600 ${px}px system-ui, -apple-system, 'Segoe UI', sans-serif`;
      ctx.font = font(size);
      while (size > 34 && ctx.measureText(label).width > room) {
        size -= 2;
        ctx.font = font(size);
      }
      let text = label;
      while (text.length > 1 && ctx.measureText(text).width > room) text = text.slice(0, -1);
      ctx.fillText(text === label ? label : `${text.slice(0, -1).trimEnd()}…`, w / 2, h / 2 + 2);
    });
    material = new MeshStandardMaterial({
      color: map ? "#ffffff" : OFFICE.signBoard, map, roughness: 0.8,
      // The lead's gold lettering glows faintly, like a lit plaque.
      ...(lead && map ? { emissive: "#ffffff", emissiveMap: map, emissiveIntensity: 0.35 } : {}),
    });
    signMaterials.set(key, material);
  }
  return material;
}

/** Dark-wood board with the label on both faces, centred on its local origin. */
function SignBoard({ label, lead = false }: { label: string; lead?: boolean }) {
  const material = signMaterial(label, lead);
  const textH = (SIGN_TEXT_W * SIGN_CANVAS.h) / SIGN_CANVAS.w;
  return (
    <group>
      <Rounded size={[SIGN_W, SIGN_H, 0.06]} radius={0.025} position={[0, 0, 0]} material={lead ? LEAD_MAT.brass : MAT.sign} />
      <mesh position={[0, 0, 0.0305]} material={material}><planeGeometry args={[SIGN_TEXT_W, textH]} /></mesh>
      <mesh position={[0, 0, -0.0305]} rotation={[0, Math.PI, 0]} material={material}><planeGeometry args={[SIGN_TEXT_W, textH]} /></mesh>
    </group>
  );
}

/** Where a door-top sign sits: the door's centre on its wall line, turned to run along the wall. */
function doorAnchor(room: Room, door: Door): { x: number; z: number; rotationY: number } {
  switch (door.side) {
    case "south": return { x: door.at, z: room.maxZ, rotationY: 0 };
    case "north": return { x: door.at, z: room.minZ, rotationY: 0 };
    case "west": return { x: room.minX, z: door.at, rotationY: Math.PI / 2 };
    case "east": return { x: room.maxX, z: door.at, rotationY: Math.PI / 2 };
  }
}

/**
 * A room's name sign. Walled rooms: a board sitting on a header rail across
 * their first door, readable from both sides. Open rooms: a free-standing arch
 * (two slim posts, board on top) centred on the room's north edge.
 */
export function RoomSign({ room, label }: { room: Room; label: string }) {
  const door = room.walled ? room.doors[0] : undefined;
  if (door) {
    const { x, z, rotationY } = doorAnchor(room, door);
    return (
      <group position={[x, 0, z]} rotation={[0, rotationY, 0]}>
        <Box size={[door.width, 0.07, 0.07]} position={[0, WALL_HEIGHT - 0.035, 0]} material={room.kind === "lead" ? LEAD_MAT.brass : MAT.railing} />
        <group position={[0, WALL_HEIGHT + SIGN_H / 2 + 0.02, 0]}><SignBoard label={label} lead={room.kind === "lead"} /></group>
      </group>
    );
  }
  const archY = 2.3;
  const postH = archY + SIGN_H / 2;
  // Post positions come from the layout, which also makes them navigation obstacles.
  const [west, east] = archPosts(room);
  const cx = (west.x + east.x) / 2;
  return (
    <group position={[cx, 0, west.z]}>
      <Box size={[ARCH.post, postH, ARCH.post]} position={[west.x - cx, postH / 2, 0]} material={MAT.woodDark} />
      <Box size={[ARCH.post, postH, ARCH.post]} position={[east.x - cx, postH / 2, 0]} material={MAT.woodDark} />
      <Box size={[east.x - west.x + ARCH.post, 0.06, ARCH.post]} position={[0, archY - SIGN_H / 2 - 0.03, 0]} material={MAT.woodDark} />
      <group position={[0, archY, 0]}><SignBoard label={label} /></group>
    </group>
  );
}
