/**
 * The coding floor's own look. It shares the agents office's plan — rooms,
 * desks, paths — but none of its surfaces: terrazzo instead of planks, a
 * glowing rim around the slab, and every workspace department furnished as a
 * studio of its own (carpet pattern, wall colour, desks and chairs).
 *
 * Purely visual. Nothing here is an obstacle: the carpet and rim are flat or
 * outside the railing, so navigation stays exactly the layout's.
 */
import { useEffect, useMemo } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import { MeshStandardMaterial, RepeatWrapping, type Texture } from "three";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { cachedCanvasTexture } from "./canvasMaterials";
import { DeskInstances, type DeskTone } from "./DeskInstances";
import { Box, matte, Railing, SignWall } from "./OfficeFurniture";
import type { Department, OfficeLayout } from "./officeLayout";
import { CODING_SCENE, CODING_STUDIOS, type CarpetPattern, type StudioStyle } from "./officePalette";

/** The studio a department is furnished as: cycles through the set by the department's tint index. */
export function studioStyle(index: number): StudioStyle {
  const n = CODING_STUDIOS.length;
  return CODING_STUDIOS[((index % n) + n) % n];
}

/** Metres covered by one repeat of the terrazzo tile and of a carpet pattern. */
const TERRAZZO_METRES = 2.4;
const CARPET_METRES = 2.4;
/** The rug's border band around the patterned field. */
const BORDER_M = 0.22;

/** Deterministic pseudo-random numbers, so the chips never shimmer between loads. */
function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

function drawTerrazzo(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const { base, seam, chips } = CODING_SCENE.terrazzo;
  const rand = lcg(0x7e22a);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 1400; i += 1) {
    ctx.fillStyle = chips[Math.floor(rand() * chips.length)];
    const size = 0.8 + rand() * 1.8;
    ctx.beginPath();
    ctx.ellipse(rand() * w, rand() * h, size, size * (0.5 + rand() * 0.5), rand() * Math.PI, 0, Math.PI * 2);
    ctx.fill();
  }
  // Four slabs per tile: seams on the tile's edges and centre lines, so it wraps seamlessly.
  ctx.fillStyle = seam;
  for (const at of [0, w / 2]) {
    ctx.fillRect(at, 0, 2, h);
    ctx.fillRect(0, at, w, 2);
  }
}

function drawCarpet(pattern: CarpetPattern, carpet: string, weave: string, ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = carpet;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = weave;
  switch (pattern) {
    case "grid":
      for (let at = 0; at < w; at += 32) { ctx.fillRect(at, 0, 3, h); ctx.fillRect(0, at, w, 3); }
      break;
    case "stripes":
      for (let y = 0; y < h; y += 64) ctx.fillRect(0, y, w, 26);
      break;
    case "checker":
      for (let y = 0; y < h; y += 64) for (let x = 0; x < w; x += 64) if (((x + y) / 64) % 2 === 0) ctx.fillRect(x, y, 64, 64);
      break;
    case "dots":
      for (let y = 16; y < h; y += 32) for (let x = (y / 32) % 2 === 0 ? 0 : 16; x < w + 16; x += 32) {
        ctx.beginPath();
        ctx.arc(x, y, 6, 0, Math.PI * 2);
        ctx.fill();
      }
      break;
    case "diagonal":
      // 45° bands drawn across a doubled span, so the tile wraps on both axes.
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, w, h);
      ctx.clip();
      for (let k = -h; k < w + h; k += 32) {
        ctx.beginPath();
        ctx.moveTo(k, 0); ctx.lineTo(k + 14, 0); ctx.lineTo(k + 14 + h, h); ctx.lineTo(k + h, h);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
      break;
    case "zigzag":
      ctx.lineWidth = 10;
      ctx.strokeStyle = weave;
      for (let y = 16; y < h + 32; y += 48) {
        ctx.beginPath();
        for (let x = 0; x <= w; x += 32) ctx.lineTo(x, y + ((x / 32) % 2 === 0 ? 0 : 20));
        ctx.stroke();
      }
      break;
  }
}

/** A cached base texture, cloned per surface with its own repeat. */
function repeated(base: Texture | null, repeatX: number, repeatZ: number): Texture | null {
  if (!base) return null;
  const map = base.clone();
  map.wrapS = map.wrapT = RepeatWrapping;
  map.anisotropy = 8;
  map.repeat.set(repeatX, repeatZ);
  map.needsUpdate = true;
  return map;
}

/** Studio materials are shared by every department that cycles onto the same style. */
const studioMaterials = new Map<StudioStyle, { tone: DeskTone; wall: MeshStandardMaterial; slat: MeshStandardMaterial; border: MeshStandardMaterial }>();
function studioKit(style: StudioStyle) {
  let kit = studioMaterials.get(style);
  if (!kit) {
    kit = {
      tone: {
        top: matte(style.deskTop), body: matte(style.deskBody), leg: matte(style.deskLeg),
        chair: matte(style.chair), seat: matte(style.seat),
      },
      wall: matte(style.wall),
      slat: matte(style.slat),
      border: matte(style.border, { roughness: 0.95 }),
    };
    studioMaterials.set(style, kit);
  }
  return kit;
}

