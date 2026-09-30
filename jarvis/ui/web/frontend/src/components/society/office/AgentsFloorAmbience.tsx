/**
 * The agents floor's ambience between the rooms: olive trees in pale stone
 * pots closing the cross aisles at the windows, cushioned oak window benches
 * and slatted oak planters of grasses along both glass sides, a rack of felt
 * acoustic baffles floating over every bench in its department's colour with
 * two warm linear lights under it and soft pools of light below, and one warm
 * fill light for the whole floor.
 *
 * Every repeated part is one instanced mesh, so the whole dressing costs a
 * handful of draw calls. The solid pieces come from `agentsAmbience`, which
 * the layout also adds to its obstacles; the baffles and light pools are out
 * of reach (overhead, or flat on the floor). The baffles fade out whenever the
 * camera comes down towards their height, so they never block a close view.
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { AdditiveBlending, Group, MeshBasicMaterial, MeshStandardMaterial, PlaneGeometry } from "three";
import { cachedCanvasTexture } from "./canvasMaterials";
import { agentsAmbience, BENCH_H, PLANTER_H, type AgentsAmbienceItem } from "./agentsAmbience";
import { agentsRug, AGENTS_SCENE } from "./AgentsFloorLook";
import { benchesOf, Instances, type Placement } from "./CodingFloorAmbience";
import { GEO, MAT, matte } from "./OfficeFurniture";
import type { Department, OfficeLayout } from "./officeLayout";

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

const AMB = {
  stone: matte("#e4ddd1", { roughness: 0.8 }),
  stoneDark: matte("#cfc6b8", { roughness: 0.85 }),
  gravel: matte("#b9b0a3", { roughness: 1 }),
  bark: matte("#7a6a58", { roughness: 0.9 }),
  oak: matte("#d9bf97", { roughness: 0.6 }),
  oakDark: matte("#b89b74", { roughness: 0.65 }),
  linen: matte("#efe7d8", { roughness: 1 }),
  /** White, so each instance's colour is the fabric's or the felt's colour. */
  fabric: matte("#ffffff", { roughness: 1 }),
  plume: matte("#e9dcc0", { roughness: 1 }),
  stalk: matte("#b7a582", { roughness: 1 }),
};

/** Olive leaves: silvery sage greens, lighter than the office's other plants. */
const OLIVE_LEAVES = [
  matte("#8c9c6e", { flatShading: true }), matte("#9fae82", { flatShading: true }),
  matte("#77885d", { flatShading: true }), matte("#b0b995", { flatShading: true }),
];
/** Grasses in the planters: deeper greens, the office's own leaf tones. */
const GRASS = [MAT.leaf, MAT.leafDark, MAT.leafLight, OLIVE_LEAVES[0]];
/** Throw pillows on the benches: clay, sage, mustard, dusty blue and oat. */
const PILLOWS = ["#c07f63", "#8fa383", "#d2a650", "#7f93a8", "#d9c9ab"];

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
// Olive trees
// ---------------------------------------------------------------------------

