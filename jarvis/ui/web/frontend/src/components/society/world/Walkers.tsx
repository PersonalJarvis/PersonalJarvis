import { createRoutePlanner } from "./routePlanner";
import { mulberry32, seedFromString } from "./wander";
/** All figures share one fixed-step movement owner. Animation only poses the mesh. */
import { useEffect, useRef, useState } from "react";
import { Html } from "@react-three/drei";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import type { Group } from "three";
import type { AgentCheckpoint, SocietyAgent } from "../data";
import { catalogBaseFor } from "../figures/figureRegistry";
import { useBuildingPoses } from "./buildingPoses";
import { useCameraStore } from "./cameraStore";
import { buildIsland, groundY, randomPlazaTile, tileToWorld, type PlaceId } from "./islandLayout";
import { WORLD_HERO_SCALE, WalkerFigure, type WalkerAnim } from "./WalkerFigure";
import { speechPose } from "./conversationStore";
import { claimEntrance } from "./spawnStore";
import { clearWalkerPin, setWalkerPin } from "./walkerRegistry";
import { MotionWorld, setMotionWorld, type Actor } from "./locomotion";
import { worldNavigation } from "./navigation";
import { BUILDING_ASSETS } from "./worldManifest";
import { forgetRetired } from "./retireStore";
import { turnToward, TURN_RATE_RAD_S } from "./walkerKinematics";

export const CHECKPOINT_PLACE: Record<AgentCheckpoint, PlaceId | null> = {
  desk: "workshop",
  // A room has a room now: the Town Hall, not the open square.
  meeting: "civic",
  archive: "archive",
  gate: "harbor",
  idle: null,
  gallery: "gallery",
  "hub:plugins": "plugins",
  "hub:skills": "skills",
  "hub:mcp": "mcp",
  "hub:cli": "cli",
  "hub:comms": "comms",
  "hub:desktop": "desktop",
  "hub:web": "web",
  "hub:models": "models",
};

function desired(agent: SocietyAgent): [number, number] | null {
  const place = CHECKPOINT_PLACE[agent.checkpoint];
  return place ? tileToWorld(...buildIsland().content.places[place].standTile) : null;
}
function stateOf(agent: SocietyAgent): Actor["state"] {
  return agent.state === "paused" ? "paused" : agent.state === "working" ? "working" : "idle";
}
function radiusOf(agent: SocietyAgent): number {
  const base = agent.figure ? catalogBaseFor(agent.figure) : null;
  const height = agent.figure?.heightM ?? base?.heightM ?? 1.75;
  // Imported assets use a conservative envelope until their measured bounds
  // become available. Catalog bounds include the walking clip and accessories.
  const ratio = base?.motionRadiusM && base.height_m ? base.motionRadiusM / base.height_m : .38;
  return Math.max(.35, height * WORLD_HERO_SCALE * ratio * 1.1);
}

function Walker({ agent, world, paused, selected, onSelect }: {
  agent: SocietyAgent; world: MotionWorld; paused: boolean; selected: boolean; onSelect: (id: string) => void;
}) {
  const group = useRef<Group>(null);
  const anim = useRef<WalkerAnim>({ mode: "rest", speed: 0 });
  const gl = useThree(s => s.gl);
  const [hover, setHover] = useState(false);
  useFrame((_, dt) => {
    const a = world.actors.get(agent.agentId), g = group.current;
    if (!a || !g) return;
    g.visible = !a.hidden;
    const say = speechPose(agent.agentId);
    // Speech is a transient animation overlay, never a locomotion state.
    const talking = !paused && say?.talking && (a.mode === "rest" || a.mode === "work");
    if (talking && say.heading !== null) a.heading = turnToward(a.heading, say.heading, TURN_RATE_RAD_S * Math.min(dt, .1));
    anim.current = { mode: talking ? "talk" : a.mode, speed: a.speed };
    g.position.set(a.x, groundY(world.nav.map, a.x, a.z), a.z);
    g.rotation.y = a.heading;
    if (!a.hidden) setWalkerPin(agent.agentId, { x: a.x, z: a.z, color: agent.palette.accent, name: agent.name });
    else clearWalkerPin(agent.agentId);
  });
  useEffect(() => {
    if (hover) gl.domElement.style.cursor = "pointer";
    return () => { gl.domElement.style.cursor = ""; };
  }, [hover, gl]);
  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (!useCameraStore.getState().dragging && !useBuildingPoses.getState().rotating) onSelect(agent.agentId);
  };
  return <group ref={group} visible={false} onClick={click}
    onPointerOver={e => { e.stopPropagation(); setHover(true); }} onPointerOut={() => setHover(false)}>
    <WalkerFigure palette={agent.palette} anim={anim} paused={paused} selected={selected} recipe={agent.figure} onRadius={radius => {
      const a = world.actors.get(agent.agentId);
      if (a && radius > a.radius) { a.radius = radius; a.path = []; a.arrived = false; a.mode = "rest"; world.request(a.id); }
    }} />
    {(hover || selected || agent.state === "working") && <Html position={[0, (agent.figure?.heightM ?? 1.75) * WORLD_HERO_SCALE + .35, 0]} center zIndexRange={[30, 10]} style={{ pointerEvents: "none" }}>
      <div className="sw-nameplate" data-state={agent.state} data-selected={selected || undefined}>
        <span className="sw-nameplate-dot" style={{ background: agent.palette.accent }} />{agent.name}
      </div>
    </Html>}
  </group>;
}

