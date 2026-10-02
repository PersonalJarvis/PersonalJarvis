/**
 * The arcade floor: a neon-lit 80s/90s arcade hall floating in a violet
 * night — the top floor of the building. A blacklight carpet, two midnight
 * back walls with neon signs (the camera looks in from the south-east like
 * into a doll's house; the walls are one-sided, so from behind they vanish),
 * glass railings on the open sides, the prize corner and the snack bar, and
 * every machine of the layout.
 *
 * Renders everything floor-specific; OfficeScene adds the person, the
 * camera, the elevator's call button and the checkpoint marker on top.
 *
 * Light budget: one shadow-casting sun, a hemisphere and an ambient light
 * and three small point lights. The neon look comes from self-lit materials.
 * Screens: every cabinet shows its game's two-frame attract screen (flipped
 * by moving a texture offset, nothing redrawn); the cabinet the person stands
 * at runs its game's own title screen live, a dozen frames a second.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Stars } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import {
  AdditiveBlending, BoxGeometry, Color, InstancedMesh, MeshBasicMaterial, MeshStandardMaterial, Object3D, PlaneGeometry, RepeatWrapping,
  type BufferGeometry, type Texture,
} from "three";
import { useT } from "@/i18n";
import { cachedCanvasTexture } from "../office/canvasMaterials";
import { Box, Railing } from "../office/OfficeFurniture";
import { FurniturePiece } from "../office/OfficeProps";
import { RoomSign, RoomWalls } from "../office/OfficeRooms";
import type { Furniture, OfficeLayout, Point, Rect, Room } from "../office/officeLayout";
import { ARCADE_GAMES, gameForCabinet, isRetroGame, loadRetroGame } from "./arcadeGames";
import { ARCADE_WALL_INSET, arcadeDecor } from "./arcadeFloorLayout";
import {
  CARPET_METRES, drawCarpet, drawGlowSpot, drawPrizeCarpet, drawSnackFloor, drawWallPanels, HALL, HALL_MAT, PartKit, ROOM_FLOOR_METRES,
} from "./arcadeHallLook";
import { CABINET_SCREEN } from "./ArcadeHallProps";
import { createLiveScreen, medallionMaterial, neonMaterial, setScreenFrame, type LiveScreen, type NeonSymbol } from "./arcadeScreens";

export interface ArcadeHallProps {
  layout: OfficeLayout;
  onFloorClick: (event: ThreeEvent<MouseEvent>) => void;
  /** Furniture id of the cabinet the person stands at, or null. */
  nearCabinet: string | null;
  awake: boolean;
  reduced: boolean;
}

/** Height of the back walls. */
const WALL_H = 3.0;
/** Seconds each attract frame stays on the cabinets' screens. */
const ATTRACT_FRAME_S = 0.6;
/** Seconds between two steps of the dance pad's light chase. */
const PAD_STEP_S = 0.45;

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

// ---------------------------------------------------------------------------
// Slab, carpets, walls
// ---------------------------------------------------------------------------

const RIM = new MeshBasicMaterial({ color: HALL.rim, toneMapped: false });
const STRIP = {
  magenta: new MeshBasicMaterial({ color: HALL.neon.magenta, toneMapped: false }),
  cyan: new MeshBasicMaterial({ color: HALL.neon.cyan, toneMapped: false }),
};
const UNIT_PLANE = new PlaneGeometry(1, 1);
const UNIT_BOX = new BoxGeometry(1, 1, 1);
/** The dance pad's dark frame between its lit tiles. */
const PAD_BASE = new MeshStandardMaterial({ color: "#07060d", roughness: 0.4 });

/** The slab: a dark edge with a glowing rim, and the blacklight carpet everyone walks (and clicks) on. */
function HallSlab({ layout, onFloorClick }: { layout: OfficeLayout; onFloorClick: (event: ThreeEvent<MouseEvent>) => void }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const w = maxX - minX, d = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  const carpet = useMemo(() => {
    const map = repeated(cachedCanvasTexture("arcade:carpet", 512, 512, drawCarpet), w / CARPET_METRES, d / CARPET_METRES);
    // The neon fibres glow faintly under the "blacklight"; the near-black ground barely does.
    return new MeshStandardMaterial({
      color: map ? "#ffffff" : HALL.carpet.base, map, roughness: 0.95,
      emissive: map ? "#ffffff" : "#000000", emissiveMap: map, emissiveIntensity: 0.22,
    });
  }, [w, d]);
  const edge = useMemo(() => new MeshStandardMaterial({ color: HALL.slabEdge, roughness: 0.8 }), []);
  useEffect(() => () => { carpet.map?.dispose(); carpet.dispose(); edge.dispose(); }, [carpet, edge]);
  const glow = 0.05;
  return (
    <group>
      <Box size={[w, 0.6, d]} position={[cx, -0.3, cz]} material={edge} cast={false} />
      {/* The rim runs just below the floor's edge on all four sides. */}
      <mesh geometry={UNIT_BOX} material={RIM} position={[cx, -0.06, maxZ]} scale={[w + glow, glow, glow]} />
      <mesh geometry={UNIT_BOX} material={RIM} position={[maxX, -0.06, cz]} scale={[glow, glow, d + glow]} />
      <mesh geometry={UNIT_BOX} material={RIM} position={[cx, -0.06, minZ]} scale={[w + glow, glow, glow]} />
      <mesh geometry={UNIT_BOX} material={RIM} position={[minX, -0.06, cz]} scale={[glow, glow, d + glow]} />
      <mesh position={[cx, 0.001, cz]} rotation={[-Math.PI / 2, 0, 0]} material={carpet} receiveShadow onClick={onFloorClick}>
        <planeGeometry args={[w, d]} />
      </mesh>
    </group>
  );
}

