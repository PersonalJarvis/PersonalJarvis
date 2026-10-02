/**
 * The arcade floor: a neon-lit arcade floating in a violet night, the top
 * floor of the building. Its rooms (arcadeFloorLayout) stand behind smoked
 * glass with a neon cap in each room's colour, a lit name board over every
 * door, the room's own floor and a neon logo inlaid in it; the open hall in
 * the middle keeps the blacklight carpet, the pinballs, the air hockey and
 * the dance floor.
 *
 * Renders everything floor-specific; OfficeScene adds the person, the
 * camera, the elevator's call button and the checkpoint marker on top.
 *
 * Kept cheap on purpose: one shadow-casting light plus a hemisphere and an
 * ambient light (the neon comes from self-lit materials, not from lamps);
 * all wall frames are one merged mesh, all glass another, all neon caps a
 * third; cabinet screens flip between two baked attract frames by moving a
 * texture offset, so nothing is redrawn or re-uploaded while you walk around.
 */
import { useEffect, useMemo, useRef } from "react";
import { Stars } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import {
  AdditiveBlending, BoxGeometry, Color, DoubleSide, InstancedMesh, MeshBasicMaterial, MeshStandardMaterial, Object3D, PlaneGeometry, RepeatWrapping,
  type BufferGeometry, type Texture,
} from "three";
import { useT } from "@/i18n";
import { cachedCanvasTexture } from "../office/canvasMaterials";
import { Box, Railing } from "../office/OfficeFurniture";
import { FurniturePiece } from "../office/OfficeProps";
import type { Furniture, OfficeLayout, Point, Rect, Room, RoomKind, WallSegment } from "../office/officeLayout";
import { ARCADE_GAMES, gameForCabinet } from "./arcadeGames";
import { arcadeDecor, ROOM_ACCENT } from "./arcadeFloorLayout";
import { drawGlowSpot, HALL, HALL_MAT, PartKit, ROOM_FLOORS, type ArcadeRoomKind } from "./arcadeHallLook";
import { floorLogoMaterial, neonMaterial, roomBoardMaterial, setScreenFrame, type NeonSymbol } from "./arcadeScreens";

export interface ArcadeHallProps {
  layout: OfficeLayout;
  onFloorClick: (event: ThreeEvent<MouseEvent>) => void;
  /** Furniture id of the cabinet the person stands at, or null. Its screen and floor glow brighten. */
  nearCabinet: string | null;
  awake: boolean;
  reduced: boolean;
}

/** Height of the rooms' glass walls; the name boards sit on top of the door headers. */
const WALL_H = 2.2;
/** The name board over a door: width, height, and how far above the wall top its centre sits. */
const BOARD = { w: 2.5, h: 0.58, lift: 0.36 } as const;
/** Seconds each attract frame stays on the cabinets' screens. */
const ATTRACT_FRAME_S = 0.6;
/** Seconds between two steps of the dance pad's light chase. */
const PAD_STEP_S = 0.45;

/** Each room's neon symbol: on its glass and in its floor logo. */
const ROOM_SYMBOL: Partial<Record<RoomKind, NeonSymbol>> = {
  classics: "alien", puzzle: "joystick", action: "bolt", foyer: "coin", prizes: "ticket", snack: "soda", arcade: "star",
};

const accentOf = (kind: RoomKind) => ROOM_ACCENT[kind] ?? HALL.neon.magenta;

/** A cached base texture, cloned per surface with its own repeat (the clone belongs to the caller). */
function repeated(base: Texture | null, repeatX: number, repeatY: number): Texture | null {
  if (!base) return null;
  const map = base.clone();
  map.wrapS = map.wrapT = RepeatWrapping;
  map.anisotropy = 8;
  map.repeat.set(repeatX, repeatY);
  map.needsUpdate = true;
  return map;
}

const RIM = new MeshBasicMaterial({ color: HALL.rim, toneMapped: false });
const UNIT_BOX = new BoxGeometry(1, 1, 1);
/** The dance pad's dark frame between its lit tiles. */
const PAD_BASE = new MeshStandardMaterial({ color: "#07060d", roughness: 0.4 });
/** Smoked glass: dark enough to read as a wall, clear enough that no camera angle loses a room behind it. */
const SMOKED_GLASS = new MeshStandardMaterial({
  color: "#3a3358", transparent: true, opacity: 0.3, roughness: 0.08, metalness: 0.2, side: DoubleSide, depthWrite: false,
});
const BOARD_BACK = new MeshStandardMaterial({ color: HALL.frame, roughness: 0.5, metalness: 0.3 });
const BOARD_PLANE = new PlaneGeometry(BOARD.w - 0.06, BOARD.h - 0.06);
const LOGO_PLANE = new PlaneGeometry(1, 1);

