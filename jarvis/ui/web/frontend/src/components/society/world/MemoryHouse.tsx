/**
 * The Memory House on the archive plot — the society's shared memory as a
 * building (docs/agent-society/memory-house.md §3.5). The GLB comes from
 * `scripts/world/kit_memory_house.py`; this component gives it the life the
 * file cannot carry: the halo turns, the core breathes, the memory shards
 * drift, and everything brightens while an agent stands at the house.
 *
 * "An agent is at the house" is read from the roster's `checkpoint` — the
 * same truth the walkers follow — never from anything the building decides.
 * Reduced motion freezes the lot. Effects write to materials and transforms
 * inside `useFrame`, never through React state.
 *
 * The monolith is translucent: the GLB's BLEND materials keep their alpha
 * here (the shared kit restyle drops it), so the core shows through the glass.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useGLTF } from "@react-three/drei";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import {
  Color,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  MeshToonMaterial,
  type Object3D,
} from "three";
import * as SkeletonUtils from "three/examples/jsm/utils/SkeletonUtils.js";

import memoryHouseUrl from "@/assets/society/world/kit/memory-house.glb";
import { useCameraStore } from "./cameraStore";
import { buildIsland, groundY, tileToWorld, type PlaceId } from "./islandLayout";
import { createToonRamp } from "./worldMaterials";

/** Emission above this strength renders unlit (a lamp, the core), below it stays a lit toon. */
const GLOW_STRENGTH = 1.5;
/** The core's resting and busy colours (the GLB palette in kit_memory_house.py). */
const CORE_REST = new Color("#7df9ff");
const CORE_BUSY = new Color("#ffffff");
const SHARD_REST = new Color("#b388ff");
const SHARD_BUSY = new Color("#e9d5ff");

interface Rig {
  halo: Object3D | null;
  orb: Object3D | null;
  orbMat: MeshBasicMaterial | null;
  shards: Array<{ obj: Object3D; baseY: number; phase: number; mat: MeshBasicMaterial | null }>;
  glow: MeshBasicMaterial[];
}

function restyle(root: Object3D, ramp: ReturnType<typeof createToonRamp>): Rig {
  const rig: Rig = { halo: null, orb: null, orbMat: null, shards: [], glow: [] };
  root.traverse((o) => {
    if (!(o instanceof Mesh)) return;
    const src = o.material as MeshStandardMaterial;
    if (!(src instanceof MeshStandardMaterial)) return;
    const emissiveStrength = src.emissiveIntensity * Math.max(src.emissive.r, src.emissive.g, src.emissive.b);
    const translucent = src.transparent && src.opacity < 0.999;
    if (emissiveStrength >= GLOW_STRENGTH && !translucent) {
      const mat = new MeshBasicMaterial({ color: src.emissive.clone() });
      o.material = mat;
      if (o.name.startsWith("shard_") || o.name === "core_orb" || o.name.startsWith("pylon_light") || o.name.startsWith("band_") || o.name === "roof_edge" || o.name === "pool_ring") {
        rig.glow.push(mat);
      }
    } else {
      // Glass reads as glass only when it is clearer than Blender's preview
      // needs it: a pale tint, a fifth of the opacity, and the core behind it.
      const color = translucent ? src.color.clone().lerp(new Color("#ffffff"), 0.45) : src.color.clone();
      o.material = new MeshToonMaterial({
        color,
        map: src.map,
        vertexColors: !!o.geometry.getAttribute("color"),
        gradientMap: ramp,
        emissive: emissiveStrength > 0 && !translucent ? src.emissive.clone() : new Color(0, 0, 0),
        emissiveIntensity: emissiveStrength > 0 && !translucent ? Math.min(0.6, src.emissiveIntensity) : 0,
        transparent: translucent,
        opacity: translucent ? Math.min(src.opacity, 0.2) : 1,
        depthWrite: !translucent,
      });
      if (translucent) o.renderOrder = 10; // the glass draws after what it contains
    }
    o.castShadow = !translucent;
    o.receiveShadow = true;
  });
  rig.halo = root.getObjectByName("halo") ?? null;
  rig.orb = root.getObjectByName("core_orb") ?? null;
  const orb = rig.orb;
  rig.orbMat = orb instanceof Mesh && orb.material instanceof MeshBasicMaterial ? orb.material : null;
  for (let i = 0; i < 12; i++) {
    const obj = root.getObjectByName(`shard_${i}`);
    if (!obj) break;
    const mat = obj instanceof Mesh && obj.material instanceof MeshBasicMaterial ? obj.material : null;
    rig.shards.push({ obj, baseY: obj.position.y, phase: i * 0.9, mat });
  }
  return rig;
}