/** An olive in a round stone pot: a leaning, forked trunk and an airy crown of small silvery clusters. */
function Olives({ items }: { items: readonly AgentsAmbienceItem[] }) {
  const parts = useMemo(() => {
    const pot: Placement[] = [], lip: Placement[] = [], gravel: Placement[] = [], trunk: Placement[] = [];
    const leaves: Placement[][] = OLIVE_LEAVES.map(() => []);
    for (const item of items) {
      const x = (item.minX + item.maxX) / 2, z = (item.minZ + item.maxZ) / 2, r = (item.maxX - item.minX) / 2;
      const rand = lcg(seedOf(item.id));
      pot.push({ position: [x, 0.3, z], scale: [r - 0.03, 0.6, r - 0.03] });
      lip.push({ position: [x, 0.61, z], scale: [r, 0.04, r] });
      gravel.push({ position: [x, 0.625, z], scale: [r - 0.07, 0.01, r - 0.07] });
      // Two leaning stems that fork from a short trunk.
      const lean = (rand() - 0.5) * 0.3;
      trunk.push({ position: [x, 0.85, z], scale: [0.055, 0.5, 0.055], rotation: [lean * 0.5, 0, 0.06] });
      trunk.push({ position: [x - 0.08, 1.35, z + 0.02], scale: [0.035, 0.62, 0.035], rotation: [0.1, 0, 0.28] });
      trunk.push({ position: [x + 0.09, 1.4, z - 0.03], scale: [0.032, 0.66, 0.032], rotation: [-0.12, 0, -0.26] });
      // The crown: an open dome of small clusters, so the trunk shows through.
      for (let k = 0; k < 22; k += 1) {
        const a = rand() * Math.PI * 2, ring = 0.15 + rand() * 0.45;
        const y = 1.55 + rand() * 0.7 - ring * 0.35;
        const s = 0.1 + rand() * 0.09;
        leaves[Math.floor(rand() * leaves.length)].push({
          position: [x + Math.cos(a) * ring, y, z + Math.sin(a) * ring], scale: [s * 1.3, s * 0.8, s * 1.3],
        });
      }
    }
    return { pot, lip, gravel, trunk, leaves };
  }, [items]);
  return (
    <group>
      <Instances geometry={GEO.potCyl} material={AMB.stone} items={parts.pot} />
      <Instances geometry={GEO.cyl} material={AMB.stoneDark} items={parts.lip} />
      <Instances geometry={GEO.cyl} material={AMB.gravel} items={parts.gravel} cast={false} />
      <Instances geometry={GEO.cyl} material={AMB.bark} items={parts.trunk} />
      {parts.leaves.map((list, i) => <Instances key={i} geometry={GEO.blob} material={OLIVE_LEAVES[i]} items={list} />)}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Window benches
// ---------------------------------------------------------------------------

/**
 * A long oak bench against the glass: a recessed plinth, an oak seat box, a
 * linen seat cushion, back cushions leaning on the glass side, a few throw
 * pillows and now and then a book left behind.
 */
function Benches({ items }: { items: readonly AgentsAmbienceItem[] }) {
  const parts = useMemo(() => {
    const plinth: Placement[] = [], box: Placement[] = [], cushion: Placement[] = [], fabric: Placement[] = [];
    for (const item of items) {
      const rand = lcg(seedOf(item.id));
      const depth = item.maxX - item.minX, len = item.maxZ - item.minZ;
      const cx = (item.minX + item.maxX) / 2, cz = (item.minZ + item.maxZ) / 2;
      // The glass side: -1 (west) when the bench faces east, +1 when it faces west.
      const out = item.facing === "east" ? -1 : 1;
      const seatTop = BENCH_H - 0.1;
      plinth.push({ position: [cx + out * 0.03, 0.04, cz], scale: [depth - 0.1, 0.08, len - 0.1] });
      box.push({ position: [cx, 0.08 + (seatTop - 0.08) / 2, cz], scale: [depth, seatTop - 0.08, len] });
      cushion.push({ position: [cx - out * 0.02, seatTop + 0.05, cz], scale: [depth - 0.06, 0.1, len - 0.06] });
      // Back cushions, one per ~0.8 m, leaning back onto the glass.
      const backs = Math.max(1, Math.round(len / 0.8));
      const bw = (len - 0.1) / backs;
      for (let b = 0; b < backs; b += 1) {
        cushion.push({
          position: [cx + out * (depth / 2 - 0.07), seatTop + 0.3, item.minZ + 0.05 + bw * (b + 0.5)],
          scale: [0.1, 0.38, bw - 0.03], rotation: [0, 0, -out * 0.16],
        });
      }
      // Two or three throw pillows in front of the back cushions.
      const pillows = 2 + Math.floor(rand() * 2);
      for (let p = 0; p < pillows; p += 1) {
        const pz = item.minZ + 0.3 + rand() * (len - 0.6);
        fabric.push({
          position: [cx + out * (depth / 2 - 0.16), seatTop + 0.24, pz], scale: [0.1, 0.3, 0.32],
          rotation: [(rand() - 0.5) * 0.3, 0, -out * 0.3], color: PILLOWS[Math.floor(rand() * PILLOWS.length)],
        });
      }
      if (rand() < 0.5) {
        fabric.push({
          position: [cx - out * 0.06, seatTop + 0.115, item.minZ + len * (0.2 + rand() * 0.6)], scale: [0.16, 0.03, 0.22],
          rotation: [0, rand() * 0.8, 0], color: PILLOWS[Math.floor(rand() * PILLOWS.length)],
        });
      }
    }
    return { plinth, box, cushion, fabric };
  }, [items]);
  return (
    <group>
      <Instances geometry={GEO.box} material={AMB.oakDark} items={parts.plinth} cast={false} />
      <Instances geometry={GEO.box} material={AMB.oak} items={parts.box} />
      <Instances geometry={GEO.box} material={AMB.linen} items={parts.cushion} />
      <Instances geometry={GEO.box} material={AMB.fabric} items={parts.fabric} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Slatted planters
// ---------------------------------------------------------------------------

/** Long oak planters wrapped in vertical slats, planted with tall grasses and a few pale feather-grass plumes. */
function Planters({ items }: { items: readonly AgentsAmbienceItem[] }) {
  const parts = useMemo(() => {
    const liner: Placement[] = [], slats: Placement[] = [], cap: Placement[] = [], soil: Placement[] = [], plumes: Placement[] = [], stalks: Placement[] = [];
    const grass: Placement[][] = GRASS.map(() => []);
    for (const item of items) {
      const rand = lcg(seedOf(item.id));
      const depth = item.maxX - item.minX, len = item.maxZ - item.minZ;
      const cx = (item.minX + item.maxX) / 2, cz = (item.minZ + item.maxZ) / 2;
      const bodyH = PLANTER_H - 0.03;
      liner.push({ position: [cx, bodyH / 2, cz], scale: [depth - 0.03, bodyH, len - 0.03] });
      // Slats every 7 cm down both long faces, and across both ends.
      for (let z = item.minZ + 0.035; z < item.maxZ - 0.02; z += 0.07) {
        for (const x of [item.minX + 0.012, item.maxX - 0.012]) slats.push({ position: [x, bodyH / 2, z], scale: [0.024, bodyH, 0.045] });
      }
      for (let x = item.minX + 0.05; x < item.maxX - 0.03; x += 0.07) {
        for (const z of [item.minZ + 0.012, item.maxZ - 0.012]) slats.push({ position: [x, bodyH / 2, z], scale: [0.045, bodyH, 0.024] });
      }
      cap.push({ position: [cx, PLANTER_H - 0.015, cz], scale: [depth + 0.01, 0.03, len + 0.01] });
      soil.push({ position: [cx, PLANTER_H + 0.002, cz], scale: [depth - 0.06, 0.006, len - 0.06] });
      // Clumps of fine blades fanning out of a low mound, and every so often a pale plume above them.
      for (let z = item.minZ + 0.12; z < item.maxZ - 0.08; z += 0.11 + rand() * 0.05) {
        const bx = cx + (rand() - 0.5) * 0.1, tone = Math.floor(rand() * GRASS.length);
        grass[tone].push({ position: [bx, PLANTER_H + 0.03, z], scale: [0.07, 0.06, 0.07] });
        let tallest = 0;
        for (let k = 0; k < 6; k += 1) {
          const h = 0.18 + rand() * 0.24, rx = (rand() - 0.5) * 0.9, rz = (rand() - 0.5) * 0.9;
          tallest = Math.max(tallest, h);
          grass[(tone + (k % 2)) % GRASS.length].push({
            // A blade's local +y leans to (-sin rz, cos rz · cos rx, sin rx): its centre sits one half-length up that line.
            position: [bx - Math.sin(rz) * h * 0.9, PLANTER_H + Math.cos(rz) * Math.cos(rx) * h * 0.9, z + Math.sin(rx) * h * 0.9],
            scale: [0.018 + rand() * 0.01, h, 0.028], rotation: [rx, 0, rz],
          });
        }
        if (rand() < 0.3) {
          // A plume on a thin stalk rising just above the clump.
          const top = tallest * 1.9 + 0.1;
          plumes.push({ position: [bx, PLANTER_H + top, z], scale: [0.03, 0.12, 0.03] });
          stalks.push({ position: [bx, PLANTER_H + top / 2, z], scale: [0.004, top, 0.004] });
        }
      }
    }
    return { liner, slats, cap, soil, grass, plumes, stalks };
  }, [items]);
  return (
    <group>
      <Instances geometry={GEO.box} material={AMB.oakDark} items={parts.liner} />
      <Instances geometry={GEO.box} material={AMB.oak} items={parts.slats} cast={false} />
      <Instances geometry={GEO.box} material={AMB.oak} items={parts.cap} />
      <Instances geometry={GEO.box} material={MAT.soil} items={parts.soil} cast={false} />
      {parts.grass.map((list, i) => <Instances key={i} geometry={GEO.blob} material={GRASS[i]} items={list} />)}
      <Instances geometry={GEO.blob} material={AMB.plume} items={parts.plumes} />
      <Instances geometry={GEO.cyl} material={AMB.stalk} items={parts.stalks} cast={false} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Felt baffles over the benches
// ---------------------------------------------------------------------------

/** Top of the baffle rack, a baffle's height, thickness and length (across the bench), and their pitch along it. */
const BAFFLE = { top: 3.3, h: 0.36, t: 0.035, len: 1.8, pitch: 0.4 } as const;
/** The baffles fade out between these camera heights, so a close or low view never looks through them. */
const BAFFLE_FADE = { from: 6.5, to: 9 } as const;

let poolMaterial: MeshBasicMaterial | null = null;
/** A soft oval of warm light, added onto the floor. */
function pool(): MeshBasicMaterial {
  if (!poolMaterial) {
    const map = cachedCanvasTexture("agents:pool", 128, 128, (ctx, w, h) => {
      const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
      g.addColorStop(0, "rgba(255,255,255,1)");
      g.addColorStop(0.55, "rgba(255,255,255,0.4)");
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    });
    poolMaterial = new MeshBasicMaterial({
      color: AGENTS_SCENE.light.pool, map, transparent: true, opacity: map ? 0.13 : 0, blending: AdditiveBlending, depthWrite: false,
    });
  }
  return poolMaterial;
}
const POOL_GEOMETRY = new PlaneGeometry(1, 1).rotateX(-Math.PI / 2);

function Baffles({ departments }: { departments: readonly Department[] }) {
  const lamps = useRef<Group>(null);
  // Own materials, so fading them touches nothing else in the scene.
  const mats = useMemo(() => ({
    felt: matte("#ffffff", { roughness: 1, transparent: true }),
    strip: new MeshStandardMaterial({
      color: AGENTS_SCENE.light.glow, emissive: AGENTS_SCENE.light.glow, emissiveIntensity: 1.8, toneMapped: false, transparent: true,
    }),
    cable: matte("#2a2724", { roughness: 0.5, transparent: true }),
  }), []);
  useEffect(() => () => { mats.felt.dispose(); mats.strip.dispose(); mats.cable.dispose(); }, [mats]);
  const parts = useMemo(() => {
    const fins: Placement[] = [], rails: Placement[] = [], strips: Placement[] = [], cables: Placement[] = [], pools: Placement[] = [];
    for (const dept of departments) {
      const felt = agentsRug(dept.tint).felt;
      for (const bench of benchesOf([dept])) {
        // A rack of vertical felt fins across the bench: seen from above they are fine lines, so the desks stay visible.
        const run = bench.length - 0.9, count = Math.max(2, Math.round(run / BAFFLE.pitch) + 1);
        for (let i = 0; i < count; i += 1) {
          const x = bench.x - run / 2 + (run * i) / (count - 1);
          fins.push({ position: [x, BAFFLE.top - BAFFLE.h / 2, bench.z], scale: [BAFFLE.t, BAFFLE.h, BAFFLE.len], color: felt });
        }
        for (const dz of [-BAFFLE.len * 0.35, BAFFLE.len * 0.35]) {
          rails.push({ position: [bench.x, BAFFLE.top + 0.012, bench.z + dz], scale: [run + 0.08, 0.024, 0.03], color: felt });
        }
        // Two linear lights under the fins, one over each desk row.
        for (const dz of [-0.45, 0.45]) {
          strips.push({ position: [bench.x, BAFFLE.top - BAFFLE.h - 0.05, bench.z + dz], scale: [run - 0.4, 0.025, 0.05] });
        }
        for (const dx of [-run * 0.4, run * 0.4]) {
          for (const dz of [-BAFFLE.len * 0.35, BAFFLE.len * 0.35]) cables.push({ position: [bench.x + dx, BAFFLE.top + 0.6, bench.z + dz], scale: [0.005, 1.2, 0.005] });
          // The light hangs from the rack on short drops.
          for (const dz of [-0.45, 0.45]) cables.push({ position: [bench.x + dx, BAFFLE.top - (BAFFLE.h + 0.05) / 2, bench.z + dz], scale: [0.005, BAFFLE.h + 0.05, 0.005] });
        }
        pools.push({ position: [bench.x, 0.012, bench.z], scale: [bench.length + 1.4, 1, 3.4] });
      }
    }
    return { fins, rails, strips, cables, pools };
  }, [departments]);
  const fade = useRef(-1);
  useFrame(({ camera }) => {
    const t = Math.min(1, Math.max(0, (camera.position.y - BAFFLE_FADE.from) / (BAFFLE_FADE.to - BAFFLE_FADE.from)));
    if (t === fade.current) return;
    fade.current = t;
    for (const m of [mats.felt, mats.strip, mats.cable]) {
      m.opacity = t;
      // Opaque when fully shown, so the fins sort and shadow like any solid.
      m.depthWrite = t >= 1;
    }
    if (lamps.current) lamps.current.visible = t > 0;
  });
  return (
    <group>
      <group ref={lamps}>
        <Instances geometry={GEO.box} material={mats.felt} items={parts.fins} cast={false} />
        <Instances geometry={GEO.box} material={mats.felt} items={parts.rails} cast={false} />
        <Instances geometry={GEO.box} material={mats.strip} items={parts.strips} cast={false} />
        <Instances geometry={GEO.box} material={mats.cable} items={parts.cables} cast={false} />
      </group>
      <Instances geometry={POOL_GEOMETRY} material={pool()} items={parts.pools} cast={false} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// The floor's ambience
// ---------------------------------------------------------------------------

export function AgentsFloorAmbience({ layout }: { layout: OfficeLayout }) {
  const items = useMemo(() => agentsAmbience(layout), [layout]);
  const byKind = useMemo(() => ({
    olive: items.filter((i) => i.kind === "olive"),
    bench: items.filter((i) => i.kind === "bench"),
    planter: items.filter((i) => i.kind === "planter"),
  }), [items]);
  const { minX, maxZ } = layout.bounds;
  return (
    <group>
      {/* Soft warm daylight from the south-west, no shadows: the one extra light the floor pays for. */}
      <directionalLight position={[minX - 12, 14, maxZ + 8]} intensity={0.35} color="#ffdcb4" />
      <Olives items={byKind.olive} />
      <Benches items={byKind.bench} />
      <Planters items={byKind.planter} />
      <Baffles departments={layout.departments} />
    </group>
  );
}
