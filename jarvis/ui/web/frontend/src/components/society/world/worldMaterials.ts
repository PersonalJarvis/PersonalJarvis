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
  DataTexture,
  IcosahedronGeometry,
  MeshBasicMaterial,
  MeshToonMaterial,
  NearestFilter,
  RGBAFormat,
  SphereGeometry,
  TorusGeometry,
} from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

import { BUILDING, NATURE } from "./worldPalette";

/**
 * The four-step light ramp every lit object shares (world-masterplan-v2.md §3.4):
 * a deep shade, a mid tone, a lit face and a bright top. Toon shading with a
 * ramp is the Clash-of-Clans look — one saturated hue per material, light
 * carried by steps instead of by a smooth gradient.
 */
export function createToonRamp(): DataTexture {
  const steps = [0.58, 0.8, 0.94, 1.0];
  const data = new Uint8Array(steps.length * 4);
  steps.forEach((v, i) => {
    const b = Math.round(v * 255);
    data.set([b, b, b, 255], i * 4);
  });
  const tex = new DataTexture(data, steps.length, 1, RGBAFormat);
  tex.minFilter = NearestFilter;
  tex.magFilter = NearestFilter;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  return tex;
}

export interface WorldMaterials {
  lit: (hex: string) => MeshToonMaterial;
  glow: (hex: string) => MeshBasicMaterial;
  dispose: () => void;
}

/** Build the material cache. Every colour is created at most once. */
export function createWorldMaterials(): WorldMaterials {
  const lit = new Map<string, MeshToonMaterial>();
  const glow = new Map<string, MeshBasicMaterial>();
  const ramp = createToonRamp();
  return {
    lit: (hex) => {
      let m = lit.get(hex);
      if (!m) {
        m = new MeshToonMaterial({ color: new Color(hex), gradientMap: ramp });
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
      ramp.dispose();
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
  /** A unit box with a small bevel — every edge catches the light (§2 shape language). */
  box: RoundedBoxGeometry;
  /** The sharp unit box, for thin slabs where a bevel would smear. */
  slab: BoxGeometry;
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
  const box = new RoundedBoxGeometry(1, 1, 1, 2, 0.05);
  const slab = new BoxGeometry(1, 1, 1);
  const cylinder = new CylinderGeometry(0.5, 0.5, 1, 10);
  const halfCylinder = new CylinderGeometry(0.5, 0.5, 1, 10, 1, false, 0, Math.PI);
  const cone = new CylinderGeometry(0, 0.5, 1, 8);
  const blob = new IcosahedronGeometry(0.5, 0); // 20 faces: chunky canopies under the pixel pass
  const sphere = new SphereGeometry(0.5, 10, 8);
  const dome = new SphereGeometry(0.5, 12, 6, 0, Math.PI * 2, 0, Math.PI / 2);
  const ring = new TorusGeometry(0.5, 0.06, 6, 24);
  const all = [box, slab, cylinder, halfCylinder, cone, blob, sphere, dome, ring];
  return {
    box,
    slab,
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
