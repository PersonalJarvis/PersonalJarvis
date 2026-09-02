/**
 * A World Kit building: a GLB built by `scripts/world/build_world_kit.py`,
 * placed on its island plot, restyled to the world's toon ramp on load and
 * clickable (world-masterplan-v2.md §4, §5).
 *
 * The GLB's materials arrive as Standard (PBR) from Blender; the island lights
 * everything with the same four-step ramp, so every mesh gets a Toon material
 * with the GLB's colour and emissive — one code path for kit and primitives.
 */
import { useEffect, useMemo, useState } from "react";
import { useGLTF } from "@react-three/drei";
import { useThree, type ThreeEvent } from "@react-three/fiber";
import { Color, Mesh, MeshBasicMaterial, MeshStandardMaterial, MeshToonMaterial, type Object3D } from "three";
import * as SkeletonUtils from "three/examples/jsm/utils/SkeletonUtils.js";

import pluginDocksUrl from "@/assets/society/world/kit/plugin-docks.glb";
import { useCameraStore } from "./cameraStore";
import { buildIsland, groundY, tileToWorld, type PlaceId } from "./islandLayout";
import { createToonRamp } from "./worldMaterials";

/** Every kit file the registry knows. Adding a building = adding a row. */
export const KIT_URLS = {
  "plugin-docks": pluginDocksUrl,
} as const;
export type KitId = keyof typeof KIT_URLS;

/** Where each kit building stands and which place it is. */
export const KIT_PLACEMENTS: ReadonlyArray<{ kit: KitId; place: PlaceId; rotation: number }> = [
  { kit: "plugin-docks", place: "plugins", rotation: 0 },
];

/** Emission above this strength renders unlit (a lamp, a neon tube), below it stays a lit toon. */
const GLOW_STRENGTH = 1.5;

function restyle(root: Object3D, ramp: ReturnType<typeof createToonRamp>): void {
  root.traverse((o) => {
    if (!(o instanceof Mesh)) return;
    const src = o.material as MeshStandardMaterial;
    if (!(src instanceof MeshStandardMaterial)) return;
    const emissiveStrength = src.emissiveIntensity * Math.max(src.emissive.r, src.emissive.g, src.emissive.b);
    if (emissiveStrength >= GLOW_STRENGTH) {
      o.material = new MeshBasicMaterial({ color: src.emissive.clone().multiplyScalar(1) });
    } else {
      o.material = new MeshToonMaterial({
        color: src.color.clone(),
        gradientMap: ramp,
        emissive: emissiveStrength > 0 ? src.emissive.clone() : new Color(0, 0, 0),
        emissiveIntensity: emissiveStrength > 0 ? Math.min(0.6, src.emissiveIntensity) : 0,
      });
    }
    o.castShadow = true;
    o.receiveShadow = true;
  });
}

export function KitBuilding({
  kit,
  place,
  rotation,
  onClick,
  selected,
}: {
  kit: KitId;
  place: PlaceId;
  rotation: number;
  onClick?: (place: PlaceId) => void;
  selected?: boolean;
}) {
  const { scene } = useGLTF(KIT_URLS[kit]);
  const gl = useThree((s) => s.gl);
  const [hover, setHover] = useState(false);
  const ramp = useMemo(createToonRamp, []);
  const instance = useMemo(() => {
    const clone = SkeletonUtils.clone(scene);
    restyle(clone, ramp);
    return clone;
  }, [scene, ramp]);

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

  const { map, content } = buildIsland();
  const [tx, tz] = content.places[place].tile;
  const [x, z] = tileToWorld(tx, tz);
  const y = groundY(map, x, z);

  const click = (e: ThreeEvent<MouseEvent>) => {
    if (!onClick) return;
    e.stopPropagation();
    if (useCameraStore.getState().dragging) return;
    onClick(place);
  };

  return (
    <group
      position={[x, y, z]}
      rotation={[0, rotation, 0]}
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
          <ringGeometry args={[9.6, 10.2, 48]} />
          <meshBasicMaterial color={selected ? "#ffd166" : "#fffaf0"} transparent opacity={0.85} />
        </mesh>
      )}
    </group>
  );
}

useGLTF.preload(pluginDocksUrl);
