/**
 * The market district: twelve solarpunk houses in a ring around the open
 * square, the lead agent's hub at its head, the Quest Board monument with the
 * long table in the middle, the hedge ring with its four gates, and the lamps.
 *
 * Everything is primitives from `worldMaterials.ts` — no asset files. The
 * building language (docs/agent-society/world-art-direction.md §4): white
 * walls, glass, solar barrels, garden roofs, wood accents, rounded shapes.
 */
import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Color, InstancedMesh, Mesh, Object3D } from "three";

import { buildIsland, groundY, type HousePlot, type Post } from "./islandLayout";
import { QuestMonument } from "./QuestMonument";
import { Block, useKit, type Kit } from "./WorldKit";
import { PAL } from "./worldMaterials";

/** One house. Local +z is the front (door side); the plot rotation points it at the square. */
function House({ kit, plot }: { kit: Kit; plot: HousePlot }) {
  const { map } = buildIsland();
  const y = groundY(map, plot.x, plot.z);
  const w = plot.w * 2; // footprint in metres
  const d = plot.d * 2;
  const h = plot.variant === "glass-loft" ? 3.0 : 3.4;
  const shade = plot.seed > 0.5 ? PAL.wall : PAL.wallShade;
  return (
    <group position={[plot.x, y, plot.z]} rotation={[0, plot.rotation, 0]}>
      {/* body */}
      <Block kit={kit} at={[0, h / 2, 0]} size={[w, h, d]} color={shade} />
      {/* plinth */}
      <Block kit={kit} at={[0, 0.15, 0]} size={[w + 0.4, 0.3, d + 0.4]} color={PAL.trim} />
      {/* door and two windows on the front */}
      <Block kit={kit} at={[0, 1.05, d / 2 + 0.05]} size={[1.1, 2.1, 0.12]} color={PAL.door} />
      <Block kit={kit} at={[-w / 2 + 1.2, 1.9, d / 2 + 0.05]} size={[1.2, 1.1, 0.12]} color={PAL.glass} glow />
      <Block kit={kit} at={[w / 2 - 1.2, 1.9, d / 2 + 0.05]} size={[1.2, 1.1, 0.12]} color={PAL.glass} glow />
      {/* side windows */}
      <Block kit={kit} at={[w / 2 + 0.05, 1.9, 0]} size={[0.12, 1.1, 1.6]} color={PAL.glass} glow />
      <Block kit={kit} at={[-w / 2 - 0.05, 1.9, 0]} size={[0.12, 1.1, 1.6]} color={PAL.glass} glow />
      {/* wooden awning over the door */}
      <Block kit={kit} at={[0, 2.35, d / 2 + 0.5]} size={[2.2, 0.14, 1.0]} color={PAL.wood} />
      {plot.variant === "solar-barrel" && (
        <>
          {/* barrel roof: a half cylinder laid along the house's width */}
          <mesh
            geometry={kit.g.halfCylinder}
            material={kit.m.lit(PAL.solar)}
            position={[0, h, 0]}
            rotation={[0, 0, Math.PI / 2]}
            scale={[d + 0.6, w + 0.4, d + 0.6]}
          />
          <Block kit={kit} at={[0, h + d / 2 + 0.25, 0]} size={[w + 0.5, 0.1, 0.5]} color={PAL.solarLine} />
        </>
      )}
      {plot.variant === "garden-roof" && (
        <>
          <Block kit={kit} at={[0, h + 0.2, 0]} size={[w + 0.5, 0.4, d + 0.5]} color={PAL.trim} />
          <Block kit={kit} at={[0, h + 0.5, 0]} size={[w, 0.25, d]} color={PAL.gardenRoof} />
          <mesh geometry={kit.g.blob} material={kit.m.lit(PAL.gardenRoofBush)} position={[-w / 4, h + 0.95, 0]} scale={1.1} />
          <mesh geometry={kit.g.blob} material={kit.m.lit(PAL.gardenRoofBush)} position={[w / 4, h + 0.9, -d / 5]} scale={0.9} />
          <mesh geometry={kit.g.blob} material={kit.m.lit(PAL.flower)} position={[w / 5, h + 0.8, d / 4]} scale={0.5} />
        </>
      )}
      {plot.variant === "glass-loft" && (
        <>
          <Block kit={kit} at={[0, h + 0.1, 0]} size={[w + 0.3, 0.2, d + 0.3]} color={PAL.trim} />
          <Block kit={kit} at={[0, h + 1.0, 0]} size={[w - 1.6, 1.6, d - 1.0]} color={PAL.glass} glow />
          <Block kit={kit} at={[0, h + 1.9, 0]} size={[w - 1.2, 0.2, d - 0.6]} color={PAL.wall} />
          <Block kit={kit} at={[0, h + 2.05, 0]} size={[w - 1.6, 0.1, d - 1.2]} color={PAL.solar} />
        </>
      )}
    </group>
  );
}

