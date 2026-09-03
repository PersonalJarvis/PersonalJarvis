/**
 * Shared materials and geometries for everything built from primitives.
 * One Lambert material per palette colour (flat, chunky lighting — never PBR
 * on a 16-colour world, character-pipeline.md §9.3), created once per stage
 * mount and disposed with it.
 */
import { useEffect, useMemo } from "react";
import {
  AdditiveBlending,
  BoxGeometry,
  CircleGeometry,
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
  type BufferGeometry,
} from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

import { BUILDING, CEREMONY, NATURE } from "./worldPalette";

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
  /** Additive, translucent: light pools, beams, halos — the cheap "lighting effect". */
  halo: (hex: string, opacity?: number) => MeshBasicMaterial;
  dispose: () => void;
}

/** Build the material cache. Every colour is created at most once. */
export function createWorldMaterials(): WorldMaterials {
  const lit = new Map<string, MeshToonMaterial>();
  const glow = new Map<string, MeshBasicMaterial>();
  const halo = new Map<string, MeshBasicMaterial>();
  const ramp = createToonRamp();
  return {
    halo: (hex, opacity = 0.35) => {
      const key = `${hex}@${opacity}`;
      let m = halo.get(key);
      if (!m) {
        m = new MeshBasicMaterial({
          color: new Color(hex),
          transparent: true,
          opacity,
          blending: AdditiveBlending,
          depthWrite: false,
        });
        halo.set(key, m);
      }
      return m;
    },
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
      for (const m of halo.values()) m.dispose();
      ramp.dispose();
      lit.clear();
      glow.clear();
      halo.clear();
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
  /** A conifer crown: two stacked cones, unit height, for the forest and the alpine slopes. */
  pine: BufferGeometry;
  /** A palm crown: six drooping fronds around the trunk top, unit radius. */
  palmCrown: BufferGeometry;
  /** A flat unit disc facing +y, for light pools on the ground. */
  disc: CircleGeometry;
  dispose: () => void;
}

function makePine(): BufferGeometry {
  const lower = new CylinderGeometry(0, 0.5, 0.62, 7).translate(0, 0.31, 0);
  const upper = new CylinderGeometry(0, 0.36, 0.55, 7).translate(0, 0.72, 0);
  const merged = mergeGeometries([lower, upper], false);
  lower.dispose();
  upper.dispose();
  return merged ?? lower;
}

function makePalmCrown(): BufferGeometry {
  const fronds: BufferGeometry[] = [];
  for (let k = 0; k < 6; k++) {
    const frond = new BoxGeometry(0.28, 0.06, 1.0)
      .translate(0, 0, 0.5)
      .rotateX(0.42)
      .rotateY((k / 6) * Math.PI * 2 + 0.3);
    fronds.push(frond);
  }
  const merged = mergeGeometries(fronds, false);
  fronds.forEach((f) => f.dispose());
  return merged ?? fronds[0];
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
  const pine = makePine();
  const palmCrown = makePalmCrown();
  const disc = new CircleGeometry(1, 20).rotateX(-Math.PI / 2);
  const all = [box, slab, cylinder, halfCylinder, cone, blob, sphere, dome, ring, pine, palmCrown, disc];
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
    pine,
    palmCrown,
    disc,
    dispose: () => all.forEach((g) => g.dispose()),
  };
}

export function useWorldGeometries(): WorldGeometries {
  const geometries = useMemo(createWorldGeometries, []);
  useEffect(() => () => geometries.dispose(), [geometries]);
  return geometries;
}

/** The palette entries most components reach for, re-exported for brevity. */
export const PAL = { ...BUILDING, ...NATURE, ...CEREMONY };
