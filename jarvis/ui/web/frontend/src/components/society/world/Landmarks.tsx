/**
 * The four quarters' landmarks — the places MASTERPLAN §4.1 names, in the
 * solarpunk language: the workshop hall (west), the archive tower (north), the
 * harbor gate with its dock and boat (south), the lighthouse on the eastern
 * cape, plus the greenhouses and the solar field that fill the corners.
 */
import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { InstancedMesh, Mesh, Object3D } from "three";

import { buildIsland, groundY, tileToWorld, type PlaceId, type Post } from "./islandLayout";
import { KIT_PLACEMENTS, KitBuilding } from "./KitBuilding";
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

function Archive({ kit }: { kit: Kit }) {
  const [x, y, z] = placeWorld("archive");
  return (
    <group position={[x, y, z]}>
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.trim)} position={[0, 0.2, 0]} scale={[13, 0.4, 13]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.wall)} position={[0, 4.5, 0]} scale={[11, 9, 11]} />
      {/* two glass bands */}
      <mesh geometry={kit.g.cylinder} material={kit.m.glow(PAL.glass)} position={[0, 3.2, 0]} scale={[11.1, 0.9, 11.1]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.glow(PAL.glass)} position={[0, 6.6, 0]} scale={[11.1, 0.9, 11.1]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.hubAccent)} position={[0, 9.1, 0]} scale={[11.6, 0.3, 11.6]} />
      <mesh geometry={kit.g.dome} material={kit.m.lit(PAL.archiveDome)} position={[0, 9.2, 0]} scale={[11, 6, 11]} />
      <mesh geometry={kit.g.cone} material={kit.m.lit(PAL.metal)} position={[0, 12.9, 0]} scale={[0.6, 1.6, 0.6]} />
      {/* entrance arch facing the village (south, +z) */}
      <Block kit={kit} at={[0, 1.7, 5.6]} size={[3.6, 3.4, 0.6]} color={PAL.wood} />
      <Block kit={kit} at={[0, 1.4, 5.95]} size={[2.2, 2.8, 0.2]} color={PAL.door} />
    </group>
  );
}

function Harbor({ kit, paused }: { kit: Kit; paused: boolean }) {
  const [x, y, z] = placeWorld("harbor");
  const boat = useRef<Mesh>(null);
  useFrame(({ clock }) => {
    if (!boat.current || paused) return;
    const t = clock.getElapsedTime();
    boat.current.position.y = 0.15 + Math.sin(t * 1.1) * 0.08;
    boat.current.rotation.z = Math.sin(t * 0.8) * 0.03;
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
      {/* a boat moored beside the dock */}
      <group position={[6.5, -y, 24]}>
        <mesh ref={boat} geometry={kit.g.box} material={kit.m.lit(PAL.wood)} position={[0, 0.15, 0]} scale={[2.2, 1.0, 6]}>
          <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.woodDark)} position={[0, 2.4, 0.1]} scale={[0.08, 4.4, 0.04]} />
          <mesh geometry={kit.g.box} material={kit.m.lit(PAL.wall)} position={[0.45, 2.6, 0.1]} scale={[0.75, 2.6, 0.03]} />
        </mesh>
      </group>
    </group>
  );
}

function Lighthouse({ kit, paused }: { kit: Kit; paused: boolean }) {
  const [x, y, z] = placeWorld("lighthouse");
  const lamp = useRef<Mesh>(null);
  useFrame(({ clock }) => {
    if (!lamp.current || paused) return;
    lamp.current.rotation.y = clock.getElapsedTime() * 1.4;
  });
  return (
    <group position={[x, y, z]}>
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.trim)} position={[0, 0.25, 0]} scale={[5.5, 0.5, 5.5]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.wall)} position={[0, 5.5, 0]} scale={[3.4, 11, 3.4]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.lighthouseStripe)} position={[0, 3.2, 0]} scale={[3.45, 1.5, 3.45]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.lighthouseStripe)} position={[0, 7.4, 0]} scale={[3.45, 1.5, 3.45]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.metal)} position={[0, 11.2, 0]} scale={[4.2, 0.4, 4.2]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.glow(PAL.hubGlass)} position={[0, 12.3, 0]} scale={[2.6, 1.8, 2.6]} />
      <mesh ref={lamp} geometry={kit.g.box} material={kit.m.glow(PAL.beaconCore)} position={[0, 12.3, 0]} scale={[3.2, 0.9, 0.7]} />
      <mesh geometry={kit.g.dome} material={kit.m.lit(PAL.lighthouseStripe)} position={[0, 13.2, 0]} scale={[4.2, 2.2, 4.2]} />
      {/* keeper's hut */}
      <Block kit={kit} at={[-5, 1.3, 3]} size={[4, 2.6, 3.4]} color={PAL.wallShade} />
      <Block kit={kit} at={[-5, 2.75, 3]} size={[4.6, 0.3, 4]} color={PAL.solar} />
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
}: {
  paused: boolean;
  /** A kit building was clicked — the stage opens that hub's drawer. */
  onHubClick?: (place: PlaceId) => void;
  openHub?: PlaceId | null;
}) {
  const kit = useKit();
  const { content } = buildIsland();
  return (
    <group>
      {KIT_PLACEMENTS.map((k) => (
        <KitBuilding key={k.kit} kit={k.kit} place={k.place} rotation={k.rotation} onClick={onHubClick} selected={openHub === k.place} />
      ))}
      <Workshop kit={kit} />
      <Archive kit={kit} />
      <Harbor kit={kit} paused={paused} />
      <Lighthouse kit={kit} paused={paused} />
      <Greenhouses kit={kit} posts={content.greenhouses} />
      <SolarField kit={kit} posts={content.panels} />
    </group>
  );
}
