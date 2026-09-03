/**
 * The four quarters' landmarks — the places MASTERPLAN §4.1 names, in the
 * solarpunk language: the workshop hall (west), the Memory House (north), the
 * harbor gate with its dock and boat (south), the lighthouse on the eastern
 * cape, plus the greenhouses and the solar field that fill the corners.
 */
import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Group, InstancedMesh, Mesh, Object3D } from "three";

import { AgentFoundry } from "./AgentFoundry";
import { buildIsland, groundY, tileToWorld, type PlaceId, type Post } from "./islandLayout";
import { KIT_PLACEMENTS, KitBuilding } from "./KitBuilding";
import { MemoryHouse } from "./MemoryHouse";
import { Block, useKit, type Kit } from "./WorldKit";
import { PAL } from "./worldMaterials";

function placeWorld(id: PlaceId): [number, number, number] {
  const { map, content } = buildIsland();
  const [tx, tz] = content.places[id].tile;
  const [x, z] = tileToWorld(tx, tz);
  return [x, groundY(map, x, z), z];
}

function Workshop({ kit }: { kit: Kit }) {
  const [x, y, z] = placeWorld("workshop");
  return (
    <group position={[x, y, z]}>
      <Block kit={kit} at={[0, 0.2, 0]} size={[25, 0.4, 13]} color={PAL.trim} />
      <Block kit={kit} at={[0, 2.6, 0]} size={[24, 4.8, 12]} color={PAL.wall} />
      {/* sawtooth skylight roof: three slanted glass panes and their backs */}
      {[-8, 0, 8].map((ox) => (
        <group key={ox} position={[ox, 5.0, 0]}>
          <Block kit={kit} at={[-1.6, 0.9, 0]} size={[5.2, 0.16, 12.2]} color={PAL.glass} glow rotation={[0, 0, 0.42]} />
          <Block kit={kit} at={[2.2, 0.9, 0]} size={[3.6, 0.16, 12.2]} color={PAL.workshopRoof} rotation={[0, 0, -0.62]} />
        </group>
      ))}
      {/* wide door facing the village (east, +x) */}
      <Block kit={kit} at={[12.1, 1.9, 0]} size={[0.2, 3.8, 5]} color={PAL.wood} />
      <Block kit={kit} at={[12.15, 1.9, 0]} size={[0.14, 3.2, 0.3]} color={PAL.woodDark} />
      {/* window band */}
      <Block kit={kit} at={[0, 3.4, 6.05]} size={[20, 1.0, 0.12]} color={PAL.glass} glow />
      <Block kit={kit} at={[0, 3.4, -6.05]} size={[20, 1.0, 0.12]} color={PAL.glass} glow />
      {/* chimney */}
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.metal)} position={[-9, 6.8, -4]} scale={[1.1, 4, 1.1]} />
    </group>
  );
}

