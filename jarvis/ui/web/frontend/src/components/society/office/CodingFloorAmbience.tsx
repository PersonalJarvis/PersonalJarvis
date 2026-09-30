/**
 * The coding floor's ambience between the rooms: tree planters closing the
 * cross aisles at the windows, a low library of walnut bookcases with reading
 * lamps along the west glass, planter troughs along the east glass, slim
 * linear pendants floating over every bench with warm pools of light below,
 * and one warm fill light for the whole floor.
 *
 * Every repeated part is one instanced mesh, so the whole dressing costs a
 * handful of draw calls. The solid pieces come from `codingAmbience`, which
 * the layout also adds to its obstacles; the pendants and light pools are out
 * of reach (overhead, or flat on the floor). The pendants hide whenever the
 * camera comes down to their height, so they never block a close view.
 */
import { useLayoutEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import {
  AdditiveBlending, Color, Euler, Group, InstancedMesh, Matrix4, MeshBasicMaterial, PlaneGeometry, Quaternion, Vector3,
  type BufferGeometry, type Material,
} from "three";
import { cachedCanvasTexture } from "./canvasMaterials";
import { codingAmbience, LIBRARY_H, TROUGH_H, type AmbienceItem } from "./codingAmbience";
import { GEO, MAT, matte } from "./OfficeFurniture";
import type { Department, OfficeLayout } from "./officeLayout";
import { CODING_SCENE } from "./officePalette";

// ---------------------------------------------------------------------------
// Instancing
// ---------------------------------------------------------------------------

export interface Placement {
  position: [number, number, number];
  scale: [number, number, number];
  /** Rotation (x, y, z) in radians; absent = none. */
  rotation?: [number, number, number];
  color?: string;
}

const _m = new Matrix4(), _q = new Quaternion(), _e = new Euler(), _p = new Vector3(), _s = new Vector3(), _c = new Color();

/** One instanced mesh for every placement of a part (the agents floor's ambience uses it too). */
export function Instances({ geometry, material, items, cast = true }: {
  geometry: BufferGeometry; material: Material; items: readonly Placement[]; cast?: boolean;
}) {
  const ref = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    items.forEach((item, i) => {
      _e.set(...(item.rotation ?? [0, 0, 0]));
      _m.compose(_p.set(...item.position), _q.setFromEuler(_e), _s.set(...item.scale));
      mesh.setMatrixAt(i, _m);
      if (item.color) mesh.setColorAt(i, _c.set(item.color));
    });
    mesh.count = items.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [items]);
  if (items.length === 0) return null;
  return <instancedMesh key={items.length} ref={ref} args={[geometry, material, items.length]} castShadow={cast} receiveShadow frustumCulled={false} />;
}

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

const AMB = {
  clay: matte("#b9694b", { roughness: 0.75 }),
  cream: matte("#efe7da", { roughness: 0.6 }),
  walnut: matte(CODING_SCENE.walnut.slat, { roughness: 0.6 }),
  walnutDark: matte(CODING_SCENE.walnut.gap, { roughness: 0.7 }),
  brass: matte(CODING_SCENE.brass, { roughness: 0.3, metalness: 0.55 }),
  shade: matte("#fff1d6", { emissive: "#ffd79a", emissiveIntensity: 0.9, roughness: 0.6 }),
  /** White, so each instance's colour is the book's colour. */
  book: matte("#ffffff", { roughness: 0.8 }),
  olive: matte("#6f8a58", { flatShading: true }),
  pendant: matte(CODING_SCENE.pendant.body, { roughness: 0.4, metalness: 0.4 }),
  pendantGlow: matte(CODING_SCENE.pendant.glow, { emissive: CODING_SCENE.pendant.glow, emissiveIntensity: 2, toneMapped: false }),
};

/** Book spines: muted cloth and paper tones, warm like an old library. */
const SPINES = ["#8c3b2b", "#c8553d", "#e0b25c", "#3e5c76", "#5e7d5a", "#e9e0cf", "#2f2a26", "#b08a5a", "#7a5a8c", "#d9c7a4"];

/** Deterministic pseudo-random numbers per seed, so the dressing never changes between loads. */
function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

function seedOf(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i += 1) h = Math.imul(h ^ id.charCodeAt(i), 16777619);
  return h >>> 0;
}

// ---------------------------------------------------------------------------
// Trees
// ---------------------------------------------------------------------------

/** Crown clusters of a tree in its own space (x, y, z, radius) and which leaf tone each takes. */
const CROWN: readonly [number, number, number, number, 0 | 1 | 2 | 3][] = [
  [0, 1.95, 0, 0.36, 0], [0.24, 1.72, 0.1, 0.27, 1], [-0.22, 1.78, -0.08, 0.28, 2], [0.05, 2.18, 0.08, 0.26, 3],
  [-0.1, 1.55, 0.2, 0.22, 1], [0.16, 2.02, -0.2, 0.24, 2], [-0.2, 2.06, 0.16, 0.22, 0], [0.12, 1.5, -0.14, 0.2, 3],
];
const LEAVES = [MAT.leaf, MAT.leafDark, MAT.leafLight, AMB.olive];

