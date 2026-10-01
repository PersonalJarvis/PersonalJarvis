/**
 * Lived-in workstations. The coding floor: desk mats, laptops on stands,
 * lamps, succulents, books, bottles, notebooks, headphones on hooks, sticky
 * notes, cable trays under the benches, and a walnut planter at each bench
 * end. The agents floor: letter trays, documents, tablets with a calendar,
 * phone docks, pen cups, framed photos, takeaway coffee, leafy plants, brass
 * lamps, pinned pages, and a slatted pale-oak planter at each bench end.
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
import {
  dressDesk, dressOfficeDesk, kitPlacements, oakPlanterPlacements, officeKitPlacements, planterPlacements, type DressingPart, type Placement,
} from "./deskKit";
import { GEO, MAT, matte } from "./OfficeFurniture";
import { benchPlanters, type Department, type DeskSlot, type OfficeVariant } from "./officeLayout";

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

/** A week in a calendar app, for the tablets on the agents floor. */
function drawCalendar(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#f7f5f0";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#8fa58f";
  ctx.fillRect(0, 0, w, 12);
  ctx.fillStyle = "#e4dfd4";
  const cols = 5, left = 10, colW = (w - left) / cols;
  for (let i = 0; i <= cols; i += 1) ctx.fillRect(left + i * colW, 12, 1, h - 12);
  for (let y = 22; y < h; y += 10) ctx.fillRect(left, y, w - left, 1);
  const tones = ["#c98f6f", "#6f86a6", "#8fa58f", "#d8b36a", "#a58fb8"];
  let seed = 11;
  for (let col = 0; col < cols; col += 1) {
    let y = 14;
    while (y < h - 10) {
      seed = (seed * 1103515245 + 12345) >>> 0;
      y += ((seed >>> 8) % 3) * 10;
      const len = 10 + ((seed >>> 4) % 3) * 10;
      if (y + len > h - 2) break;
      ctx.fillStyle = tones[(seed >>> 12) % tones.length];
      ctx.fillRect(left + col * colW + 2, y + 1, colW - 4, len - 2);
      y += len + 4;
    }
  }
}

/** A chat thread, for the phones. */
function drawMessages(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#1b1d22";
  ctx.fillRect(0, 0, w, h);
  let y = 10;
  for (let i = 0; y < h - 12; i += 1) {
    const mine = i % 3 === 1;
    const bw = 22 + ((i * 17) % 18), bh = 8 + (i % 2) * 6;
    ctx.fillStyle = mine ? "#7fa88a" : "#3a3e46";
    ctx.fillRect(mine ? w - bw - 5 : 5, y, bw, bh);
    y += bh + 5;
  }
}

/** A printed page: a heading, body lines and a small chart block. */
function drawPage(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#fbfaf6";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#3a3d42";
  ctx.fillRect(7, 8, w * 0.55, 4);
  ctx.fillStyle = "#b9b4aa";
  for (let y = 18, i = 0; y < h - 8; y += 5, i += 1) {
    if (i === 5) {
      ctx.fillStyle = "#9db09a";
      ctx.fillRect(7, y, w * 0.4, 14);
      ctx.fillStyle = "#b9b4aa";
      y += 14;
      continue;
    }
    ctx.fillRect(7, y, (w - 14) * (i % 4 === 3 ? 0.6 : 1), 2);
  }
}