function Harbor({ kit, paused }: { kit: Kit; paused: boolean }) {
  const [x, y, z] = placeWorld("harbor");
  const boat = useRef<Mesh>(null);
  const boat2 = useRef<Mesh>(null);
  const buoys = useRef<Group>(null);
  useFrame(({ clock }) => {
    if (paused) return;
    const t = clock.getElapsedTime();
    if (boat.current) {
      boat.current.position.y = 0.15 + Math.sin(t * 1.1) * 0.08;
      boat.current.rotation.z = Math.sin(t * 0.8) * 0.03;
    }
    if (boat2.current) {
      boat2.current.position.y = 0.15 + Math.sin(t * 0.9 + 1.7) * 0.09;
      boat2.current.rotation.z = Math.sin(t * 0.7 + 0.4) * 0.035;
    }
    if (buoys.current) {
      buoys.current.children.forEach((b, i) => {
        b.position.y = 0.25 + Math.sin(t * 1.3 + i * 2.1) * 0.12;
      });
    }
  });
  return (
    <group position={[x, y, z]}>
      {/* the gate: two pillars and a beam over the start of the dock */}
      <Block kit={kit} at={[-3, 2.6, 8]} size={[1.1, 5.2, 1.1]} color={PAL.wall} />
      <Block kit={kit} at={[3, 2.6, 8]} size={[1.1, 5.2, 1.1]} color={PAL.wall} />
      <Block kit={kit} at={[0, 5.4, 8]} size={[8, 0.7, 1.2]} color={PAL.wood} />
      <Block kit={kit} at={[0, 5.95, 8]} size={[8.4, 0.3, 1.4]} color={PAL.hubAccent} />
      <mesh geometry={kit.g.box} material={kit.m.glow(PAL.lampLight)} position={[-3, 5.5, 8]} scale={[0.6, 0.5, 0.6]} />
      <mesh geometry={kit.g.box} material={kit.m.glow(PAL.lampLight)} position={[3, 5.5, 8]} scale={[0.6, 0.5, 0.6]} />
      {/* harbor master's kiosk */}
      <Block kit={kit} at={[-7, 1.4, 2]} size={[4, 2.8, 3.5]} color={PAL.wallShade} />
      <Block kit={kit} at={[-7, 2.95, 2]} size={[4.6, 0.3, 4.1]} color={PAL.solar} />
      <Block kit={kit} at={[-7, 1.6, 3.8]} size={[2.4, 1.0, 0.12]} color={PAL.glass} glow />
      {/* boats moored beside the dock, and the buoys marking the fairway */}
      <group position={[6.5, -y, 26]}>
        <mesh ref={boat} geometry={kit.g.box} material={kit.m.lit(PAL.wood)} position={[0, 0.15, 0]} scale={[2.2, 1.0, 6]}>
          <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.woodDark)} position={[0, 2.4, 0.1]} scale={[0.08, 4.4, 0.04]} />
          <mesh geometry={kit.g.box} material={kit.m.lit(PAL.wall)} position={[0.45, 2.6, 0.1]} scale={[0.75, 2.6, 0.03]} />
        </mesh>
      </group>
      <group position={[-7, -y, 32]} rotation={[0, 0.35, 0]}>
        <mesh ref={boat2} geometry={kit.g.box} material={kit.m.lit(PAL.hubAccent)} position={[0, 0.15, 0]} scale={[2.6, 1.0, 7]}>
          <mesh geometry={kit.g.box} material={kit.m.lit(PAL.wall)} position={[0, 0.9, -0.1]} scale={[0.7, 0.9, 0.36]} />
        </mesh>
      </group>
      <group ref={buoys} position={[0, -y, 0]}>
        {[
          [16, 44],
          [-14, 48],
          [4, 58],
        ].map(([bx, bz]) => (
          <mesh key={`${bx},${bz}`} geometry={kit.g.sphere} material={kit.m.lit(PAL.buoy)} position={[bx, 0.25, bz]} scale={[1.1, 1.3, 1.1]}>
            <mesh geometry={kit.g.sphere} material={kit.m.glow(PAL.lampLight)} position={[0, 0.75, 0]} scale={[0.35, 0.3, 0.35]} />
          </mesh>
        ))}
      </group>
    </group>
  );
}

