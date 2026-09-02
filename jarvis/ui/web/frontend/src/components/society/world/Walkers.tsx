/**
 * The society on foot. One `Walker` per roster row: a small state machine
 * (rest / walk / work / sleep) that walks A* paths over the tile grid with the
 * kinematics of character-pipeline.md §6 and idles with the rest-biased
 * wander model (MASTERPLAN §2.7). The backend owns the semantic place
 * (`checkpoint`); every footstep here is client-side and never synced.
 *
 * Idle costs nothing: no network, no LLM — a timer and a random tile.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Html } from "@react-three/drei";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import type { Group } from "three";

import type { AgentCheckpoint, AgentRunState, SocietyAgent } from "../data";
import { useCameraStore } from "./cameraStore";
import {
  FOUNDRY_BELT_LIFT_M,
  buildIsland,
  findPath,
  foundryWalkOut,
  groundY,
  randomPlazaTile,
  smoothPath,
  tileToWorld,
  worldToTile,
  type PlaceId,
} from "./islandLayout";
import {
  NOMINAL_WALK_MPS,
  TURN_RATE_RAD_S,
  WANDER_SPEED_MPS,
  headingFor,
  isMoving,
  lateralOffset,
  stepAlong,
  turnToward,
} from "./walkerKinematics";
import { WORLD_HERO_SCALE, WalkerFigure, type WalkerAnim, type WalkerMode } from "./WalkerFigure";
import { claimEntrance } from "./spawnStore";
import { clearWalkerPin, setWalkerPin } from "./walkerRegistry";
import { mulberry32, nextBeat, seedFromString } from "./wander";

/** Semantic place → island place (MASTERPLAN §2.7 checkpoints). */
export const CHECKPOINT_PLACE: Record<AgentCheckpoint, PlaceId | null> = {
  desk: "workshop",
  meeting: "market",
  archive: "archive",
  gate: "harbor",
  idle: null,
};

interface Sim {
  x: number;
  z: number;
  y: number;
  heading: number;
  mode: WalkerMode;
  timer: number;
  waypoints: Array<[number, number]>;
  index: number;
  speed: number;
  purpose: PlaceId | null;
  atPurpose: boolean;
  /** True while the figure is still riding the foundry's conveyor out of the
   *  portal: it follows a fixed two-point path instead of an A* route, because
   *  its first metres run over the building's own blocked footprint. */
  exiting: boolean;
  /** Height above the ground the belt still carries it, in metres. */
  lift: number;
  rng: () => number;
}

function stateMode(state: AgentRunState, atPurpose: boolean): WalkerMode {
  if (state === "paused") return "sleep";
  if (state === "working" && atPurpose) return "work";
  return "rest";
}

function pathTo(sim: Sim, target: [number, number]): boolean {
  const { map } = buildIsland();
  const from = worldToTile(sim.x, sim.z);
  const raw = findPath(map, from, target);
  if (!raw) return false;
  const smooth = smoothPath(map, raw).slice(1);
  sim.waypoints = smooth.map(([tx, tz]) => tileToWorld(tx, tz));
  if (sim.waypoints.length === 0) return false;
  sim.index = 0;
  sim.mode = "walk";
  return true;
}