// ---------------------------------------------------------------------------
// Slab and floors
// ---------------------------------------------------------------------------

function floorMaterial(kind: ArcadeRoomKind, w: number, d: number): MeshStandardMaterial {
  const floor = ROOM_FLOORS[kind];
  const map = repeated(cachedCanvasTexture(`arcade:floor:${kind}`, 256, 256, floor.draw), w / floor.metres, d / floor.metres);
  return new MeshStandardMaterial({
    color: map ? "#ffffff" : HALL.carpet.base, map, roughness: floor.roughness,
    // Carpets glow faintly under the "blacklight"; stone and tiles do not.
    ...(map && floor.glow > 0 ? { emissive: "#ffffff", emissiveMap: map, emissiveIntensity: floor.glow } : {}),
  });
}

/** The slab: a dark edge with a glowing rim, the hall's carpet over all of it (everyone walks and clicks on it), railings round it. */
function HallSlab({ layout, onFloorClick }: { layout: OfficeLayout; onFloorClick: (event: ThreeEvent<MouseEvent>) => void }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const w = maxX - minX, d = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  const carpet = useMemo(() => floorMaterial("arcade", w, d), [w, d]);
  const edge = useMemo(() => new MeshStandardMaterial({ color: HALL.slabEdge, roughness: 0.8 }), []);
  useEffect(() => () => { carpet.map?.dispose(); carpet.dispose(); edge.dispose(); }, [carpet, edge]);
  const glow = 0.05, inset = 0.15;
  return (
    <group>
      <Box size={[w, 0.6, d]} position={[cx, -0.3, cz]} material={edge} cast={false} />
      <mesh geometry={UNIT_BOX} material={RIM} position={[cx, -0.06, maxZ]} scale={[w + glow, glow, glow]} />
      <mesh geometry={UNIT_BOX} material={RIM} position={[maxX, -0.06, cz]} scale={[glow, glow, d + glow]} />
      <mesh geometry={UNIT_BOX} material={RIM} position={[cx, -0.06, minZ]} scale={[w + glow, glow, glow]} />
      <mesh geometry={UNIT_BOX} material={RIM} position={[minX, -0.06, cz]} scale={[glow, glow, d + glow]} />
      <mesh position={[cx, 0.001, cz]} rotation={[-Math.PI / 2, 0, 0]} material={carpet} receiveShadow onClick={onFloorClick}>
        <planeGeometry args={[w, d]} />
      </mesh>
      <Railing from={[minX + inset, maxZ - inset]} to={[maxX - inset, maxZ - inset]} />
      <Railing from={[maxX - inset, maxZ - inset]} to={[maxX - inset, minZ + inset]} />
      <Railing from={[maxX - inset, minZ + inset]} to={[minX + inset, minZ + inset]} />
      <Railing from={[minX + inset, minZ + inset]} to={[minX + inset, maxZ - inset]} />
    </group>
  );
}

/** A walled room's own floor, laid over the hall's carpet (and just as clickable). */
function RoomFloor({ room, onFloorClick }: { room: Room; onFloorClick: (event: ThreeEvent<MouseEvent>) => void }) {
  const w = room.maxX - room.minX, d = room.maxZ - room.minZ;
  const material = useMemo(() => (room.kind in ROOM_FLOORS ? floorMaterial(room.kind as ArcadeRoomKind, w, d) : null), [room.kind, w, d]);
  useEffect(() => () => { material?.map?.dispose(); material?.dispose(); }, [material]);
  if (!material) return null;
  return (
    <mesh position={[(room.minX + room.maxX) / 2, 0.004, (room.minZ + room.maxZ) / 2]} rotation={[-Math.PI / 2, 0, 0]}
      material={material} receiveShadow onClick={onFloorClick}>
      <planeGeometry args={[w, d]} />
    </mesh>
  );
}

// ---------------------------------------------------------------------------
// Walls, door headers and name boards
// ---------------------------------------------------------------------------