function Lighthouse({ kit, paused }: { kit: Kit; paused: boolean }) {
  const [x, y, z] = placeWorld("lighthouse");
  const lamp = useRef<Mesh>(null);
  const beam = useRef<Group>(null);
  useFrame(({ clock }) => {
    if (paused) return;
    const t = clock.getElapsedTime() * 1.4;
    if (lamp.current) lamp.current.rotation.y = t;
    if (beam.current) beam.current.rotation.y = t;
  });
  return (
    <group position={[x, y, z]} scale={[1.3, 1.3, 1.3]}>
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.trim)} position={[0, 0.25, 0]} scale={[5.5, 0.5, 5.5]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.wall)} position={[0, 5.5, 0]} scale={[3.4, 11, 3.4]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.lighthouseStripe)} position={[0, 3.2, 0]} scale={[3.45, 1.5, 3.45]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.lighthouseStripe)} position={[0, 7.4, 0]} scale={[3.45, 1.5, 3.45]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.metal)} position={[0, 11.2, 0]} scale={[4.2, 0.4, 4.2]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.glow(PAL.hubGlass)} position={[0, 12.3, 0]} scale={[2.6, 1.8, 2.6]} />
      <mesh ref={lamp} geometry={kit.g.box} material={kit.m.glow(PAL.beaconCore)} position={[0, 12.3, 0]} scale={[3.2, 0.9, 0.7]} />
      <mesh geometry={kit.g.dome} material={kit.m.lit(PAL.lighthouseStripe)} position={[0, 13.2, 0]} scale={[4.2, 2.2, 4.2]} />
      {/* the beam: two translucent cones sweeping with the lamp, one each way */}
      <group ref={beam} position={[0, 12.3, 0]}>
        {[1, -1].map((dir) => (
          <mesh
            key={dir}
            geometry={kit.g.cone}
            material={kit.m.halo(PAL.beacon, 0.16)}
            position={[dir * 24, 0, 0]}
            rotation={[0, 0, (-dir * Math.PI) / 2]}
            scale={[7, 48, 7]}
          />
        ))}
      </group>
      <mesh geometry={kit.g.disc} material={kit.m.halo(PAL.lampGlow, 0.2)} position={[0, 0.55, 0]} scale={[7, 1, 7]} />
      {/* keeper's hut */}
      <Block kit={kit} at={[-5, 1.3, 3]} size={[4, 2.6, 3.4]} color={PAL.wallShade} />
      <Block kit={kit} at={[-5, 2.75, 3]} size={[4.6, 0.3, 4]} color={PAL.solar} />
    </group>
  );
}

/**
 * The cliff's south face, in metres along −z from the mine place's origin —
 * where the rock starts. The retirement ceremony throws a body into the mouth
 * cut into it (`retirementGeometry.ts`), so the number is shared, not copied.
 */
export const MINE_FACE_DZ = -6.8;

/** The tunnel mouth's own z, half a metre into the rock behind the face. */
export const MINE_PORTAL_DZ = MINE_FACE_DZ - 0.5;

/** Half-way up the tunnel mouth: what a thrown body is aimed at, in metres. */
export const MINE_PORTAL_Y = 1.6;

/**
 * The mine: a timber portal cut into the cliff north of the quarry's forecourt,
 * lanterns on its posts, rails running out of the dark to a cart of ore, a
 * headframe with its wheel, crates, and the foreman's hut.
 */
