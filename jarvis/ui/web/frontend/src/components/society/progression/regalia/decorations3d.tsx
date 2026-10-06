/**
 * Decorations in 3D: the ribbon rack over the left breast, full-size medals
 * on their drapes, and the gold aiguillette from the right shoulder. Each
 * piece is built round its own anchor so the same component sits on a
 * figure's chest and on the velvet board of a display case.
 *
 * - Ribbons and medals: origin at the centre of the rack, lying in the x/y
 *   plane, facing +z.
 * - Aiguillette: origin where it hangs from the shoulder; its loops fall in
 *   the x/y plane towards +x (the chest's centre when worn on the right).
 */
import { useMemo } from "react";
import { ExtrudeGeometry, MeshStandardMaterial, Shape, Vector2 } from "three";
import { cachedCanvasTexture } from "../../office/canvasMaterials";
import type { RewardId } from "../levelCatalog";
import { ellipse, star, type Pt } from "../insignia/rankArt";
import { MEDAL_COUNT, MEDAL_METALS, paintRibbon, RIBBONS, type RibbonSpec } from "./ribbons";
import { REGALIA_MATERIALS } from "./insignia3d";

/** A ribbon bar's size in metres (a real one is 35 × 10 mm; the toy wears it larger). */
export const RIBBON = { w: 0.03, h: 0.0095, gap: 0.0008 };

const ribbonMaterials = new Map<string, MeshStandardMaterial>();
function ribbonMaterial(spec: RibbonSpec): MeshStandardMaterial {
  let material = ribbonMaterials.get(spec.id);
  if (!material) {
    const map = cachedCanvasTexture(`ribbon:${spec.id}`, 128, 40, (ctx, w, h) => paintRibbon(ctx, spec, w, h));
    material = new MeshStandardMaterial({ map, color: map ? "#ffffff" : spec.stripes[0][0], roughness: 0.55 });
    ribbonMaterials.set(spec.id, material);
  }
  return material;
}

/** Which ribbons a decoration wears, in rack order (highest first). */
export function ribbonsFor(decoration: RewardId): RibbonSpec[] {
  if (decoration === "decoration_ribbon_bar") return [RIBBONS[4], RIBBONS[5], RIBBONS[6]];
  if (decoration === "decoration_medals") return RIBBONS.slice(0, MEDAL_COUNT);
  return [...RIBBONS];
}

/** Rows of three, the short row on top and centred, the way a rack is mounted. */
export function rackRows(count: number): number[] {
  const rows: number[] = [];
  let left = count;
  while (left > 0) { const n = left % 3 || 3; rows.push(n); left -= n; }
  return rows;
}

export function RibbonRack({ ribbons }: { ribbons: readonly RibbonSpec[] }) {
  const rows = rackRows(ribbons.length);
  const { w, h, gap } = RIBBON;
  const height = rows.length * (h + gap);
  let index = 0;
  return (
    <group>
      {rows.map((n, row) => {
        const y = height / 2 - (row + 0.5) * (h + gap);
        return Array.from({ length: n }, (_, i) => {
          const spec = ribbons[index++];
          const x = (i - (n - 1) / 2) * (w + gap);
          return (
            <mesh key={spec.id} material={ribbonMaterial(spec)} position={[x, y, 0.0025]}>
              <boxGeometry args={[w, h, 0.005]} />
            </mesh>
          );
        });
      })}
    </group>
  );
}

function shape(pts: readonly Pt[], s: number): Shape {
  return new Shape(pts.map(([x, y]) => new Vector2((x - 50) * s, (50 - y) * s)));
}

const medalGeometry = new Map<string, ExtrudeGeometry>();
function medalShape(kind: RibbonSpec["medal"]["shape"], size: number): ExtrudeGeometry {
  const key = `${kind}:${size}`;
  let geometry = medalGeometry.get(key);
  if (!geometry) {
    const s = size / 100;
    const outline = kind === "star" ? star(50, 52, 50) : kind === "cross"
      ? [[38, 4], [62, 4], [58, 38], [96, 38], [96, 62], [58, 62], [62, 96], [38, 96], [42, 62], [4, 62], [4, 38], [42, 38]] as Pt[]
      : ellipse(50, 50, 48, 48, 32);
    geometry = new ExtrudeGeometry(shape(outline, s), { depth: size * 0.06, bevelEnabled: true, bevelThickness: size * 0.03, bevelSize: size * 0.03, bevelSegments: 2 });
    geometry.computeVertexNormals();
    medalGeometry.set(key, geometry);
  }
  return geometry;
}