export function MemoryHouse({
  paused,
  busy,
  onClick,
  selected,
}: {
  paused: boolean;
  /** How many agents stand at the house right now (roster checkpoints). */
  busy: number;
  onClick?: (place: PlaceId) => void;
  selected?: boolean;
}) {
  const { scene } = useGLTF(memoryHouseUrl);
  const gl = useThree((s) => s.gl);
  const [hover, setHover] = useState(false);
  const ramp = useMemo(createToonRamp, []);
  const rig = useRef<Rig | null>(null);
  const instance = useMemo(() => {
    const clone = SkeletonUtils.clone(scene);
    rig.current = restyle(clone, ramp);
    return clone;
  }, [scene, ramp]);
  const heat = useRef(0);

  useEffect(
    () => () => {
      instance.traverse((o) => {
        if (o instanceof Mesh) (o.material as MeshToonMaterial | MeshBasicMaterial).dispose();
      });
      ramp.dispose();
    },
    [instance, ramp],
  );

  useEffect(() => {
    if (!onClick) return;
    gl.domElement.style.cursor = hover ? "pointer" : "";
    return () => {
      gl.domElement.style.cursor = "";
    };
  }, [hover, gl, onClick]);

  useFrame(({ clock }, dt) => {
    const r = rig.current;
    if (!r || paused) return;
    const step = Math.min(dt, 0.1);
    const t = clock.getElapsedTime();
    // Ease toward "busy" so a figure arriving lights the house up, not on.
    const target = busy > 0 ? 1 : 0;
    heat.current += (target - heat.current) * Math.min(1, step * 1.6);
    const h = heat.current;
    if (r.halo) r.halo.rotation.y += (0.12 + h * 0.5) * step;
    if (r.orb) {
      const pulse = 1 + Math.sin(t * (1.4 + h * 2.2)) * (0.04 + h * 0.08);
      r.orb.scale.setScalar(pulse);
    }
    if (r.orbMat) r.orbMat.color.copy(CORE_REST).lerp(CORE_BUSY, 0.25 + h * 0.75);
    for (const s of r.shards) {
      s.obj.position.y = s.baseY + Math.sin(t * 0.9 + s.phase) * (0.12 + h * 0.2);
      s.obj.rotation.y += (0.15 + h * 0.6) * step;
      if (s.mat) s.mat.color.copy(SHARD_REST).lerp(SHARD_BUSY, h);
    }
  });

  const { map, content } = buildIsland();
  const [tx, tz] = content.places.archive.tile;
  const [x, z] = tileToWorld(tx, tz);
  const y = groundY(map, x, z);

  const click = (e: ThreeEvent<MouseEvent>) => {
    if (!onClick) return;
    e.stopPropagation();
    if (useCameraStore.getState().dragging) return;
    onClick("archive");
  };

  return (
    <group
      position={[x, y, z]}
      onClick={click}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHover(true);
      }}
      onPointerOut={() => setHover(false)}
    >
      <primitive object={instance} />
      {(hover || selected) && (
        <mesh position={[0, 0.06, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[11.2, 11.8, 48]} />
          <meshBasicMaterial color={selected ? "#ffd166" : "#fffaf0"} transparent opacity={0.85} />
        </mesh>
      )}
    </group>
  );
}

useGLTF.preload(memoryHouseUrl);
