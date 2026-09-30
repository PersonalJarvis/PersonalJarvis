/**
 * The coding floor's own look. It shares the agents office's plan — rooms,
 * desks, paths — but none of its surfaces: honey-oak herringbone framed by a
 * polished concrete band along the windows, a warm glowing rim around the
 * slab, and every workspace department furnished as a studio of its own
 * (bordered rug, a walnut slat wall with brass lettering, desks and chairs).
 *
 * Purely visual. The only solid part is the studio wall, which stays inside
 * the sign-wall footprint the layout already walks round.
 */
import { useEffect, useMemo } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import { AdditiveBlending, MeshBasicMaterial, MeshStandardMaterial, RepeatWrapping, type Texture } from "three";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { cachedCanvasTexture } from "./canvasMaterials";
import { DeskInstances, type DeskTone } from "./DeskInstances";
import { Box, GEO, MAT, matte, Railing } from "./OfficeFurniture";
import type { Department, OfficeLayout } from "./officeLayout";
import { CODING_SCENE, CODING_STUDIOS, type CarpetPattern, type StudioStyle } from "./officePalette";

/** The studio a department is furnished as: cycles through the set by the department's tint index. */
export function studioStyle(index: number): StudioStyle {
  const n = CODING_STUDIOS.length;
  return CODING_STUDIOS[((index % n) + n) % n];
}

/** Metres covered by one repeat of the herringbone tile and of a carpet pattern. */
const OAK_METRES = 2.4;
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

/**
 * Herringbone oak blocks, 4:1, on an axis-aligned lattice: block n of strip m
 * lies at (n + 4m, n - 4m) block widths, one horizontal and one vertical. The
 * pattern repeats every 8 widths, so the 256-px tile (two periods) wraps.
 */
function drawHerringbone(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const { seam, planks } = CODING_SCENE.oak;
  const u = 16, k = 4;
  ctx.fillStyle = seam;
  ctx.fillRect(0, 0, w, h);
  const block = (x: number, y: number, bw: number, bh: number) => {
    // The tone hashes the block's wrapped position, so every copy of it agrees.
    const wx = ((x % w) + w) % w, wy = ((y % h) + h) % h;
    const tone = planks[Math.floor((wx * 7 + wy * 13) / u) % planks.length];
    for (const dx of [-w, 0, w]) {
      for (const dy of [-h, 0, h]) {
        const px = wx + dx, py = wy + dy;
        if (px > w || py > h || px + bw < 0 || py + bh < 0) continue;
        ctx.fillStyle = tone;
        ctx.fillRect(px + 0.75, py + 0.75, bw - 1.5, bh - 1.5);
        // A faint grain along the block's length.
        ctx.fillStyle = "rgba(96,64,36,0.10)";
        if (bw > bh) for (const gy of [0.3, 0.62]) ctx.fillRect(px + 2, py + bh * gy, bw - 4, 0.8);
        else for (const gx of [0.3, 0.62]) ctx.fillRect(px + bw * gx, py + 2, 0.8, bh - 4);
      }
    }
  };
  const span = w / u;
  for (let m = -span; m <= span; m += 1) {
    for (let n = -span * 2; n <= span * 2; n += 1) {
      const x = (n + k * m) * u, y = (n - k * m) * u;
      if (x < -k * u * 2 || x > w + k * u || y < -k * u * 2 || y > h + k * u) continue;
      block(x, y, k * u, u);
      block(x, y + u, u, k * u);
    }
  }
}