export function Walkers({ agents, paused, selectedId, onSelect }: {
  agents: SocietyAgent[]; paused: boolean; selectedId: string | null; onSelect: (id: string) => void;
}) {
  const ref = useRef<MotionWorld | null>(null);
  if (!ref.current) ref.current = new MotionWorld(worldNavigation(), rng => tileToWorld(...randomPlazaTile(buildIsland().map, rng)));
  const world = ref.current;
  const planner = useRef<ReturnType<typeof createRoutePlanner> | null>(null);
  useEffect(() => {
    setMotionWorld(world);
    planner.current = createRoutePlanner(world.nav);
    world.planner = planner.current.plan;
    return () => {
      for (const id of world.actors.keys()) { clearWalkerPin(id); forgetRetired(id); }
      planner.current?.dispose();
      world.planner = null;
      setMotionWorld(null);
    };
  }, [world]);
  useEffect(() => {
    const ids = new Set(agents.map(a => a.agentId));
    for (const id of world.actors.keys()) if (!ids.has(id)) { world.remove(id); clearWalkerPin(id); forgetRetired(id); }
    for (const agent of agents) {
      const radius = radiusOf(agent), target = desired(agent);
      let a = world.actors.get(agent.agentId);
      if (!a) {
        let start = target ?? tileToWorld(...randomPlazaTile(buildIsland().map, mulberry32(seedFromString(agent.agentId))));
        if (claimEntrance(agent.agentId, agent.createdMs)) {
          const p = buildIsland().content.kitPoses.foundry;
          const ahead = Math.max(BUILDING_ASSETS.foundry.sizeM[1] / 2 + radius + .2, BUILDING_ASSETS.foundry.stand[1]);
          start = [p.x + Math.sin(p.rotation) * ahead, p.z + Math.cos(p.rotation) * ahead];
        }
        a = world.add(agent.agentId, start, radius, stateOf(agent), target) ?? undefined;
      }
      if (a && !a.exiting) {
        a.state = stateOf(agent);
        if (a.radius !== radius) { a.radius = radius; world.request(a.id); }
        world.target(a.id, target);
      }
    }
  }, [agents, world]);
  useEffect(() => useBuildingPoses.subscribe(() => {
    const nav = worldNavigation();
    if (world.nav !== nav) {
      world.replaceNavigation(nav);
      planner.current?.dispose();
      planner.current = createRoutePlanner(nav);
      world.planner = planner.current.plan;
      for (const agent of agents) if (!world.actors.get(agent.agentId)?.exiting) world.target(agent.agentId, desired(agent));
    }
  }), [world, agents]);
  useFrame((_, dt) => { if (!paused) world.advance(dt); }, -1);
  return <group>{agents.map(agent => <Walker key={agent.agentId} agent={agent} world={world} paused={paused} selected={agent.agentId === selectedId} onSelect={onSelect} />)}</group>;
}