/** An axis-aligned box along a wall run (or across a door), `thick` deep, from y0 to y1. */
function runBox(kit: PartKit, from: Point, to: Point, thick: number, y0: number, y1: number, colour: string): void {
  const length = Math.hypot(to.x - from.x, to.z - from.z);
  if (length < 0.02) return;
  const alongX = Math.abs(to.x - from.x) >= Math.abs(to.z - from.z);
  kit.box(alongX ? [length, y1 - y0, thick] : [thick, y1 - y0, length], [(from.x + to.x) / 2, (y0 + y1) / 2, (from.z + to.z) / 2], colour);
}

/** Every door of the walled rooms as a run across its gap, with the room it belongs to. */
function doorRuns(rooms: readonly Room[]): { room: Room; from: Point; to: Point }[] {
  return rooms.filter((r) => r.walled).flatMap((room) => room.doors.map((door) => {
    const half = door.width / 2;
    switch (door.side) {
      case "north": return { room, from: { x: door.at - half, z: room.minZ }, to: { x: door.at + half, z: room.minZ } };
      case "south": return { room, from: { x: door.at - half, z: room.maxZ }, to: { x: door.at + half, z: room.maxZ } };
      case "west": return { room, from: { x: room.minX, z: door.at - half }, to: { x: room.minX, z: door.at + half } };
      case "east": return { room, from: { x: room.maxX, z: door.at - half }, to: { x: room.maxX, z: door.at + half } };
    }
  }));
}

/**
 * All glass walls in three draws: the dark steel frames (foot, posts, top
 * rail, door headers), the smoked panes, and the neon caps in each room's colour.
 */
function GlassWalls({ walls, rooms }: { walls: readonly WallSegment[]; rooms: readonly Room[] }) {
  const geometries = useMemo(() => {
    const frame = new PartKit(), glass = new PartKit(), neon = new PartKit();
    for (const wall of walls) {
      const from = { x: wall.x1, z: wall.z1 }, to = { x: wall.x2, z: wall.z2 };
      const length = Math.hypot(to.x - from.x, to.z - from.z);
      if (length < 0.05) continue;
      runBox(frame, from, to, 0.1, 0, 0.1, HALL.frame);
      runBox(glass, from, to, 0.02, 0.1, WALL_H - 0.05, "#ffffff");
      runBox(frame, from, to, 0.08, WALL_H - 0.05, WALL_H, HALL.frame);
      runBox(neon, from, to, 0.05, WALL_H, WALL_H + 0.025, accentOf(wall.room));
      // Posts at both ends and at most every two metres between.
      const posts = Math.max(2, Math.ceil(length / 2) + 1);
      for (let i = 0; i < posts; i += 1) {
        const k = i / (posts - 1);
        const p = { x: from.x + (to.x - from.x) * k, z: from.z + (to.z - from.z) * k };
        frame.box([0.06, WALL_H, 0.06], [p.x, WALL_H / 2, p.z], HALL.frame);
      }
    }
    // Each door: a header across the top of the gap, lit underneath like an entrance.
    for (const { room, from, to } of doorRuns(rooms)) {
      runBox(frame, from, to, 0.1, WALL_H - 0.1, WALL_H, HALL.frame);
      runBox(neon, from, to, 0.05, WALL_H - 0.125, WALL_H - 0.1, accentOf(room.kind));
    }
    return { frame: frame.build(), glass: glass.build(), neon: neon.build() };
  }, [walls, rooms]);
  useEffect(() => () => { geometries.frame.dispose(); geometries.glass.dispose(); geometries.neon.dispose(); }, [geometries]);
  return (
    <group>
      <mesh geometry={geometries.frame} material={HALL_MAT.gloss} castShadow receiveShadow />
      <mesh geometry={geometries.glass} material={SMOKED_GLASS} renderOrder={2} />
      <mesh geometry={geometries.neon} material={HALL_MAT.glow} />
    </group>
  );
}

