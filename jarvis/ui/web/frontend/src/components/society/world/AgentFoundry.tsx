/**
 * The Agent Foundry on its plot — the World Kit hall plus the life the GLB
 * cannot carry: the assembly core spinning in its crown, the portal breathing,
 * and the flare that plays when a new agent walks out of it
 * (world-masterplan-v2.md §5, world-behaviour-manual.md §2 `foundry`).
 *
 * Everything here is cosmetic and client-side. The building never decides that
 * an agent was born; it reacts to `spawnStore.portalOpenedMs`, which the
 * walker sets as it claims its entrance. Reduced motion freezes the lot: the
 * core stops, the portal sits at its resting glow, no flare.
 *
 * All effect meshes are driven inside `useFrame` by writing to materials and
 * transforms directly — never through React state, which would re-render the
 * whole island sixty times a second.
 */
import { useCallback, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import {
  AdditiveBlending,
  Color,
  DoubleSide,
  InstancedMesh,
  Mesh,
  MeshBasicMaterial,
  Object3D,
} from "three";

import { buildIsland, groundY, type KitPlace } from "./islandLayout";
import { KitBuilding } from "./KitBuilding";
import { PORTAL_FLARE_MS, useSpawnStore } from "./spawnStore";

/** The foundry's own accents, matching the GLB palette in build_world_kit.py. */
const CORE = "#4cc9f0";
const CORE_BRIGHT = "#b8f2ff";
/** The roof beacon blinks between its hazard amber and the core's cyan. */
const BEACON_COLD = new Color("#ffb703");
const BEACON_HOT = new Color(CORE_BRIGHT);

/** Nodes of the GLB the core animation drives, with their turn rate (rad/s). */
const SPINNERS: ReadonlyArray<{ node: string; rate: number }> = [
  { node: "core_ring_a", rate: 0.28 },
  { node: "core_ring_b", rate: -0.44 },
  { node: "core_ring_c", rate: 0.62 },
];

const SPARK_COUNT = 22;
/** Local z of the portal mouth and of the ramp's foot (the kit's own anchors). */
const PORTAL_Z = 6.4;
const RAMP_END_Z = 13.4;
/** Height of the conveyor deck above the plot, in metres. */
const BELT_Y = 0.55;

interface Spark {
  /** Sideways drift, metres per unit of flare. */
  dx: number;
  /** Forward drift. */
  dz: number;
  /** How high it climbs. */
  up: number;
  /** Fraction of the flare it waits before appearing. */
  delay: number;
  spin: number;
}

/** Deterministic spark field — no RNG, so both windows show the same burst. */
function makeSparks(): Spark[] {
  return Array.from({ length: SPARK_COUNT }, (_, i) => {
    const a = (i / SPARK_COUNT) * Math.PI * 2;
    const r = 0.5 + ((i * 37) % 13) / 13;
    return {
      dx: Math.sin(a) * r * 2.6,
      dz: 0.4 + ((i * 53) % 11) / 11 * 3.4,
      up: 1.4 + ((i * 29) % 17) / 17 * 3.2,
      delay: ((i * 43) % 19) / 19 * 0.3,
      spin: ((i * 17) % 7) - 3,
    };
  });
}

function PortalEffects({ paused }: { paused: boolean }) {
  const glow = useRef<Mesh>(null);
  const pool = useRef<Mesh>(null);
  const wave = useRef<Mesh>(null);
  const sparks = useRef<InstancedMesh>(null);
  const dummy = useMemo(() => new Object3D(), []);
  const field = useMemo(makeSparks, []);

  useFrame(({ clock }) => {
    const opened = useSpawnStore.getState().portalOpenedMs;
    const elapsed = opened === 0 ? Infinity : Date.now() - opened;
    // 1 the instant a figure comes through, easing to 0 over the flare.
    const flare = paused || !Number.isFinite(elapsed) ? 0 : Math.max(0, 1 - elapsed / PORTAL_FLARE_MS);
    const eased = flare * flare;
    const breathe = paused ? 0 : Math.sin(clock.getElapsedTime() * 1.6) * 0.06;

    const glowMat = glow.current?.material as MeshBasicMaterial | undefined;
    if (glowMat) glowMat.opacity = 0.34 + breathe + eased * 0.55;

    const poolMat = pool.current?.material as MeshBasicMaterial | undefined;
    if (pool.current && poolMat) {
      pool.current.visible = eased > 0.01;
      poolMat.opacity = eased * 0.5;
    }

    const waveMat = wave.current?.material as MeshBasicMaterial | undefined;
    if (wave.current && waveMat) {
      const travel = 1 - flare; // rides out along the belt as the flare fades
      wave.current.visible = flare > 0.02;
      wave.current.position.z = PORTAL_Z + travel * (RAMP_END_Z - PORTAL_Z + 1.5);
      wave.current.scale.setScalar(0.7 + travel * 0.9);
      waveMat.opacity = flare * 0.75;
    }

    if (sparks.current) {
      sparks.current.visible = flare > 0.02;
      if (sparks.current.visible) {
        const t = 1 - flare;
        field.forEach((s, i) => {
          const life = Math.max(0, Math.min(1, (t - s.delay) / (1 - s.delay)));
          const scale = life === 0 ? 0 : (1 - life) * 0.34;
          dummy.position.set(
            s.dx * life,
            BELT_Y + 0.9 + s.up * life - life * life * 1.8,
            PORTAL_Z + s.dz * life,
          );
          dummy.rotation.set(life * s.spin, life * s.spin * 0.6, 0);
          dummy.scale.setScalar(scale);
          dummy.updateMatrix();
          sparks.current?.setMatrixAt(i, dummy.matrix);
        });
        sparks.current.instanceMatrix.needsUpdate = true;
      }
    }
  });

  return (
    <group>
      {/* the containment field in the portal mouth */}
      <mesh ref={glow} position={[0, 3.0, PORTAL_Z + 0.12]}>
        <planeGeometry args={[6.0, 4.9]} />
        <meshBasicMaterial color={CORE_BRIGHT} transparent opacity={0.34} depthWrite={false} side={DoubleSide} />
      </mesh>
      {/* light spilling onto the conveyor deck */}
      <mesh ref={pool} position={[0, BELT_Y + 0.06, PORTAL_Z + 2.6]} rotation={[-Math.PI / 2, 0, 0]} visible={false}>
        <planeGeometry args={[6.6, 7.4]} />
        <meshBasicMaterial
          color={CORE}
          transparent
          opacity={0}
          depthWrite={false}
          blending={AdditiveBlending}
        />
      </mesh>
      {/* the pulse that rides out along the belt with the new figure */}
      <mesh ref={wave} position={[0, BELT_Y + 0.1, PORTAL_Z]} rotation={[-Math.PI / 2, 0, 0]} visible={false}>
        <ringGeometry args={[2.1, 2.9, 32]} />
        <meshBasicMaterial
          color={CORE_BRIGHT}
          transparent
          opacity={0}
          depthWrite={false}
          blending={AdditiveBlending}
        />
      </mesh>
      <instancedMesh ref={sparks} args={[undefined, undefined, SPARK_COUNT]} visible={false} frustumCulled={false}>
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial color={CORE_BRIGHT} transparent opacity={0.9} depthWrite={false} blending={AdditiveBlending} />
      </instancedMesh>
    </group>
  );
}

export function AgentFoundry({
  paused,
  onClick,
  selected,
}: {
  paused: boolean;
  onClick?: (place: KitPlace) => void;
  selected?: boolean;
}) {
  const { map, content } = buildIsland();
  const { x, z, rotation } = content.kitPoses.foundry;
  const y = groundY(map, x, z);
  const spinners = useRef<Array<{ obj: Object3D; rate: number }>>([]);
  const orb = useRef<Object3D | null>(null);
  const beaconMat = useRef<MeshBasicMaterial | null>(null);

  // The GLB's named nodes are the animation rig; a renamed node simply means
  // that part stays still, never an error.
  const bind = useCallback((root: Object3D) => {
    spinners.current = SPINNERS.flatMap(({ node, rate }) => {
      const obj = root.getObjectByName(node);
      return obj ? [{ obj, rate }] : [];
    });
    orb.current = root.getObjectByName("core_orb") ?? null;
    const beacon = root.getObjectByName("core_beacon");
    const mat = beacon instanceof Mesh ? beacon.material : null;
    beaconMat.current = mat instanceof MeshBasicMaterial ? mat : null;
  }, []);

  useFrame(({ clock }, dt) => {
    if (paused) return;
    const opened = useSpawnStore.getState().portalOpenedMs;
    const elapsed = opened === 0 ? Infinity : Date.now() - opened;
    const flare = Number.isFinite(elapsed) ? Math.max(0, 1 - elapsed / PORTAL_FLARE_MS) : 0;
    // The core runs faster while the foundry is delivering.
    const boost = 1 + flare * 3.5;
    const step = Math.min(dt, 0.1);
    for (const s of spinners.current) s.obj.rotation.z += s.rate * boost * step;
    if (orb.current) {
      const pulse = 1 + Math.sin(clock.getElapsedTime() * 2.2) * 0.03 + flare * 0.12;
      orb.current.scale.set(pulse, pulse, pulse);
    }
    if (beaconMat.current) {
      // The roof beacon blinks like a real plant's; it flashes on a delivery.
      const blink = 0.55 + Math.sin(clock.getElapsedTime() * 3.4) * 0.45;
      beaconMat.current.color.copy(BEACON_COLD).lerp(BEACON_HOT, Math.min(1, blink * 0.6 + flare));
    }
  });

  return (
    <>
      <KitBuilding
        kit="agent-foundry"
        place="foundry"
        onClick={onClick}
        selected={selected}
        onInstance={bind}
      />
      <group position={[x, y, z]} rotation={[0, rotation, 0]}>
        <PortalEffects paused={paused} />
      </group>
    </>
  );
}

