/** Authored assets: static geometry batches, with independent moving metro parts. */
import { memo, useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { useGLTF } from "@react-three/drei";
import {
  Group,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshStandardMaterial,
  Plane,
  Quaternion,
  Vector3,
  type Material,
  type Object3D,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { Vec3 } from "./cityModel";
import { trainAt, type CitySimulation } from "./citySimulation";
import { releaseCityInstances } from "./cityInstances";
import station from "../../../../../../../../art/studies/city-realism-study/exports/station.glb";
import terminal from "../../../../../../../../art/studies/city-realism-study/exports/building.glb";
import central from "../../../../../../../../art/studies/city-realism-study/exports/central.glb";
import knowledge from "../../../../../../../../art/studies/city-realism-study/exports/knowledge.glb";
import workshop from "../../../../../../../../art/studies/city-realism-study/exports/fabrication.glb";
import communications from "../../../../../../../../art/studies/city-realism-study/exports/communications.glb";
import office from "../../../../../../../../art/studies/city-realism-study/exports/skyline-office.glb";
import tower from "../../../../../../../../art/studies/city-realism-study/exports/skyline-tower.glb";
import residence from "../../../../../../../../art/studies/city-realism-study/exports/skyline-residence.glb";
import lamp from "../../../../../../../../art/studies/city-realism-study/exports/streetlamp.glb";
import tree from "../../../../../../../../art/studies/city-realism-study/exports/tree.glb";
import metro from "../../../../../../../../art/studies/city-realism-study/exports/train.glb";
export const CITY_ASSETS = {
  station,
  terminal,
  central,
  knowledge,
  workshop,
  communications,
  office,
  tower,
  residence,
  lamp,
  tree,
  metro,
};
export interface InstancePose {
  position: Vec3;
  yaw?: number;
  scale?: number;
}

function prepare(scene: Object3D) {
  scene.updateMatrixWorld(true);
  const groups = new Map<Material, Mesh[]>();
  scene.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    if (Array.isArray(object.material))
      throw new Error("City GLBs must export one material per mesh primitive");
    const meshes = groups.get(object.material) ?? [];
    meshes.push(object);
    groups.set(object.material, meshes);
  });
  return [...groups].map(([source, meshes]) => {
    const pieces = meshes.map((mesh) =>
      mesh.geometry.clone().applyMatrix4(mesh.matrixWorld),
    );
    const geometry = mergeGeometries(pieces);
    pieces.forEach((piece) => piece.dispose());
    if (!geometry) throw new Error("Incompatible city mesh attributes");
    return { geometry, material: source.clone() };
  });
}
export const CityAsset = memo(function CityAsset({
  url,
  position,
  yaw = 0,
  onClick,
  working = false,
  cutaway = false,
}: {
  url: string;
  position: Vec3;
  yaw?: number;
  onClick?: () => void;
  working?: boolean;
  cutaway?: boolean;
}) {
  const { scene } = useGLTF(url);
  const invalidate = useThree((state) => state.invalidate);
  const parts = useMemo(() => prepare(scene), [scene]);
  useEffect(
    () => () => {
      parts.forEach((part) => {
        part.geometry.dispose();
        part.material.dispose();
      });
    },
    [parts],
  );
  useEffect(() => {
    for (const { material } of parts)
      if (
        material instanceof MeshStandardMaterial &&
        /screen/i.test(material.name)
      )
        material.emissiveIntensity = working ? 2 : 0.5;
  }, [working, parts]);
  useEffect(() => {
    const planes = cutaway
      ? [new Plane(new Vector3(0, -1, 0), position[1] + 4.8)]
      : [];
    for (const { material } of parts) {
      material.clippingPlanes = planes;
      material.needsUpdate = true;
    }
    invalidate();
  }, [cutaway, parts, position, invalidate]);
  return (
    <group
      position={position}
      rotation={[0, yaw, 0]}
      onClick={
        onClick
          ? (event) => {
              // Raycasting sees geometry that the shader clipped away; let those hits pass through.
              if (cutaway && event.point.y > position[1] + 4.8) return;
              event.stopPropagation();
              onClick();
            }
          : undefined
      }
    >
      {parts.map((part, index) => (
        <mesh
          key={index}
          geometry={part.geometry}
          material={part.material}
          castShadow
          receiveShadow
          dispose={null}
        />
      ))}
    </group>
  );
});