/** The prize corner's plum star carpet and the snack bar's diner checker, laid over the hall's carpet. */
function RoomCarpet({ room, onFloorClick }: { room: Room; onFloorClick: (event: ThreeEvent<MouseEvent>) => void }) {
  const w = room.maxX - room.minX, d = room.maxZ - room.minZ;
  const material = useMemo(() => {
    const prizes = room.kind === "prizes";
    const base = cachedCanvasTexture(`arcade:floor:${room.kind}`, 256, 256, prizes ? drawPrizeCarpet : drawSnackFloor);
    const map = repeated(base, w / ROOM_FLOOR_METRES, d / ROOM_FLOOR_METRES);
    return new MeshStandardMaterial({ color: map ? "#ffffff" : prizes ? HALL.prizes.base : HALL.snack.red, map, roughness: prizes ? 0.95 : 0.4 });
  }, [room.kind, w, d]);
  useEffect(() => () => { material.map?.dispose(); material.dispose(); }, [material]);
  return (
    <mesh position={[(room.minX + room.maxX) / 2, 0.004, (room.minZ + room.maxZ) / 2]} rotation={[-Math.PI / 2, 0, 0]}
      material={material} receiveShadow onClick={onFloorClick}>
      <planeGeometry args={[w, d]} />
    </mesh>
  );
}

interface NeonSign { symbol: NeonSymbol; wall: "north" | "west"; along: number; y: number; size: number }

/** Where the neon signs hang: over the prize counter, the pinballs, both ends of cabinet row A, the dance pad, the claws and the snack bar. */
function neonSigns(layout: OfficeLayout): NeonSign[] {
  const at = (id: string): Point | null => layout.furniture.find((f) => f.id === id) ?? null;
  const room = (kind: Room["kind"]) => layout.rooms.find((r) => r.kind === kind) ?? null;
  const signs: NeonSign[] = [];
  const prizes = room("prizes"), snack = room("snack"), hall = room("arcade");
  if (prizes) signs.push({ symbol: "ticket", wall: "north", along: (prizes.minX + prizes.maxX) / 2, y: 2.72, size: 0.62 });
  const pinball = at("pinball-1");
  if (pinball) signs.push({ symbol: "bolt", wall: "north", along: pinball.x, y: 2.45, size: 0.95 });
  const rowA = layout.furniture.filter((f) => f.kind === "retroCabinet" && f.rotationY === 0 && hall && f.z < hall.minZ + 1.5);
  if (rowA.length > 0) {
    const xs = rowA.map((f) => f.x);
    signs.push({ symbol: "joystick", wall: "north", along: Math.min(...xs) - 0.95, y: 2.35, size: 0.9 });
    signs.push({ symbol: "star", wall: "north", along: Math.max(...xs) + 0.95, y: 2.35, size: 0.9 });
  }
  if (hall) signs.push({ symbol: "alien", wall: "north", along: hall.maxX - 2.7, y: 2.2, size: 1.2 });
  const claws = layout.furniture.filter((f) => f.kind === "clawMachine");
  if (claws.length > 0) signs.push({ symbol: "claw", wall: "west", along: claws.reduce((s, c) => s + c.z, 0) / claws.length, y: 2.45, size: 0.9 });
  if (snack) {
    signs.push({ symbol: "coin", wall: "west", along: snack.minZ + 1.4, y: 2.1, size: 0.8 });
    signs.push({ symbol: "soda", wall: "west", along: snack.minZ + 4.4, y: 2.2, size: 1.0 });
  }
  return signs;
}

