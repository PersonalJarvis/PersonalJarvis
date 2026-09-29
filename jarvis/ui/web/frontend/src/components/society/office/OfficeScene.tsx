/**
 * The office floor: a microcement plate floating in a starry night behind
 * frameless glass, walled rooms in the north and south, one felt-zoned
 * department per provider family in between — and everybody in it.
 */
import { useLayoutEffect, useMemo } from "react";
import { Stars } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { CanvasTexture, Color, RepeatWrapping, Shape, SRGBColorSpace, type Texture } from "three";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { ToyLook } from "./toyFigureModel";
import { Railing, SignWall } from "./OfficeFurniture";
import { DeskInstances } from "./DeskInstances";
import { DeskDressing } from "./DeskDressing";
import { CodingSlab, CodingStudio } from "./CodingFloorLook";
import { ExecutiveDesks, LeadOfficeLight } from "./LeadSuite";
import { LiveMonitors } from "./LiveMonitors";
import { TerminalMonitors } from "./TerminalMonitors";
import type { PaneOccupant } from "./codingFloor";
import { GigiFlyer } from "./GigiFlyer";
import { useEventStore } from "@/store/events";
import type { DeskChat } from "./useDeskChats";
import { FurniturePiece, MeetingChairs } from "./OfficeProps";
import { TeamBoardFace } from "./TeamBoardFace";
import { RoomFloors, RoomSign, RoomWalls } from "./OfficeRooms";
import { CHECKPOINT_ICON, CheckpointMarker } from "./CheckpointMarker";
import { ElevatorCallButton } from "./ElevatorCallButton";
import { OFFICE_FIGURE_HEIGHT_M, OfficeAgents, type WalkerContext } from "./OfficeAgents";
import { OfficePlayer } from "./OfficePlayer";
import { OfficeDog } from "./OfficeDog";
import { PlayerBubble } from "./OfficeBubbles";
import { OfficeCameraRig } from "./OfficeCameraRig";
import { allDesks, type Department, type OfficeLayout, type Point } from "./officeLayout";
import { isWalkable, nearestWalkable, type NavGrid } from "./officeNav";
import { CODING_SCENE, DEPARTMENT_ZONES, OFFICE } from "./officePalette";
import { officeSession, player as playerBody, useOfficeStore, type OfficeFloor, type Selection } from "./officeStore";
import { arrivalPose } from "./officeFloors";

/** The person's character as a mover for Gigi to follow (the body object itself, mutated every frame). */
const PLAYER_OWNER = { current: playerBody };

/**
 * Places the character on a floor it just arrived at (elevator ride, or a
 * mount that asked for another floor). Runs as a layout effect, so it lands
 * before the player controller checks its spot; it repeats for every rebuilt
 * plan until the floor's roster has loaded, then the arrival is done.
 */
function FloorArrival({ floor, layout, grid, ready }: { floor: OfficeFloor; layout: OfficeLayout; grid: NavGrid; ready: boolean }) {
  useLayoutEffect(() => {
    const arrival = officeSession.arrival;
    if (!arrival || arrival.floor !== floor) return;
    const pose = arrivalPose(layout, arrival.at, officeSession.floors[floor]);
    const spot = isWalkable(grid, pose) ? pose : nearestWalkable(grid, pose) ?? layout.spawn;
    playerBody.x = spot.x; playerBody.z = spot.z; playerBody.heading = pose.heading;
    playerBody.path = []; playerBody.moving = false;
    officeSession.playerPlaced = true;
    if (ready) officeSession.arrival = null;
  }, [floor, layout, grid, ready]);
  return null;
}

/** On the coding floor Jarvis is nobody's desk mate: Gigi flies along with the person. */
function GigiCompanion({ grid, awake, reduced }: { grid: NavGrid; awake: boolean; reduced: boolean }) {
  const speaking = useEventStore((s) => s.voiceState === "speaking");
  const clear = useMemo(() => (x: number, z: number) => isWalkable(grid, { x, z }), [grid]);
  return <GigiFlyer owner={PLAYER_OWNER} mode="follow" speaking={speaking} paused={!awake} reduced={reduced} clear={clear} />;
}

/** Metres covered by one repeat of the floor texture (one poured panel). */
const FLOOR_PANEL_M = 4;

