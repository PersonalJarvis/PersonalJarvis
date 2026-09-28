/**
 * Agents living in the office. Working agents sit at their screens, waiting
 * agents stand and wave at their desk, idle agents wander: coffee, couch,
 * window, arcade, a chat at a busy colleague's desk. Paused agents nap.
 *
 * The office is a projection of the roster: run state comes from the backend,
 * everything else is client-side choreography that costs no tokens and never
 * starts or stops work.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { DoubleSide, Vector3, type Group, type Mesh, type MeshBasicMaterial } from "three";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { FigureDrive, FigureMode } from "../figures/FigureRig";
import { ToyFigure } from "./ToyFigure";
import { GigiFlyer } from "./GigiFlyer";
import type { GigiFlightMode } from "./gigiFlight";
import { useEventStore } from "@/store/events";
import { SEAT_HEIGHT, toyLookFor } from "./toyFigureModel";
import { OFFICE } from "./officePalette";
import type { DeskSlot, OfficeLayout, Point } from "./officeLayout";
import { findPath, isWalkable, type NavGrid } from "./officeNav";
import { AgentFollower } from "../companion/AgentFollower";
import { resolveCompanion } from "../companion/appearance";
import type { TrailPoint } from "../companion/trail";
import { stepMover, turnToward, WALK_SPEED, type Mover } from "./officeMotion";
import { createRng, planFor, type ActivityKind, type Plan, type Pose, type SpotBook } from "./officeBehavior";
import { useOfficeStore } from "./officeStore";
import { agentPositions, seatedAtDesk } from "./walkerRegistry";

/** The agent's symbol walks behind it as a little pet, about a fifth of its height. */
export const PET_SIZE_M = 0.26;
const PET_FOLLOW_M = 0.7;

/** Every figure shares one toy scale, so desks and couches read the same everywhere. */
export const OFFICE_FIGURE_HEIGHT_M = 1.3;

/** Pose → animation clip. Seated work uses the seated clip; the monitor shows the typing. */
export const POSE_CLIP: Record<Pose, FigureMode> = { sit: "sit", work: "sit", stand: "idle", wave: "wave", talk: "talk", sleep: "sleep" };

/** The seat-top height under a seated agent, per activity; the figure puts its hips exactly there. */
export function seatHeightFor(kind: ActivityKind): number {
  if (kind === "couch" || kind === "nap") return SEAT_HEIGHT.couch;
  if (kind === "beanbag") return SEAT_HEIGHT.beanbag;
  if (kind === "meeting") return SEAT_HEIGHT.meeting;
  return SEAT_HEIGHT.chair;
}

/** Nameplate scale by camera distance: readable up close, compact in the overview, never huge. */
export function plateScale(distance: number): number {
  return Math.min(1.05, Math.max(0.8, 24 / Math.max(1, distance)));
}

const RING_COLOUR = { working: OFFICE.ringWorking, idle: OFFICE.ringIdle, waiting: OFFICE.ringWaiting, paused: OFFICE.ringPaused } as const;

/** Jarvis is not a person in the office: it is Gigi, flying at chest height. */
function gigiModeFor(pose: Pose | null, travelling: boolean): GigiFlightMode {
  if (travelling || !pose) return "idle";
  if (pose === "sit" || pose === "work") return "work";
  if (pose === "stand") return "idle";
  return pose;
}

function Nameplate({ agent, activity, selected, onSelect, height = OFFICE_FIGURE_HEIGHT_M + 0.35 }: {
  agent: SocietyAgent; activity: ActivityKind | null; selected: boolean; onSelect: (id: string) => void; height?: number;
}) {
  const t = useT();
  const plate = useRef<HTMLButtonElement>(null);
  const anchor = useRef<Group>(null);
  const last = useRef(0);
  const world = useMemo(() => new Vector3(), []);
  useFrame(({ camera }) => {
    if (!anchor.current || !plate.current) return;
    const scale = plateScale(camera.position.distanceTo(anchor.current.getWorldPosition(world)));
    if (Math.abs(scale - last.current) < 0.02) return;
    last.current = scale;
    plate.current.style.transform = `scale(${scale.toFixed(2)})`;
  });
  const detail = agent.state !== "idle" ? t(`society.office.state_${agent.state}`) : activity ? t(`society.office.activity_${activity}`) : "";
  return (
    <group ref={anchor} position={[0, height, 0]}>
      <Html center zIndexRange={[20, 0]}>
        <button ref={plate} type="button" data-office-ui className="office-plate" data-state={agent.state} data-selected={selected || undefined}
          onClick={(event) => { event.stopPropagation(); onSelect(agent.agentId); }}
          aria-label={t("society.office.open_agent").replace("{0}", agent.name)}>
          <span className="office-plate-badge" style={{ background: agent.palette.primary }} aria-hidden>
            {agent.tier === "lead" ? "★" : agent.name.slice(0, 1).toUpperCase()}
          </span>
          <span className="office-plate-name">{agent.name}</span>
          <span className="office-plate-state" title={t(`society.office.state_${agent.state}`)}>
            <i aria-hidden />{detail ? <em>{detail}</em> : null}
          </span>
        </button>
      </Html>
    </group>
  );
}