/** Polished concrete: soft clouds of tone and a fine grain, seamless on both axes. */
function drawConcrete(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const { base, cloud } = CODING_SCENE.concrete;
  const rand = lcg(0xc0ffee);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 40; i += 1) {
    const x = rand() * w, y = rand() * h, r = 16 + rand() * 50;
    const tone = cloud[i % cloud.length];
    for (const dx of [-w, 0, w]) {
      for (const dy of [-h, 0, h]) {
        const g = ctx.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, r);
        g.addColorStop(0, `${tone}88`);
        g.addColorStop(1, `${tone}00`);
        ctx.fillStyle = g;
        ctx.fillRect(x + dx - r, y + dy - r, r * 2, r * 2);
      }
    }
  }
  for (let i = 0; i < 1800; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.12)" : "rgba(80,70,60,0.08)";
    ctx.fillRect(Math.floor(rand() * w), Math.floor(rand() * h), 1, 1);
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
const studioMaterials = new Map<StudioStyle, { tone: DeskTone; slat: MeshStandardMaterial; border: MeshStandardMaterial }>();
function studioKit(style: StudioStyle) {
  let kit = studioMaterials.get(style);
  if (!kit) {
    kit = {
      tone: {
        top: matte(style.deskTop), body: matte(style.deskBody), leg: matte(style.deskLeg),
        chair: matte(style.chair), seat: matte(style.seat),
      },
      slat: matte(style.slat),
      border: matte(style.border, { roughness: 0.95 }),
    };
    studioMaterials.set(style, kit);
  }
  return kit;
}

/** Width of the polished concrete band along the slab's edge, and of its brass inlay. */
const BAND_M = 1.35;
const INLAY_M = 0.035;

/**
 * The slab, railing and floor of the coding floor: herringbone oak, framed by
 * a concrete band with a brass inlay along the windows, a dark edge and a
 * warm glowing rim.
 */