function Mine({ kit, paused }: { kit: Kit; paused: boolean }) {
  const [x, y, z] = placeWorld("mine");
  const wheel = useRef<Mesh>(null);
  useFrame(({ clock }) => {
    if (!wheel.current || paused) return;
    wheel.current.rotation.x = clock.getElapsedTime() * 0.8;
  });
  const face = MINE_FACE_DZ;
  return (
    <group position={[x, y, z]}>
      {/* the tunnel: a dark mouth set into the cliff */}
      <Block kit={kit} at={[0, 2.2, face - 0.5]} size={[5.2, 4.4, 1.6]} color={PAL.mineDark} glow />
      {/* timber frame */}
      <Block kit={kit} at={[-2.9, 2.4, face + 0.2]} size={[0.7, 4.8, 0.7]} color={PAL.woodDark} />
      <Block kit={kit} at={[2.9, 2.4, face + 0.2]} size={[0.7, 4.8, 0.7]} color={PAL.woodDark} />
      <Block kit={kit} at={[0, 4.9, face + 0.2]} size={[6.8, 0.7, 0.8]} color={PAL.woodDark} />
      <Block kit={kit} at={[0, 3.6, face - 0.3]} size={[5.4, 0.4, 0.5]} color={PAL.wood} />
      {/* the sign over the lintel and the lanterns on the posts */}
      <Block kit={kit} at={[0, 5.9, face + 0.25]} size={[3.6, 1.0, 0.16]} color={PAL.wall} />
      <Block kit={kit} at={[0, 5.9, face + 0.36]} size={[2.6, 0.22, 0.04]} color={PAL.woodDark} />
      {[-2.9, 2.9].map((px) => (
        <group key={px} position={[px, 3.9, face + 0.75]}>
          <mesh geometry={kit.g.box} material={kit.m.glow(PAL.lampLight)} scale={[0.42, 0.5, 0.42]} />
          <mesh geometry={kit.g.box} material={kit.m.lit(PAL.metal)} position={[0, 0.34, 0]} scale={[0.5, 0.12, 0.5]} />
          <mesh geometry={kit.g.disc} material={kit.m.halo(PAL.lampGlow, 0.3)} position={[0, -3.85, 0.6]} scale={[2.6, 1, 2.6]} />
        </group>
      ))}
      {/* rails out of the tunnel across the forecourt */}
      {[-0.75, 0.75].map((px) => (
        <Block key={px} kit={kit} at={[px, 0.08, face + 8]} size={[0.14, 0.14, 16.5]} color={PAL.rail} />
      ))}
      {Array.from({ length: 9 }, (_, k) => (
        <Block key={k} kit={kit} at={[0, 0.04, face + 1 + k * 1.8]} size={[2.2, 0.08, 0.36]} color={PAL.woodDark} />
      ))}
      {/* the ore cart on the rails */}
      <group position={[0, 0, face + 6.5]}>
        <Block kit={kit} at={[0, 0.85, 0]} size={[1.7, 1.0, 2.3]} color={PAL.woodDark} />
        <Block kit={kit} at={[0, 0.85, 0]} size={[1.5, 1.05, 2.1]} color={PAL.metal} />
        {[
          [-0.85, -0.7],
          [0.85, -0.7],
          [-0.85, 0.7],
          [0.85, 0.7],
        ].map(([wx, wz]) => (
          <mesh key={`${wx},${wz}`} geometry={kit.g.cylinder} material={kit.m.lit(PAL.rail)} position={[wx, 0.3, wz]} rotation={[0, 0, Math.PI / 2]} scale={[0.6, 0.16, 0.6]} />
        ))}
        <mesh geometry={kit.g.blob} material={kit.m.glow(PAL.ore)} position={[0.2, 1.5, 0.3]} scale={0.8} />
        <mesh geometry={kit.g.blob} material={kit.m.glow(PAL.ore)} position={[-0.4, 1.45, -0.4]} scale={0.7} />
        <mesh geometry={kit.g.blob} material={kit.m.lit(PAL.boulderB)} position={[0.35, 1.4, -0.5]} scale={0.6} />
      </group>
      {/* the headframe: an A-frame with the winding wheel, and the foreman's hut */}
      <group position={[6.5, 0, face + 3]}>
        <Block kit={kit} at={[-1.1, 3.0, 0]} size={[0.35, 6.4, 0.35]} color={PAL.woodDark} rotation={[0, 0, 0.18]} />
        <Block kit={kit} at={[1.1, 3.0, 0]} size={[0.35, 6.4, 0.35]} color={PAL.woodDark} rotation={[0, 0, -0.18]} />
        <Block kit={kit} at={[0, 0.3, -1.4]} size={[0.35, 0.6, 3.2]} color={PAL.woodDark} />
        <Block kit={kit} at={[0, 3.6, 0]} size={[2.4, 0.3, 0.3]} color={PAL.wood} />
        <mesh ref={wheel} geometry={kit.g.ring} material={kit.m.lit(PAL.rail)} position={[0, 6.1, 0]} rotation={[0, Math.PI / 2, 0]} scale={[2.2, 2.2, 2.2]} />
        <Block kit={kit} at={[0, 6.1, 0]} size={[0.16, 0.16, 0.6]} color={PAL.metal} />
        <Block kit={kit} at={[0, 3.3, 0]} size={[0.06, 5.6, 0.06]} color={PAL.rail} />
      </group>
      <group position={[-6.2, 0, face + 3.5]}>
        <Block kit={kit} at={[0, 1.4, 0]} size={[3.6, 2.8, 3.2]} color={PAL.wallShade} />
        <Block kit={kit} at={[0, 2.95, 0]} size={[4.2, 0.3, 3.8]} color={PAL.solar} />
        <Block kit={kit} at={[0, 1.5, 1.66]} size={[1.6, 1.0, 0.12]} color={PAL.glass} glow />
        <Block kit={kit} at={[1.2, 1.0, 1.66]} size={[0.8, 2.0, 0.12]} color={PAL.door} />
      </group>
      {/* crates and a lantern post at the forecourt's edge */}
      <Block kit={kit} at={[-4.5, 0.5, face + 8]} size={[1.0, 1.0, 1.0]} color={PAL.wood} />
      <Block kit={kit} at={[-5.6, 0.45, face + 8.4]} size={[0.9, 0.9, 0.9]} color={PAL.woodDark} />
      <Block kit={kit} at={[-5.0, 1.45, face + 8.1]} size={[0.9, 0.9, 0.9]} color={PAL.wood} rotation={[0, 0.5, 0]} />
    </group>
  );
}