/** A holiday snapshot: a warm sky over hills and a lake. */
function drawPhoto(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const sky = ctx.createLinearGradient(0, 0, 0, h * 0.6);
  sky.addColorStop(0, "#9cc3e0");
  sky.addColorStop(1, "#f3d3a8");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#fff1c9";
  ctx.beginPath();
  ctx.arc(w * 0.7, h * 0.36, w * 0.09, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#7d9a78";
  ctx.beginPath();
  ctx.moveTo(0, h * 0.6);
  ctx.quadraticCurveTo(w * 0.3, h * 0.38, w * 0.55, h * 0.56);
  ctx.quadraticCurveTo(w * 0.8, h * 0.44, w, h * 0.55);
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.fill();
  ctx.fillStyle = "#6d8fa8";
  ctx.fillRect(0, h * 0.7, w, h * 0.3);
  ctx.fillStyle = "#3a3d42";
  ctx.fillRect(w * 0.3, h * 0.6, 3, 9);
  ctx.fillRect(w * 0.36, h * 0.62, 3, 7);
}

/** A canvas-drawn face; self-lit when `glow` > 0, on a white base so instance colours tint it. */
function faceMaterial(
  key: string, w: number, h: number, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void, fallback: string, glow = 0,
): MeshStandardMaterial {
  const map = cachedCanvasTexture(key, w, h, draw);
  return new MeshStandardMaterial({
    color: map ? "#ffffff" : fallback, map, roughness: glow > 0 ? 0.35 : 0.85,
    emissive: glow > 0 ? "#ffffff" : "#000000", emissiveMap: glow > 0 ? map : null, emissiveIntensity: map ? glow : 0,
  });
}

/** White base for parts coloured per instance. */
const tintedMatte = matte("#ffffff", { roughness: 0.8 });
const tintedGloss = matte("#ffffff", { roughness: 0.4 });
const tintedLeaf = matte("#ffffff", { roughness: 0.75, flatShading: true });
const aluminium = matte("#c7ccd3", { roughness: 0.35, metalness: 0.55 });
const blackMetal = matte("#23262c", { roughness: 0.55, metalness: 0.3 });
const brass = matte("#b8914f", { roughness: 0.32, metalness: 0.75 });
const graphite = matte("#2a2c31", { roughness: 0.4, metalness: 0.3 });

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
  letterTray: { geometry: GEO.box, material: tintedGloss, tinted: true, cast: true },
  trayPost: { geometry: GEO.cyl, material: brass, tinted: false, cast: false },
  paper: { geometry: GEO.box, material: tintedMatte, tinted: true, cast: true },
  page: { geometry: new PlaneGeometry(1, 1), material: faceMaterial("desk:page", 64, 88, drawPage, "#fbfaf6"), tinted: false, cast: false },
  folder: { geometry: GEO.box, material: tintedMatte, tinted: true, cast: true },
  tabletBody: { geometry: new RoundedBoxGeometry(0.24, 0.17, 0.008, 2, 0.004), material: graphite, tinted: false, cast: true },
  tabletScreen: {
    geometry: new PlaneGeometry(0.224, 0.154), material: faceMaterial("desk:calendar", 128, 88, drawCalendar, "#e9e5dc", 0.45), tinted: false, cast: false,
  },
  tabletStand: { geometry: GEO.box, material: brass, tinted: false, cast: true },
  phoneBody: { geometry: new RoundedBoxGeometry(0.072, 0.15, 0.008, 2, 0.004), material: graphite, tinted: false, cast: true },
  phoneScreen: {
    geometry: new PlaneGeometry(0.064, 0.138), material: faceMaterial("desk:messages", 64, 128, drawMessages, "#1b1d22", 0.5), tinted: false, cast: false,
  },
  dock: { geometry: GEO.box, material: aluminium, tinted: false, cast: true },
  penCup: { geometry: GEO.cyl, material: tintedGloss, tinted: true, cast: true },
  pencil: { geometry: GEO.cyl, material: tintedGloss, tinted: true, cast: false },
  frame: { geometry: GEO.box, material: tintedGloss, tinted: true, cast: true },
  photo: { geometry: new PlaneGeometry(1, 1), material: faceMaterial("desk:photo", 64, 84, drawPhoto, "#c9b99c"), tinted: true, cast: false },
  cup: { geometry: new CylinderGeometry(1, 0.78, 1, 16), material: tintedMatte, tinted: true, cast: true },
  cupLid: { geometry: GEO.cyl, material: tintedGloss, tinted: true, cast: false },
  cupSleeve: { geometry: new CylinderGeometry(1, 0.9, 1, 16), material: matte("#b98b5e", { roughness: 0.9 }), tinted: false, cast: false },
  oakPlanter: { geometry: GEO.box, material: matte("#d8bd91", { roughness: 0.7 }), tinted: false, cast: true },
  oakSlat: { geometry: GEO.box, material: matte("#caa574", { roughness: 0.65 }), tinted: false, cast: true },
  brassTrim: { geometry: GEO.box, material: brass, tinted: false, cast: false },
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

/**
 * Every instance of every part on the floor: desk kits (desk space → world) and planters (already world space).
 * The coding floor carries the developer's kit and walnut planters, the agents floor the office kit and oak planters.
 */
export function buildDressing(
  desks: readonly DeskSlot[], departments: readonly Department[], floor: OfficeVariant = "coding",
): Map<DressingPart, Batch> {
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
    const occupied = desk.agentId !== null;
    const kit = floor === "coding" ? kitPlacements(dressDesk(desk.id, occupied)) : officeKitPlacements(dressOfficeDesk(desk.id, occupied));
    for (const part of kit) add(part, world);
  }
  const planter = floor === "coding" ? planterPlacements : oakPlanterPlacements;
  for (const dept of departments) {
    for (const { key, rect } of benchPlanters(dept)) for (const part of planter(rect, key)) add(part);
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
export function DeskDressing({ desks, departments, floor = "coding" }: {
  desks: readonly DeskSlot[]; departments: readonly Department[]; floor?: OfficeVariant;
}) {
  const batches = useMemo(() => buildDressing(desks, departments, floor), [desks, departments, floor]);
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
