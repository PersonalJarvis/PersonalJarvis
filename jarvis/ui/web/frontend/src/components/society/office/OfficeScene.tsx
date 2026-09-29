/**
 * The office floor: a wood-plank plate floating in a starry night, glass
 * railings around it, walled rooms in the north and south, one carpeted
 * department per provider family in between — and everybody in it.
 */
import { useLayoutEffect, useMemo } from "react";
import { Stars } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { CanvasTexture, Color, RepeatWrapping, SRGBColorSpace, type Texture } from "three";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { ToyLook } from "./toyFigureModel";
import { Railing, SignWall } from "./OfficeFurniture";
import { DeskInstances } from "./DeskInstances";
import { ExecutiveDesks, LeadOfficeLight } from "./LeadSuite";
import { LiveMonitors } from "./LiveMonitors";
import { TerminalMonitors } from "./TerminalMonitors";
import type { PaneOccupant } from "./codingFloor";
import { GigiFlyer } from "./GigiFlyer";
import { useEventStore } from "@/store/events";
import type { DeskChat } from "./useDeskChats";
import { FurniturePiece, MeetingChairs } from "./OfficeProps";
import { RoomFloors, RoomSign, RoomWalls } from "./OfficeRooms";
import { CHECKPOINT_ICON, CheckpointMarker } from "./CheckpointMarker";
import { OFFICE_FIGURE_HEIGHT_M, OfficeAgents, type WalkerContext } from "./OfficeAgents";
import { OfficePlayer } from "./OfficePlayer";
import { PlayerBubble } from "./OfficeBubbles";
import { OfficeCameraRig } from "./OfficeCameraRig";
import { allDesks, type Department, type OfficeLayout, type Point } from "./officeLayout";
import { isWalkable, nearestWalkable, type NavGrid } from "./officeNav";
import { DEPARTMENT_TINTS, OFFICE } from "./officePalette";
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

/** Warm planks drawn once; repeated across the floor. */
let plankTexture: Texture | null | undefined;
function planks(): Texture | null {
  if (plankTexture !== undefined) return plankTexture;
  const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
  const ctx = canvas?.getContext("2d") ?? null;
  plankTexture = null;
  if (canvas && ctx) {
    canvas.width = 256;
    canvas.height = 256;
    const tones = ["#c9a27a", "#c29a71", "#cfa983", "#bf966c"];
    ctx.fillStyle = tones[0];
    ctx.fillRect(0, 0, 256, 256);
    for (let row = 0; row < 8; row += 1) {
      // Staggered joints; segments run past both edges so the tile wraps seamlessly.
      const offset = ((row % 2) * 64 + ((row * 53) % 64)) - 128;
      for (let seg = 0; seg < 4; seg += 1) {
        ctx.fillStyle = tones[(row + seg + 4) % tones.length];
        ctx.fillRect(offset + seg * 128, row * 32, 128, 32);
        ctx.fillStyle = "rgba(90,60,35,0.35)";
        ctx.fillRect(offset + seg * 128, row * 32, 2, 32);
      }
      ctx.fillStyle = "rgba(90,60,35,0.3)";
      ctx.fillRect(0, row * 32, 256, 2);
    }
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.wrapS = texture.wrapT = RepeatWrapping;
    texture.anisotropy = 8;
    plankTexture = texture;
  }
  return plankTexture;
}