/** Microcement drawn once: soft clouds of tone and faint panel seams; repeated across the floor. */
let floorTexture: Texture | null | undefined;
function microcement(): Texture | null {
  if (floorTexture !== undefined) return floorTexture;
  const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas?.getContext("2d") ?? null;
  } catch {
    // jsdom without the canvas package: no texture, the flat floor colour is the right fallback.
    ctx = null;
  }
  floorTexture = null;
  if (canvas && ctx) {
    const size = 512;
    canvas.width = size;
    canvas.height = size;
    ctx.fillStyle = OFFICE.floor;
    ctx.fillRect(0, 0, size, size);
    // Deterministic clouds, drawn at every wrapped offset so the tile repeats seamlessly.
    let seed = 0x2f6e2b1;
    const rand = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 0x1_0000_0000);
    for (let i = 0; i < 70; i += 1) {
      const x = rand() * size, y = rand() * size, r = 30 + rand() * 90;
      const tone = OFFICE.floorCloud[i % OFFICE.floorCloud.length];
      for (const dx of [-size, 0, size]) {
        for (const dy of [-size, 0, size]) {
          const g = ctx.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, r);
          g.addColorStop(0, `${tone}66`);
          g.addColorStop(1, `${tone}00`);
          ctx.fillStyle = g;
          ctx.fillRect(x + dx - r, y + dy - r, r * 2, r * 2);
        }
      }
    }
    // Fine grain, then the panel seams on two edges (they meet the next tile's).
    for (let i = 0; i < 5000; i += 1) {
      ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.10)" : "rgba(90,84,76,0.07)";
      ctx.fillRect(Math.floor(rand() * size), Math.floor(rand() * size), 1, 1);
    }
    ctx.fillStyle = OFFICE.floorSeam;
    ctx.fillRect(0, 0, size, 2);
    ctx.fillRect(0, 0, 2, size);
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.wrapS = texture.wrapT = RepeatWrapping;
    texture.anisotropy = 8;
    floorTexture = texture;
  }
  return floorTexture;
}

