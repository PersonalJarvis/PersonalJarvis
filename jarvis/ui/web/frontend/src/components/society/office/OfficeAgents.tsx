/**
 * Agents living in the office. Working agents sit at their screens, waiting
 * agents stand and wave at their desk, idle agents wander: coffee, couch,
 * window, arcade, a chat at a busy colleague's desk. Paused agents nap.
 *
 * The office is a projection of the roster: run state comes from the backend,
 * everything else is client-side choreography that costs no tokens and never
 * starts or stops work.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { DoubleSide, Vector3, type Group, type Mesh, type MeshBasicMaterial } from "three";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { FigureDrive, FigureMode } from "../figures/FigureRig";
import { ToyFigure } from "./ToyFigure";
import { GigiFlyer } from "./GigiFlyer";
import { followAnchor, type GigiFlightMode } from "./gigiFlight";
import { useEventStore } from "@/store/events";
import { SEAT_HEIGHT, toyLookFor } from "./toyFigureModel";
import { OFFICE } from "./officePalette";
import type { DeskSlot, OfficeLayout, Point } from "./officeLayout";
import { findPath, isWalkable, type NavGrid } from "./officeNav";
import { AgentFollower } from "../companion/AgentFollower";
import { resolveCompanion } from "../companion/appearance";
import type { TrailPoint } from "../companion/trail";
import { stepMover, stepMoverAvoiding, turnToward, WALK_SPEED, type Mover } from "./officeMotion";
import { createRng, planFor, type ActivityKind, type Plan, type Pose, type SpotBook } from "./officeBehavior";
import { player, useOfficeStore } from "./officeStore";
import { agentPositions, bodiesExcept, companions, seatedAtDesk } from "./walkerRegistry";
import { AgentBubble } from "./OfficeBubbles";
import type { ChatLine } from "./deskChat";
import type { DeskChat } from "./useDeskChats";
import { officeTalkChat, useOfficeTalk } from "./officeTalk";
import { deliverySpot, ERRAND_SPEED, useErrandFeed, useGigiErrands } from "./gigiErrands";
import { isPaneAgentId, plateTitle } from "./codingFloor";
import { promptOpening } from "@/components/agentic/sessionTitle";
import { agentLogoAsset } from "@/components/agentic/AgentMark";

/** The agent's symbol walks behind it as a little pet, about a fifth of its height. */
export const PET_SIZE_M = 0.26;
const PET_FOLLOW_M = 0.7;

/** The person's character as a mover for Gigi to follow (the body object itself, mutated every frame). */
const PLAYER_OWNER = { current: player };
/** Farther than this from the person's shoulder and Gigi flies back first instead of hovering beside them. */
const FOLLOW_CATCH_M = 1;

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
  const [open, setOpen] = useState(false);
  const detail = agent.state !== "idle" ? t(`society.office.state_${agent.state}`) : activity ? t(`society.office.activity_${activity}`) : "";
  const pane = isPaneAgentId(agent.agentId);
  const logo = pane ? agentLogoAsset(agent.provider) : null;
  const state = (
    <span className="office-plate-state" title={t(`society.office.state_${agent.state}`)}>
      <i aria-hidden />{detail ? <em>{detail}</em> : null}
    </span>
  );
  return (
    <group ref={anchor} position={[0, height, 0]}>
      {/* An opened plate draws above its neighbours' plates and bubbles, which sit shoulder to shoulder at a desk row. */}
      <Html center zIndexRange={open ? [40, 30] : [20, 0]}>
        <button ref={plate} type="button" data-office-ui className="office-plate" data-state={agent.state} data-selected={selected || undefined}
          data-pane={pane || undefined} data-open={open || undefined}
          onClick={(event) => { event.stopPropagation(); onSelect(agent.agentId); }}
          onPointerEnter={() => setOpen(true)} onPointerLeave={() => setOpen(false)}
          onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
          aria-label={t("society.office.open_agent").replace("{0}", agent.name)}>
          {pane && logo ? <PaneLogo url={logo.url} ground={logo.ground} /> : (
            <span className="office-plate-badge" style={{ background: agent.palette.primary }} aria-hidden>
              {agent.tier === "lead" ? "★" : (pane ? agent.provider : agent.name).slice(0, 1).toUpperCase()}
            </span>
          )}
          {pane ? <PanePlateText agent={agent} open={open} state={state} /> : (
            <>
              <span className="office-plate-name" title={agent.name}>{agent.name}</span>
              {state}
            </>
          )}
        </button>
      </Html>
    </group>
  );
}