function Slab({ layout, onFloorClick }: { layout: OfficeLayout; onFloorClick: (event: ThreeEvent<MouseEvent>) => void }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const w = maxX - minX, d = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  const floorMap = useMemo(() => {
    const base = planks();
    if (!base) return null;
    const map = base.clone();
    map.repeat.set(w / 3, d / 3);
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
        <meshStandardMaterial color={floorMap ? "#ffffff" : OFFICE.walkway} map={floorMap} roughness={0.8} />
      </mesh>
      <Railing from={[minX + 0.2, minZ + 0.2]} to={[maxX - 0.2, minZ + 0.2]} />
      <Railing from={[maxX - 0.2, minZ + 0.2]} to={[maxX - 0.2, maxZ - 0.2]} />
      <Railing from={[maxX - 0.2, maxZ - 0.2]} to={[minX + 0.2, maxZ - 0.2]} />
      <Railing from={[minX + 0.2, maxZ - 0.2]} to={[minX + 0.2, minZ + 0.2]} />
    </group>
  );
}

function DepartmentArea({ dept }: { dept: Department }) {
  const t = useT();
  const w = dept.maxX - dept.minX, d = dept.maxZ - dept.minZ;
  const cx = (dept.minX + dept.maxX) / 2, cz = (dept.minZ + dept.maxZ) / 2;
  const tint = DEPARTMENT_TINTS[dept.tint % DEPARTMENT_TINTS.length];
  return (
    <group>
      <mesh position={[cx, 0.006, cz]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial color={tint} roughness={0.95} />
      </mesh>
      <SignWall label={dept.label || t("society.office.open_space")} width={w - 0.4} position={[cx, 0, dept.minZ + 0.1]} />
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
}

export function OfficeScene({ floor, occupants, ready, layout, grid, walkers, agents, newcomers, awake, reduced, overview, player, selection, nearby, chats, onOpenScreen }: OfficeSceneProps) {
  const t = useT();
  const desks = useMemo(() => allDesks(layout), [layout]);
  // Lead desks carry their own size and are built as executive desks, not bench instances.
  const benchDesks = useMemo(() => desks.filter((d) => !d.size), [desks]);
  const leadRoom = layout.rooms.find((r) => r.kind === "lead");
  const background = useMemo(() => new Color(OFFICE.space), []);
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const span = Math.max(maxX - minX, maxZ - minZ);
  const select = useOfficeStore((s) => s.select);
  const table = layout.furniture.find((f) => f.kind === "meetingTable");
  const onFloorClick = (event: ThreeEvent<MouseEvent>) => {
    // A drag that ends on the floor rotated the camera; only a real click walks.
    if (event.delta > 6) return;
    event.stopPropagation();
    useOfficeStore.getState().requestWalk({ x: event.point.x, z: event.point.z });
  };
  return (
    <>
      <primitive attach="background" object={background} />
      <fog attach="fog" args={[OFFICE.space, span * 2.2, span * 4.5]} />
      <Stars radius={span * 3} depth={span} count={2500} factor={4} saturation={0} fade speed={reduced ? 0 : 0.3} />
      <hemisphereLight args={["#dfe9ff", "#6b5a48", 0.9]} />
      <ambientLight intensity={0.25} />
      <directionalLight position={[maxX + 10, 26, maxZ + 6]} intensity={1.6} castShadow
        shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004} shadow-normalBias={0.03}
        shadow-camera-left={-span * 0.7} shadow-camera-right={span * 0.7}
        shadow-camera-top={span * 0.7} shadow-camera-bottom={-span * 0.7} shadow-camera-far={120} />
      <Slab layout={layout} onFloorClick={onFloorClick} />
      <RoomFloors rooms={layout.rooms} />
      <RoomWalls walls={layout.walls} />
      {layout.rooms.map((room) => <RoomSign key={room.id} room={room} label={t(`society.office.room_${room.kind}`)} />)}
      {layout.departments.map((dept) => <DepartmentArea key={dept.id} dept={dept} />)}
      <DeskInstances desks={benchDesks} agents={agents} />
      <ExecutiveDesks desks={desks} agents={agents} onOpenScreen={onOpenScreen} />
      {leadRoom && <LeadOfficeLight room={leadRoom} />}
      {floor === "coding"
        ? <TerminalMonitors desks={desks} occupants={occupants} awake={awake} onOpen={onOpenScreen} />
        : <LiveMonitors desks={desks} agents={agents} chats={chats} onOpen={onOpenScreen} />}
      {layout.furniture.map((item) => <FurniturePiece key={item.id} item={item} />)}
      {table && <MeetingChairs table={table} />}
      {layout.checkpoints.map((cp) => (
        <CheckpointMarker key={cp.id} checkpoint={cp} label={t(`society.office.cp_${cp.id}`)} icon={CHECKPOINT_ICON[cp.id]}
          active={(nearby?.kind === "checkpoint" && nearby.id === cp.id) || (selection?.kind === "checkpoint" && selection.id === cp.id)}
          animate={awake && !reduced} onActivate={() => select({ kind: "checkpoint", id: cp.id })} />
      ))}
      <FloorArrival floor={floor} layout={layout} grid={grid} ready={ready} />
      <OfficePlayer layout={layout} grid={grid} look={player.look} name={player.name} awake={awake} reduced={reduced} />
      <PlayerBubble height={OFFICE_FIGURE_HEIGHT_M + 0.49} />
      <OfficeAgents desks={desks} agents={agents} ctx={walkers} newcomers={newcomers} awake={awake} reduced={reduced} chats={chats}
        selectedId={selection?.kind === "agent" ? selection.id : null} onSelect={(id) => select({ kind: "agent", id })} />
      {floor === "coding" && <GigiCompanion grid={grid} awake={awake} reduced={reduced} />}
      <OfficeCameraRig layout={layout} overview={overview} />
    </>
  );
}