/** The campfire on the cove's beach: a ring of stones, logs, a flame that breathes. */
function Campfire({ kit, paused }: { kit: Kit; paused: boolean }) {
  const { campfire } = buildIsland().content;
  const flames = useRef<Group>(null);
  const glow = useRef<Mesh>(null);
  useFrame(({ clock }) => {
    if (paused) return;
    const t = clock.getElapsedTime();
    if (flames.current) {
      flames.current.children.forEach((f, i) => {
        const s = 0.85 + 0.25 * Math.sin(t * (5 + i) + i * 1.7);
        f.scale.set(s, s * (1.1 + 0.3 * Math.sin(t * 7 + i)), s);
        f.position.y = 0.55 + i * 0.35 + 0.1 * Math.sin(t * 6 + i);
      });
    }
    if (glow.current) glow.current.scale.setScalar(4.2 + 0.5 * Math.sin(t * 4.3));
  });
  return (
    <group position={[campfire.x, campfire.y, campfire.z]}>
      {Array.from({ length: 7 }, (_, k) => {
        const a = (k / 7) * Math.PI * 2;
        return (
          <mesh key={k} geometry={kit.g.blob} material={kit.m.lit(k % 2 ? PAL.boulderA : PAL.boulderB)} position={[Math.cos(a) * 1.3, 0.2, Math.sin(a) * 1.3]} scale={[0.55, 0.4, 0.5]} rotation={[0, a, 0]} />
        );
      })}
      <Block kit={kit} at={[0, 0.25, 0]} size={[0.35, 0.35, 1.8]} color={PAL.woodDark} rotation={[0, 0.5, 0]} />
      <Block kit={kit} at={[0, 0.25, 0]} size={[0.35, 0.35, 1.8]} color={PAL.woodDark} rotation={[0, -0.7, 0]} />
      <group ref={flames}>
        <mesh geometry={kit.g.blob} material={kit.m.glow(PAL.fire)} position={[0, 0.6, 0]} scale={0.9} />
        <mesh geometry={kit.g.blob} material={kit.m.glow(PAL.fireCore)} position={[0.1, 0.9, 0]} scale={0.6} />
        <mesh geometry={kit.g.blob} material={kit.m.glow(PAL.ember)} position={[-0.15, 1.2, 0.1]} scale={0.35} />
      </group>
      <mesh ref={glow} geometry={kit.g.disc} material={kit.m.halo(PAL.fire, 0.3)} position={[0, 0.06, 0]} scale={4.2} />
      {/* two driftwood benches */}
      <Block kit={kit} at={[0, 0.3, 3.2]} size={[3.2, 0.5, 0.6]} color={PAL.wood} rotation={[0, 0.15, 0]} />
      <Block kit={kit} at={[-3.0, 0.3, -1.2]} size={[3.0, 0.5, 0.6]} color={PAL.woodDark} rotation={[0, 1.2, 0]} />
    </group>
  );
}