/** The lead agent's hub — the largest house, at the head of the square, with a beacon. */
function Hub({ kit, paused }: { kit: Kit; paused: boolean }) {
  const { map, content } = buildIsland();
  const [tx, tz] = content.places.hub.tile;
  const x = (tx + 0.5 - map.size / 2) * 2;
  const z = (tz + 0.5 - map.size / 2) * 2;
  const y = groundY(map, x, z);
  const beacon = useRef<Mesh>(null);
  useFrame(({ clock }) => {
    if (!beacon.current || paused) return;
    const t = clock.getElapsedTime();
    const s = 1 + Math.sin(t * 2.2) * 0.12;
    beacon.current.scale.setScalar(s * 1.3);
  });
  return (
    <group position={[x, y, z]}>
      <Block kit={kit} at={[0, 0.2, 0]} size={[15, 0.4, 11]} color={PAL.trim} />
      <Block kit={kit} at={[0, 2.4, 0]} size={[14, 4.4, 10]} color={PAL.wall} />
      {/* glass band all around */}
      <Block kit={kit} at={[0, 2.6, 0]} size={[14.1, 1.2, 10.1]} color={PAL.hubGlass} glow />
      {/* accent trim in the lead's colour */}
      <Block kit={kit} at={[0, 4.7, 0]} size={[14.6, 0.3, 10.6]} color={PAL.hubAccent} />
      {/* entrance: wide door, two steps, facing the square (south, +z) */}
      <Block kit={kit} at={[0, 1.4, 5.1]} size={[3.2, 2.8, 0.2]} color={PAL.door} />
      <Block kit={kit} at={[0, 0.5, 6.2]} size={[5, 0.2, 1.6]} color={PAL.trim} />
      <Block kit={kit} at={[0, 0.3, 7.4]} size={[6, 0.2, 1.4]} color={PAL.trim} />
      {/* dome */}
      <mesh geometry={kit.g.dome} material={kit.m.lit(PAL.hubGlass)} position={[0, 4.85, 0]} scale={[9, 7, 9]} />
      {/* beacon mast and light */}
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.metal)} position={[0, 9.2, 0]} scale={[0.35, 2.4, 0.35]} />
      <mesh ref={beacon} geometry={kit.g.sphere} material={kit.m.glow(PAL.beacon)} position={[0, 10.9, 0]} scale={1.3} />
    </group>
  );
}

/** The Quest Board in the middle of the square, the ring bench and the long table. */
function SquareCentre({
  kit,
  paused,
  onQuestClick,
  questOpen,
}: {
  kit: Kit;
  paused: boolean;
  onQuestClick?: () => void;
  questOpen?: boolean;
}) {
  const { map } = buildIsland();
  const y = groundY(map, 1, 1);
  return (
    <group position={[0, y, 0]}>
      <QuestMonument paused={paused} onClick={onQuestClick} selected={questOpen} />
      {/* ring bench around the monument's plinth */}
      <mesh geometry={kit.g.ring} material={kit.m.lit(PAL.bench)} position={[0, 0.45, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[10.2, 10.2, 4]} />
      {/* the long table on the south side, where everyone gathers */}
      <Block kit={kit} at={[0, 0.85, 8.5]} size={[10, 0.2, 1.8]} color={PAL.tableWood} />
      <Block kit={kit} at={[-4, 0.4, 8.5]} size={[0.4, 0.8, 1.4]} color={PAL.woodDark} />
      <Block kit={kit} at={[4, 0.4, 8.5]} size={[0.4, 0.8, 1.4]} color={PAL.woodDark} />
      <Block kit={kit} at={[0, 0.25, 7.0]} size={[9.6, 0.5, 0.5]} color={PAL.bench} />
      <Block kit={kit} at={[0, 0.25, 10.0]} size={[9.6, 0.5, 0.5]} color={PAL.bench} />
    </group>
  );
}

/** Instanced hedge segments (the village ring) — one draw call. */
function Hedges({ kit, posts }: { kit: Kit; posts: Post[] }) {
  const dummy = useMemo(() => new Object3D(), []);
  const colorA = useMemo(() => new Color(PAL.hedge), []);
  const colorB = useMemo(() => new Color(PAL.hedgeLight), []);
  const setup = (mesh: InstancedMesh | null) => {
    if (!mesh) return;
    posts.forEach((p, i) => {
      dummy.position.set(p.x, p.y + 0.55, p.z);
      dummy.rotation.set(0, p.rotation, 0);
      dummy.scale.set(2.3, 1.1, 0.8);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, i % 3 === 0 ? colorB : colorA);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  };
  return (
    <instancedMesh ref={setup} args={[kit.g.box, kit.m.lit("#ffffff"), posts.length]} />
  );
}

/** Lamps along the spokes: a post and a glowing head. */
function Lamps({ kit, posts }: { kit: Kit; posts: Post[] }) {
  const dummy = useMemo(() => new Object3D(), []);
  const setup = (mesh: InstancedMesh | null, yOffset: number, scale: [number, number, number]) => {
    if (!mesh) return;
    posts.forEach((p, i) => {
      dummy.position.set(p.x, p.y + yOffset, p.z);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.set(...scale);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  };
  return (
    <>
      <instancedMesh ref={(m) => setup(m, 1.6, [0.18, 3.2, 0.18])} args={[kit.g.cylinder, kit.m.lit(PAL.lampPost), posts.length]} />
      <instancedMesh ref={(m) => setup(m, 3.35, [0.55, 0.45, 0.55])} args={[kit.g.box, kit.m.glow(PAL.lampLight), posts.length]} />
    </>
  );
}

export function Village({
  paused,
  onQuestClick,
  questOpen,
}: {
  paused: boolean;
  /** The Quest Board monument was clicked — the stage opens its drawer. */
  onQuestClick?: () => void;
  questOpen?: boolean;
}) {
  const kit = useKit();
  const { content } = buildIsland();
  return (
    <group>
      {content.houses.map((plot, i) => (
        <House key={i} kit={kit} plot={plot} />
      ))}
      <Hub kit={kit} paused={paused} />
      <SquareCentre kit={kit} paused={paused} onQuestClick={onQuestClick} questOpen={questOpen} />
      <Hedges kit={kit} posts={content.hedges} />
      <Lamps kit={kit} posts={content.lamps} />
    </group>
  );
}
