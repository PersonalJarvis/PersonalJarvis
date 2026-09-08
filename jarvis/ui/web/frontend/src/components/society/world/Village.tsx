import { HouseMesh } from "./HouseMesh";
/**
 * The market district: the solarpunk houses in a ring around the open square,
 * the lead agent's hub on its podium at the head, the Quest Board monument with
 * the long table in the middle, the hedge ring with its four gates, and the lamps.
 *
 * Everything is primitives from `worldMaterials.ts` — no asset files. The
 * building language (docs/agent-society/world-art-direction.md §4): white
 * walls, glass, solar barrels, garden roofs, wood accents, rounded shapes.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Color, InstancedMesh, Mesh, Object3D, Vector3 } from "three";

import { useBuildingPoses, useBuildingYaw } from "./buildingPoses";
import { useCameraStore } from "./cameraStore";
import {
  HEDGE_SEGMENT_M,
  LEVEL_Y,
  PLATEAU_LEVEL,
  buildIsland,
  groundY,
  houseId,
  type HousePlot,
  type Post,
} from "./islandLayout";
import { QuestMonument } from "./QuestMonument";
import { RotateHandle } from "./RotateHandle";
import { Block, useKit, type Kit } from "./WorldKit";
import { PAL } from "./worldMaterials";

/** Radius of the hover/selection ring under a house, in metres. */
const HOUSE_RING_R = 4.6;

/**
 * One house. Local +z is the front (door side); the heading comes from the
 * pose store — the designed one (toward the square, or toward the road when
 * the square side is the camera's blind side) unless the viewer turned it.
 * A click selects the house and shows its rotate handle.
 */
function House({ plot }: { plot: HousePlot }) {
  const { map } = buildIsland();
  const id = houseId(plot.slot);
  const rotation = useBuildingYaw(id);
  const selected = useBuildingPoses((s) => s.selected === id);
  const gl = useThree((s) => s.gl);
  const [hover, setHover] = useState(false);
  const y = groundY(map, plot.x, plot.z);


  useEffect(() => {
    if (!hover) return;
    gl.domElement.style.cursor = "pointer";
    return () => {
      gl.domElement.style.cursor = "";
    };
  }, [hover, gl]);

  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    const poses = useBuildingPoses.getState();
    if (useCameraStore.getState().dragging || poses.rotating) return;
    poses.select(selected ? null : id);
  };

  return (
    <group
      position={[plot.x, y, plot.z]}
      rotation={[0, rotation, 0]}
      onClick={click}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHover(true);
      }}
      onPointerOut={() => setHover(false)}
    >
      {(hover || selected) && (
        <mesh position={[0, 0.06, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[HOUSE_RING_R - 0.25, HOUSE_RING_R + 0.25, 40]} />
          <meshBasicMaterial color={selected ? "#ffd166" : "#fffaf0"} transparent opacity={0.85} />
        </mesh>
      )}
      {selected && <RotateHandle id={id} x={plot.x} y={y} z={plot.z} radius={HOUSE_RING_R} />}
      <HouseMesh plot={plot} />
    </group>
  );
}

/**
 * The lead agent's hub — the landmark at the head of the square, on its own
 * podium one step above the village: a wide hall with a glass band and garden
 * roofs, a set-back upper floor, a glass atrium tower under the dome, the
 * beacon spire, two solar-roofed wings, a colonnade over the entrance, the
 * grand stair down to the square and the reflecting pool at its foot.
 * Local +z is the front (south, toward the square).
 */