export function CodingSlab({ layout, onFloorClick }: { layout: OfficeLayout; onFloorClick: (event: ThreeEvent<MouseEvent>) => void }) {
  const { minX, maxX, minZ, maxZ } = layout.bounds;
  const w = maxX - minX, d = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  const innerW = w - BAND_M * 2, innerD = d - BAND_M * 2;
  const floor = useMemo(() => {
    const map = repeated(cachedCanvasTexture("coding:herringbone", 256, 256, drawHerringbone), w / OAK_METRES, d / OAK_METRES);
    return new MeshStandardMaterial({ color: map ? "#ffffff" : CODING_SCENE.oak.planks[0], map, roughness: 0.5 });
  }, [w, d]);
  // The band's strips: north and south run the full width, east and west fit between them; one repeat per 3 m.
  const band = useMemo(() => {
    const base = cachedCanvasTexture("coding:concrete", 256, 256, drawConcrete);
    const strip = (along: number, across: number) => {
      const map = repeated(base, along / 3, across / 3);
      return new MeshStandardMaterial({ color: map ? "#ffffff" : CODING_SCENE.concrete.base, map, roughness: 0.4 });
    };
    return { ns: strip(w, BAND_M), ew: strip(BAND_M, innerD) };
  }, [w, innerD]);
  const inlay = useMemo(() => matte(CODING_SCENE.concrete.brass, {
    roughness: 0.35, metalness: 0.5, emissive: CODING_SCENE.concrete.brass, emissiveIntensity: 0.15,
  }), []);
  const edge = useMemo(() => matte(CODING_SCENE.slabEdge, { roughness: 0.7 }), []);
  const rim = useMemo(() => new MeshStandardMaterial({
    color: CODING_SCENE.rim, emissive: CODING_SCENE.rim, emissiveIntensity: 1.4, roughness: 0.4, toneMapped: false,
  }), []);
  useEffect(() => () => {
    for (const m of [floor, band.ns, band.ew]) { m.map?.dispose(); m.dispose(); }
    inlay.dispose(); edge.dispose(); rim.dispose();
  }, [floor, band, inlay, edge, rim]);
  const glow = 0.06;
  return (
    <group>
      <Box size={[w, 0.6, d]} position={[cx, -0.3, cz]} material={edge} cast={false} />
      {/* The glowing rim runs just below the floor's edge on all four sides. */}
      <Box size={[w + glow, glow, glow]} position={[cx, -0.06, minZ]} material={rim} cast={false} />
      <Box size={[w + glow, glow, glow]} position={[cx, -0.06, maxZ]} material={rim} cast={false} />
      <Box size={[glow, glow, d + glow]} position={[minX, -0.06, cz]} material={rim} cast={false} />
      <Box size={[glow, glow, d + glow]} position={[maxX, -0.06, cz]} material={rim} cast={false} />
      <mesh position={[cx, 0.001, cz]} rotation={[-Math.PI / 2, 0, 0]} material={floor} receiveShadow onClick={onFloorClick}>
        <planeGeometry args={[w, d]} />
      </mesh>
      {[minZ + BAND_M / 2, maxZ - BAND_M / 2].map((z) => (
        <mesh key={`ns${z}`} position={[cx, 0.002, z]} rotation={[-Math.PI / 2, 0, 0]} material={band.ns} receiveShadow onClick={onFloorClick}>
          <planeGeometry args={[w, BAND_M]} />
        </mesh>
      ))}
      {[minX + BAND_M / 2, maxX - BAND_M / 2].map((x) => (
        <mesh key={`ew${x}`} position={[x, 0.002, cz]} rotation={[-Math.PI / 2, 0, 0]} material={band.ew} receiveShadow onClick={onFloorClick}>
          <planeGeometry args={[BAND_M, innerD]} />
        </mesh>
      ))}
      {/* The brass inlay where oak meets concrete. */}
      <Box size={[innerW + INLAY_M, 0.004, INLAY_M]} position={[cx, 0.002, minZ + BAND_M]} material={inlay} cast={false} />
      <Box size={[innerW + INLAY_M, 0.004, INLAY_M]} position={[cx, 0.002, maxZ - BAND_M]} material={inlay} cast={false} />
      <Box size={[INLAY_M, 0.004, innerD]} position={[minX + BAND_M, 0.002, cz]} material={inlay} cast={false} />
      <Box size={[INLAY_M, 0.004, innerD]} position={[maxX - BAND_M, 0.002, cz]} material={inlay} cast={false} />
      <Railing from={[minX + 0.2, minZ + 0.2]} to={[maxX - 0.2, minZ + 0.2]} />
      <Railing from={[maxX - 0.2, minZ + 0.2]} to={[maxX - 0.2, maxZ - 0.2]} />
      <Railing from={[maxX - 0.2, maxZ - 0.2]} to={[minX + 0.2, maxZ - 0.2]} />
      <Railing from={[minX + 0.2, maxZ - 0.2]} to={[minX + 0.2, minZ + 0.2]} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Studio wall
// ---------------------------------------------------------------------------

const WALL_H = 1.9;
const LETTERS = { w: 1024, h: 224 } as const;

/** Vertical walnut slats with dark reveals: four slats per 64-px repeat. */
function drawSlats(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const { slat, gap } = CODING_SCENE.walnut;
  ctx.fillStyle = gap;
  ctx.fillRect(0, 0, w, h);
  for (let x = 0; x < w; x += 16) {
    ctx.fillStyle = slat;
    ctx.fillRect(x + 3, 0, 11, h);
    ctx.fillStyle = "rgba(255,220,180,0.08)";
    ctx.fillRect(x + 4, 0, 3, h);
    ctx.fillStyle = "rgba(0,0,0,0.12)";
    ctx.fillRect(x + 12, 0, 2, h);
  }
}

const slatMaterials = new Map<string, MeshStandardMaterial>();
/** Slats about 5 cm apart: one texture repeat per 20 cm of wall. */
function slatMaterial(width: number): MeshStandardMaterial {
  const key = width.toFixed(2);
  let material = slatMaterials.get(key);
  if (!material) {
    const map = repeated(cachedCanvasTexture("coding:slats", 64, 64, drawSlats), width / 0.2, 1);
    material = new MeshStandardMaterial({ color: map ? "#ffffff" : CODING_SCENE.walnut.slat, map, roughness: 0.7 });
    slatMaterials.set(key, material);
  }
  return material;
}

/** Brass letters on a transparent ground: the department's name, and "Studio 0n" in small caps under it. */
function drawLetters(label: string, number: number, ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.clearRect(0, 0, w, h);
  const tracked = (text: string, font: string, y: number, spacing: number, fill: string | CanvasGradient) => {
    ctx.font = font;
    const chars = [...text];
    const widths = chars.map((ch) => ctx.measureText(ch).width);
    const total = widths.reduce((sum, cw) => sum + cw, 0) + spacing * Math.max(0, chars.length - 1);
    // Squeeze a long name to fit rather than cutting it off.
    const scale = Math.min(1, (w - 60) / total);
    ctx.save();
    ctx.translate(w / 2, y);
    ctx.scale(scale, 1);
    let x = -total / 2;
    chars.forEach((ch, i) => {
      // A dark offset first: the raised letters cast a hairline shadow on the slats.
      ctx.fillStyle = "rgba(20,12,6,0.6)";
      ctx.fillText(ch, x + 3, 3);
      ctx.fillStyle = fill;
      ctx.fillText(ch, x, 0);
      x += widths[i] + spacing;
    });
    ctx.restore();
  };
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const gold = ctx.createLinearGradient(0, 30, 0, 140);
  gold.addColorStop(0, "#f7dc95");
  gold.addColorStop(0.55, CODING_SCENE.brass);
  gold.addColorStop(1, "#a97d2c");
  const name = label.length > 24 ? `${label.slice(0, 23)}…` : label;
  tracked(name.toUpperCase(), "600 100px Georgia, 'Times New Roman', serif", 86, 12, gold);
  ctx.fillStyle = CODING_SCENE.brass;
  ctx.fillRect(w / 2 - 70, 156, 140, 3);
  tracked(`STUDIO ${String(number).padStart(2, "0")}`, "600 36px system-ui, -apple-system, 'Segoe UI', sans-serif", 194, 12, "#ecdcb9");
}

const letterMaterials = new Map<string, MeshStandardMaterial>();
function letterMaterial(label: string, number: number): MeshStandardMaterial {
  const key = `${number}:${label}`;
  let material = letterMaterials.get(key);
  if (!material) {
    const map = cachedCanvasTexture(`coding:letters:${key}`, LETTERS.w, LETTERS.h, (ctx, w, h) => drawLetters(label, number, ctx, w, h));
    material = new MeshStandardMaterial({
      color: map ? "#ffffff" : CODING_SCENE.brass, map, transparent: true, alphaTest: 0.3, roughness: 0.35, metalness: 0.3,
      // Lit by the picture light above: a faint glow keeps the brass readable in any light.
      ...(map ? { emissive: "#ffffff", emissiveMap: map, emissiveIntensity: 0.35 } : {}),
    });
    letterMaterials.set(key, material);
  }
  return material;
}

let washCache: MeshBasicMaterial | null = null;
/** Warm light washing down from the picture light, added over the slats. */
function washMaterial(): MeshBasicMaterial {
  if (!washCache) {
    const map = cachedCanvasTexture("coding:wash", 64, 128, (ctx, w, h) => {
      const g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, "rgba(255,214,150,0.8)");
      g.addColorStop(1, "rgba(255,214,150,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
      // Fade the sides too, so the wash has no hard vertical edge.
      ctx.globalCompositeOperation = "destination-in";
      const sides = ctx.createLinearGradient(0, 0, w, 0);
      sides.addColorStop(0, "rgba(0,0,0,0)");
      sides.addColorStop(0.25, "rgba(0,0,0,1)");
      sides.addColorStop(0.75, "rgba(0,0,0,1)");
      sides.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = sides;
      ctx.fillRect(0, 0, w, h);
    });
    washCache = new MeshBasicMaterial({ color: "#ffffff", map, transparent: true, opacity: map ? 0.45 : 0, blending: AdditiveBlending, depthWrite: false });
  }
  return washCache;
}

const STUDIO_MAT = {
  frame: matte(CODING_SCENE.walnut.cap, { roughness: 0.45, metalness: 0.3 }),
  brass: matte(CODING_SCENE.brass, { roughness: 0.3, metalness: 0.55, emissive: CODING_SCENE.brass, emissiveIntensity: 0.12 }),
  lamp: new MeshStandardMaterial({ color: CODING_SCENE.pendant.glow, emissive: CODING_SCENE.pendant.glow, emissiveIntensity: 1.6, toneMapped: false }),
  walnut: matte(CODING_SCENE.walnut.slat, { roughness: 0.6 }),
};

/**
 * A studio's back wall, centred on its front face: a walnut slat wall in a
 * black steel frame, the department's name in brass letters under a brass
 * picture light, a kick band in the studio's colour, and two walnut ledges
 * with books and small planters. It stays inside the layout's sign-wall footprint.
 */
function StudioWall({ label, number, width, position, accent }: {
  label: string; number: number; width: number; position: [number, number, number]; accent: MeshStandardMaterial;
}) {
  const plateW = Math.min(3.2, width * 0.52);
  const plateH = (plateW * LETTERS.h) / LETTERS.w;
  const ledgeX = width / 2 - 0.9;
  return (
    <group position={position}>
      <Box size={[width, WALL_H, 0.1]} position={[0, WALL_H / 2, -0.02]} material={STUDIO_MAT.frame} />
      <mesh position={[0, WALL_H / 2 + 0.05, 0.031]} material={slatMaterial(width - 0.08)} receiveShadow>
        <planeGeometry args={[width - 0.08, WALL_H - 0.18]} />
      </mesh>
      <Box size={[width - 0.08, 0.1, 0.02]} position={[0, 0.07, 0.04]} material={accent} cast={false} />
      <Box size={[width + 0.02, 0.035, 0.13]} position={[0, WALL_H + 0.0175, -0.02]} material={STUDIO_MAT.frame} />
      <mesh position={[0, 1.36, 0.035]} material={washMaterial()}>
        <planeGeometry args={[plateW * 1.1, 0.6]} />
      </mesh>
      <mesh position={[0, 1.34, 0.04]} material={letterMaterial(label, number)}>
        <planeGeometry args={[plateW, plateH]} />
      </mesh>
      {/* The brass picture light on two short arms, its lit underside facing the letters. */}
      <Box size={[plateW * 0.7, 0.035, 0.06]} position={[0, 1.7, 0.1]} material={STUDIO_MAT.brass} cast={false} />
      <Box size={[plateW * 0.66, 0.006, 0.03]} position={[0, 1.68, 0.1]} material={STUDIO_MAT.lamp} cast={false} />
      {[-plateW * 0.25, plateW * 0.25].map((x) => (
        <Box key={x} size={[0.015, 0.015, 0.08]} position={[x, 1.71, 0.06]} material={STUDIO_MAT.brass} cast={false} />
      ))}
      {ledgeX > plateW / 2 + 0.55 && [-ledgeX, ledgeX].map((x) => (
        <group key={x} position={[x, 0, 0]}>
          <Box size={[0.9, 0.035, 0.13]} position={[0, 1.12, 0.1]} material={STUDIO_MAT.walnut} />
          <mesh geometry={GEO.cyl} material={MAT.planterLight} position={[-0.22, 1.2, 0.1]} scale={[0.055, 0.13, 0.055]} castShadow />
          <mesh geometry={GEO.blob} material={MAT.leaf} position={[-0.22, 1.33, 0.1]} scale={[0.1, 0.09, 0.1]} castShadow />
          {[0.02, 0.085, 0.15].map((bx, i) => (
            <Box key={bx} size={[0.055, 0.2 + i * 0.025, 0.1]} position={[bx, 1.1375 + (0.2 + i * 0.025) / 2, 0.1]}
              material={MAT.books[(i * 2 + number) % MAT.books.length]} />
          ))}
          <mesh geometry={GEO.cyl} material={MAT.pot} position={[0.32, 1.18, 0.1]} scale={[0.045, 0.09, 0.045]} castShadow />
          <mesh geometry={GEO.blob} material={MAT.leafDark} position={[0.32, 1.27, 0.1]} scale={[0.075, 0.065, 0.075]} castShadow />
        </group>
      ))}
    </group>
  );
}

/** One workspace department as a studio: bordered patterned rug, walnut studio wall, desks in its own colours. */
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
      <StudioWall label={dept.label || t("society.office.open_space")} number={dept.tint + 1} width={w - 0.4}
        position={[cx, 0, dept.minZ + 0.1]} accent={kit.slat} />
      <DeskInstances desks={dept.desks} agents={agents} tone={kit.tone} />
    </group>
  );
}
