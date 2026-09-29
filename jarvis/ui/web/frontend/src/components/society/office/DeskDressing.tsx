/**
 * The coding floor's lived-in workstations: desk mats, laptops on stands,
 * lamps, succulents, books, bottles, notebooks, headphones on hooks, sticky
 * notes, cable trays under the benches, and a planter box at each bench end.
 *
 * `deskKit.ts` decides what goes where; this draws it as one instanced
 * mesh per part type across the whole floor, rebuilt only when the desks
 * change. Geometries and materials are module-level singletons.
 */
import { useLayoutEffect, useMemo, useRef } from "react";
import {
  BufferGeometry, Color, CylinderGeometry, Euler, IcosahedronGeometry, InstancedMesh, Matrix4, MeshStandardMaterial,
  PlaneGeometry, Quaternion, TorusGeometry, Vector3, type Material,
} from "three";
import { RoundedBoxGeometry } from "three-stdlib";
import { cachedCanvasTexture } from "./canvasMaterials";
import { dressDesk, kitPlacements, planterPlacements, type DressingPart, type Placement } from "./deskKit";
import { GEO, MAT, matte } from "./OfficeFurniture";
import { benchPlanters, type Department, type DeskSlot } from "./officeLayout";

/** A few lines of code in a dark editor, for the laptop screens. */
function drawLaptopScreen(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#15181f";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#1f2430";
  ctx.fillRect(0, 0, w * 0.22, h);
  const colours = ["#7dd3c0", "#c4a7f5", "#f0c987", "#8fb8f0", "#9aa3b2"];
  let seed = 7;
  for (let y = 8, line = 0; y < h - 4; y += 7, line += 1) {
    seed = (seed * 1103515245 + 12345) >>> 0;
    const indent = 4 + ((seed >>> 8) % 4) * 6;
    let x = w * 0.22 + indent;
    for (let word = 0; word < 1 + ((seed >>> 4) % 4); word += 1) {
      const len = 8 + ((seed >>> (word * 3)) % 22);
      ctx.fillStyle = colours[(line + word) % colours.length];
      ctx.fillRect(x, y, len, 3);
      x += len + 4;
    }
  }
}

const laptopScreen = (() => {
  const map = cachedCanvasTexture("desk:laptop-code", 128, 80, drawLaptopScreen);
  return new MeshStandardMaterial({
    color: map ? "#ffffff" : "#1a1e27", map, roughness: 0.35,
    emissive: "#ffffff", emissiveMap: map, emissiveIntensity: map ? 0.55 : 0,
  });
})();

/** White base for parts coloured per instance. */
const tintedMatte = matte("#ffffff", { roughness: 0.8 });
const tintedGloss = matte("#ffffff", { roughness: 0.4 });
const tintedLeaf = matte("#ffffff", { roughness: 0.75, flatShading: true });
const aluminium = matte("#c7ccd3", { roughness: 0.35, metalness: 0.55 });
const blackMetal = matte("#23262c", { roughness: 0.55, metalness: 0.3 });

interface PartSpec { geometry: BufferGeometry; material: Material; tinted: boolean; cast: boolean }