const medalMaterials = new Map<string, MeshStandardMaterial>();
function medalMaterial(metal: RibbonSpec["medal"]["metal"]): MeshStandardMaterial {
  let material = medalMaterials.get(metal);
  if (!material) {
    const tone = MEDAL_METALS[metal];
    material = new MeshStandardMaterial({ color: tone.face, metalness: 0.65, roughness: 0.3, emissive: tone.edge, emissiveIntensity: 0.25 });
    medalMaterials.set(metal, material);
  }
  return material;
}

/** Full-size medals in a row: each on its own short drape of ribbon, slightly overlapping like a court mount. */
export function MedalRow({ ribbons }: { ribbons: readonly RibbonSpec[] }) {
  const step = 0.019, drape = 0.024, size = 0.02;
  return (
    <group>
      {ribbons.map((spec, i) => {
        const x = (i - (ribbons.length - 1) / 2) * step;
        return (
          <group key={spec.id} position={[x, 0, 0.003 + i * 0.0006]}>
            <mesh material={ribbonMaterial(spec)} position={[0, -drape / 2, 0]}>
              <boxGeometry args={[step * 0.95, drape, 0.003]} />
            </mesh>
            <mesh geometry={medalShape(spec.medal.shape, size)} material={medalMaterial(spec.medal.metal)} position={[0, -drape - size * 0.42, 0.001]} castShadow />
          </group>
        );
      })}
      {/* The brass bar the drapes hang from. */}
      <mesh material={REGALIA_MATERIALS.gold} position={[0, 0.002, 0.004]}>
        <boxGeometry args={[step * ribbons.length + 0.004, 0.004, 0.004]} />
      </mesh>
    </group>
  );
}

/** Height of a decoration on the chest, so the anchor can sit it above a pocket. */
export function decorationSize(decoration: RewardId): { w: number; h: number } {
  if (decoration === "decoration_medals") return { w: 0.019 * MEDAL_COUNT, h: 0.024 + 0.02 };
  const rows = rackRows(ribbonsFor(decoration).length).length;
  return { w: 3 * (RIBBON.w + RIBBON.gap), h: rows * (RIBBON.h + RIBBON.gap) };
}

/** The decoration over the left breast: a rack, or the medals. Origin: the top centre of the piece. */
export function BreastDecoration({ decoration }: { decoration: RewardId }) {
  const ribbons = useMemo(() => ribbonsFor(decoration), [decoration]);
  if (decoration === "decoration_medals") return <MedalRow ribbons={ribbons} />;
  const { h } = decorationSize(decoration);
  return <group position={[0, -h / 2, 0]}><RibbonRack ribbons={ribbons} /></group>;
}

/**
 * The aiguillette: braided gold cords looping from the shoulder across the
 * chest, two of them ending in pointed metal tips.
 */
export function Aiguillette({ span = 0.11 }: { span?: number }) {
  const loops = [0.05, 0.075, 0.1];
  return (
    <group>
      {loops.map((sag, i) => (
        <mesh key={i} material={REGALIA_MATERIALS.braid} position={[span / 2, 0, i * 0.003]} rotation={[0, 0, Math.PI]} scale={[1, sag / (span / 2), 1]}>
          <torusGeometry args={[span / 2, 0.0042, 6, 28, Math.PI]} />
        </mesh>
      ))}
      {[0.32, 0.55].map((k, i) => {
        const x = span * k, top = -0.02, length = 0.07 + i * 0.015;
        return (
          <group key={k} position={[x, top, 0.008]}>
            <mesh material={REGALIA_MATERIALS.braid} position={[0, -length / 2, 0]}>
              <cylinderGeometry args={[0.0038, 0.0038, length, 6]} />
            </mesh>
            <mesh material={REGALIA_MATERIALS.gold} position={[0, -length - 0.008, 0]} rotation={[Math.PI, 0, 0]}>
              <coneGeometry args={[0.0055, 0.018, 10]} />
            </mesh>
          </group>
        );
      })}
      <mesh material={REGALIA_MATERIALS.gold} position={[span, 0, 0.004]}>
        <sphereGeometry args={[0.006, 10, 8]} />
      </mesh>
    </group>
  );
}