function Hub({ kit, paused }: { kit: Kit; paused: boolean }) {
  const { map, content } = buildIsland();
  const [tx, tz] = content.places.hub.tile;
  const x = (tx + 0.5 - map.size / 2) * 2;
  const z = (tz + 0.5 - map.size / 2) * 2;
  const y = groundY(map, x, z);
  const plazaDrop = LEVEL_Y[PLATEAU_LEVEL] - y; // negative: the square lies below the podium
  const beacon = useRef<Mesh>(null);
  const halo = useRef<Mesh>(null);
  useFrame(({ clock }) => {
    if (paused) return;
    const t = clock.getElapsedTime();
    if (beacon.current) beacon.current.scale.setScalar((1 + Math.sin(t * 2.2) * 0.12) * 1.6);
    if (halo.current) {
      halo.current.rotation.z = t * 0.6;
      const s = 1 + Math.sin(t * 2.2 + 1.2) * 0.06;
      halo.current.scale.set(2.6 * s, 2.6 * s, 2.6);
    }
  });
  const towerZ = -0.5;
  return (
    <group position={[x, y, z]}>
      {/* plinth and the main hall */}
      <Block kit={kit} at={[0, 0.15, 0]} size={[25, 0.3, 13]} color={PAL.trim} />
      <Block kit={kit} at={[0, 2.6, 0]} size={[24, 5.2, 12]} color={PAL.wall} />
      <Block kit={kit} at={[0, 2.7, 0]} size={[24.1, 1.3, 12.1]} color={PAL.hubGlass} glow />
      <Block kit={kit} at={[0, 5.35, 0]} size={[24.6, 0.3, 12.6]} color={PAL.hubAccent} />
      {/* garden roofs on both ends of the hall */}
      {[-8.6, 8.6].map((ox) => (
        <group key={ox} position={[ox, 5.5, 0]}>
          <Block kit={kit} at={[0, 0.2, 0]} size={[6.2, 0.4, 11.4]} color={PAL.trim} />
          <Block kit={kit} at={[0, 0.5, 0]} size={[5.6, 0.25, 10.8]} color={PAL.gardenRoof} />
          <mesh geometry={kit.g.blob} material={kit.m.lit(PAL.gardenRoofBush)} position={[0.6, 1.0, -2.4]} scale={1.3} />
          <mesh geometry={kit.g.blob} material={kit.m.lit(PAL.gardenRoofBush)} position={[-0.9, 0.95, 2.2]} scale={1.1} />
          <mesh geometry={kit.g.blob} material={kit.m.lit(PAL.flower)} position={[1.1, 0.85, 1.4]} scale={0.6} />
        </group>
      ))}
      {/* the upper floor, set back */}
      <Block kit={kit} at={[0, 7.4, towerZ]} size={[15, 4.2, 9]} color={PAL.wall} />
      <Block kit={kit} at={[0, 7.6, towerZ]} size={[15.1, 1.2, 9.1]} color={PAL.hubGlass} glow />
      <Block kit={kit} at={[0, 9.55, towerZ]} size={[15.6, 0.3, 9.6]} color={PAL.hubAccent} />
      {/* the glass atrium under the dome */}
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.trim)} position={[0, 9.85, towerZ]} scale={[8.6, 0.3, 8.6]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.glow(PAL.hubGlass)} position={[0, 12.3, towerZ]} scale={[8, 4.8, 8]} />
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.hubAccent)} position={[0, 14.75, towerZ]} scale={[8.6, 0.3, 8.6]} />
      <mesh geometry={kit.g.dome} material={kit.m.lit(PAL.hubGlass)} position={[0, 14.9, towerZ]} scale={[9, 6.6, 9]} />
      {/* the spire: mast, halo ring and the pulsing beacon */}
      <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.metal)} position={[0, 20.6, towerZ]} scale={[0.45, 5.4, 0.45]} />
      <mesh ref={halo} geometry={kit.g.ring} material={kit.m.glow(PAL.beacon)} position={[0, 22.6, towerZ]} rotation={[Math.PI / 2, 0, 0]} scale={[2.6, 2.6, 2.6]} />
      <mesh ref={beacon} geometry={kit.g.sphere} material={kit.m.glow(PAL.beaconCore)} position={[0, 23.8, towerZ]} scale={1.6} />
      {/* two wings with solar barrel roofs */}
      {[-15.5, 15.5].map((ox) => (
        <group key={ox} position={[ox, 0, 1]}>
          <Block kit={kit} at={[0, 0.15, 0]} size={[7.6, 0.3, 8.6]} color={PAL.trim} />
          <Block kit={kit} at={[0, 1.9, 0]} size={[7, 3.8, 8]} color={PAL.wallShade} />
          <Block kit={kit} at={[0, 1.9, 4.05]} size={[4.4, 1.2, 0.12]} color={PAL.glass} glow />
          <mesh geometry={kit.g.halfCylinder} material={kit.m.lit(PAL.solar)} position={[0, 3.8, 0]} rotation={[0, 0, Math.PI / 2]} scale={[3.2, 7.4, 8.6]} />
          <Block kit={kit} at={[0, 5.42, 0]} size={[7.5, 0.1, 0.5]} color={PAL.solarLine} />
        </group>
      ))}
      {/* the colonnade over the entrance */}
      {[-10, -6, -2, 2, 6, 10].map((px) => (
        <mesh key={px} geometry={kit.g.cylinder} material={kit.m.lit(PAL.wall)} position={[px, 2.6, 7.4]} scale={[0.7, 5.2, 0.7]} />
      ))}
      <Block kit={kit} at={[0, 5.4, 7.5]} size={[23, 0.3, 3.2]} color={PAL.trim} />
      <Block kit={kit} at={[0, 5.62, 7.5]} size={[22, 0.12, 2.8]} color={PAL.glass} glow />
      {/* entrance: the wide door with glass to both sides */}
      <Block kit={kit} at={[0, 1.8, 6.1]} size={[4.2, 3.6, 0.2]} color={PAL.door} />
      <Block kit={kit} at={[-4.6, 1.9, 6.08]} size={[3.2, 2.6, 0.14]} color={PAL.glass} glow />
      <Block kit={kit} at={[4.6, 1.9, 6.08]} size={[3.2, 2.6, 0.14]} color={PAL.glass} glow />
      {/* planters on the podium's front corners */}
      {[-13, 13].map((px) => (
        <group key={px} position={[px, 0, 7.5]}>
          <Block kit={kit} at={[0, 0.35, 0]} size={[2.4, 0.7, 2.4]} color={PAL.trim} />
          <mesh geometry={kit.g.blob} material={kit.m.lit(PAL.gardenRoofBush)} position={[0, 1.2, 0]} scale={1.8} />
        </group>
      ))}
      {/* the grand stair down to the square: three broad steps */}
      {[0, 1, 2].map((k) => (
        <Block
          key={k}
          kit={kit}
          at={[0, plazaDrop * ((k + 1) / 3) + 0.17, 9.6 + k * 1.0]}
          size={[11, 0.34, 1.0]}
          color={PAL.trim}
        />
      ))}
      {/* the reflecting pool and two flag masts at the foot of the stair */}
      <Block kit={kit} at={[0, plazaDrop + 0.12, 13.2]} size={[8.4, 0.24, 2.2]} color={PAL.trim} />
      <Block kit={kit} at={[0, plazaDrop + 0.2, 13.2]} size={[8, 0.12, 1.8]} color={PAL.pool} glow />
      {[-6.2, 6.2].map((px) => (
        <group key={px} position={[px, plazaDrop, 13.2]}>
          <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.metal)} position={[0, 3.2, 0]} scale={[0.2, 6.4, 0.2]} />
          <Block kit={kit} at={[0.8, 5.8, 0]} size={[1.5, 0.9, 0.08]} color={PAL.hubAccent} />
        </group>
      ))}
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
      dummy.scale.set(HEDGE_SEGMENT_M[0], 1.1, HEDGE_SEGMENT_M[1]);
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