const PARTS: Record<DressingPart, PartSpec> = {
  mat: { geometry: new RoundedBoxGeometry(0.82, 0.004, 0.34, 2, 0.002), material: tintedMatte, tinted: true, cast: false },
  standPlate: { geometry: GEO.box, material: aluminium, tinted: false, cast: true },
  standLeg: { geometry: GEO.box, material: aluminium, tinted: false, cast: false },
  laptopBase: { geometry: new RoundedBoxGeometry(0.3, 0.012, 0.21, 2, 0.005), material: aluminium, tinted: false, cast: true },
  laptopLid: { geometry: new RoundedBoxGeometry(0.3, 0.2, 0.007, 2, 0.003), material: aluminium, tinted: false, cast: true },
  laptopScreen: { geometry: new PlaneGeometry(0.28, 0.18), material: laptopScreen, tinted: false, cast: false },
  lampBase: { geometry: GEO.cyl, material: tintedGloss, tinted: true, cast: true },
  lampArm: { geometry: GEO.cyl, material: tintedGloss, tinted: true, cast: true },
  lampShade: { geometry: new CylinderGeometry(0.45, 1, 1, 16), material: tintedGloss, tinted: true, cast: true },
  lampBulb: {
    geometry: GEO.cyl, tinted: false, cast: false,
    material: new MeshStandardMaterial({ color: "#fff2d6", emissive: "#ffd89a", emissiveIntensity: 1.6, toneMapped: false }),
  },
  pot: { geometry: GEO.potCyl, material: tintedGloss, tinted: true, cast: true },
  soil: { geometry: GEO.cyl, material: MAT.soil, tinted: false, cast: false },
  succulent: { geometry: new IcosahedronGeometry(1, 0), material: tintedLeaf, tinted: true, cast: true },
  book: { geometry: GEO.box, material: tintedMatte, tinted: true, cast: true },
  bottle: { geometry: GEO.cyl, material: tintedGloss, tinted: true, cast: true },
  bottleCap: { geometry: GEO.cyl, material: blackMetal, tinted: false, cast: false },
  notebook: { geometry: GEO.box, material: tintedMatte, tinted: true, cast: true },
  notebookBand: { geometry: GEO.box, material: blackMetal, tinted: false, cast: false },
  pen: { geometry: GEO.cyl, material: blackMetal, tinted: false, cast: false },
  hook: { geometry: GEO.box, material: blackMetal, tinted: false, cast: false },
  headband: { geometry: new TorusGeometry(0.08, 0.01, 8, 24, Math.PI), material: tintedMatte, tinted: true, cast: true },
  earCup: { geometry: GEO.cyl, material: tintedMatte, tinted: true, cast: true },
  note: { geometry: new PlaneGeometry(0.065, 0.065), material: tintedMatte, tinted: true, cast: false },
  tray: { geometry: GEO.box, material: blackMetal, tinted: false, cast: false },
  snake: { geometry: new CylinderGeometry(1, 1, 1, 10), material: blackMetal, tinted: false, cast: false },
  puck: { geometry: GEO.box, material: aluminium, tinted: false, cast: false },
  planterBody: { geometry: GEO.box, material: matte("#7a563b", { roughness: 0.7 }), tinted: false, cast: true },
  planterPlinth: { geometry: GEO.box, material: blackMetal, tinted: false, cast: false },
  planterRail: { geometry: GEO.box, material: MAT.wood, tinted: false, cast: false },
  planterSoil: { geometry: GEO.box, material: MAT.soil, tinted: false, cast: false },
  foliage: { geometry: GEO.blob, material: tintedLeaf, tinted: true, cast: true },
  blade: { geometry: new CylinderGeometry(0.15, 1, 1, 4), material: tintedLeaf, tinted: true, cast: true },
};
const PART_KEYS = Object.keys(PARTS) as DressingPart[];

interface Batch { matrices: Matrix4[]; colours: Color[] }

const euler = new Euler(0, 0, 0, "YXZ");
const quat = new Quaternion();
const pos = new Vector3();
const scl = new Vector3();

function localMatrix(part: Placement): Matrix4 {
  const [rx, ry, rz] = part.r ?? [0, 0, 0];
  euler.set(rx, ry, rz, "YXZ");
  return new Matrix4().compose(pos.set(...part.p), quat.setFromEuler(euler), scl.set(...(part.s ?? [1, 1, 1])));
}

/** Every instance of every part on the floor: desk kits (desk space → world) and planters (already world space). */
export function buildDressing(desks: readonly DeskSlot[], departments: readonly Department[]): Map<DressingPart, Batch> {
  const batches = new Map<DressingPart, Batch>(PART_KEYS.map((k) => [k, { matrices: [], colours: [] }]));
  const add = (part: Placement, world?: Matrix4) => {
    const batch = batches.get(part.part)!;
    const local = localMatrix(part);
    batch.matrices.push(world ? world.clone().multiply(local) : local);
    batch.colours.push(new Color(part.c ?? "#ffffff"));
  };
  for (const desk of desks) {
    const turn = desk.facing === "north" ? 0 : Math.PI;
    const world = new Matrix4().makeRotationY(turn).setPosition(desk.x, 0, desk.z);
    for (const part of kitPlacements(dressDesk(desk.id, desk.agentId !== null))) add(part, world);
  }
  for (const dept of departments) {
    for (const { key, rect } of benchPlanters(dept)) for (const part of planterPlacements(rect, key)) add(part);
  }
  return batches;
}

function PartInstances({ spec, batch }: { spec: PartSpec; batch: Batch }) {
  const ref = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    batch.matrices.forEach((m, i) => {
      mesh.setMatrixAt(i, m);
      if (spec.tinted) mesh.setColorAt(i, batch.colours[i]);
    });
    mesh.count = batch.matrices.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [spec, batch]);
  return <instancedMesh ref={ref} args={[spec.geometry, spec.material, batch.matrices.length]} castShadow={spec.cast} receiveShadow frustumCulled={false} />;
}

/** The dressing of every bench desk on the floor and the planters of every department's benches. */
export function DeskDressing({ desks, departments }: { desks: readonly DeskSlot[]; departments: readonly Department[] }) {
  const batches = useMemo(() => buildDressing(desks, departments), [desks, departments]);
  return (
    <group>
      {PART_KEYS.map((key) => {
        const batch = batches.get(key)!;
        // Keyed by count: an InstancedMesh cannot grow in place.
        return batch.matrices.length > 0 ? <PartInstances key={`${key}:${batch.matrices.length}`} spec={PARTS[key]} batch={batch} /> : null;
      })}
    </group>
  );
}
