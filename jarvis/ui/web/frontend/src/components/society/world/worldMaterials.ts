/**
 * Shared materials and geometries for everything built from primitives.
 * One Lambert material per palette colour (flat, chunky lighting — never PBR
 * on a 16-colour world, character-pipeline.md §9.3), created once per stage
 * mount and disposed with it.
 */
import { useEffect, useMemo } from "react";
import {
  BoxGeometry,
  Color,
  CylinderGeometry,
  IcosahedronGeometry,
  MeshBasicMaterial,
  MeshLambertMaterial,
  SphereGeometry,
  TorusGeometry,
} from "three";

import { BUILDING, NATURE } from "./worldPalette";

export interface WorldMaterials {
  lit: (hex: string) => MeshLambertMaterial;
  glow: (hex: string) => MeshBasicMaterial;
  dispose: () => void;
}

/** Build the material cache. Every colour is created at most once. */
export function createWorldMaterials(): WorldMaterials {
  const lit = new Map<string, MeshLambertMaterial>();
  const glow = new Map<string, MeshBasicMaterial>();
  return {
    lit: (hex) => {
      let m = lit.get(hex);
      if (!m) {
        m = new MeshLambertMaterial({ color: new Color(hex) });
        lit.set(hex, m);
      }
      return m;
    },
    glow: (hex) => {
      let m = glow.get(hex);
      if (!m) {
        m = new MeshBasicMaterial({ color: new Color(hex) });
        glow.set(hex, m);
      }
      return m;
    },
    dispose: () => {
      for (const m of lit.values()) m.dispose();
      for (const m of glow.values()) m.dispose();
      lit.clear();
      glow.clear();
    },
  };
}

export function useWorldMaterials(): WorldMaterials {
  const materials = useMemo(createWorldMaterials, []);
  useEffect(() => () => materials.dispose(), [materials]);
  return materials;
}

/** Unit geometries, scaled per use. Shared by every house, tree and lamp. */
export interface WorldGeometries {
  box: BoxGeometry;
  cylinder: CylinderGeometry;
  halfCylinder: CylinderGeometry;
  cone: CylinderGeometry;
  blob: IcosahedronGeometry;
  sphere: SphereGeometry;
  dome: SphereGeometry;
  ring: TorusGeometry;
  dispose: () => void;
}

export function createWorldGeometries(): WorldGeometries {
  const box = new BoxGeometry(1, 1, 1);
  const cylinder = new CylinderGeometry(0.5, 0.5, 1, 10);
  const halfCylinder = new CylinderGeometry(0.5, 0.5, 1, 10, 1, false, 0, Math.PI);
  const cone = new CylinderGeometry(0, 0.5, 1, 8);
  const blob = new IcosahedronGeometry(0.5, 0); // 20 faces: chunky canopies under the pixel pass
  const sphere = new SphereGeometry(0.5, 10, 8);
  const dome = new SphereGeometry(0.5, 12, 6, 0, Math.PI * 2, 0, Math.PI / 2);
  const ring = new TorusGeometry(0.5, 0.06, 6, 24);
  const all = [box, cylinder, halfCylinder, cone, blob, sphere, dome, ring];
  return {
    box,
    cylinder,
    halfCylinder,
    cone,
    blob,
    sphere,
    dome,
    ring,
    dispose: () => all.forEach((g) => g.dispose()),
  };
}

export function useWorldGeometries(): WorldGeometries {
  const geometries = useMemo(createWorldGeometries, []);
  useEffect(() => () => geometries.dispose(), [geometries]);
  return geometries;
}

/** The palette entries most components reach for, re-exported for brevity. */
export const PAL = { ...BUILDING, ...NATURE };