function Slab({ layout, onFloorClick }: { layout: OfficeLayout; onFloorClick: (event: ThreeEvent<MouseEvent>) => void }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const w = maxX - minX, d = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  const floorMap = useMemo(() => {
    const base = microcement();
    if (!base) return null;
    const map = base.clone();
    map.repeat.set(w / FLOOR_PANEL_M, d / FLOOR_PANEL_M);
    map.needsUpdate = true;
    return map;
  }, [w, d]);
  return (
    <group>
      <mesh position={[cx, -0.3, cz]} receiveShadow>
        <boxGeometry args={[w, 0.6, d]} />
        <meshStandardMaterial color={OFFICE.slabEdge} roughness={0.9} />
      </mesh>
      <mesh position={[cx, 0.001, cz]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow onClick={onFloorClick}>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial color={floorMap ? "#ffffff" : OFFICE.walkway} map={floorMap} roughness={0.55} />
      </mesh>
      <Railing from={[minX + 0.2, minZ + 0.2]} to={[maxX - 0.2, minZ + 0.2]} />
      <Railing from={[maxX - 0.2, minZ + 0.2]} to={[maxX - 0.2, maxZ - 0.2]} />
      <Railing from={[maxX - 0.2, maxZ - 0.2]} to={[minX + 0.2, maxZ - 0.2]} />
      <Railing from={[minX + 0.2, maxZ - 0.2]} to={[minX + 0.2, minZ + 0.2]} />
    </group>
  );
}

/** A flat rounded rectangle in the XY plane, laid on the floor by its mesh's rotation. */
function roundedRect(w: number, d: number, r: number): Shape {
  const x = -w / 2, y = -d / 2;
  const shape = new Shape();
  shape.moveTo(x + r, y);
  shape.lineTo(x + w - r, y);
  shape.quadraticCurveTo(x + w, y, x + w, y + r);
  shape.lineTo(x + w, y + d - r);
  shape.quadraticCurveTo(x + w, y + d, x + w - r, y + d);
  shape.lineTo(x + r, y + d);
  shape.quadraticCurveTo(x, y + d, x, y + d - r);
  shape.lineTo(x, y + r);
  shape.quadraticCurveTo(x, y, x + r, y);
  return shape;
}

/** A department zone: a rounded felt rug with a darker border band, and its fluted back wall. */
function DepartmentArea({ dept }: { dept: Department }) {
  const t = useT();
  const w = dept.maxX - dept.minX, d = dept.maxZ - dept.minZ;
  const cx = (dept.minX + dept.maxX) / 2, cz = (dept.minZ + dept.maxZ) / 2;
  const zone = DEPARTMENT_ZONES[dept.tint % DEPARTMENT_ZONES.length];
  const outer = useMemo(() => roundedRect(w - 0.1, d - 0.1, 0.45), [w, d]);
  const inner = useMemo(() => roundedRect(w - 0.4, d - 0.4, 0.32), [w, d]);
  return (
    <group>
      <mesh position={[cx, 0.005, cz]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <shapeGeometry args={[outer, 6]} />
        <meshStandardMaterial color={zone.panel} roughness={1} />
      </mesh>
      <mesh position={[cx, 0.007, cz]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <shapeGeometry args={[inner, 6]} />
        <meshStandardMaterial color={zone.rug} roughness={1} />
      </mesh>
      <SignWall label={dept.label || t("society.office.open_space")} width={w - 0.4} position={[cx, 0, dept.minZ + 0.1]} zone={dept.tint} />
    </group>
  );
}

export interface OfficeSceneProps {
  floor: OfficeFloor;
  /** The coding floor's figures by agent id (their IDE panes); empty on the agents floor. */
  occupants: ReadonlyMap<string, PaneOccupant>;
  /** The floor's roster has loaded (ends a pending arrival). */
  ready: boolean;
  layout: OfficeLayout;
  grid: NavGrid;
  walkers: WalkerContext;
  agents: ReadonlyMap<string, SocietyAgent>;
  newcomers: ReadonlySet<string>;
  awake: boolean;
  reduced: boolean;
  overview: number;
  player: { look: ToyLook; name: string };
  selection: Selection | null;
  nearby: Selection | null;
  chats: ReadonlyMap<string, DeskChat>;
  onOpenScreen: (agentId: string, screen: Point & { y: number }, facing: number) => void;
  /** The elevator's call button: lit after a press, how many work on the other floor, and the press itself. */
  elevatorCall: { lit: boolean; count: number | null; onPress: () => void };
}

export function OfficeScene({ floor, occupants, ready, layout, grid, walkers, agents, newcomers, awake, reduced, overview, player, selection, nearby, chats, onOpenScreen, elevatorCall }: OfficeSceneProps) {
  const t = useT();
  const desks = useMemo(() => allDesks(layout), [layout]);
  const shaft = layout.furniture.find((f) => f.kind === "elevator");
  const atLift = nearby?.kind === "checkpoint" && nearby.id === "elevator";
  // Lead desks carry their own size and are built as executive desks, not bench instances.
  const benchDesks = useMemo(() => desks.filter((d) => !d.size), [desks]);
  // Each desk takes its department's zone colour for the felt screen and the seat fabric.
  const zones = useMemo(() => new Map(layout.departments.flatMap((dept) => dept.desks.map((desk) => [desk.id, dept.tint] as const))), [layout]);
  const leadRoom = layout.rooms.find((r) => r.kind === "lead");
  const dogBeds = useMemo(() => layout.furniture.filter((f) => f.kind === "dogBed"), [layout]);
  const treatJar = layout.furniture.find((f) => f.kind === "treatJar") ?? null;
  const coding = floor === "coding";
  // The coding floor floats in a violet night of its own, so a glance tells the floors apart.
  const space = coding ? CODING_SCENE.space : OFFICE.space;
  const background = useMemo(() => new Color(space), [space]);
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const span = Math.max(maxX - minX, maxZ - minZ);
  const select = useOfficeStore((s) => s.select);
  const table = layout.furniture.find((f) => f.kind === "meetingTable");
  const board = layout.furniture.find((f) => f.kind === "teamBoard");
  const onFloorClick = (event: ThreeEvent<MouseEvent>) => {
    // A drag that ends on the floor rotated the camera; only a real click walks.
    if (event.delta > 6) return;
    event.stopPropagation();
    useOfficeStore.getState().requestWalk({ x: event.point.x, z: event.point.z });
  };
  return (
    <>
      <primitive attach="background" object={background} />
      <fog attach="fog" args={[space, span * 2.2, span * 4.5]} />
      <Stars radius={span * 3} depth={span} count={2500} factor={4} saturation={0} fade speed={reduced ? 0 : 0.3} />
      <hemisphereLight args={coding ? [CODING_SCENE.sky, CODING_SCENE.ground, 0.95] : ["#eef2ff", "#8a8279", 0.95]} />
      <ambientLight intensity={0.25} />
      <directionalLight position={[maxX + 10, 26, maxZ + 6]} intensity={1.55} color="#fff7ec" castShadow
        shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004} shadow-normalBias={0.03}
        shadow-camera-left={-span * 0.7} shadow-camera-right={span * 0.7}
        shadow-camera-top={span * 0.7} shadow-camera-bottom={-span * 0.7} shadow-camera-far={120} />
      {coding ? <CodingSlab layout={layout} onFloorClick={onFloorClick} /> : <Slab layout={layout} onFloorClick={onFloorClick} />}
      <RoomFloors rooms={layout.rooms} />
      <RoomWalls walls={layout.walls} />
      {layout.rooms.map((room) => <RoomSign key={room.id} room={room} label={t(`society.office.room_${room.kind}`)} />)}
      {coding
        ? <>
          {layout.departments.map((dept) => <CodingStudio key={dept.id} dept={dept} agents={agents} />)}
          <DeskDressing desks={benchDesks} departments={layout.departments} />
        </>
        : <>
          {layout.departments.map((dept) => <DepartmentArea key={dept.id} dept={dept} />)}
          <DeskInstances desks={benchDesks} agents={agents} zones={zones} />
        </>}
      <ExecutiveDesks desks={desks} agents={agents} onOpenScreen={onOpenScreen} />
      {leadRoom && <LeadOfficeLight room={leadRoom} />}
      {floor === "coding"
        ? <TerminalMonitors desks={desks} occupants={occupants} awake={awake} onOpen={onOpenScreen} />
        : <LiveMonitors desks={desks} agents={agents} chats={chats} onOpen={onOpenScreen} />}
      {layout.furniture.map((item) => <FurniturePiece key={item.id} item={item} />)}
      {table && <MeetingChairs table={table} />}
      {board && <TeamBoardFace board={board} enabled={floor === "agents"} />}
      {/* At the elevator its call button takes over from the floating token, which would hide it. */}
      {layout.checkpoints.filter((cp) => cp.id !== "elevator" || !atLift).map((cp) => (
        <CheckpointMarker key={cp.id} checkpoint={cp} label={t(`society.office.cp_${cp.id}`)} icon={CHECKPOINT_ICON[cp.id]}
          active={(nearby?.kind === "checkpoint" && nearby.id === cp.id) || (selection?.kind === "checkpoint" && selection.id === cp.id)}
          animate={awake && !reduced} onActivate={() => select({ kind: "checkpoint", id: cp.id })} />
      ))}
      {shaft && (
        <ElevatorCallButton shaft={shaft} floor={floor} lit={elevatorCall.lit} count={elevatorCall.count} animate={awake && !reduced}
          near={atLift} onPress={elevatorCall.onPress} />
      )}
      <FloorArrival floor={floor} layout={layout} grid={grid} ready={ready} />
      <OfficePlayer layout={layout} grid={grid} look={player.look} name={player.name} awake={awake} reduced={reduced} />
      {dogBeds.length > 0 && <OfficeDog beds={dogBeds} rooms={layout.rooms} jar={treatJar} grid={grid} awake={awake} reduced={reduced} />}
      <PlayerBubble height={OFFICE_FIGURE_HEIGHT_M + 0.49} />
      <OfficeAgents desks={desks} agents={agents} ctx={walkers} newcomers={newcomers} awake={awake} reduced={reduced} chats={chats}
        selectedId={selection?.kind === "agent" ? selection.id : null} onSelect={(id) => select({ kind: "agent", id })} />
      {floor === "coding" && <GigiCompanion grid={grid} awake={awake} reduced={reduced} />}
      <OfficeCameraRig layout={layout} overview={overview} />
    </>
  );
}