/** The slab, railing and floor of the coding floor: terrazzo, a dark edge and a glowing rim. */
export function CodingSlab({ layout, onFloorClick }: { layout: OfficeLayout; onFloorClick: (event: ThreeEvent<MouseEvent>) => void }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const w = maxX - minX, d = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  const floor = useMemo(() => {
    const map = repeated(cachedCanvasTexture("coding:terrazzo", 256, 256, drawTerrazzo), w / TERRAZZO_METRES, d / TERRAZZO_METRES);
    return new MeshStandardMaterial({ color: map ? "#ffffff" : CODING_SCENE.terrazzo.base, map, roughness: 0.55 });
  }, [w, d]);
  const edge = useMemo(() => matte(CODING_SCENE.slabEdge, { roughness: 0.7 }), []);
  const rim = useMemo(() => new MeshStandardMaterial({
    color: CODING_SCENE.rim, emissive: CODING_SCENE.rim, emissiveIntensity: 1.4, roughness: 0.4, toneMapped: false,
  }), []);
  useEffect(() => () => { floor.map?.dispose(); floor.dispose(); edge.dispose(); rim.dispose(); }, [floor, edge, rim]);
  const band = 0.06;
  return (
    <group>
      <Box size={[w, 0.6, d]} position={[cx, -0.3, cz]} material={edge} cast={false} />
      {/* The glowing rim runs just below the floor's edge on all four sides. */}
      <Box size={[w + band, band, band]} position={[cx, -0.06, minZ]} material={rim} cast={false} />
      <Box size={[w + band, band, band]} position={[cx, -0.06, maxZ]} material={rim} cast={false} />
      <Box size={[band, band, d + band]} position={[minX, -0.06, cz]} material={rim} cast={false} />
      <Box size={[band, band, d + band]} position={[maxX, -0.06, cz]} material={rim} cast={false} />
      <mesh position={[cx, 0.001, cz]} rotation={[-Math.PI / 2, 0, 0]} material={floor} receiveShadow onClick={onFloorClick}>
        <planeGeometry args={[w, d]} />
      </mesh>
      <Railing from={[minX + 0.2, minZ + 0.2]} to={[maxX - 0.2, minZ + 0.2]} />
      <Railing from={[maxX - 0.2, minZ + 0.2]} to={[maxX - 0.2, maxZ - 0.2]} />
      <Railing from={[maxX - 0.2, maxZ - 0.2]} to={[minX + 0.2, maxZ - 0.2]} />
      <Railing from={[minX + 0.2, maxZ - 0.2]} to={[minX + 0.2, minZ + 0.2]} />
    </group>
  );
}

/** One workspace department as a studio: bordered patterned rug, painted sign wall, desks in its own colours. */
export function CodingStudio({ dept, agents }: { dept: Department; agents: ReadonlyMap<string, SocietyAgent> }) {
  const t = useT();
  const style = studioStyle(dept.tint);
  const kit = studioKit(style);
  const w = dept.maxX - dept.minX, d = dept.maxZ - dept.minZ;
  const cx = (dept.minX + dept.maxX) / 2, cz = (dept.minZ + dept.maxZ) / 2;
  const fieldW = w - BORDER_M * 2, fieldD = d - BORDER_M * 2;
  const carpet = useMemo(() => {
    const base = cachedCanvasTexture(`coding:carpet:${style.pattern}:${style.carpet}:${style.weave}`, 256, 256,
      (ctx, cw, ch) => drawCarpet(style.pattern, style.carpet, style.weave, ctx, cw, ch));
    const map = repeated(base, fieldW / CARPET_METRES, fieldD / CARPET_METRES);
    return new MeshStandardMaterial({ color: map ? "#ffffff" : style.carpet, map, roughness: 0.95 });
  }, [style, fieldW, fieldD]);
  useEffect(() => () => { carpet.map?.dispose(); carpet.dispose(); }, [carpet]);
  return (
    <group>
      <mesh position={[cx, 0.005, cz]} rotation={[-Math.PI / 2, 0, 0]} material={kit.border} receiveShadow>
        <planeGeometry args={[w, d]} />
      </mesh>
      <mesh position={[cx, 0.007, cz]} rotation={[-Math.PI / 2, 0, 0]} material={carpet} receiveShadow>
        <planeGeometry args={[fieldW, fieldD]} />
      </mesh>
      <SignWall label={dept.label || t("society.office.open_space")} width={w - 0.4} position={[cx, 0, dept.minZ + 0.1]}
        tone={{ wall: kit.wall, slat: kit.slat }} />
      <DeskInstances desks={dept.desks} agents={agents} tone={kit.tone} />
    </group>
  );
}