export interface WalkerContext {
  layout: OfficeLayout;
  grid: NavGrid;
  book: SpotBook;
  /** Busy colleagues someone idle may visit, refreshed with the roster. */
  colleagues: () => { agentId: string; desk: DeskSlot }[];
  spawn: Point;
}

function Walker({ agent, desk, ctx, arrivesByElevator, awake, reduced, selected, onSelect }: {
  agent: SocietyAgent; desk: DeskSlot | null; ctx: WalkerContext; arrivesByElevator: boolean;
  awake: boolean; reduced: boolean; selected: boolean; onSelect: (id: string) => void;
}) {
  const look = useMemo(() => toyLookFor(agent.figure, agent.agentId), [agent.figure, agent.agentId]);
  const group = useRef<Group>(null);
  const body = useRef<Group>(null);
  const ring = useRef<Mesh>(null);
  const drive = useRef<FigureDrive>({ mode: "idle", speed: 0 });
  const rng = useMemo(() => createRng(agent.agentId), [agent.agentId]);
  const mover = useRef<Mover>({ ...(arrivesByElevator ? ctx.spawn : { x: 0, z: 0 }), heading: Math.PI, path: [] });
  const plan = useRef<Plan | null>(null);
  const phase = useRef<"travel" | "dwell">("dwell");
  const dwellUntil = useRef(0);
  const placed = useRef(arrivesByElevator);
  const planState = useRef("");
  const summonKey = useRef("");
  const [activity, setActivity] = useState<ActivityKind | null>(null);
  const [seatHeight, setSeatHeight] = useState<number>(SEAT_HEIGHT.chair);
  const isGigi = agent.tier === "lead";
  const [gigiPose, setGigiPose] = useState<{ pose: Pose | null; travelling: boolean }>({ pose: null, travelling: false });
  const speaking = useEventStore((s) => isGigi && s.voiceState === "speaking");

  // Leaving the office releases the agent's spot and its registry entry.
  useEffect(() => () => {
    ctx.book.release(agent.agentId);
    agentPositions.delete(agent.agentId);
    seatedAtDesk.delete(agent.agentId);
  }, [ctx.book, agent.agentId]);
  const pet = useMemo(() => ({ ...resolveCompanion(agent.agentId, agent.figure?.companion), sizeM: PET_SIZE_M, followDistanceM: PET_FOLLOW_M }),
    [agent.agentId, agent.figure?.companion]);
  const petClear = useMemo(() => (point: TrailPoint, radius: number) =>
    isWalkable(ctx.grid, { x: point[0] - radius, z: point[2] }) && isWalkable(ctx.grid, { x: point[0] + radius, z: point[2] })
    && isWalkable(ctx.grid, { x: point[0], z: point[2] - radius }) && isWalkable(ctx.grid, { x: point[0], z: point[2] + radius }), [ctx.grid]);

  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.1);
    const now = Date.now();
    const m = mover.current;
    const summon = useOfficeStore.getState().summons[agent.agentId];
    const calledTo = summon && summon.untilMs > now ? summon.target : null;
    const sKey = calledTo ? `${calledTo.x.toFixed(2)},${calledTo.z.toFixed(2)}` : "";
    const needsPlan = !plan.current || planState.current !== agent.state || summonKey.current !== sKey
      || (phase.current === "dwell" && now >= dwellUntil.current);
    if (needsPlan) {
      const next = planFor({
        agentId: agent.agentId, state: agent.state, desk, layout: ctx.layout, grid: ctx.grid, rng, book: ctx.book,
        previous: plan.current?.kind ?? null, workingColleagues: ctx.colleagues().filter((c) => c.agentId !== agent.agentId), calledTo,
      });
      // Reduced motion: no idle wandering — a placement holds until the state changes.
      if (reduced && !calledTo) next.dwellMs = Infinity;
      plan.current = next;
      planState.current = agent.state;
      summonKey.current = sKey;
      if (!placed.current || reduced) {
        // First sight (or reduced motion): already there, no walk across the floor.
        m.x = next.target.x; m.z = next.target.z; m.path = [];
        if (next.facing !== null) m.heading = next.facing;
        placed.current = true;
        phase.current = "dwell";
        dwellUntil.current = now + next.dwellMs;
      } else {
        m.path = findPath(ctx.grid, m, next.target) ?? [];
        if (m.path.length === 0) { m.x = next.target.x; m.z = next.target.z; }
        phase.current = m.path.length > 0 ? "travel" : "dwell";
        if (phase.current === "dwell") dwellUntil.current = now + next.dwellMs;
      }
      if (next.kind !== activity) setActivity(next.kind);
      if (isGigi) setGigiPose({ pose: next.pose, travelling: phase.current === "travel" });
      const nextSeat = seatHeightFor(next.kind);
      if (nextSeat !== seatHeight) setSeatHeight(nextSeat);
    }
    const p = plan.current!;
    if (phase.current === "travel") {
      const { moved, arrived } = awake ? stepMover(m, WALK_SPEED, dt) : { moved: 0, arrived: false };
      drive.current.mode = "walk";
      drive.current.speed = moved / Math.max(dt, 1e-3);
      if (arrived) {
        phase.current = "dwell";
        dwellUntil.current = now + p.dwellMs;
        if (isGigi) setGigiPose({ pose: p.pose, travelling: false });
      }
    } else {
      if (p.facing !== null) m.heading = turnToward(m.heading, p.facing, 8 * dt);
      drive.current.mode = POSE_CLIP[p.pose];
      drive.current.speed = 0;
    }
    agentPositions.set(agent.agentId, { x: m.x, z: m.z });
    if (phase.current === "dwell" && (p.kind === "work" || p.kind === "desk")) seatedAtDesk.add(agent.agentId);
    else seatedAtDesk.delete(agent.agentId);
    if (group.current) group.current.position.set(m.x, 0, m.z);
    if (body.current) body.current.rotation.y = m.heading;
    if (ring.current) {
      const material = ring.current.material as MeshBasicMaterial;
      material.opacity = agent.state === "working" && awake && !reduced ? 0.55 + Math.sin(now / 330) * 0.3 : 0.8;
      ring.current.visible = !isGigi && (phase.current === "dwell" || selected);
    }
  });

  return (
    <>
    <group ref={group} userData={{ agentId: agent.agentId }}>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.015, 0]}>
        <ringGeometry args={[selected ? 0.4 : 0.46, 0.54, 40]} />
        <meshBasicMaterial color={selected ? "#93c5fd" : RING_COLOUR[agent.state]} transparent opacity={0.8} side={DoubleSide} depthWrite={false} />
      </mesh>
      <group ref={body}
        onClick={(event) => { event.stopPropagation(); onSelect(agent.agentId); }}
        onPointerOver={() => { document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { document.body.style.cursor = ""; }}>
        {!isGigi && <ToyFigure look={look} drive={drive} paused={!awake} heightM={OFFICE_FIGURE_HEIGHT_M} seatHeight={seatHeight} />}
      </group>
      <Nameplate agent={agent} activity={activity} selected={selected} onSelect={onSelect} height={isGigi ? 1.75 : undefined} />
    </group>
    {isGigi
      ? <GigiFlyer owner={mover} mode={gigiModeFor(gigiPose.pose, gigiPose.travelling)} speaking={speaking} paused={!awake} reduced={reduced} />
      : <AgentFollower owner={group} appearance={pet} paused={!awake || reduced} clear={petClear} />}
    </>
  );
}

export function OfficeAgents({ desks, agents, ctx, newcomers, awake, reduced, selectedId, onSelect }: {
  desks: DeskSlot[]; agents: ReadonlyMap<string, SocietyAgent>; ctx: WalkerContext; newcomers: ReadonlySet<string>;
  awake: boolean; reduced: boolean; selectedId: string | null; onSelect: (id: string) => void;
}) {
  const deskOf = useMemo(() => new Map(desks.filter((d) => d.agentId).map((d) => [d.agentId as string, d])), [desks]);
  return (
    <group>
      {[...agents.values()].map((agent) => (
        <Walker key={agent.agentId} agent={agent} desk={deskOf.get(agent.agentId) ?? null} ctx={ctx}
          arrivesByElevator={newcomers.has(agent.agentId)} awake={awake} reduced={reduced}
          selected={selectedId === agent.agentId} onSelect={onSelect} />
      ))}
    </group>
  );
}