/**
 * The coding CLI's own mark in front of a pane's plate, instead of a lettered
 * hexagon: a floor of Claude panes read "C", "C", "C", which named nothing.
 * Drawn here rather than with `AgentMark`, whose `ink` marks follow the app
 * theme's text colour — the plate is dark in both themes, so a light-mode ink
 * mark would vanish on it. An `ink` mark is masked in the plate's own white;
 * a full-colour lockup keeps its colours on a dark tile it can sit on.
 */
function PaneLogo({ url, ground }: { url: string; ground: "ink" | "dark" | "any" }) {
  if (ground === "ink") {
    const mask = { WebkitMaskImage: `url("${url}")`, maskImage: `url("${url}")` };
    return <span className="office-plate-logo" data-ground="ink" aria-hidden><i style={mask} /></span>;
  }
  return <span className="office-plate-logo" data-ground={ground} aria-hidden><img src={url} alt="" draggable={false} /></span>;
}

/**
 * A coding pane's plate: its title on two lines (subject, then result and run
 * state), so the words that tell four "Office …" panes apart are never the ones
 * clipped. Hovered or focused, it opens to the whole title, the call-sign that
 * finds the pane in the IDE, and the opening of what it was last asked.
 */
function PanePlateText({ agent, open, state }: { agent: SocietyAgent; open: boolean; state: ReactNode }) {
  const { subject, result } = plateTitle(agent.name);
  const asked = open ? promptOpening(agent.description) : "";
  return (
    <span className="office-plate-text">
      <span className="office-plate-name">{subject}</span>
      <span className="office-plate-line">
        {result ? <span className="office-plate-result">{result}</span> : null}
        {state}
      </span>
      {open ? <span className="office-plate-sign">{agent.title}</span> : null}
      {asked && asked !== agent.name ? <span className="office-plate-asked">“{asked}”</span> : null}
    </span>
  );
}