/** A tall tree in a square clay planter with a cream rim, filling its TREE_BOX footprint. */
function Trees({ items }: { items: readonly AmbienceItem[] }) {
  const parts = useMemo(() => {
    const planter: Placement[] = [], rim: Placement[] = [], soil: Placement[] = [], trunk: Placement[] = [];
    const leaves: Placement[][] = LEAVES.map(() => []);
    for (const item of items) {
      const x = (item.minX + item.maxX) / 2, z = (item.minZ + item.maxZ) / 2, size = item.maxX - item.minX;
      const rand = lcg(seedOf(item.id));
      const turn = rand() * Math.PI * 2, s = 0.9 + rand() * 0.18;
      planter.push({ position: [x, 0.27, z], scale: [size - 0.06, 0.54, size - 0.06] });
      rim.push({ position: [x, 0.555, z], scale: [size, 0.03, size] });
      soil.push({ position: [x, 0.572, z], scale: [size - 0.14, 0.006, size - 0.14] });
      trunk.push({ position: [x, 1.1, z], scale: [0.045, 1.1, 0.045], rotation: [0.04, 0, -0.05] });
      for (const [cx, cy, cz, r, tone] of CROWN) {
        const lx = cx * Math.cos(turn) - cz * Math.sin(turn), lz = cx * Math.sin(turn) + cz * Math.cos(turn);
        leaves[tone].push({ position: [x + lx * s, 0.1 + cy * s, z + lz * s], scale: [r * s, r * s * 0.9, r * s] });
      }
    }
    return { planter, rim, soil, trunk, leaves };
  }, [items]);
  return (
    <group>
      <Instances geometry={GEO.box} material={AMB.clay} items={parts.planter} />
      <Instances geometry={GEO.box} material={AMB.cream} items={parts.rim} />
      <Instances geometry={GEO.box} material={MAT.soil} items={parts.soil} cast={false} />
      <Instances geometry={GEO.cyl} material={MAT.trunk} items={parts.trunk} />
      {parts.leaves.map((list, i) => <Instances key={i} geometry={GEO.blob} material={LEAVES[i]} items={list} />)}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Library along the west glass
// ---------------------------------------------------------------------------

/**
 * Low walnut bookcases facing into the office: a carcass with two shelves of
 * books, and on top a reading lamp, vases and small plants.
 */
function Library({ items }: { items: readonly AmbienceItem[] }) {
  const parts = useMemo(() => {
    const carcass: Placement[] = [], back: Placement[] = [], books: Placement[] = [];
    const lampBrass: Placement[] = [], lampShade: Placement[] = [], vases: Placement[] = [], sprigs: Placement[] = [];
    items.forEach((item, index) => {
      const rand = lcg(seedOf(item.id));
      const x0 = item.minX, x1 = item.maxX, depth = x1 - x0, cx = (x0 + x1) / 2;
      const len = item.maxZ - item.minZ, cz = (item.minZ + item.maxZ) / 2;
      // Back panel against the glass, then two ends, the plinth, one shelf and the top.
      back.push({ position: [x0 + 0.015, LIBRARY_H / 2, cz], scale: [0.03, LIBRARY_H, len] });
      for (const z of [item.minZ + 0.02, item.maxZ - 0.02]) carcass.push({ position: [cx, LIBRARY_H / 2, z], scale: [depth, LIBRARY_H, 0.04] });
      carcass.push({ position: [cx, 0.05, cz], scale: [depth, 0.1, len] });
      carcass.push({ position: [cx, 0.54, cz], scale: [depth - 0.02, 0.03, len - 0.06] });
      carcass.push({ position: [cx, LIBRARY_H - 0.02, cz], scale: [depth + 0.02, 0.04, len + 0.02] });
      // A vertical divider every ~0.8 m.
      const bays = Math.max(1, Math.round(len / 0.8));
      for (let b = 1; b < bays; b += 1) carcass.push({ position: [cx, LIBRARY_H / 2, item.minZ + (len * b) / bays], scale: [depth - 0.02, LIBRARY_H - 0.1, 0.025] });
      // Books on both shelves: runs of upright spines, now and then a gap or a small stack lying flat.
      for (const [floor, top] of [[0.1, 0.525], [0.555, LIBRARY_H - 0.04]] as const) {
        let z = item.minZ + 0.06;
        while (z < item.maxZ - 0.1) {
          const roll = rand();
          if (roll < 0.07) { z += 0.12 + rand() * 0.1; continue; }
          if (roll < 0.14) {
            // A stack lying flat.
            let y = floor;
            for (let k = 0; k < 3; k += 1) {
              const t = 0.035 + rand() * 0.02;
              books.push({ position: [cx + 0.01, y + t / 2, z + 0.11], scale: [0.2, t, 0.22 - k * 0.02], color: SPINES[Math.floor(rand() * SPINES.length)] });
              y += t;
            }
            z += 0.26;
            continue;
          }
          const w = 0.03 + rand() * 0.035, h = Math.min(top - floor - 0.03, 0.24 + rand() * 0.14);
          const lean = rand() < 0.06 ? 0.18 : 0;
          books.push({
            position: [cx + 0.01, floor + h / 2, z + w / 2], scale: [0.2 + rand() * 0.04, h, w], rotation: [lean, 0, 0],
            color: SPINES[Math.floor(rand() * SPINES.length)],
          });
          z += w + 0.004;
        }
      }
      // On top: a brass reading lamp on every other case, vases and small plants.
      const topY = LIBRARY_H;
      if (index % 2 === 0) {
        const lz = item.minZ + len * 0.22;
        lampBrass.push({ position: [cx, topY + 0.01, lz], scale: [0.08, 0.02, 0.08] });
        lampBrass.push({ position: [cx, topY + 0.2, lz], scale: [0.008, 0.38, 0.008] });
        lampShade.push({ position: [cx, topY + 0.42, lz], scale: [0.11, 0.14, 0.11] });
      }
      vases.push({ position: [cx, topY + 0.1, item.minZ + len * 0.55], scale: [0.05, 0.2, 0.05] });
      vases.push({ position: [cx - 0.02, topY + 0.07, item.minZ + len * 0.8], scale: [0.07, 0.14, 0.07] });
      sprigs.push({ position: [cx - 0.02, topY + 0.2, item.minZ + len * 0.8], scale: [0.12, 0.1, 0.12] });
      sprigs.push({ position: [cx, topY + 0.23, item.minZ + len * 0.55], scale: [0.06, 0.08, 0.06] });
    });
    return { carcass, back, books, lampBrass, lampShade, vases, sprigs };
  }, [items]);
  return (
    <group>
      <Instances geometry={GEO.box} material={AMB.walnut} items={parts.carcass} />
      <Instances geometry={GEO.box} material={AMB.walnutDark} items={parts.back} />
      <Instances geometry={GEO.box} material={AMB.book} items={parts.books} />
      <Instances geometry={GEO.cyl} material={AMB.brass} items={parts.lampBrass} />
      <Instances geometry={GEO.potCyl} material={AMB.shade} items={parts.lampShade} cast={false} />
      <Instances geometry={GEO.cyl} material={AMB.cream} items={parts.vases} />
      <Instances geometry={GEO.blob} material={MAT.leaf} items={parts.sprigs} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Planter troughs along the east glass
// ---------------------------------------------------------------------------

/** Long cream troughs on a walnut plinth, planted with low foliage, kept low so they hide nothing. */
function Troughs({ items }: { items: readonly AmbienceItem[] }) {
  const parts = useMemo(() => {
    const body: Placement[] = [], plinth: Placement[] = [], soil: Placement[] = [];
    const leaves: Placement[][] = LEAVES.map(() => []);
    for (const item of items) {
      const rand = lcg(seedOf(item.id));
      const cx = (item.minX + item.maxX) / 2, depth = item.maxX - item.minX;
      const len = item.maxZ - item.minZ, cz = (item.minZ + item.maxZ) / 2;
      plinth.push({ position: [cx, 0.03, cz], scale: [depth - 0.04, 0.06, len - 0.04] });
      body.push({ position: [cx, 0.06 + (TROUGH_H - 0.06) / 2, cz], scale: [depth, TROUGH_H - 0.06, len] });
      soil.push({ position: [cx, TROUGH_H + 0.003, cz], scale: [depth - 0.06, 0.006, len - 0.06] });
      for (let z = item.minZ + 0.18; z < item.maxZ - 0.12; z += 0.2 + rand() * 0.08) {
        const r = 0.13 + rand() * 0.08;
        leaves[Math.floor(rand() * LEAVES.length)].push({ position: [cx + (rand() - 0.5) * 0.08, TROUGH_H + r * 0.7, z], scale: [r * 0.9, r * (1 + rand() * 0.6), r] });
      }
    }
    return { body, plinth, soil, leaves };
  }, [items]);
  return (
    <group>
      <Instances geometry={GEO.box} material={AMB.walnut} items={parts.plinth} />
      <Instances geometry={GEO.box} material={AMB.cream} items={parts.body} />
      <Instances geometry={GEO.box} material={MAT.soil} items={parts.soil} cast={false} />
      {parts.leaves.map((list, i) => <Instances key={i} geometry={GEO.blob} material={LEAVES[i]} items={list} />)}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Pendants over the benches
// ---------------------------------------------------------------------------

const PENDANT_Y = 3.0;
/** Below this camera height the pendants hide, so a close or low view never looks through them. */
const PENDANT_HIDE_BELOW = PENDANT_Y + 4;

interface Bench { x: number; z: number; length: number }

/** One bench per pair of desk rows: its centre and length from the desks it holds. */
export function benchesOf(departments: readonly Pick<Department, "id" | "desks">[]): Bench[] {
  const out: Bench[] = [];
  for (const dept of departments) {
    const groups = new Map<string, { xs: number[]; zs: number[] }>();
    for (const desk of dept.desks) {
      // Desk ids are "<dept>:<bench>:<facing>:<col>".
      const key = desk.id.split(":").slice(0, -2).join(":");
      const group = groups.get(key) ?? { xs: [], zs: [] };
      group.xs.push(desk.x);
      group.zs.push(desk.z);
      groups.set(key, group);
    }
    for (const { xs, zs } of groups.values()) {
      const minX = Math.min(...xs), maxX = Math.max(...xs);
      out.push({ x: (minX + maxX) / 2, z: zs.reduce((s, v) => s + v, 0) / zs.length, length: maxX - minX + 1.2 });
    }
  }
  return out;
}

let poolMaterial: MeshBasicMaterial | null = null;
/** A soft oval of warm light, added onto the floor. */
function pool(): MeshBasicMaterial {
  if (!poolMaterial) {
    const map = cachedCanvasTexture("coding:pool", 128, 128, (ctx, w, h) => {
      const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
      g.addColorStop(0, "rgba(255,255,255,1)");
      g.addColorStop(0.5, "rgba(255,255,255,0.45)");
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    });
    poolMaterial = new MeshBasicMaterial({
      color: CODING_SCENE.pendant.pool, map, transparent: true, opacity: map ? 0.16 : 0, blending: AdditiveBlending, depthWrite: false,
    });
  }
  return poolMaterial;
}
const POOL_GEOMETRY = new PlaneGeometry(1, 1).rotateX(-Math.PI / 2);

function Pendants({ departments }: { departments: readonly Department[] }) {
  const lamps = useRef<Group>(null);
  const parts = useMemo(() => {
    const body: Placement[] = [], glow: Placement[] = [], cable: Placement[] = [], pools: Placement[] = [];
    for (const bench of benchesOf(departments)) {
      const len = bench.length - 0.4;
      body.push({ position: [bench.x, PENDANT_Y, bench.z], scale: [len, 0.05, 0.07] });
      glow.push({ position: [bench.x, PENDANT_Y - 0.027, bench.z], scale: [len - 0.06, 0.006, 0.045] });
      for (const dx of [-len * 0.38, len * 0.38]) cable.push({ position: [bench.x + dx, PENDANT_Y + 0.6, bench.z], scale: [0.006, 1.2, 0.006] });
      pools.push({ position: [bench.x, 0.012, bench.z], scale: [bench.length + 1.6, 1, 3.6] });
    }
    return { body, glow, cable, pools };
  }, [departments]);
  useFrame(({ camera }) => {
    const group = lamps.current;
    if (group) group.visible = camera.position.y > PENDANT_HIDE_BELOW;
  });
  return (
    <group>
      <group ref={lamps}>
        <Instances geometry={GEO.box} material={AMB.pendant} items={parts.body} cast={false} />
        <Instances geometry={GEO.box} material={AMB.pendantGlow} items={parts.glow} cast={false} />
        <Instances geometry={GEO.box} material={AMB.pendant} items={parts.cable} cast={false} />
      </group>
      <Instances geometry={POOL_GEOMETRY} material={pool()} items={parts.pools} cast={false} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// The floor's ambience
// ---------------------------------------------------------------------------

export function CodingFloorAmbience({ layout }: { layout: OfficeLayout }) {
  const items = useMemo(() => codingAmbience(layout), [layout]);
  const byKind = useMemo(() => ({
    tree: items.filter((i) => i.kind === "tree"),
    library: items.filter((i) => i.kind === "library"),
    trough: items.filter((i) => i.kind === "trough"),
  }), [items]);
  const { minX, maxZ } = layout.bounds;
  return (
    <group>
      {/* Warm late-afternoon fill from the south-west, no shadows: the one extra light the floor pays for. */}
      <directionalLight position={[minX - 12, 12, maxZ + 6]} intensity={0.4} color="#ffc68a" />
      <Trees items={byKind.tree} />
      <Library items={byKind.library} />
      <Troughs items={byKind.trough} />
      <Pendants departments={layout.departments} />
    </group>
  );
}