/** Instanced greenhouses: glass barrels on wooden sills. */
function Greenhouses({ kit, posts }: { kit: Kit; posts: Post[] }) {
  const dummy = useMemo(() => new Object3D(), []);
  const setup = (mesh: InstancedMesh | null, yOffset: number, scale: [number, number, number], rot: [number, number, number]) => {
    if (!mesh) return;
    posts.forEach((p, i) => {
      dummy.position.set(p.x, p.y + yOffset, p.z);
      dummy.rotation.set(...rot);
      dummy.scale.set(...scale);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  };
  return (
    <>
      <instancedMesh ref={(m) => setup(m, 0.15, [7.2, 0.3, 4.4], [0, 0, 0])} args={[kit.g.box, kit.m.lit(PAL.wood), posts.length]} />
      <instancedMesh ref={(m) => setup(m, 0.3, [4.2, 7.0, 4.2], [0, 0, Math.PI / 2])} args={[kit.g.halfCylinder, kit.m.glow(PAL.glass), posts.length]} />
    </>
  );
}

/** Instanced solar panels on posts, tilted toward the afternoon sun. */
function SolarField({ kit, posts }: { kit: Kit; posts: Post[] }) {
  const dummy = useMemo(() => new Object3D(), []);
  const setup = (mesh: InstancedMesh | null, yOffset: number, scale: [number, number, number], rot: [number, number, number]) => {
    if (!mesh) return;
    posts.forEach((p, i) => {
      dummy.position.set(p.x, p.y + yOffset, p.z);
      dummy.rotation.set(...rot);
      dummy.scale.set(...scale);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  };
  return (
    <>
      <instancedMesh ref={(m) => setup(m, 0.6, [0.2, 1.2, 0.2], [0, 0, 0])} args={[kit.g.cylinder, kit.m.lit(PAL.metal), posts.length]} />
      <instancedMesh ref={(m) => setup(m, 1.3, [3.6, 0.12, 2.2], [-0.45, 0, 0])} args={[kit.g.box, kit.m.lit(PAL.solar), posts.length]} />
      <instancedMesh ref={(m) => setup(m, 1.36, [3.4, 0.04, 0.12], [-0.45, 0, 0])} args={[kit.g.box, kit.m.lit(PAL.solarLine), posts.length]} />
    </>
  );
}

export function Landmarks({
  paused,
  onHubClick,
  openHub,
  memoryOpen,
  atMemory,
}: {
  paused: boolean;
  /** A kit building was clicked — the stage opens that hub's drawer. */
  onHubClick?: (place: PlaceId) => void;
  openHub?: PlaceId | null;
  /** The Memory House's drawer is open (it is not a ring hub, so it has its own flag). */
  memoryOpen?: boolean;
  /** Agents whose checkpoint is `archive` right now — the house lights up for them. */
  atMemory?: number;
}) {
  const kit = useKit();
  const { content } = buildIsland();
  return (
    <group>
      {/* The foundry has its own component: it animates its core and portal. */}
      {KIT_PLACEMENTS.filter((k) => k.place !== "foundry").map((k) => (
        <KitBuilding
          key={k.kit}
          kit={k.kit}
          place={k.place}
          onClick={onHubClick}
          selected={openHub === k.place}
        />
      ))}
      <AgentFoundry paused={paused} onClick={onHubClick} selected={openHub === "foundry"} />
      <Workshop kit={kit} />
      <MemoryHouse paused={paused} busy={atMemory ?? 0} onClick={onHubClick} selected={memoryOpen} />
      <Harbor kit={kit} paused={paused} />
      <Lighthouse kit={kit} paused={paused} />
      <Mine kit={kit} paused={paused} />
      <Campfire kit={kit} paused={paused} />
      <Greenhouses kit={kit} posts={content.greenhouses} />
      <SolarField kit={kit} posts={content.panels} />
    </group>
  );
}