/** The sealed envelope Gigi carries on an errand, bobbing under it. */
function ErrandEnvelope({ owner }: { owner: RefObject<Group | null> }) {
  const env = useRef<Group>(null);
  useFrame(({ clock }) => {
    if (!env.current || !owner.current) return;
    const p = owner.current.position;
    env.current.position.set(p.x, 0.62 + Math.sin(clock.elapsedTime * 5) * 0.04, p.z);
    env.current.rotation.y = clock.elapsedTime * 1.6;
  });
  return (
    <group ref={env}>
      <mesh castShadow>
        <boxGeometry args={[0.26, 0.17, 0.02]} />
        <meshStandardMaterial color="#fffaf0" roughness={0.6} />
      </mesh>
      <mesh position={[0, 0.02, 0.012]} rotation={[0, 0, Math.PI / 4]}>
        <boxGeometry args={[0.13, 0.13, 0.004]} />
        <meshStandardMaterial color="#efe3c8" roughness={0.7} />
      </mesh>
      <mesh position={[0, -0.01, 0.016]}>
        <circleGeometry args={[0.03, 16]} />
        <meshStandardMaterial color="#c0392b" roughness={0.4} />
      </mesh>
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

function Walker({ agent, desk, ctx, arrivesByElevator, awake, reduced, selected, lines, onSelect }: {
  agent: SocietyAgent; desk: DeskSlot | null; ctx: WalkerContext; arrivesByElevator: boolean;
  awake: boolean; reduced: boolean; selected: boolean; lines: readonly ChatLine[] | undefined; onSelect: (id: string) => void;
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
  // The desk the current plan was made for: a roster change can move an agent to another desk.
  const planDesk = useRef<string | null>(null);
  const summonKey = useRef("");
  const [activity, setActivity] = useState<ActivityKind | null>(null);
  const [seatHeight, setSeatHeight] = useState<number>(SEAT_HEIGHT.chair);
  const isGigi = agent.tier === "lead";
  const [gigiPose, setGigiPose] = useState<{ pose: Pose | null; travelling: boolean }>({ pose: null, travelling: false });
  const speaking = useEventStore((s) => isGigi && s.voiceState === "speaking");
  const errandKey = useRef("");
  const carrying = useGigiErrands((s) => isGigi && s.current?.phase === "fly");
  const [following, setFollowing] = useState(false);
  const followingRef = useRef(false);
  const gigiSide = useRef<1 | -1>(1);
  const airClear = useMemo(() => (x: number, z: number) => isWalkable(ctx.grid, { x, z }), [ctx.grid]);

  // Leaving the office releases the agent's spot and its registry entry.
  useEffect(() => () => {
    ctx.book.release(agent.agentId);
    agentPositions.delete(agent.agentId);
    seatedAtDesk.delete(agent.agentId);
    companions.delete(agent.agentId);
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
    // Gigi on an errand: fly straight to the recipient, hover and deliver, then back to its day.
    const errand = isGigi ? useGigiErrands.getState().current : null;
    const recipient = errand ? agentPositions.get(errand.to) : undefined;
    if (errand && !recipient) useGigiErrands.getState().finish(errand.id);
    if (errand && recipient) {
      if (followingRef.current) { followingRef.current = false; setFollowing(false); }
      companions.delete(agent.agentId);
      const key = `${errand.id}:${errand.phase}`;
      if (errand.phase === "fly") {
        const spot = deliverySpot(recipient, m);
        const end = m.path[m.path.length - 1];
        if (errandKey.current !== key || !end || Math.hypot(end.x - spot.x, end.z - spot.z) > 0.4) m.path = [spot];
        if (reduced) { m.x = spot.x; m.z = spot.z; m.path = []; }
        const { arrived } = stepMover(m, ERRAND_SPEED, dt);
        if (arrived) useGigiErrands.getState().arrive(errand.id);
      } else {
        m.heading = turnToward(m.heading, Math.atan2(recipient.x - m.x, recipient.z - m.z), 8 * dt);
        if (now >= errand.untilMs) useGigiErrands.getState().finish(errand.id);
      }
      if (errandKey.current !== key) {
        errandKey.current = key;
        setGigiPose({ pose: errand.phase === "deliver" ? "talk" : null, travelling: errand.phase === "fly" });
      }
      // The day resumes from wherever the errand ended.
      plan.current = null;
      agentPositions.set(agent.agentId, { x: m.x, z: m.z });
      seatedAtDesk.delete(agent.agentId);
      if (group.current) group.current.position.set(m.x, 0, m.z);
      return;
    }
    errandKey.current = "";
    const summon = useOfficeStore.getState().summons[agent.agentId];
    const calledTo = summon && summon.untilMs > now ? summon.target : null;
    if (isGigi && !calledTo) {
      // Gigi keeps the person company, as on the coding floor; an errand or a summons still takes it away.
      const anchor = followAnchor(player.x, player.z, player.heading, gigiSide.current, airClear);
      gigiSide.current = anchor.side;
      const far = Math.hypot(anchor.x - m.x, anchor.z - m.z) > FOLLOW_CATCH_M;
      if (!placed.current || reduced || !far) {
        m.x = anchor.x; m.z = anchor.z; m.path = [];
        placed.current = true;
      } else if (awake) {
        // Back from an errand or a meeting: fly over to the person, then fall in beside them.
        m.path = [anchor];
        stepMover(m, ERRAND_SPEED, dt);
      }
      const nowFollowing = Math.hypot(anchor.x - m.x, anchor.z - m.z) <= FOLLOW_CATCH_M;
      if (nowFollowing) { m.heading = player.heading; companions.add(agent.agentId); } else companions.delete(agent.agentId);
      if (followingRef.current !== nowFollowing || plan.current) {
        followingRef.current = nowFollowing;
        setFollowing(nowFollowing);
        setGigiPose({ pose: null, travelling: !nowFollowing });
        setActivity(null);
      }
      // The day resumes from wherever the person leaves Gigi (a summons).
      plan.current = null;
      summonKey.current = "";
      agentPositions.set(agent.agentId, { x: m.x, z: m.z });
      seatedAtDesk.delete(agent.agentId);
      if (group.current) group.current.position.set(m.x, 0, m.z);
      return;
    }
    if (followingRef.current) { followingRef.current = false; setFollowing(false); }
    companions.delete(agent.agentId);
    const calledSeat = calledTo ? summon.spotId ?? null : null;
    const sKey = calledTo ? `${calledTo.x.toFixed(2)},${calledTo.z.toFixed(2)},${calledSeat ?? ""}` : "";
    const deskKey = desk ? `${desk.id}@${desk.x.toFixed(2)},${desk.z.toFixed(2)}` : null;
    const needsPlan = !plan.current || planState.current !== agent.state || summonKey.current !== sKey
      || planDesk.current !== deskKey
      || (phase.current === "dwell" && now >= dwellUntil.current);
    if (needsPlan) {
      const next = planFor({
        agentId: agent.agentId, state: agent.state, desk, layout: ctx.layout, grid: ctx.grid, rng, book: ctx.book,
        previous: plan.current?.kind ?? null, workingColleagues: ctx.colleagues().filter((c) => c.agentId !== agent.agentId), calledTo, calledSeat,
      });
      // Reduced motion: no idle wandering — a placement holds until the state changes.
      if (reduced && !calledTo) next.dwellMs = Infinity;
      plan.current = next;
      planState.current = agent.state;
      planDesk.current = deskKey;
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
        // Gigi flies: after an errand it may hover over a desk, where no floor path starts.
        if (m.path.length === 0 && isGigi && Math.hypot(next.target.x - m.x, next.target.z - m.z) > 0.05) m.path = [next.target];
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
      // Walkers steer around other people instead of through them; Gigi flies over everyone.
      const { moved, arrived } = !awake ? { moved: 0, arrived: false }
        : isGigi ? stepMover(m, WALK_SPEED, dt)
        : stepMoverAvoiding(m, WALK_SPEED, dt, Array.from(bodiesExcept(agent.agentId, player)), (q) => isWalkable(ctx.grid, q));
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
      <AgentBubble agent={agent} lines={lines} selected={selected} onSelect={onSelect}
        height={(isGigi ? 1.75 : OFFICE_FIGURE_HEIGHT_M + 0.35) + 0.14} />
    </group>
    {carrying && <ErrandEnvelope owner={group} />}
    {isGigi
      // One flyer for both roles, so switching keeps its flight state instead of re-spawning it.
      ? <GigiFlyer owner={following ? PLAYER_OWNER : mover} mode={following ? "follow" : gigiModeFor(gigiPose.pose, gigiPose.travelling)}
          speaking={speaking} paused={!awake} reduced={reduced} clear={following ? airClear : undefined} />
      : <AgentFollower owner={group} appearance={pet} paused={!awake || reduced} clear={petClear} />}
    </>
  );
}

export function OfficeAgents({ desks, agents, ctx, newcomers, awake, reduced, selectedId, chats, onSelect }: {
  desks: DeskSlot[]; agents: ReadonlyMap<string, SocietyAgent>; ctx: WalkerContext; newcomers: ReadonlySet<string>;
  awake: boolean; reduced: boolean; selectedId: string | null; chats?: ReadonlyMap<string, DeskChat>; onSelect: (id: string) => void;
}) {
  const deskOf = useMemo(() => new Map(desks.filter((d) => d.agentId).map((d) => [d.agentId as string, d])), [desks]);
  useErrandFeed(agents, awake);
  // Leaving the office hangs up: the talk socket closes, the chat keeps running on the server.
  useEffect(() => () => { officeTalkChat.getState().disconnect(); useOfficeTalk.getState().setAgent(null); }, []);
  return (
    <group>
      {[...agents.values()].map((agent) => (
        <Walker key={agent.agentId} agent={agent} desk={deskOf.get(agent.agentId) ?? null} ctx={ctx}
          arrivesByElevator={newcomers.has(agent.agentId)} awake={awake} reduced={reduced}
          selected={selectedId === agent.agentId} lines={chats?.get(agent.agentId)?.lines} onSelect={onSelect} />
      ))}
    </group>
  );
}
