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

import agentFoundryUrl from "@/assets/society/world/kit/agent-foundry.glb";
import controlRoomUrl from "@/assets/society/world/kit/control-room.glb";
import galleryHallUrl from "@/assets/society/world/kit/gallery-hall.glb";
import modelBoilerhouseUrl from "@/assets/society/world/kit/model-boilerhouse.glb";
import observatoryUrl from "@/assets/society/world/kit/observatory.glb";
import pluginDocksUrl from "@/assets/society/world/kit/plugin-docks.glb";
import relayTowerUrl from "@/assets/society/world/kit/relay-tower.glb";
import signalOfficeUrl from "@/assets/society/world/kit/signal-office.glb";
import skillForgeUrl from "@/assets/society/world/kit/skill-forge.glb";
import terminalCantinaUrl from "@/assets/society/world/kit/terminal-cantina.glb";
import townHallUrl from "@/assets/society/world/kit/town-hall.glb";
import { useBuildingPoses, useBuildingYaw } from "./buildingPoses";
import { useCameraStore } from "./cameraStore";
import { buildIsland, groundY, kitId, type KitPlace } from "./islandLayout";
import { RotateHandle } from "./RotateHandle";
import { createToonRamp } from "./worldMaterials";

/** Every kit file the registry knows. Adding a building = adding a row. */
export const KIT_URLS = {
  "plugin-docks": pluginDocksUrl,
  "skill-forge": skillForgeUrl,
  "relay-tower": relayTowerUrl,
  "terminal-cantina": terminalCantinaUrl,
  "agent-foundry": agentFoundryUrl,
  "signal-office": signalOfficeUrl,
  "control-room": controlRoomUrl,
  "gallery-hall": galleryHallUrl,
  "model-boilerhouse": modelBoilerhouseUrl,
  "town-hall": townHallUrl,
  observatory: observatoryUrl,
} as const;
export type KitId = keyof typeof KIT_URLS;

/** Radius of the hover/selection ring drawn under each kit, in metres. */
const KIT_RING_R: Record<KitId, number> = {
  "plugin-docks": 9.9,
  "agent-foundry": 13.6,
  "skill-forge": 8.6,
  "relay-tower": 6.4,
  "terminal-cantina": 8.8,
  "signal-office": 8.6,
  "control-room": 8.6,
  "gallery-hall": 8.6,
  "model-boilerhouse": 7.0,
  "town-hall": 8.8,
  observatory: 5.8,
};

/** Which kit file stands at which kit place; the pose comes from the island layout. */
export const KIT_PLACEMENTS: ReadonlyArray<{ kit: KitId; place: KitPlace }> = [
  { kit: "plugin-docks", place: "plugins" },
  { kit: "agent-foundry", place: "foundry" },
  { kit: "skill-forge", place: "skills" },
  { kit: "relay-tower", place: "mcp" },
  { kit: "terminal-cantina", place: "cli" },
  { kit: "signal-office", place: "comms" },
  { kit: "control-room", place: "desktop" },
  { kit: "gallery-hall", place: "gallery" },
  { kit: "model-boilerhouse", place: "models" },
  { kit: "town-hall", place: "civic" },
  { kit: "observatory", place: "web" },
];

/** Emission above this strength renders unlit (a lamp, a neon tube), below it stays a lit toon. */
const GLOW_STRENGTH = 1.5;

/**
 * Restyle a kit GLB's Standard materials to the island's toon ramp. Shared
 * with the building card's viewer, so a building looks the same on its card
 * as on the island.
 */
export function restyle(root: Object3D, ramp: ReturnType<typeof createToonRamp>): void {
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
  onClick,
  selected,
  onInstance,
}: {
  kit: KitId;
  place: KitPlace;
  onClick?: (place: KitPlace) => void;
  selected?: boolean;
  /** The restyled clone, once per load — a building animates its own nodes. */
  onInstance?: (root: Object3D) => void;
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

  useEffect(() => {
    onInstance?.(instance);
  }, [instance, onInstance]);

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
  const { x, z } = content.kitPoses[place];
  // The heading: the designed one unless the viewer turned the building.
  const rotation = useBuildingYaw(kitId(place));
  const y = groundY(map, x, z);

  const click = (e: ThreeEvent<MouseEvent>) => {
    if (!onClick) return;
    e.stopPropagation();
    if (useCameraStore.getState().dragging || useBuildingPoses.getState().rotating) return;
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
          <ringGeometry args={[KIT_RING_R[kit] - 0.3, KIT_RING_R[kit] + 0.3, 48]} />
          <meshBasicMaterial color={selected ? "#ffd166" : "#fffaf0"} transparent opacity={0.85} />
        </mesh>
      )}
      {selected && <RotateHandle id={kitId(place)} x={x} y={y} z={z} radius={KIT_RING_R[kit]} />}
    </group>
  );
}

useGLTF.preload(pluginDocksUrl);
useGLTF.preload(agentFoundryUrl);