function Walker({
  agent,
  paused,
  selected,
  onSelect,
}: {
  agent: SocietyAgent;
  paused: boolean;
  selected: boolean;
  onSelect: (agentId: string) => void;
}) {
  const group = useRef<Group>(null);
  const anim = useRef<WalkerAnim>({ mode: "rest", speed: 0 });
  const gl = useThree((s) => s.gl);
  const [hover, setHover] = useState(false);
  const offset = useMemo(() => lateralOffset(agent.agentId), [agent.agentId]);

  const sim = useRef<Sim | null>(null);
  if (sim.current === null) {
    const { map, content } = buildIsland();
    const rng = mulberry32(seedFromString(agent.agentId) ^ Date.now());
    const purpose = CHECKPOINT_PLACE[agent.checkpoint];
    // A row that was just created walks out of the Agent Foundry; every other
    // figure is simply standing where the backend says it is.
    const born = claimEntrance(agent.agentId, agent.createdMs);
    if (born) {
      const exit = foundryWalkOut();
      sim.current = {
        x: exit.from[0],
        z: exit.from[1],
        y: groundY(map, exit.from[0], exit.from[1]) + FOUNDRY_BELT_LIFT_M,
        heading: exit.heading,
        mode: "walk",
        timer: 0,
        waypoints: [exit.to],
        index: 0,
        speed: NOMINAL_WALK_MPS,
        purpose,
        atPurpose: false,
        exiting: true,
        lift: FOUNDRY_BELT_LIFT_M,
        rng,
      };
    } else {
      const startTile = purpose ? content.places[purpose].standTile : randomPlazaTile(map, rng);
      const [x, z] = tileToWorld(startTile[0], startTile[1]);
      const facing = purpose ? content.places[purpose].facing : rng() * Math.PI * 2;
      sim.current = {
        x: x + offset,
        z,
        y: groundY(map, x, z),
        heading: facing,
        mode: stateMode(agent.state, purpose !== null),
        timer: 1 + rng() * 3,
        waypoints: [],
        index: 0,
        speed: WANDER_SPEED_MPS,
        purpose,
        atPurpose: purpose !== null,
        exiting: false,
        lift: 0,
        rng,
      };
    }
  }

  // A checkpoint or state change from the roster re-targets the walker.
  useEffect(() => {
    const s = sim.current;
    if (!s) return;
    const purpose = CHECKPOINT_PLACE[agent.checkpoint];
    if (s.exiting) {
      // Still on the conveyor: remember the target, walk there once outside.
      s.purpose = purpose;
      s.atPurpose = false;
    } else if (purpose !== s.purpose) {
      s.purpose = purpose;
      s.atPurpose = false;
      if (purpose) {
        s.speed = NOMINAL_WALK_MPS;
        if (!pathTo(s, buildIsland().content.places[purpose].standTile)) {
          s.atPurpose = true;
          s.mode = stateMode(agent.state, true);
        }
      } else {
        s.mode = stateMode(agent.state, false);
        s.timer = 1;
      }
    } else if (s.mode !== "walk") {
      s.mode = stateMode(agent.state, s.atPurpose);
    }
  }, [agent.checkpoint, agent.state]);

  useEffect(() => () => clearWalkerPin(agent.agentId), [agent.agentId]);

  useFrame((_, dt) => {
    const s = sim.current;
    const g = group.current;
    if (!s || !g) return;
    const { map, content } = buildIsland();
    const step = Math.min(dt, 0.1);

    if (!paused) {
      if (s.mode === "walk") {
        const r = stepAlong(s.x, s.z, s.waypoints, s.index, s.speed, step);
        s.x = r.x;
        s.z = r.z;
        s.index = r.index;
        if (isMoving(r.vx, r.vz)) {
          s.heading = turnToward(s.heading, headingFor(r.vx, r.vz), TURN_RATE_RAD_S * step);
        }
        anim.current.speed = Math.hypot(r.vx, r.vz);
        if (r.arrived && s.exiting) {
          // Off the belt and on the plaza: head for wherever the roster says.
          s.exiting = false;
          s.lift = 0;
          const target = s.purpose
            ? content.places[s.purpose].standTile
            : randomPlazaTile(map, s.rng);
          if (pathTo(s, target)) {
            s.speed = s.purpose ? NOMINAL_WALK_MPS : WANDER_SPEED_MPS;
          } else {
            s.mode = stateMode(agent.state, false);
            s.timer = 2;
            s.speed = WANDER_SPEED_MPS;
          }
        } else if (r.arrived) {
          if (s.purpose && !s.atPurpose) {
            s.atPurpose = true;
            s.heading = content.places[s.purpose].facing;
          }
          s.mode = stateMode(agent.state, s.atPurpose);
          s.timer = 2 + s.rng() * 4;
          s.speed = WANDER_SPEED_MPS;
        }
      } else if (s.mode === "rest" || s.mode === "work") {
        s.timer -= step;
        if (s.timer <= 0) {
          if (s.purpose) {
            // At a place: stay. A working agent keeps working; a resting one shifts its weight.
            s.timer = 4 + s.rng() * 6;
          } else {
            const beat = nextBeat(s.rng);
            if (beat.kind === "rest") {
              s.timer = beat.seconds;
            } else if (!pathTo(s, randomPlazaTile(map, s.rng))) {
              s.timer = 2;
            }
          }
        }
      }
      if (s.exiting) {
        // The conveyor lowers its passenger over the last two metres.
        const end = s.waypoints[s.waypoints.length - 1];
        const remain = end ? Math.hypot(end[0] - s.x, end[1] - s.z) : 0;
        s.lift = FOUNDRY_BELT_LIFT_M * Math.min(1, remain / 2.0);
      }
      // Ground snap with a short ease so a level step reads as a step.
      const targetY = groundY(map, s.x, s.z) + s.lift;
      s.y += (targetY - s.y) * Math.min(1, step * 12);
    }

    anim.current.mode = s.mode;
    g.position.set(s.x, s.y, s.z);
    g.rotation.y = s.heading;
    setWalkerPin(agent.agentId, { x: s.x, z: s.z, color: agent.palette.accent, name: agent.name });
  });

  useEffect(() => {
    gl.domElement.style.cursor = hover ? "pointer" : "";
    return () => {
      gl.domElement.style.cursor = "";
    };
  }, [hover, gl]);

  const onClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (useCameraStore.getState().dragging) return;
    onSelect(agent.agentId);
  };

  return (
    <group
      ref={group}
      onClick={onClick}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHover(true);
      }}
      onPointerOut={() => setHover(false)}
    >
      <WalkerFigure palette={agent.palette} anim={anim} paused={paused} selected={selected} recipe={agent.figure} />
      <Html position={[0, (agent.figure ? (agent.figure.heightM ?? 1.75) * WORLD_HERO_SCALE : 2.0) + 0.35, 0]} center zIndexRange={[30, 10]} style={{ pointerEvents: "none" }}>
        <div className="sw-nameplate" data-state={agent.state} data-selected={selected || undefined}>
          <span className="sw-nameplate-dot" style={{ background: agent.palette.accent }} />
          {agent.name}
        </div>
      </Html>
    </group>
  );
}

export function Walkers({
  agents,
  paused,
  selectedId,
  onSelect,
}: {
  agents: SocietyAgent[];
  paused: boolean;
  selectedId: string | null;
  onSelect: (agentId: string) => void;
}) {
  return (
    <group>
      {agents.map((a) => (
        <Walker key={a.agentId} agent={a} paused={paused} selected={a.agentId === selectedId} onSelect={onSelect} />
      ))}
    </group>
  );
}
