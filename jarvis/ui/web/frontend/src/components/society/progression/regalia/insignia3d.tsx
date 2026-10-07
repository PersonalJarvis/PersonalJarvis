/**
 * The rank insignia in 3D, extruded from the same polygons as the SVG icons
 * (`insignia/rankArt.ts`): navy cloth backing, raised gold embroidery, struck
 * gold and silver pins with a bevelled edge. The piece a figure wears and
 * the piece in a display case are the same geometry.
 *
 * Built in a local frame: the insignia lies in the x/y plane, its front
 * facing +z, its back at z = 0, `width` metres wide. With `wrapRadius` the
 * piece bends round a cylinder of that radius (an upper sleeve), so a flat
 * patch never floats off a round arm.
 */
import { useMemo } from "react";
import { BufferGeometry, ExtrudeGeometry, MeshStandardMaterial, Shape, Vector2 } from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { RankId, RankTier } from "../levelCatalog";
import { RANK_INFO } from "../levelCatalog";
import { rankArt, type Finish, type InsigniaArt, type Pt } from "../insignia/rankArt";

/** Materials shared by every insignia: struck metal catches the light, cloth stays matte. */
export const REGALIA_MATERIALS = {
  gold: new MeshStandardMaterial({ color: "#d9aa45", metalness: 0.62, roughness: 0.32, emissive: "#3b2906", emissiveIntensity: 0.35 }),
  goldDetail: new MeshStandardMaterial({ color: "#9a6f1f", metalness: 0.55, roughness: 0.42 }),
  silver: new MeshStandardMaterial({ color: "#dfe3ea", metalness: 0.66, roughness: 0.26, emissive: "#2a2e36", emissiveIntensity: 0.3 }),
  silverDetail: new MeshStandardMaterial({ color: "#8e96a3", metalness: 0.6, roughness: 0.35 }),
  cloth: new MeshStandardMaterial({ color: "#1b2340", roughness: 0.95 }),
  boardNavy: new MeshStandardMaterial({ color: "#18213d", roughness: 0.85 }),
  boardBlack: new MeshStandardMaterial({ color: "#121318", roughness: 0.7 }),
  braid: new MeshStandardMaterial({ color: "#d2a33f", metalness: 0.5, roughness: 0.45, emissive: "#3b2906", emissiveIntensity: 0.3 }),
  /** A rank not reached yet: the whole piece in one dark pewter, a silhouette that still shows its form. */
  muted: new MeshStandardMaterial({ color: "#3a3f4c", metalness: 0.35, roughness: 0.6 }),
} as const;

type Layer = "cloth" | "gold" | "silver" | "goldDetail" | "silverDetail";

/** Depths in insignia units (the art is 100 units wide). */
const DEPTH = { cloth: 2.2, metal: 3.4, detail: 1.1 };

function shapeOf(pts: readonly Pt[], h: number): Shape {
  // Art space: y down, origin top-left. Shape space: y up, centred.
  return new Shape(pts.map(([x, y]) => new Vector2(x - 50, h / 2 - y)));
}

/** Bends a piece round a vertical cylinder of radius `r` (insignia units): x runs round it, z grows outward. */
function wrap(geometry: BufferGeometry, r: number): void {
  const pos = geometry.attributes.position;
  for (let i = 0; i < pos.count; i += 1) {
    const x = pos.getX(i), z = pos.getZ(i);
    const angle = x / r;
    pos.setXYZ(i, (r + z) * Math.sin(angle), pos.getY(i), (r + z) * Math.cos(angle) - r);
  }
  pos.needsUpdate = true;
  geometry.computeVertexNormals();
}

interface Built { layer: Layer; geometry: BufferGeometry }

const builtCache = new Map<string, Built[]>();

/** The merged geometry per material layer of a rank's insignia, in insignia units. */
function buildArt(id: string, art: InsigniaArt, wrapUnits: number | null): Built[] {
  const key = `${id}|${wrapUnits ?? "flat"}`;
  const cached = builtCache.get(key);
  if (cached) return cached;
  const hasCloth = art.parts.some((p) => p.finish === "cloth");
  const metalBase = hasCloth ? DEPTH.cloth : 0;
  const byLayer = new Map<Layer, BufferGeometry[]>();
  for (const part of art.parts) {
    const shape = shapeOf(part.pts, art.h);
    let geometry: BufferGeometry;
    let layer: Layer;
    if (part.finish === "cloth") {
      geometry = new ExtrudeGeometry(shape, { depth: DEPTH.cloth, bevelEnabled: false, curveSegments: 4 });
      layer = "cloth";
    } else if (part.detail) {
      geometry = new ExtrudeGeometry(shape, { depth: DEPTH.detail, bevelEnabled: false, curveSegments: 4 });
      geometry.translate(0, 0, metalBase + DEPTH.metal);
      layer = part.finish === "gold" ? "goldDetail" : "silverDetail";
    } else {
      geometry = new ExtrudeGeometry(shape, {
        depth: DEPTH.metal - 0.8, bevelEnabled: true, bevelThickness: 0.6, bevelSize: 0.55, bevelSegments: 2, curveSegments: 4,
      });
      geometry.translate(0, 0, metalBase + 0.6);
      layer = part.finish as Finish as Layer;
    }
    // Merging needs one attribute layout; extrusions carry uv and normal alike.
    const list = byLayer.get(layer) ?? [];
    list.push(geometry.index ? geometry.toNonIndexed() : geometry);
    byLayer.set(layer, list);
  }
  const built: Built[] = [];
  for (const [layer, list] of byLayer) {
    const merged = mergeGeometries(list, false);
    if (!merged) continue;
    if (wrapUnits) wrap(merged, wrapUnits);
    else merged.computeVertexNormals();
    built.push({ layer, geometry: merged });
  }
  builtCache.set(key, built);
  return built;
}

