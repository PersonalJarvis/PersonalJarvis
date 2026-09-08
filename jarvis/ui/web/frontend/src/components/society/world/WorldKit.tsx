/**
 * One set of shared geometries and materials per mounted stage, handed to
 * every primitive-built object through context — a house, a tree and a lamp
 * post all draw from the same dozen materials, which keeps program switches
 * and GPU memory flat however many objects the island grows.
 */
import { createContext, useContext, type ReactNode } from "react";

import { useWorldGeometries, useWorldMaterials, type WorldGeometries, type WorldMaterials } from "./worldMaterials";

export interface Kit {
  g: WorldGeometries;
  m: WorldMaterials;
}

const KitContext = createContext<Kit | null>(null);

export function WorldKitProvider({ children }: { children: ReactNode }) {
  const g = useWorldGeometries();
  const m = useWorldMaterials();
  return <KitContext.Provider value={{ g, m }}>{children}</KitContext.Provider>;
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
  return (
    <mesh
      geometry={kit.g.box}
      material={glow ? kit.m.glow(color) : kit.m.lit(color)}
      position={at}
      scale={size}
      rotation={rotation}
    />
  );
}