/**
 * Lamps along the spokes, around the square, the ring road and the dock: a
 * post, a glowing head, and the warm pool of light it throws on the ground —
 * an additive disc, the cheapest lighting effect there is.
 */
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
      <instancedMesh ref={(m) => setup(m, 0.05, [3.4, 1, 3.4])} args={[kit.g.disc, kit.m.halo(PAL.lampGlow, 0.28), posts.length]} />
    </>
  );
}

/** Bulbs per string between two neighbouring poles. */
const FESTOON_BULBS = 13;
/** How far the string sags in the middle, metres. */
const FESTOON_SAG = 1.4;
/** Height of the string's ends on the pole. */
const FESTOON_Y = 5.2;

/**
 * Festoon lights over the square: strings of bulbs sagging from pole to pole
 * around the rim, and one from every pole in toward the centre — the roof of
 * light a market has on a summer evening. Bulbs and wire segments are two
 * instanced meshes.
 */
function Festoon({ kit, poles }: { kit: Kit; poles: Post[] }) {
  const dummy = useMemo(() => new Object3D(), []);
  const segments = useMemo(() => {
    const out: Array<{ a: Vector3; b: Vector3 }> = [];
    const anchor = (p: Post) => new Vector3(p.x, p.y + FESTOON_Y, p.z);
    for (let i = 0; i < poles.length; i++) {
      out.push({ a: anchor(poles[i]), b: anchor(poles[(i + 1) % poles.length]) });
    }
    return out;
  }, [poles]);
  const points = useMemo(() => {
    const pts: Vector3[] = [];
    for (const { a, b } of segments) {
      for (let k = 0; k <= FESTOON_BULBS; k++) {
        const t = k / FESTOON_BULBS;
        const p = a.clone().lerp(b, t);
        p.y -= FESTOON_SAG * 4 * t * (1 - t);
        pts.push(p);
      }
    }
    return pts;
  }, [segments]);
  const bulbCount = segments.length * (FESTOON_BULBS - 1);
  const wireCount = segments.length * FESTOON_BULBS;

  const bulbs = (mesh: InstancedMesh | null) => {
    if (!mesh) return;
    let i = 0;
    for (let s = 0; s < segments.length; s++) {
      for (let k = 1; k < FESTOON_BULBS; k++) {
        const p = points[s * (FESTOON_BULBS + 1) + k];
        dummy.position.set(p.x, p.y - 0.12, p.z);
        dummy.rotation.set(0, 0, 0);
        dummy.scale.setScalar(0.3);
        dummy.updateMatrix();
        mesh.setMatrixAt(i++, dummy.matrix);
      }
    }
    mesh.instanceMatrix.needsUpdate = true;
  };
  const wires = (mesh: InstancedMesh | null) => {
    if (!mesh) return;
    let i = 0;
    for (let s = 0; s < segments.length; s++) {
      for (let k = 0; k < FESTOON_BULBS; k++) {
        const p = points[s * (FESTOON_BULBS + 1) + k];
        const q = points[s * (FESTOON_BULBS + 1) + k + 1];
        dummy.position.copy(p).lerp(q, 0.5);
        dummy.lookAt(q);
        dummy.scale.set(0.05, 0.05, p.distanceTo(q));
        dummy.updateMatrix();
        mesh.setMatrixAt(i++, dummy.matrix);
      }
    }
    mesh.instanceMatrix.needsUpdate = true;
  };

  return (
    <group>
      {poles.map((p, i) => (
        <group key={i} position={[p.x, p.y, p.z]}>
          <mesh geometry={kit.g.cylinder} material={kit.m.lit(PAL.lampPost)} position={[0, FESTOON_Y / 2, 0]} scale={[0.22, FESTOON_Y, 0.22]} />
          <mesh geometry={kit.g.box} material={kit.m.lit(PAL.trim)} position={[0, 0.2, 0]} scale={[0.8, 0.4, 0.8]} />
        </group>
      ))}
      <instancedMesh ref={wires} args={[kit.g.slab, kit.m.lit(PAL.lampPost), wireCount]} />
      <instancedMesh ref={bulbs} args={[kit.g.sphere, kit.m.glow(PAL.bulb), bulbCount]} />
    </group>
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
      {content.houses.map((plot) => (
        <House key={plot.slot} plot={plot} />
      ))}
      <Hub kit={kit} paused={paused} />
      <SquareCentre kit={kit} paused={paused} onQuestClick={onQuestClick} questOpen={questOpen} />
      <Hedges kit={kit} posts={content.hedges} />
      <Lamps kit={kit} posts={content.lamps} />
      <Festoon kit={kit} poles={content.festoonPoles} />
    </group>
  );
}