/** Any piece of vector art (`rankArt.ts`) in 3D, `width` metres wide; `id` names it in the geometry cache. */
export function ArtPiece3D({ id, art, width, wrapRadius, cast = false, muted = false }: {
  id: string; art: InsigniaArt; width: number;
  /** Draw every layer in the muted pewter (a rank not reached yet). */
  muted?: boolean;
  /** Bend round a cylinder of this radius in metres (a sleeve, a cap's front). */
  wrapRadius?: number;
  cast?: boolean;
}) {
  const s = width / art.w;
  const parts = useMemo(() => buildArt(id, art, wrapRadius ? wrapRadius / s : null), [id, art, wrapRadius, s]);
  if (parts.length === 0) return null;
  return (
    <group scale={s}>
      {parts.map(({ layer, geometry }) => (
        <mesh key={layer} geometry={geometry} material={muted ? REGALIA_MATERIALS.muted : REGALIA_MATERIALS[layer]} castShadow={cast} />
      ))}
    </group>
  );
}

/** A rank's insignia, `width` metres wide; nothing for a private, who wears none. */
export function RankInsignia3D({ rank, width, wrapRadius, cast, muted }: {
  rank: RankId; width: number; wrapRadius?: number; cast?: boolean; muted?: boolean;
}) {
  return <ArtPiece3D id={`rank:${rank}`} art={rankArt(rank)} width={width} wrapRadius={wrapRadius} cast={cast} muted={muted} />;
}

/** The height of a rank's insignia at `width` metres wide. */
export function insigniaHeight(rank: RankId, width: number): number {
  const art = rankArt(rank);
  return (art.h / art.w) * width;
}

// ---------------------------------------------------------------------------
// Where a figure wears it

/** Upper-sleeve patch of an enlisted rank: centred on the arm surface, bent round it. */
export function SleevePatch({ rank, armRadius, width = 0.07 }: { rank: RankId; armRadius: number; width?: number }) {
  if (rankArt(rank).mount !== "sleeve") return null;
  return <RankInsignia3D rank={rank} width={width} wrapRadius={armRadius} />;
}

/** The shoulder board of an officer or general: its length runs along local +x, its top faces +y. */
export const BOARD = { length: 0.135, width: 0.066, thick: 0.012 };

function boardLook(tier: RankTier): { base: MeshStandardMaterial; edge: number } {
  return tier === "general" ? { base: REGALIA_MATERIALS.boardBlack, edge: 0.012 } : { base: REGALIA_MATERIALS.boardNavy, edge: 0.004 };
}

export function ShoulderBoard({ rank }: { rank: RankId }) {
  const art = rankArt(rank);
  if (art.mount !== "shoulder") return null;
  const { base, edge } = boardLook(RANK_INFO[rank].tier);
  const { length: L, width: W, thick: T } = BOARD;
  // The insignia's long axis (art y) runs along the board, fitted inside the braid.
  const fit = Math.min((W - edge * 2) * 0.92, ((L - edge * 2 - 0.02) * 0.92 * art.w) / art.h);
  return (
    <group>
      <mesh material={base} position={[0, T / 2, 0]} castShadow>
        <boxGeometry args={[L, T, W]} />
      </mesh>
      {/* The pointed inner end that buttons at the collar. */}
      <mesh material={base} position={[-L / 2, T / 2, 0]} rotation={[0, Math.PI / 4, 0]}>
        <boxGeometry args={[W / Math.SQRT2, T, W / Math.SQRT2]} />
      </mesh>
      {/* Gold braid round the edge: a fine line for officers, a broad band for generals. */}
      {[-1, 1].map((sz) => (
        <mesh key={sz} material={REGALIA_MATERIALS.braid} position={[0.004, T + 0.0012, sz * (W / 2 - edge / 2)]}>
          <boxGeometry args={[L - 0.008, 0.0024, edge]} />
        </mesh>
      ))}
      <mesh material={REGALIA_MATERIALS.braid} position={[L / 2 - edge / 2, T + 0.0012, 0]}>
        <boxGeometry args={[edge, 0.0024, W]} />
      </mesh>
      <mesh material={REGALIA_MATERIALS.braid} position={[-L / 2 - W * 0.36, T + 0.002, 0]}>
        <cylinderGeometry args={[0.009, 0.009, 0.004, 16]} />
      </mesh>
      <group position={[0.008, T + 0.0024, 0]} rotation={[-Math.PI / 2, 0, -Math.PI / 2]}>
        <RankInsignia3D rank={rank} width={fit} />
      </group>
    </group>
  );
}