/** A lit name board over a room's door, readable from both sides. */
function RoomBoard({ room, label }: { room: Room; label: string }) {
  const door = doorRuns([room])[0];
  if (!door) return null;
  const along = Math.abs(door.to.x - door.from.x) >= Math.abs(door.to.z - door.from.z);
  const material = roomBoardMaterial(label, accentOf(room.kind));
  return (
    <group position={[(door.from.x + door.to.x) / 2, WALL_H + BOARD.lift, (door.from.z + door.to.z) / 2]} rotation={[0, along ? 0 : Math.PI / 2, 0]}>
      <mesh geometry={UNIT_BOX} material={BOARD_BACK} scale={[BOARD.w, BOARD.h, 0.05]} />
      <mesh geometry={BOARD_PLANE} material={material} position={[0, 0, 0.026]} />
      <mesh geometry={BOARD_PLANE} material={material} position={[0, 0, -0.026]} rotation={[0, Math.PI, 0]} />
    </group>
  );
}

interface WallSign { symbol: NeonSymbol; x: number; z: number; y: number; size: number }

/**
 * Each room's neon symbol on its glass: on the back wall beside the cabinets
 * in the game rooms, on the far (south) wall in the rooms of the south strip.
 */
function wallSigns(layout: OfficeLayout): WallSign[] {
  const hall = layout.rooms.find((r) => r.kind === "arcade");
  const signs: WallSign[] = [];
  for (const room of layout.rooms) {
    const symbol = ROOM_SYMBOL[room.kind];
    if (!symbol || !room.walled) continue;
    const north = !!hall && room.maxZ <= hall.minZ + 1e-6;
    if (north) {
      const cabinets = layout.furniture.filter((f) => f.kind === "retroCabinet" && f.room === room.kind);
      const xs = cabinets.map((c) => c.x);
      // On whichever side of the cabinet row has more room.
      const left = xs.length ? Math.min(...xs) - room.minX : 0, right = xs.length ? room.maxX - Math.max(...xs) : 0;
      const x = left >= right ? (room.minX + Math.min(...xs)) / 2 - 0.2 : (room.maxX + Math.max(...xs)) / 2 + 0.2;
      signs.push({ symbol, x, z: room.minZ + 0.04, y: 1.45, size: 0.95 });
    } else {
      signs.push({ symbol, x: (room.minX + room.maxX) / 2 + 1.2, z: room.maxZ - 0.04, y: 1.45, size: 1.0 });
    }
  }
  return signs;
}

