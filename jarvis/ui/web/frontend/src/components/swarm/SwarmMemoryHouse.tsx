import { useMemo } from "react";
import { Html, useGLTF } from "@react-three/drei";
import { Box3, Vector3, type Color } from "three";
import memoryHouseUrl from "@/assets/society/world/kit/memory-house.glb";
import { exactCount } from "./types";
import { useSwarmText } from "./strings";
import type { Point } from "./worldLayout";

/** A Swarm-owned placement of the shipped asset, with no Society state or motion. */
export function SwarmMemoryHouse({ position, accent, artifacts, publications }: {
  position: Point; accent: Color; artifacts: string; publications: string;
}) {
  const { scene } = useGLTF(memoryHouseUrl);
  const t = useSwarmText();
  const { instance, scale, offset } = useMemo(() => {
    const instance = scene.clone(true);
    const bounds = new Box3().setFromObject(instance);
    const center = bounds.getCenter(new Vector3());
    const size = bounds.getSize(new Vector3());
    const scale = 2.6 / Math.max(size.x, size.y, size.z, .01);
    return { instance, scale, offset: [-center.x * scale, -bounds.min.y * scale, -center.z * scale] as Point };
  }, [scene]);
  return <group position={position}>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, .025, 0]}>
      <ringGeometry args={[1.5, 1.56, 40]} /><meshBasicMaterial color={accent} transparent opacity={.4} />
    </mesh>
    {/* The GLTF cache owns the shared geometry and materials; dispose neither. */}
    <group position={offset}><primitive object={instance} scale={scale} dispose={null} /></group>
    <Html position={[0, 3.15, 0]} center style={{ pointerEvents: "none" }}>
      <div className="swarm-world-label swarm-memory-label"><strong>{t("memoryHouse")}</strong>
        <span>{exactCount(artifacts)} {t("artifactCount")} · {exactCount(publications)} {t("publicationCount")}</span>
      </div>
    </Html>
  </group>;
}