/** The north and west walls: midnight panels with neon strips along the top and the foot, and the neon signs. */
function BackWalls({ layout }: { layout: OfficeLayout }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const west = minX + ARCADE_WALL_INSET, east = maxX - ARCADE_WALL_INSET, north = minZ + ARCADE_WALL_INSET, south = maxZ - ARCADE_WALL_INSET;
  const northLen = east - west, westLen = south - north;
  const [northWall, westWall] = useMemo(() => {
    const base = cachedCanvasTexture("arcade:wall", 256, 256, drawWallPanels);
    const make = (length: number) => {
      const map = repeated(base, length / 2.4, 1);
      return new MeshStandardMaterial({ color: map ? "#ffffff" : HALL.wall.base, map, roughness: 0.85 });
    };
    return [make(northLen), make(westLen)];
  }, [northLen, westLen]);
  useEffect(() => () => { for (const m of [northWall, westWall]) { m.map?.dispose(); m.dispose(); } }, [northWall, westWall]);
  const signs = useMemo(() => neonSigns(layout), [layout]);
  const strip = (key: string, wall: "north" | "west", y: number, h: number, material: MeshBasicMaterial) => wall === "north"
    ? <mesh key={key} geometry={UNIT_PLANE} material={material} position={[(west + east) / 2, y, north + 0.012]} scale={[northLen, h, 1]} />
    : <mesh key={key} geometry={UNIT_PLANE} material={material} position={[west + 0.012, y, (north + south) / 2]} rotation={[0, Math.PI / 2, 0]}
      scale={[westLen, h, 1]} />;
  return (
    <group>
      {/* One-sided planes facing into the hall: from outside (an orbiting camera) the walls disappear. */}
      <mesh position={[(west + east) / 2, WALL_H / 2, north]} material={northWall} receiveShadow>
        <planeGeometry args={[northLen, WALL_H]} />
      </mesh>
      <mesh position={[west, WALL_H / 2, (north + south) / 2]} rotation={[0, Math.PI / 2, 0]} material={westWall} receiveShadow>
        <planeGeometry args={[westLen, WALL_H]} />
      </mesh>
      {strip("n-top", "north", WALL_H - 0.12, 0.045, STRIP.magenta)}
      {strip("w-top", "west", WALL_H - 0.12, 0.045, STRIP.magenta)}
      {strip("n-foot", "north", 0.16, 0.03, STRIP.cyan)}
      {strip("w-foot", "west", 0.16, 0.03, STRIP.cyan)}
      {signs.map((sign) => (
        <mesh key={sign.symbol} geometry={UNIT_PLANE} material={neonMaterial(sign.symbol)} scale={[sign.size, sign.size, 1]}
          position={sign.wall === "north" ? [sign.along, sign.y, north + 0.02] : [west + 0.02, sign.y, sign.along]}
          rotation={sign.wall === "north" ? [0, 0, 0] : [0, Math.PI / 2, 0]} />
      ))}
      <Railing from={[west, south]} to={[east, south]} />
      <Railing from={[east, south]} to={[east, north]} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Lights
// ---------------------------------------------------------------------------

function HallLights({ layout }: { layout: OfficeLayout }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const span = Math.max(maxX - minX, maxZ - minZ);
  const hall = layout.rooms.find((r) => r.kind === "arcade");
  const prizes = layout.rooms.find((r) => r.kind === "prizes");
  const hockey = layout.furniture.find((f) => f.kind === "airHockey");
  return (
    <>
      <hemisphereLight args={[HALL.sky, HALL.ground, 0.8]} />
      <ambientLight intensity={0.2} />
      <directionalLight position={[maxX + 10, 26, maxZ + 6]} intensity={1.15} color={HALL.sun} castShadow
        shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004} shadow-normalBias={0.03}
        shadow-camera-left={-span * 0.7} shadow-camera-right={span * 0.7}
        shadow-camera-top={span * 0.7} shadow-camera-bottom={-span * 0.7} shadow-camera-far={120} />
      {hall && <pointLight position={[(hall.minX + hall.maxX) / 2, 2.9, hall.minZ + 4]} color={HALL.neon.magenta} intensity={9} distance={10} decay={2} />}
      {prizes && <pointLight position={[(prizes.minX + prizes.maxX) / 2, 2.6, prizes.maxZ]} color="#ffb36b" intensity={7} distance={8} decay={2} />}
      {hockey && <pointLight position={[hockey.x, 2.4, hockey.z]} color={HALL.neon.cyan} intensity={6} distance={8} decay={2} />}
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

function Medallion({ at }: { at: Point & { r: number } }) {
  if (at.r <= 0) return null;
  return (
    <mesh position={[at.x, 0.006, at.z]} rotation={[-Math.PI / 2, 0, 0]} material={medallionMaterial()} renderOrder={1}>
      <planeGeometry args={[at.r * 2, at.r * 2]} />
    </mesh>
  );
}

/** The coloured pool of light each cabinet throws on the carpet in front of it; one instanced draw for all of them. */
function CabinetGlows({ cabinets }: { cabinets: Furniture[] }) {
  const mesh = useMemo(() => {
    const map = cachedCanvasTexture("arcade:glow-spot", 128, 128, drawGlowSpot);
    const material = new MeshBasicMaterial({
      map, color: "#ffffff", transparent: true, opacity: map ? 1 : 0, depthWrite: false, blending: AdditiveBlending, toneMapped: false,
    });
    const made = new InstancedMesh(new PlaneGeometry(1, 1).rotateX(-Math.PI / 2), material, Math.max(1, cabinets.length));
    const dummy = new Object3D();
    const colour = new Color();
    cabinets.forEach((cabinet, i) => {
      const ahead = 0.75;
      dummy.position.set(cabinet.x + Math.sin(cabinet.rotationY) * ahead, 0.012, cabinet.z + Math.cos(cabinet.rotationY) * ahead);
      dummy.rotation.set(0, cabinet.rotationY, 0);
      dummy.scale.set(1.15, 1, 0.95);
      dummy.updateMatrix();
      made.setMatrixAt(i, dummy.matrix);
      made.setColorAt(i, colour.set(gameForCabinet(cabinet.id)?.look.accent ?? HALL.neon.violet));
    });
    made.count = cabinets.length;
    made.instanceMatrix.needsUpdate = true;
    if (made.instanceColor) made.instanceColor.needsUpdate = true;
    made.computeBoundingSphere();
    return made;
  }, [cabinets]);
  useEffect(() => () => { mesh.geometry.dispose(); (mesh.material as MeshBasicMaterial).dispose(); mesh.dispose(); }, [mesh]);
  return <primitive object={mesh} />;
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

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

const PREVIEW_GEOMETRY = new PlaneGeometry(CABINET_SCREEN.w, CABINET_SCREEN.h);

/**
 * The cabinet the person stands at shows its game's own title screen, live.
 * A game whose code fails to load (or is still being written) keeps its
 * static attract screen: the preview is decoration, the overlay reports
 * real load errors when the person starts the game.
 */
function LivePreview({ cabinet }: { cabinet: Furniture }) {
  const game = gameForCabinet(cabinet.id);
  const [screen, setScreen] = useState<LiveScreen | null>(null);
  useEffect(() => {
    if (!game || !isRetroGame(game.id)) return undefined;
    let alive = true;
    let made: LiveScreen | null = null;
    loadRetroGame(game.id).then((retro) => {
      if (!alive) return;
      made = createLiveScreen(retro);
      setScreen(made);
    }, () => {
      // See above: no preview, the static attract screen stays.
    });
    return () => {
      alive = false;
      made?.dispose();
    };
  }, [game]);
  useFrame((_, dt) => screen?.tick(dt));
  if (!screen) return null;
  return (
    <group position={[cabinet.x, 0, cabinet.z]} rotation={[0, cabinet.rotationY, 0]}>
      <mesh geometry={PREVIEW_GEOMETRY} material={screen.material} position={[0, CABINET_SCREEN.y, CABINET_SCREEN.z + 0.003]}
        rotation={[CABINET_SCREEN.tilt, 0, 0]} />
    </group>
  );
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
  const near = nearCabinet ? cabinets.find((c) => c.id === nearCabinet) ?? null : null;
  const animate = awake && !reduced;
  return (
    <>
      <primitive attach="background" object={background} />
      <fog attach="fog" args={[HALL.space, span * 2.2, span * 4.5]} />
      <Stars radius={span * 3} depth={span} count={2200} factor={4} saturation={0.35} fade speed={reduced ? 0 : 0.3} />
      <HallLights layout={layout} />
      <HallSlab layout={layout} onFloorClick={onFloorClick} />
      {layout.rooms.filter((r) => r.kind === "prizes" || r.kind === "snack").map((room) => (
        <RoomCarpet key={room.id} room={room} onFloorClick={onFloorClick} />
      ))}
      <BackWalls layout={layout} />
      <RoomWalls walls={layout.walls} />
      {layout.rooms.map((room) => <RoomSign key={room.id} room={room} label={t(`society.office.room_${room.kind}`)} />)}
      {layout.furniture.map((item) => <FurniturePiece key={item.id} item={item} />)}
      <SnackTables tables={decor.tables} />
      <DancePad pad={decor.pad} animate={animate} />
      <Medallion at={decor.medallion} />
      <CabinetGlows cabinets={cabinets} />
      <AttractScreens animate={animate} />
      {near && animate && <LivePreview key={near.id} cabinet={near} />}
    </>
  );
}