function WallSigns({ layout }: { layout: OfficeLayout }) {
  const signs = useMemo(() => wallSigns(layout), [layout]);
  return (
    <group>
      {signs.map((sign) => (
        <mesh key={`${sign.symbol}:${sign.x}`} geometry={LOGO_PLANE} material={neonMaterial(sign.symbol)} scale={[sign.size, sign.size, 1]}
          position={[sign.x, sign.y, sign.z]} />
      ))}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Lights
// ---------------------------------------------------------------------------

function HallLights({ layout }: { layout: OfficeLayout }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const span = Math.max(maxX - minX, maxZ - minZ);
  return (
    <>
      <hemisphereLight args={[HALL.sky, HALL.ground, 1.05]} />
      <ambientLight intensity={0.32} />
      <directionalLight position={[maxX + 10, 26, maxZ + 6]} intensity={1.2} color={HALL.sun} castShadow
        shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004} shadow-normalBias={0.03}
        shadow-camera-left={-span * 0.7} shadow-camera-right={span * 0.7}
        shadow-camera-top={span * 0.7} shadow-camera-bottom={-span * 0.7} shadow-camera-far={120} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Floor decor
// ---------------------------------------------------------------------------

let tableGeometry: BufferGeometry | null = null;
/** A bistro table on a chrome pedestal with three red stools round it, inside the layout's SNACK_TABLE_HALF footprint. */
function bistroTable(): BufferGeometry {
  if (!tableGeometry) {
    const kit = new PartKit();
    kit.cylinder(0.22, 0.03, [0, 0.015, 0], HALL.chrome, { segments: 18 });
    kit.cylinder(0.04, 0.7, [0, 0.38, 0], HALL.chrome, { segments: 10 });
    kit.cylinder(0.37, 0.03, [0, 0.735, 0], HALL.chrome, { segments: 24 });
    kit.cylinder(0.35, 0.02, [0, 0.755, 0], HALL.snack.cream, { segments: 24 });
    for (let i = 0; i < 3; i += 1) {
      const a = (i / 3) * Math.PI * 2 + Math.PI / 6;
      const x = Math.cos(a) * 0.45, z = Math.sin(a) * 0.45;
      kit.cylinder(0.13, 0.02, [x, 0.01, z], HALL.chrome, { segments: 14 });
      kit.cylinder(0.022, 0.6, [x, 0.31, z], HALL.chrome, { segments: 8 });
      kit.cylinder(0.16, 0.07, [x, 0.635, z], "#c0392b", { segments: 18 });
    }
    tableGeometry = kit.build();
  }
  return tableGeometry;
}

function SnackTables({ tables }: { tables: Point[] }) {
  const geometry = bistroTable();
  return <>{tables.map((t) => <mesh key={`${t.x}:${t.z}`} geometry={geometry} material={HALL_MAT.gloss} position={[t.x, 0, t.z]} castShadow receiveShadow />)}</>;
}

const PAD_TILES = 4;
const PAD_COLOURS = [HALL.neon.magenta, HALL.neon.cyan, HALL.neon.yellow, HALL.neon.green, HALL.neon.violet];

/** Light step `n` of the pad's chase: every third diagonal bright, the rest dimmed. */
function paintPad(mesh: InstancedMesh, bright: Color[], dim: Color[], n: number): void {
  for (let r = 0; r < PAD_TILES; r += 1) {
    for (let c = 0; c < PAD_TILES; c += 1) {
      const k = (r + c + n) % PAD_COLOURS.length;
      mesh.setColorAt(r * PAD_TILES + c, (r + c + n) % 3 === 0 ? bright[k] : dim[k]);
    }
  }
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
}

/** A light-up dance floor of 4 × 4 tiles; the colours chase diagonally while the floor is awake. */
function DancePad({ pad, animate }: { pad: Rect; animate: boolean }) {
  const w = pad.maxX - pad.minX, d = pad.maxZ - pad.minZ;
  const tile = Math.min(w, d) / PAD_TILES;
  const { mesh, bright, dim } = useMemo(() => {
    const geometry = new PlaneGeometry(tile * 0.9, tile * 0.9).rotateX(-Math.PI / 2);
    const material = new MeshBasicMaterial({ color: "#ffffff", toneMapped: false });
    const made = new InstancedMesh(geometry, material, PAD_TILES * PAD_TILES);
    const dummy = new Object3D();
    for (let r = 0; r < PAD_TILES; r += 1) {
      for (let c = 0; c < PAD_TILES; c += 1) {
        dummy.position.set(pad.minX + (c + 0.5) * tile, 0.008, pad.minZ + (r + 0.5) * tile);
        dummy.updateMatrix();
        made.setMatrixAt(r * PAD_TILES + c, dummy.matrix);
      }
    }
    made.instanceMatrix.needsUpdate = true;
    made.computeBoundingSphere();
    return {
      mesh: made,
      bright: PAD_COLOURS.map((c) => new Color(c)),
      dim: PAD_COLOURS.map((c) => new Color(c).multiplyScalar(0.28)),
    };
  }, [pad.minX, pad.minZ, tile]);
  const step = useRef(0);
  const since = useRef(0);
  useEffect(() => paintPad(mesh, bright, dim, step.current), [mesh, bright, dim]);
  useEffect(() => () => { mesh.geometry.dispose(); (mesh.material as MeshBasicMaterial).dispose(); mesh.dispose(); }, [mesh]);
  useFrame((_, dt) => {
    if (!animate) return;
    since.current += dt;
    if (since.current < PAD_STEP_S) return;
    since.current = 0;
    step.current += 1;
    paintPad(mesh, bright, dim, step.current);
  });
  return (
    <group>
      <mesh position={[(pad.minX + pad.maxX) / 2, 0.004, (pad.minZ + pad.maxZ) / 2]} rotation={[-Math.PI / 2, 0, 0]} material={PAD_BASE} receiveShadow>
        <planeGeometry args={[w, d]} />
      </mesh>
      <primitive object={mesh} />
    </group>
  );
}

/** The neon logos inlaid in the floors: each game room's symbol, the foyer's welcome coin and the hall's star. */
function FloorLogos({ logos }: { logos: { x: number; z: number; r: number; room: RoomKind }[] }) {
  return (
    <group>
      {logos.map((logo) => {
        const symbol = ROOM_SYMBOL[logo.room];
        if (!symbol) return null;
        return (
          <mesh key={`${logo.room}:${logo.x}`} geometry={LOGO_PLANE} material={floorLogoMaterial(symbol, accentOf(logo.room))}
            position={[logo.x, 0.009, logo.z]} rotation={[-Math.PI / 2, 0, 0]} scale={[logo.r * 2, logo.r * 2, 1]} renderOrder={1} />
        );
      })}
    </group>
  );
}

/** The coloured pool of light each cabinet throws on the carpet in front of it; one instanced draw for all of them. */
function CabinetGlows({ cabinets, near }: { cabinets: Furniture[]; near: string | null }) {
  const mesh = useMemo(() => {
    const map = cachedCanvasTexture("arcade:glow-spot", 128, 128, drawGlowSpot);
    const material = new MeshBasicMaterial({
      map, color: "#ffffff", transparent: true, opacity: map ? 1 : 0, depthWrite: false, blending: AdditiveBlending, toneMapped: false,
    });
    const made = new InstancedMesh(new PlaneGeometry(1, 1).rotateX(-Math.PI / 2), material, Math.max(1, cabinets.length));
    made.count = cabinets.length;
    return made;
  }, [cabinets]);
  // Placing and tinting is cheap (ten instances) and only happens when the cabinet you stand at changes.
  useEffect(() => {
    const dummy = new Object3D();
    const colour = new Color();
    cabinets.forEach((cabinet, i) => {
      const ahead = 0.75, big = cabinet.id === near;
      dummy.position.set(cabinet.x + Math.sin(cabinet.rotationY) * ahead, 0.012, cabinet.z + Math.cos(cabinet.rotationY) * ahead);
      dummy.rotation.set(0, cabinet.rotationY, 0);
      dummy.scale.set(big ? 1.6 : 1.15, 1, big ? 1.3 : 0.95);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, colour.set(gameForCabinet(cabinet.id)?.look.accent ?? HALL.neon.violet).multiplyScalar(big ? 1.6 : 1));
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [mesh, cabinets, near]);
  useEffect(() => () => { mesh.geometry.dispose(); (mesh.material as MeshBasicMaterial).dispose(); mesh.dispose(); }, [mesh]);
  return <primitive object={mesh} />;
}

/** Flips every attract screen between its two frames; neighbours in the registry run out of step. */
function AttractScreens({ animate }: { animate: boolean }) {
  const since = useRef(0);
  const frame = useRef<0 | 1>(0);
  useFrame((_, dt) => {
    if (!animate) return;
    since.current += dt;
    if (since.current < ATTRACT_FRAME_S) return;
    since.current = 0;
    frame.current = frame.current === 0 ? 1 : 0;
    for (let i = 0; i < ARCADE_GAMES.length; i += 1) setScreenFrame(ARCADE_GAMES[i], ((frame.current + i) % 2) as 0 | 1);
  });
  return null;
}

// ---------------------------------------------------------------------------
// The hall
// ---------------------------------------------------------------------------

export function ArcadeHall({ layout, onFloorClick, nearCabinet, awake, reduced }: ArcadeHallProps) {
  const t = useT();
  const background = useMemo(() => new Color(HALL.space), []);
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const span = Math.max(maxX - minX, maxZ - minZ);
  const cabinets = useMemo(() => layout.furniture.filter((f) => f.kind === "retroCabinet"), [layout]);
  const decor = useMemo(() => arcadeDecor(layout.rooms), [layout]);
  const walled = useMemo(() => layout.rooms.filter((r) => r.walled), [layout]);
  const animate = awake && !reduced;
  return (
    <>
      <primitive attach="background" object={background} />
      <fog attach="fog" args={[HALL.space, span * 2.2, span * 4.5]} />
      <Stars radius={span * 3} depth={span} count={2200} factor={4} saturation={0.35} fade speed={reduced ? 0 : 0.3} />
      <HallLights layout={layout} />
      <HallSlab layout={layout} onFloorClick={onFloorClick} />
      {walled.map((room) => <RoomFloor key={room.id} room={room} onFloorClick={onFloorClick} />)}
      <GlassWalls walls={layout.walls} rooms={layout.rooms} />
      {walled.map((room) => <RoomBoard key={room.id} room={room} label={t(`society.office.room_${room.kind}`)} />)}
      <WallSigns layout={layout} />
      {layout.furniture.map((item) => <FurniturePiece key={item.id} item={item} />)}
      <SnackTables tables={decor.tables} />
      <DancePad pad={decor.pad} animate={animate} />
      <FloorLogos logos={decor.logos} />
      <CabinetGlows cabinets={cabinets} near={nearCabinet} />
      <AttractScreens animate={animate} />
    </>
  );
}
