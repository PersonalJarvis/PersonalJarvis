/** One inexpensive contact-shadow batch grounds buildings and moving figures. */
import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { CircleGeometry, InstancedMesh, MeshBasicMaterial, Object3D } from "three";
import { buildIsland, groundY } from "./islandLayout";
import { getMotionWorld } from "./locomotion";
import { BUILDING_ASSETS } from "./worldManifest";
import { BUILDING } from "./worldPalette";

export function ContactShadows() {
  const mesh = useRef<InstancedMesh>(null);
  const built = useMemo(() => ({
    geometry: new CircleGeometry(1, 16).rotateX(-Math.PI / 2),
    material: new MeshBasicMaterial({ color: BUILDING.solar, transparent: true, opacity: .16, depthWrite: false,
      polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }),
    dummy: new Object3D(),
  }), []);
  useEffect(() => () => { built.geometry.dispose(); built.material.dispose(); }, [built]);
  useFrame(() => {
    if (!mesh.current) return;
    const { map, content } = buildIsland(); let i = 0;
    const put = (x: number, z: number, w: number, d: number, yaw: number) => {
      if (i >= 128) return;
      built.dummy.position.set(x, groundY(map, x, z) + .024, z);
      built.dummy.rotation.set(0, yaw, 0); built.dummy.scale.set(w, 1, d); built.dummy.updateMatrix();
      mesh.current!.setMatrixAt(i++, built.dummy.matrix);
    };
    for (const h of content.houses) put(h.x, h.z, h.w * 1.17, h.d * 1.17, h.rotation);
    for (const [id, p] of Object.entries(content.kitPoses)) {
      const a = BUILDING_ASSETS[id as keyof typeof BUILDING_ASSETS];
      put(p.x, p.z, a.sizeM[0] * .60, a.sizeM[1] * .60, p.rotation);
    }
    for (const a of getMotionWorld()?.actors.values() ?? []) if (!a.hidden) put(a.x, a.z, a.radius * .74, a.radius * .52, a.heading);
    mesh.current.count = i; mesh.current.instanceMatrix.needsUpdate = true;
  });
  return <instancedMesh ref={mesh} args={[built.geometry, built.material, 128]} frustumCulled={false} />;
}
