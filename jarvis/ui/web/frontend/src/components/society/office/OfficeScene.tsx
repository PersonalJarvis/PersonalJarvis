/**
 * The office floor: an oak-floored plate floating in a starry night behind
 * frameless glass, walled rooms in the north and south, one rug-zoned
 * department per provider family in between — and everybody in it. Each
 * floor dresses the shared plan in its own look (AgentsFloorLook /
 * CodingFloorLook).
 */
import { useLayoutEffect, useMemo } from "react";
import { Stars } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { Color } from "three";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { ToyLook } from "./toyFigureModel";
import { DeskInstances } from "./DeskInstances";
import { DeskDressing } from "./DeskDressing";
import { CodingSlab, CodingStudio } from "./CodingFloorLook";
import { CodingFloorAmbience } from "./CodingFloorAmbience";
import { AGENTS_SCENE, AgentsDepartment, AgentsSlab } from "./AgentsFloorLook";
import { AgentsFloorAmbience } from "./AgentsFloorAmbience";
import { ExecutiveDesks, LeadOfficeLight } from "./LeadSuite";
import { LiveMonitors } from "./LiveMonitors";
import { TerminalMonitors } from "./TerminalMonitors";
import type { PaneOccupant } from "./codingFloor";
import { GigiFlyer } from "./GigiFlyer";
import { useEventStore } from "@/store/events";
import type { DeskChat } from "./useDeskChats";
import { FurniturePiece, MeetingChairs } from "./OfficeProps";
import { TeamBoardFace } from "./TeamBoardFace";
import { TeamRoomFittings } from "./TeamRoomDecor";
import { BreakLoungeFittings } from "./BreakLounge";
import { WardrobeFittings } from "./WardrobeRoom";
import { LobbyFittings } from "./LobbyDecor";
import { SpawnFittings } from "./SpawnPoint";
import { RoomFloors, RoomSign, RoomWalls } from "./OfficeRooms";
import { CHECKPOINT_ICON, CheckpointMarker } from "./CheckpointMarker";
import { ElevatorCallButton } from "./ElevatorCallButton";
import { OFFICE_FIGURE_HEIGHT_M, OfficeAgents, type WalkerContext } from "./OfficeAgents";
import { OfficePlayer } from "./OfficePlayer";
import { OfficeDog } from "./OfficeDog";
import { PlayerBubble } from "./OfficeBubbles";
import { OfficeCameraRig } from "./OfficeCameraRig";
import { allDesks, type OfficeLayout, type Point } from "./officeLayout";
import { isWalkable, nearestWalkable, type NavGrid } from "./officeNav";
import { CODING_SCENE, OFFICE } from "./officePalette";
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
  const teamRoom = layout.rooms.find((r) => r.kind === "team");
  const breakRoom = layout.rooms.find((r) => r.kind === "break");
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
  const wardrobeRug = layout.furniture.find((f) => f.kind === "roundRug" && f.room === "wardrobe");
  const lobbyLamp = layout.furniture.find((f) => f.kind === "lobbyLamp");
  const spawnTerminal = layout.furniture.find((f) => f.kind === "spawnTerminal");
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
      <hemisphereLight args={coding ? [CODING_SCENE.sky, CODING_SCENE.ground, 0.95] : [AGENTS_SCENE.sky, AGENTS_SCENE.ground, 0.95]} />
      <ambientLight intensity={0.25} />
      <directionalLight position={[maxX + 10, 26, maxZ + 6]} intensity={1.55} color="#fff7ec" castShadow
        shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004} shadow-normalBias={0.03}
        shadow-camera-left={-span * 0.7} shadow-camera-right={span * 0.7}
        shadow-camera-top={span * 0.7} shadow-camera-bottom={-span * 0.7} shadow-camera-far={120} />
      {coding ? <CodingSlab layout={layout} onFloorClick={onFloorClick} /> : <AgentsSlab layout={layout} onFloorClick={onFloorClick} />}
      {coding ? <CodingFloorAmbience layout={layout} /> : <AgentsFloorAmbience layout={layout} />}
      <RoomFloors rooms={layout.rooms} />
      <RoomWalls walls={layout.walls} />
      {layout.rooms.map((room) => <RoomSign key={room.id} room={room} label={t(`society.office.room_${room.kind}`)} />)}
      {coding
        ? <>
          {layout.departments.map((dept) => <CodingStudio key={dept.id} dept={dept} agents={agents} />)}
          <DeskDressing desks={benchDesks} departments={layout.departments} />
        </>
        : <>
          {layout.departments.map((dept) => <AgentsDepartment key={dept.id} dept={dept} />)}
          <DeskInstances desks={benchDesks} agents={agents} zones={zones} />
          <DeskDressing desks={benchDesks} departments={layout.departments} floor="agents" />
        </>}
      <ExecutiveDesks desks={desks} agents={agents} onOpenScreen={onOpenScreen} />
      {leadRoom && <LeadOfficeLight room={leadRoom} />}
      {floor === "coding"
        ? <TerminalMonitors desks={desks} occupants={occupants} awake={awake} onOpen={onOpenScreen} />
        : <LiveMonitors desks={desks} agents={agents} chats={chats} onOpen={onOpenScreen} />}
      {layout.furniture.map((item) => <FurniturePiece key={item.id} item={item} />)}
      {table && <MeetingChairs table={table} />}
      {board && <TeamBoardFace board={board} enabled={floor === "agents"} />}
      {teamRoom && table && <TeamRoomFittings room={teamRoom} table={table} />}
      {breakRoom && <BreakLoungeFittings room={breakRoom} furniture={layout.furniture} />}
      {wardrobeRug && <WardrobeFittings rug={wardrobeRug} />}
      {lobbyLamp && <LobbyFittings lamp={lobbyLamp} />}
      {spawnTerminal && <SpawnFittings terminal={spawnTerminal} arrival={layout.arrival} floor={floor} newcomers={newcomers} animate={awake && !reduced} />}
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
