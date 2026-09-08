/**
 * One set of shared geometries and materials per mounted stage, handed to
 * every primitive-built object through context — a house, a tree and a lamp
 * post all draw from the same dozen materials, which keeps program switches
 * and GPU memory flat however many objects the island grows.
 */
import { createContext, useContext, useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import { useFrame } from "@react-three/fiber";
import { Color, Group, InstancedMesh, Object3D } from "three";

import { useWorldGeometries, useWorldMaterials, type WorldGeometries, type WorldMaterials } from "./worldMaterials";

export interface Kit {
  g: WorldGeometries;
  m: WorldMaterials;
  blocks: Map<Object3D, { color: string; glow: boolean }>;
}

const KitContext = createContext<Kit | null>(null);

export function WorldKitProvider({ children }: { children: ReactNode }) {
  const g = useWorldGeometries();
  const m = useWorldMaterials();
  const blocks = useMemo(() => new Map<Object3D, { color: string; glow: boolean }>(), []);
  const kit = useMemo(() => ({ g, m, blocks }), [g, m, blocks]);
  return <KitContext.Provider value={kit}>{children}<BlockBatch kit={kit} /></KitContext.Provider>;
}

export function useKit(): Kit {
  const kit = useContext(KitContext);
  if (!kit) throw new Error("useKit outside WorldKitProvider");
  return kit;
}

/** A box helper: centre (x, y, z), size (w, h, d), colour. */
export function Block({
  kit,
  at,
  size,
  color,
  glow = false,
  rotation,
}: {
  kit: Kit;
  at: [number, number, number];
  size: [number, number, number];
  color: string;
  glow?: boolean;
  rotation?: [number, number, number];
}) {
  const ref = useRef<Group>(null);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    kit.blocks.set(node, { color, glow });
    return () => { kit.blocks.delete(node); };
  }, [kit.blocks, color, glow]);
  return <group ref={ref} position={at} scale={size} rotation={rotation} />;
}

/** Static architectural blocks share two instanced draws, including transforms. */
function BlockBatch({ kit }: { kit: Kit }) {
  const lit = useRef<InstancedMesh>(null), glow = useRef<InstancedMesh>(null);
  const color = useMemo(() => new Color(), []);
  useFrame(() => {
    if (!lit.current || !glow.current) return;
    let nl = 0, ng = 0;
    for (const [node, data] of kit.blocks) {
      node.updateWorldMatrix(true, false);
      const mesh = data.glow ? glow.current : lit.current;
      const i = data.glow ? ng++ : nl++;
      if (i >= 2048) throw new Error("World block batch exceeds the authored capacity");
      mesh.setMatrixAt(i, node.matrixWorld);
      mesh.setColorAt(i, color.set(data.color));
    }
    lit.current.count = nl; glow.current.count = ng;
    for (const mesh of [lit.current, glow.current]) {
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
  }, .5);
  return <>
    <instancedMesh ref={lit} args={[kit.g.box, kit.m.lit("#ffffff"), 2048]} frustumCulled={false} castShadow receiveShadow />
    <instancedMesh ref={glow} args={[kit.g.box, kit.m.glow("#ffffff"), 2048]} frustumCulled={false} />
  </>;
}