/** One preparation per asset family, independent instances, no copies per tree/window. */
export const CityInstances = memo(function CityInstances({
  url,
  poses,
  shadows = false,
}: {
  url: string;
  poses: InstancePose[];
  shadows?: boolean;
}) {
  const { scene } = useGLTF(url);
  const parts = useMemo(() => prepare(scene), [scene]);
  const group = useMemo(() => {
    const result = new Group();
    const matrix = new Matrix4();
    const quaternion = new Quaternion();
    const scale = new Vector3();
    const position = new Vector3();
    for (const part of parts) {
      const mesh = new InstancedMesh(
        part.geometry,
        part.material,
        poses.length,
      );
      poses.forEach((pose, i) => {
        quaternion.setFromAxisAngle(new Vector3(0, 1, 0), pose.yaw ?? 0);
        scale.setScalar(pose.scale ?? 1);
        position.set(...pose.position);
        matrix.compose(position, quaternion, scale);
        mesh.setMatrixAt(i, matrix);
      });
      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
      mesh.castShadow = shadows;
      mesh.receiveShadow = shadows;
      result.add(mesh);
    }
    return result;
  }, [parts, poses, shadows]);
  useEffect(
    () => () => {
      group.traverse((object) => {
        if (object instanceof InstancedMesh) releaseCityInstances(object);
      });
    },
    [group],
  );
  useEffect(
    () => () => {
      parts.forEach((part) => {
        part.geometry.dispose();
        part.material.dispose();
      });
    },
    [parts],
  );
  return <primitive object={group} dispose={null} />;
});

export function Metro({
  sim,
  paused,
  follow,
}: {
  sim: CitySimulation;
  paused: boolean;
  follow: string | null;
}) {
  const { scene } = useGLTF(metro);
  const root = useRef<Group>(null);
  const clone = useMemo(() => {
    const object = scene.clone(true);
    const materials = new Map<Material, Material>();
    object.traverse((child) => {
      if (!(child instanceof Mesh)) return;
      const original = child.material as Material;
      if (!materials.has(original)) {
        const material = original.clone();
        if (
          material instanceof MeshStandardMaterial &&
          material.name === "Glazing"
        ) {
          material.transparent = true;
          material.opacity = 0.42;
          material.depthWrite = false;
        }
        materials.set(original, material);
      }
      child.material = materials.get(original)!;
    });
    return object;
  }, [scene]);
  useEffect(
    () => () => {
      const materials = new Set<Material>();
      clone.traverse((child) => {
        if (child instanceof Mesh) materials.add(child.material as Material);
      });
      materials.forEach((m) => m.dispose());
    },
    [clone],
  );
  const doors = useMemo(
    () =>
      ["Door_Platform_Left", "Door_Platform_Right"]
        .map((name, i) => {
          const object = clone.getObjectByName(name);
          return object
            ? { object, x: object.position.x, sign: i ? 1 : -1 }
            : null;
        })
        .filter((door): door is NonNullable<typeof door> => door !== null),
    [clone],
  );
  const opening = useRef(trainAt(sim.time).doorsOpen ? 1 : 0);
  const interior = useRef(false);
  const plane = useMemo(() => new Plane(new Vector3(0, -1, 0), 14.5), []);
  useFrame((_, dt) => {
    const state = trainAt(sim.time);
    const passenger = follow ? sim.citizens.get(follow) : null;
    const inspect = passenger?.seat !== null && passenger?.seat !== undefined;
    if (inspect !== interior.current) {
      interior.current = inspect;
      clone.traverse((object) => { if (object instanceof Mesh) { const material = object.material as Material; material.clippingPlanes = inspect ? [plane] : []; material.needsUpdate = true; } });
    }
    if (root.current) {
      root.current.position.set(...state.position);
      root.current.rotation.y = state.heading;
    }
    const target = state.doorsOpen ? 1 : 0;
    if (!paused)
      opening.current +=
        Math.sign(target - opening.current) *
        Math.min(Math.abs(target - opening.current), dt * 3);
    for (const door of doors)
      door.object.position.x = door.x + door.sign * opening.current * 1.25;
  });
  return (
    <group ref={root}>
      <primitive object={clone} dispose={null} />
    </group>
  );
}
