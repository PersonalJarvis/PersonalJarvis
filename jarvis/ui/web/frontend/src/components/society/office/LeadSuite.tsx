/**
 * The lead office as an executive suite: a panelled walnut feature wall with
 * lit bookcases and a glowing star emblem, an executive desk with three
 * monitors and a high-back leather chair, club armchairs for visitors, a
 * chesterfield lounge, a bar, a floor globe and warm lamp light.
 *
 * Same rules as OfficeProps: every piece is built in local space centred on
 * the origin, front facing +z, inside its `FURNITURE_SIZE` box, from rounded
 * primitives; materials and canvas textures are module-level singletons.
 */
import { memo } from "react";
import { Html } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import {
  CylinderGeometry, DoubleSide, MeshStandardMaterial, OctahedronGeometry, SphereGeometry, TorusGeometry,
} from "three";
import type { SocietyAgent } from "../data";
import { canvasMaterial } from "./canvasMaterials";
import { Box, matte, MAT, Rounded, screenMaterial } from "./OfficeFurniture";
import { useT } from "@/i18n";
import { TreatJar } from "./dogProps";
import { useLeadSeat } from "./leadSeat";
import { SEAT_OFFSET, seatOf, type DeskSlot, type Furniture, type FurnitureKind, type Point, type Room } from "./officeLayout";
import { useOfficeStore } from "./officeStore";
import { LEAD_SUITE as L } from "./officePalette";
import type { ScreenFace } from "./screenTextures";

type Vec3 = [number, number, number];

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

export const LEAD_MAT = {
  walnut: matte(L.walnut, { roughness: 0.6 }),
  walnutDark: matte(L.walnutDark, { roughness: 0.65 }),
  walnutLight: matte(L.walnutLight, { roughness: 0.6 }),
  brass: matte(L.brass, { roughness: 0.32, metalness: 0.75 }),
  chrome: matte(L.chrome, { roughness: 0.25, metalness: 0.6 }),
  leather: matte(L.leather, { roughness: 0.55 }),
  leatherDark: matte(L.leatherDark, { roughness: 0.6 }),
  leatherTan: matte(L.leatherTan, { roughness: 0.55 }),
  velvet: matte(L.velvetGold, { roughness: 0.95 }),
  blackGlass: matte(L.blackGlass, { roughness: 0.15, metalness: 0.2 }),
  marble: matte(L.marble, { roughness: 0.3 }),
  led: new MeshStandardMaterial({ color: L.led, emissive: L.led, emissiveIntensity: 1.6, roughness: 0.5 }),
  backlight: new MeshStandardMaterial({ color: L.backlight, emissive: "#ffb45e", emissiveIntensity: 0.28, roughness: 0.8 }),
  shade: new MeshStandardMaterial({ color: L.shade, emissive: "#ffd89a", emissiveIntensity: 0.85, roughness: 0.9 }),
  lampGlass: new MeshStandardMaterial({ color: L.lampGlass, emissive: "#3fae6a", emissiveIntensity: 0.55, roughness: 0.3 }),
  glass: new MeshStandardMaterial({ color: L.glass, transparent: true, opacity: 0.35, roughness: 0.1, depthWrite: false }),
  wallGlass: new MeshStandardMaterial({ color: "#f3d9a4", transparent: true, opacity: 0.16, roughness: 0.1, side: DoubleSide, depthWrite: false }),
  mirror: new MeshStandardMaterial({ color: "#6b4a30", emissive: "#ffb766", emissiveIntensity: 0.35, roughness: 0.2, metalness: 0.4 }),
  trophy: matte(L.trophy, { roughness: 0.28, metalness: 0.8 }),
  bottles: L.bottles.map((c) => new MeshStandardMaterial({ color: c, roughness: 0.15, transparent: true, opacity: 0.85 })),
  globeLand: matte(L.globe.land),
  rugBorder: matte(L.rug.border, { roughness: 0.95 }),
  basket: matte(L.dog.basket, { roughness: 0.95 }),
  cushion: matte(L.dog.cushion, { roughness: 0.95 }),
};

const books = ["#7a2c22", "#1d2744", "#2f5a3f", "#c9a24a", "#e9e1d0", "#4a3a6a", "#8a5a36"].map((c) => matte(c));

const GEO = {
  cyl: new CylinderGeometry(1, 1, 1, 20),
  sphere: new SphereGeometry(1, 24, 16),
  star: new OctahedronGeometry(1, 0),
  ring: new TorusGeometry(1, 0.035, 8, 48),
  rim: new TorusGeometry(1, 0.16, 10, 36),
};

function Cyl({ radius, height, position, material, rotation, cast = true }: {
  radius: number; height: number; position: Vec3; material: MeshStandardMaterial; rotation?: Vec3; cast?: boolean;
}) {
  return <mesh geometry={GEO.cyl} material={material} position={position} rotation={rotation} scale={[radius, height, radius]} castShadow={cast} receiveShadow />;
}

function Ball({ radius, position, material, cast = true }: { radius: number | Vec3; position: Vec3; material: MeshStandardMaterial; cast?: boolean }) {
  return <mesh geometry={GEO.sphere} material={material} position={position} scale={radius} castShadow={cast} receiveShadow />;
}

function Panel({ size, position, material, rotation }: { size: [number, number]; position: Vec3; material: MeshStandardMaterial; rotation?: Vec3 }) {
  return <mesh position={position} rotation={rotation} material={material}><planeGeometry args={size} /></mesh>;
}

// ---------------------------------------------------------------------------
// Canvas faces
// ---------------------------------------------------------------------------

function starPath(ctx: CanvasRenderingContext2D, cx: number, cy: number, outer: number, inner: number): void {
  ctx.beginPath();
  for (let i = 0; i < 10; i += 1) {
    const r = i % 2 === 0 ? outer : inner;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

/** The feature wall's emblem: a gold star in two rings with fine rays on black glass. */
function drawEmblem(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const glow = ctx.createRadialGradient(w / 2, h / 2, 10, w / 2, h / 2, w * 0.55);
  glow.addColorStop(0, "#3a2a14");
  glow.addColorStop(1, "#0d0e12");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2, r = h * 0.36;
  ctx.strokeStyle = "rgba(245,197,90,0.35)";
  ctx.lineWidth = 2;
  for (let i = 0; i < 48; i += 1) {
    const a = (i * Math.PI * 2) / 48;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r * 1.22, cy + Math.sin(a) * r * 1.22);
    ctx.lineTo(cx + Math.cos(a) * r * (i % 2 === 0 ? 1.75 : 1.5), cy + Math.sin(a) * r * (i % 2 === 0 ? 1.75 : 1.5));
    ctx.stroke();
  }
  ctx.strokeStyle = "#f5c55a";
  ctx.lineWidth = 7;
  ctx.beginPath(); ctx.arc(cx, cy, r * 1.12, 0, Math.PI * 2); ctx.stroke();
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.arc(cx, cy, r * 0.98, 0, Math.PI * 2); ctx.stroke();
  const gold = ctx.createLinearGradient(cx - r, cy - r, cx + r, cy + r);
  gold.addColorStop(0, "#fff0b8");
  gold.addColorStop(0.5, "#f5c55a");
  gold.addColorStop(1, "#b8862a");
  ctx.fillStyle = gold;
  starPath(ctx, cx, cy + r * 0.04, r * 0.82, r * 0.34);
  ctx.fill();
}

/** Navy rug with a gold border, a burgundy band and a central medallion. */
function drawRug(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = L.rug.field;
  ctx.fillRect(0, 0, w, h);
  const band = (inset: number, colour: string, width: number) => {
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.strokeRect(inset, inset, w - inset * 2, h - inset * 2);
  };
  band(14, L.rug.border, 10);
  band(34, L.rug.accent, 14);
  band(52, L.rug.border, 3);
  ctx.fillStyle = L.rug.inner;
  ctx.fillRect(60, 60, w - 120, h - 120);
  // A lattice of small diamonds in the field.
  ctx.fillStyle = "rgba(201,162,74,0.22)";
  for (let y = 90; y < h - 80; y += 44) {
    for (let x = 90 + ((y / 44) % 2) * 22; x < w - 80; x += 44) {
      ctx.beginPath();
      ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y); ctx.lineTo(x, y + 6); ctx.lineTo(x - 6, y);
      ctx.fill();
    }
  }
  // Medallion.
  const cx = w / 2, cy = h / 2;
  ctx.fillStyle = L.rug.field;
  ctx.beginPath();
  ctx.moveTo(cx, cy - 120); ctx.lineTo(cx + 150, cy); ctx.lineTo(cx, cy + 120); ctx.lineTo(cx - 150, cy);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = L.rug.border;
  ctx.lineWidth = 5;
  ctx.stroke();
  ctx.fillStyle = L.rug.accent;
  ctx.beginPath(); ctx.arc(cx, cy, 44, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = L.rug.border;
  starPath(ctx, cx, cy + 2, 32, 13);
  ctx.fill();
}

/** A vintage globe: teal seas, parchment continents, a few meridians. */
function drawGlobe(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = L.globe.ocean;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = L.globe.land;
  const blobs: [number, number, number, number][] = [
    [0.2, 0.32, 0.1, 0.12], [0.26, 0.62, 0.06, 0.14], [0.5, 0.3, 0.07, 0.08], [0.53, 0.56, 0.07, 0.15],
    [0.68, 0.3, 0.14, 0.1], [0.83, 0.68, 0.06, 0.06], [0.15, 0.24, 0.05, 0.05], [0.72, 0.45, 0.05, 0.06],
  ];
  for (const [x, y, rx, ry] of blobs) {
    ctx.beginPath();
    ctx.ellipse(x * w, y * h, rx * w, ry * h, 0.3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.strokeStyle = L.globe.line;
  ctx.globalAlpha = 0.35;
  ctx.lineWidth = 1.5;
  for (let i = 1; i < 12; i += 1) { ctx.beginPath(); ctx.moveTo((i * w) / 12, 0); ctx.lineTo((i * w) / 12, h); ctx.stroke(); }
  for (let i = 1; i < 6; i += 1) { ctx.beginPath(); ctx.moveTo(0, (i * h) / 6); ctx.lineTo(w, (i * h) / 6); ctx.stroke(); }
  ctx.globalAlpha = 1;
}

const faces = {
  emblem: () => canvasMaterial("lead:emblem", 512, 288, drawEmblem, { glow: 0.95, fallback: "#1a1510", roughness: 0.2 }),
  rug: () => canvasMaterial("lead:rug", 512, 512, drawRug, { fallback: L.rug.field, roughness: 0.95 }),
  globe: () => canvasMaterial("lead:globe", 256, 128, drawGlobe, { fallback: L.globe.ocean, roughness: 0.5 }),
};

// ---------------------------------------------------------------------------
// Feature wall
// ---------------------------------------------------------------------------

const WALL = { w: 7.9, h: 2.9, d: 0.38 } as const;
const CASE_W = 2.2;
const SHELF_YS = [0.62, 1.18, 1.74, 2.3] as const;

/** Shelf contents, deterministic per bookcase side: book runs, trophies, a vase, a framed photo. */
function shelfItems(side: number): { kind: "books" | "trophy" | "vase" | "frame"; x: number; shelf: number; n: number }[] {
  const out: { kind: "books" | "trophy" | "vase" | "frame"; x: number; shelf: number; n: number }[] = [];
  SHELF_YS.forEach((_, shelf) => {
    const seed = shelf * 3 + side * 7;
    out.push({ kind: "books", x: -0.8 + (seed % 3) * 0.08, shelf, n: 5 + (seed % 4) });
    const accent = (["trophy", "vase", "frame", "trophy"] as const)[(shelf + side) % 4];
    out.push({ kind: accent, x: 0.6 - (seed % 2) * 0.15, shelf, n: 0 });
    if (shelf % 2 === side % 2) out.push({ kind: "books", x: 0.05, shelf, n: 3 + (seed % 3) });
  });
  return out;
}

function ShelfItem({ item, y }: { item: ReturnType<typeof shelfItems>[number]; y: number }) {
  if (item.kind === "books") {
    return (
      <group position={[item.x, y, 0.02]}>
        {Array.from({ length: item.n }, (_, i) => {
          const h = 0.3 + ((i * 7 + item.n) % 4) * 0.035;
          return <Box key={i} size={[0.055, h, 0.2]} position={[i * 0.062, h / 2, 0]} material={books[(i + item.n) % books.length]} />;
        })}
      </group>
    );
  }
  if (item.kind === "trophy") {
    return (
      <group position={[item.x, y, 0.02]}>
        <Box size={[0.12, 0.05, 0.12]} position={[0, 0.025, 0]} material={LEAD_MAT.walnutDark} />
        <Cyl radius={0.015} height={0.1} position={[0, 0.1, 0]} material={LEAD_MAT.trophy} />
        <mesh geometry={GEO.cyl} material={LEAD_MAT.trophy} position={[0, 0.21, 0]} scale={[0.07, 0.13, 0.07]} castShadow />
        <mesh geometry={GEO.star} material={LEAD_MAT.trophy} position={[0, 0.33, 0]} scale={[0.04, 0.05, 0.02]} />
      </group>
    );
  }
  if (item.kind === "vase") {
    return (
      <group position={[item.x, y, 0.02]}>
        <Ball radius={[0.08, 0.12, 0.08]} position={[0, 0.12, 0]} material={LEAD_MAT.marble} />
        <Cyl radius={0.03} height={0.08} position={[0, 0.26, 0]} material={LEAD_MAT.marble} />
      </group>
    );
  }
  return (
    <group position={[item.x, y, 0.0]} rotation={[-0.12, 0, 0]}>
      <Box size={[0.26, 0.2, 0.02]} position={[0, 0.11, 0]} material={LEAD_MAT.brass} />
      <Box size={[0.22, 0.16, 0.012]} position={[0, 0.11, 0.008]} material={books[1]} cast={false} />
    </group>
  );
}

/** One built-in bookcase: walnut carcass, backlit panel, LED-lit shelves and a cabinet base. */
function Bookcase({ x, side }: { x: number; side: number }) {
  const items = shelfItems(side);
  return (
    <group position={[x, 0, 0]}>
      <Box size={[CASE_W - 0.1, WALL.h - 0.2, 0.02]} position={[0, (WALL.h - 0.2) / 2 + 0.05, -0.09]} material={LEAD_MAT.backlight} cast={false} />
      {[-CASE_W / 2 + 0.03, CASE_W / 2 - 0.03].map((px) => (
        <Box key={px} size={[0.06, WALL.h - 0.06, 0.34]} position={[px, (WALL.h - 0.06) / 2, 0.01]} material={LEAD_MAT.walnut} />
      ))}
      <Box size={[CASE_W, 0.07, 0.36]} position={[0, WALL.h - 0.04, 0.01]} material={LEAD_MAT.walnut} />
      {/* Cabinet base with two brass-handled doors. */}
      <Box size={[CASE_W - 0.12, 0.56, 0.32]} position={[0, 0.3, 0.02]} material={LEAD_MAT.walnutDark} />
      <Box size={[0.012, 0.5, 0.01]} position={[0, 0.3, 0.185]} material={LEAD_MAT.walnut} cast={false} />
      {[-0.08, 0.08].map((hx) => <Box key={hx} size={[0.02, 0.14, 0.02]} position={[hx, 0.36, 0.19]} material={LEAD_MAT.brass} cast={false} />)}
      {SHELF_YS.map((y) => (
        <group key={y}>
          <Box size={[CASE_W - 0.12, 0.04, 0.32]} position={[0, y, 0.02]} material={LEAD_MAT.walnut} />
          {/* Warm LED strip under the shelf's front edge. */}
          <Box size={[CASE_W - 0.2, 0.012, 0.02]} position={[0, y + 0.53, 0.15]} material={LEAD_MAT.led} cast={false} />
        </group>
      ))}
      {items.map((item, i) => <ShelfItem key={i} item={item} y={SHELF_YS[item.shelf] + 0.02} />)}
    </group>
  );
}

/** The north feature wall: walnut slats around a backlit star emblem, lit bookcases on both sides. */
function LeadWall() {
  const slats = 15;
  const centreW = WALL.w - CASE_W * 2 - 0.2;
  return (
    <group>
      <Box size={[WALL.w, WALL.h, 0.1]} position={[0, WALL.h / 2, -0.14]} material={LEAD_MAT.walnutDark} />
      {Array.from({ length: slats }, (_, i) => (
        <Box key={i} size={[0.07, WALL.h - 0.3, 0.04]} position={[-centreW / 2 + 0.1 + (i * (centreW - 0.2)) / (slats - 1), (WALL.h - 0.3) / 2 + 0.15, -0.07]}
          material={LEAD_MAT.walnutLight} />
      ))}
      {/* Emblem: black glass in a brass frame, lit from within. */}
      <Box size={[2.3, 1.3, 0.05]} position={[0, 1.62, -0.03]} material={LEAD_MAT.brass} />
      <Box size={[2.2, 1.2, 0.02]} position={[0, 1.62, 0.0]} material={LEAD_MAT.blackGlass} cast={false} />
      <Panel size={[2.14, 1.14]} position={[0, 1.62, 0.0115]} material={faces.emblem()} />
      {/* Cove lights top and bottom of the centre section. */}
      <Box size={[centreW, 0.03, 0.05]} position={[0, WALL.h - 0.1, -0.03]} material={LEAD_MAT.led} cast={false} />
      <Box size={[centreW, 0.03, 0.05]} position={[0, 0.12, -0.03]} material={LEAD_MAT.led} cast={false} />
      {/* Brass pilasters between the sections. */}
      {[-1, 1].map((s) => (
        <Box key={s} size={[0.08, WALL.h, 0.12]} position={[s * (centreW / 2 + 0.05), WALL.h / 2, -0.03]} material={LEAD_MAT.brass} />
      ))}
      <Bookcase x={-(WALL.w / 2 - CASE_W / 2)} side={0} />
      <Bookcase x={WALL.w / 2 - CASE_W / 2} side={1} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Seating and lounge
// ---------------------------------------------------------------------------

/** Tan leather club armchair for visitors, facing +z. */
function GuestChair() {
  return (
    <group>
      <Rounded size={[0.72, 0.3, 0.7]} radius={0.06} position={[0, 0.2, 0]} material={LEAD_MAT.leatherTan} />
      <Rounded size={[0.5, 0.1, 0.52]} radius={0.04} position={[0, 0.4, 0.06]} material={LEAD_MAT.leatherTan} />
      <Rounded size={[0.72, 0.5, 0.16]} radius={0.07} position={[0, 0.62, -0.27]} material={LEAD_MAT.leatherTan} />
      {[-0.29, 0.29].map((x) => <Rounded key={x} size={[0.14, 0.26, 0.68]} radius={0.06} position={[x, 0.46, 0]} material={LEAD_MAT.leatherTan} />)}
      {[[-0.3, -0.3], [0.3, -0.3], [-0.3, 0.3], [0.3, 0.3]].map(([x, z]) => (
        <Cyl key={`${x}${z}`} radius={0.025} height={0.05} position={[x, 0.025, z]} material={LEAD_MAT.brass} />
      ))}
    </group>
  );
}

/** Oxblood chesterfield with rolled arms, button tufting and two gold velvet cushions; faces +z. */
function Chesterfield() {
  const buttons: Vec3[] = [];
  for (let row = 0; row < 2; row += 1) {
    for (let i = 0; i < 8; i += 1) buttons.push([-0.8 + i * (1.6 / 7) + (row ? 0.11 : 0), 0.62 + row * 0.14, -0.255]);
  }
  return (
    <group>
      <Rounded size={[1.96, 0.34, 0.86]} radius={0.06} position={[0, 0.25, 0]} material={LEAD_MAT.leather} />
      {[-0.66, 0, 0.66].map((x) => <Rounded key={x} size={[0.64, 0.12, 0.66]} radius={0.05} position={[x, 0.48, 0.08]} material={LEAD_MAT.leather} />)}
      <Rounded size={[1.96, 0.44, 0.2]} radius={0.07} position={[0, 0.62, -0.36]} material={LEAD_MAT.leather} />
      {buttons.map((p, i) => <Ball key={i} radius={0.018} position={p} material={LEAD_MAT.leatherDark} cast={false} />)}
      {[-0.97, 0.97].map((x) => (
        <group key={x}>
          <Rounded size={[0.24, 0.46, 0.9]} radius={0.06} position={[x, 0.33, 0]} material={LEAD_MAT.leather} />
          <Cyl radius={0.12} height={0.9} position={[x, 0.6, 0]} rotation={[Math.PI / 2, 0, 0]} material={LEAD_MAT.leather} />
        </group>
      ))}
      {[-0.6, 0.6].map((x) => (
        <Rounded key={x} size={[0.36, 0.3, 0.1]} radius={0.04} position={[x, 0.66, -0.19]} material={LEAD_MAT.velvet} />
      ))}
      {[[-0.9, -0.35], [0.9, -0.35], [-0.9, 0.35], [0.9, 0.35]].map(([x, z]) => (
        <Box key={`${x}${z}`} size={[0.06, 0.08, 0.06]} position={[x, 0.04, z]} material={LEAD_MAT.brass} />
      ))}
    </group>
  );
}

/** Brass-framed glass coffee table with a marble shelf, a book stack and a vase. */
function LoungeTable() {
  return (
    <group>
      {[[-0.52, -0.29], [0.52, -0.29], [-0.52, 0.29], [0.52, 0.29]].map(([x, z]) => (
        <Box key={`${x}${z}`} size={[0.03, 0.42, 0.03]} position={[x, 0.21, z]} material={LEAD_MAT.brass} />
      ))}
      <Box size={[1.08, 0.025, 0.62]} position={[0, 0.42, 0]} material={LEAD_MAT.glass} cast={false} />
      <Box size={[1.06, 0.02, 0.6]} position={[0, 0.405, 0]} material={LEAD_MAT.brass} cast={false} />
      <Box size={[1.0, 0.03, 0.54]} position={[0, 0.12, 0]} material={LEAD_MAT.marble} />
      <Box size={[0.3, 0.04, 0.22]} position={[-0.22, 0.455, 0.02]} material={books[0]} />
      <Box size={[0.26, 0.035, 0.2]} position={[-0.22, 0.49, 0.02]} material={books[3]} />
      <Ball radius={[0.06, 0.09, 0.06]} position={[0.26, 0.52, -0.04]} material={LEAD_MAT.marble} />
      <Box size={[0.26, 0.05, 0.18]} position={[0.1, 0.16, 0]} material={books[1]} />
    </group>
  );
}

/** Tall brass floor lamp with a glowing drum shade. */
function FloorLamp() {
  return (
    <group>
      <Cyl radius={0.2} height={0.05} position={[0, 0.025, 0]} material={LEAD_MAT.walnutDark} />
      <Cyl radius={0.035} height={1.46} position={[0, 0.77, 0]} material={LEAD_MAT.walnutDark} />
      <Cyl radius={0.05} height={0.06} position={[0, 1.44, 0]} material={LEAD_MAT.brass} />
      <Cyl radius={0.21} height={0.3} position={[0, 1.6, 0]} material={LEAD_MAT.shade} cast={false} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Bar and globe
// ---------------------------------------------------------------------------

/** Walnut bar cabinet with a marble top, lit mirrored back, glass shelf and bottles; faces +z. */
function ExecutiveBar() {
  const bottles = [
    { x: -0.7, y: 0.94, h: 0.3 }, { x: -0.52, y: 0.94, h: 0.26 }, { x: -0.36, y: 0.94, h: 0.34 },
    { x: 0.42, y: 1.38, h: 0.24 }, { x: 0.58, y: 1.38, h: 0.28 }, { x: 0.74, y: 1.38, h: 0.22 },
    { x: -0.6, y: 1.38, h: 0.26 }, { x: -0.44, y: 1.38, h: 0.22 },
  ];
  return (
    <group>
      <Box size={[1.9, 0.86, 0.5]} position={[0, 0.45, 0]} material={LEAD_MAT.walnut} />
      <Box size={[1.9, 0.04, 0.52]} position={[0, 0.9, 0]} material={LEAD_MAT.marble} />
      {[-0.63, 0, 0.63].map((x) => (
        <group key={x}>
          <Box size={[0.58, 0.7, 0.012]} position={[x, 0.45, 0.253]} material={LEAD_MAT.walnutDark} cast={false} />
          <Box size={[0.14, 0.02, 0.02]} position={[x, 0.72, 0.262]} material={LEAD_MAT.brass} cast={false} />
        </group>
      ))}
      {[-0.92, 0.92].map((x) => <Box key={x} size={[0.06, 0.96, 0.3]} position={[x, 1.4, -0.1]} material={LEAD_MAT.walnut} />)}
      <Box size={[1.9, 0.06, 0.3]} position={[0, 1.87, -0.1]} material={LEAD_MAT.walnut} />
      <Box size={[1.78, 0.9, 0.02]} position={[0, 1.38, -0.24]} material={LEAD_MAT.mirror} cast={false} />
      <Box size={[1.78, 0.02, 0.24]} position={[0, 1.37, -0.12]} material={LEAD_MAT.glass} cast={false} />
      <Box size={[1.7, 0.012, 0.02]} position={[0, 1.82, -0.02]} material={LEAD_MAT.led} cast={false} />
      {bottles.map((b, i) => (
        <group key={i} position={[b.x, b.y, b.y > 1 ? -0.12 : 0.05]}>
          <Cyl radius={0.045} height={b.h * 0.7} position={[0, (b.h * 0.7) / 2, 0]} material={LEAD_MAT.bottles[i % LEAD_MAT.bottles.length]} />
          <Cyl radius={0.016} height={b.h * 0.3} position={[0, b.h * 0.85, 0]} material={LEAD_MAT.bottles[i % LEAD_MAT.bottles.length]} />
        </group>
      ))}
      {/* A decanter and two tumblers on the counter. */}
      <Ball radius={[0.1, 0.11, 0.1]} position={[0.35, 1.03, 0.06]} material={LEAD_MAT.bottles[0]} />
      <Cyl radius={0.02} height={0.08} position={[0.35, 1.17, 0.06]} material={LEAD_MAT.glass} />
      {[0.62, 0.76].map((x) => <Cyl key={x} radius={0.035} height={0.08} position={[x, 0.96, 0.1]} material={LEAD_MAT.glass} cast={false} />)}
    </group>
  );
}

/** Vintage floor globe on a walnut stand inside a tilted brass meridian. */
function Globe() {
  return (
    <group>
      <Cyl radius={0.3} height={0.05} position={[0, 0.025, 0]} material={LEAD_MAT.walnutDark} />
      <Cyl radius={0.035} height={0.4} position={[0, 0.25, 0]} material={LEAD_MAT.walnut} />
      <Cyl radius={0.14} height={0.04} position={[0, 0.44, 0]} material={LEAD_MAT.brass} />
      <group position={[0, 0.76, 0]} rotation={[0, 0, 0.41]}>
        <mesh geometry={GEO.sphere} material={faces.globe()} scale={0.28} castShadow receiveShadow />
        <mesh geometry={GEO.ring} material={LEAD_MAT.brass} scale={0.33} castShadow />
      </group>
    </group>
  );
}

/** Navy rug with a gold border and medallion, sized per instance. */
function ExecutiveRug({ w, d }: { w: number; d: number }) {
  return (
    <group>
      <Box size={[w, 0.016, d]} position={[0, 0.008, 0]} material={LEAD_MAT.rugBorder} cast={false} />
      <Panel size={[w - 0.08, d - 0.08]} position={[0, 0.018, 0]} rotation={[-Math.PI / 2, 0, 0]} material={faces.rug()} />
    </group>
  );
}

/** The dog's round basket in the corner; the dog itself is a living figure (OfficeDog.tsx). */
function DogBed() {
  return (
    <group>
      <mesh geometry={GEO.cyl} material={LEAD_MAT.basket} position={[0, 0.07, 0]} scale={[0.43, 0.14, 0.34]} castShadow receiveShadow />
      <mesh geometry={GEO.rim} material={LEAD_MAT.basket} position={[0, 0.15, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[0.38, 0.29, 0.5]} castShadow />
      <mesh geometry={GEO.cyl} material={LEAD_MAT.cushion} position={[0, 0.15, 0]} scale={[0.36, 0.05, 0.27]} receiveShadow />
    </group>
  );
}

/** Renderers of the executive-suite furniture kinds; merged into OfficeProps' exhaustive table. */
export const LEAD_RENDERERS = {
  leadWall: () => <LeadWall />,
  executiveRug: ({ item }: { item: Furniture }) => <ExecutiveRug w={item.size?.w ?? 1} d={item.size?.d ?? 1} />,
  guestChair: () => <GuestChair />,
  chesterfield: () => <Chesterfield />,
  loungeTable: () => <LoungeTable />,
  executiveBar: () => <ExecutiveBar />,
  globe: () => <Globe />,
  floorLamp: () => <FloorLamp />,
  dogBed: () => <DogBed />,
  treatJar: () => <TreatJar />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;

// ---------------------------------------------------------------------------
// Executive desk
// ---------------------------------------------------------------------------

/** High-back leather executive chair on a chrome five-star base; the seat top sits at 0.52 m like every chair. */
function ExecutiveChair() {
  return (
    <group>
      <Cyl radius={0.05} height={0.06} position={[0, 0.06, 0]} material={LEAD_MAT.chrome} />
      {Array.from({ length: 5 }, (_, i) => (
        <group key={i} rotation={[0, (i * Math.PI * 2) / 5, 0]}>
          <Box size={[0.04, 0.035, 0.3]} position={[0, 0.06, 0.15]} material={LEAD_MAT.chrome} />
          <Ball radius={0.03} position={[0, 0.03, 0.29]} material={MAT.chair} />
        </group>
      ))}
      <Cyl radius={0.028} height={0.34} position={[0, 0.26, 0]} material={LEAD_MAT.chrome} />
      <Rounded size={[0.6, 0.12, 0.56]} radius={0.05} position={[0, 0.46, 0]} material={LEAD_MAT.leather} />
      <Rounded size={[0.62, 0.84, 0.14]} radius={0.06} position={[0, 0.98, 0.3]} material={LEAD_MAT.leather} />
      <Rounded size={[0.46, 0.22, 0.14]} radius={0.06} position={[0, 1.5, 0.32]} material={LEAD_MAT.leather} />
      {[0.78, 0.98, 1.18].map((y) => <Box key={y} size={[0.5, 0.015, 0.01]} position={[0, y, 0.228]} material={LEAD_MAT.leatherDark} cast={false} />)}
      {[-0.33, 0.33].map((x) => (
        <group key={x}>
          <Box size={[0.04, 0.2, 0.04]} position={[x, 0.6, 0.02]} material={LEAD_MAT.chrome} />
          <Rounded size={[0.08, 0.05, 0.42]} radius={0.02} position={[x, 0.72, 0.02]} material={LEAD_MAT.leatherDark} />
        </group>
      ))}
    </group>
  );
}

/**
 * An executive desk in the same local frame as a bench desk: the agent sits at
 * +z and looks north (-z) at its monitors; the visitor side (-z) shows a
 * walnut modesty panel with the brass star. Wide desks get three monitors.
 */
function ExecutiveDesk({ w, d, face }: { w: number; d: number; face: ScreenFace }) {
  const pedestalX = w / 2 - 0.32;
  const triple = w >= 2.2;
  const screen = screenMaterial(face);
  return (
    <group>
      <Rounded size={[w, 0.07, d]} radius={0.02} position={[0, 0.735, 0]} material={LEAD_MAT.walnut} />
      <Box size={[w - 0.02, 0.025, d - 0.02]} position={[0, 0.69, 0]} material={LEAD_MAT.brass} />
      {[-pedestalX, pedestalX].map((x) => (
        <group key={x}>
          <Box size={[0.56, 0.68, d - 0.06]} position={[x, 0.34, 0]} material={LEAD_MAT.walnutDark} />
          {[0.16, 0.38, 0.58].map((y) => <Box key={y} size={[0.16, 0.02, 0.02]} position={[x, y, d / 2 - 0.02]} material={LEAD_MAT.brass} cast={false} />)}
        </group>
      ))}
      {/* Visitor side: modesty panel with brass inlays and the lit star plaque. */}
      <Box size={[w - 1.2, 0.56, 0.04]} position={[0, 0.4, -d / 2 + 0.05]} material={LEAD_MAT.walnutDark} />
      {[0.2, 0.6].map((y) => <Box key={y} size={[w - 1.2, 0.015, 0.01]} position={[0, y, -d / 2 + 0.025]} material={LEAD_MAT.brass} cast={false} />)}
      <Panel size={[0.44, 0.25]} position={[0, 0.4, -d / 2 + 0.028]} rotation={[0, Math.PI, 0]} material={faces.emblem()} />
      {/* Leather desk pad, keyboard, mouse. */}
      <Box size={[0.95, 0.006, 0.42]} position={[0, 0.773, 0.16]} material={LEAD_MAT.leatherDark} cast={false} />
      <Box size={[0.44, 0.02, 0.14]} position={[0, 0.785, 0.24]} material={MAT.keyboard} />
      <Rounded size={[0.06, 0.025, 0.1]} radius={0.01} position={[0.34, 0.785, 0.26]} material={MAT.keyboard} />
      {/* Monitors: a centre screen, and two angled wings on a wide desk. */}
      <Box size={[0.08, 0.2, 0.08]} position={[0, 0.87, -0.24]} material={MAT.monitor} />
      <Box size={[0.3, 0.02, 0.18]} position={[0, 0.78, -0.24]} material={MAT.monitor} />
      {[0, ...(triple ? [-1, 1] : [])].map((s) => (
        <group key={s} position={[s * 0.72, 1.2, -0.26 + Math.abs(s) * 0.1]} rotation={[0, -s * 0.42, 0]}>
          <Rounded size={[0.74, 0.44, 0.04]} radius={0.02} position={[0, 0, 0]} material={MAT.monitor} />
          <mesh position={[0, 0, 0.0215]} material={screen}><planeGeometry args={[0.68, 0.39]} /></mesh>
        </group>
      ))}
      {triple && [-0.72, 0.72].map((x) => <Box key={x} size={[0.05, 0.4, 0.05]} position={[x, 0.97, -0.18]} material={MAT.monitor} />)}
      {/* Banker's lamp, a trophy star and a coffee cup. The lamp stands at the
          front-left corner: further back, the angled left wing monitor cuts through its shade. */}
      <group position={[-w / 2 + 0.25, 0.77, 0.22]}>
        <Cyl radius={0.08} height={0.03} position={[0, 0.015, 0]} material={LEAD_MAT.brass} />
        <Cyl radius={0.012} height={0.3} position={[0, 0.16, 0]} material={LEAD_MAT.brass} />
        <Rounded size={[0.34, 0.08, 0.14]} radius={0.035} position={[0, 0.33, 0.04]} material={LEAD_MAT.lampGlass} cast={false} />
      </group>
      <group position={[w / 2 - 0.22, 0.77, -0.2]}>
        <Box size={[0.12, 0.06, 0.12]} position={[0, 0.03, 0]} material={LEAD_MAT.walnutDark} />
        <mesh geometry={GEO.star} material={LEAD_MAT.trophy} position={[0, 0.17, 0]} scale={[0.07, 0.1, 0.035]} castShadow />
      </group>
      <Cyl radius={0.04} height={0.09} position={[w / 2 - 0.35, 0.815, 0.22]} material={LEAD_MAT.marble} />
      <group position={[0, 0, SEAT_OFFSET]}><ExecutiveChair /></group>
    </group>
  );
}

/** Monitor centre of a lead desk in world space, and the heading a camera dive looks along. */
function screenOf(desk: DeskSlot): { point: Point & { y: number }; facing: number } {
  const turn = desk.facing === "north" ? 0 : Math.PI;
  return { point: { x: desk.x - Math.sin(turn) * 0.24, y: 1.2, z: desk.z - Math.cos(turn) * 0.24 }, facing: turn };
}

/** "E - sit down" next to the chair, and once seated, how to open the lead and stand up again. */
function SeatPrompt({ desk }: { desk: DeskSlot }) {
  const t = useT();
  const seated = useLeadSeat((s) => s.seated === desk.id);
  const near = useLeadSeat((s) => s.near === desk.id);
  if (!seated && !near) return null;
  return (
    <Html center position={[0, 1.95, SEAT_OFFSET]} zIndexRange={[24, 0]}>
      <span className="office-plate office-seat-prompt" data-office-ui>
        {seated ? t("society.office.seat_seated") : <><kbd>E</kbd>{t("society.office.seat_sit")}</>}
      </span>
    </Html>
  );
}

/**
 * Every lead desk (the ones that carry their own size) with the monitor face
 * its agent's state calls for. The chair can be clicked to walk over and sit;
 * the monitors open the lead agent (a camera dive, then the agents view).
 */
export const ExecutiveDesks = memo(function ExecutiveDesks({ desks, agents, onOpenScreen }: {
  desks: DeskSlot[]; agents: ReadonlyMap<string, SocietyAgent>;
  onOpenScreen?: (agentId: string, screen: Point & { y: number }, facing: number) => void;
}) {
  return (
    <group>
      {desks.map((desk) => {
        const size = desk.size;
        if (!size) return null;
        const agent = desk.agentId ? agents.get(desk.agentId) : undefined;
        const openScreen = (event: ThreeEvent<MouseEvent>) => {
          if (!agent || !onOpenScreen || event.delta > 6) return;
          event.stopPropagation();
          const { point, facing } = screenOf(desk);
          onOpenScreen(agent.agentId, point, facing);
        };
        const takeSeat = (event: ThreeEvent<MouseEvent>) => {
          if (event.delta > 6) return;
          event.stopPropagation();
          const seat = seatOf(desk);
          useLeadSeat.getState().set({ pending: desk.id });
          useOfficeStore.getState().requestWalk({ x: seat.x, z: seat.z });
        };
        const hover = (cursor: string) => () => { document.body.style.cursor = cursor; };
        return (
          <group key={desk.id} position={[desk.x, 0, desk.z]} rotation={[0, desk.facing === "north" ? 0 : Math.PI, 0]} name={`desk:${desk.id}`}>
            <ExecutiveDesk w={size.w} d={size.d} face={agent ? agent.state : "empty"} />
            {/* Invisible hit boxes: the monitor bank (clickable from any side) and the chair. */}
            {agent && onOpenScreen && (
              <mesh position={[0, 1.2, -0.2]} visible={false} onClick={openScreen}
                onPointerOver={hover("zoom-in")} onPointerOut={hover("")}>
                <boxGeometry args={[size.w >= 2.2 ? 2.1 : 0.8, 0.5, 0.25]} />
              </mesh>
            )}
            <mesh position={[0, 0.75, SEAT_OFFSET + 0.1]} visible={false} onClick={takeSeat}
              onPointerOver={hover("pointer")} onPointerOut={hover("")}>
              <boxGeometry args={[0.64, 1.5, 0.7]} />
            </mesh>
            <SeatPrompt desk={desk} />
          </group>
        );
      })}
    </group>
  );
});

/** Warm light pooled over the executive desk; the suite glows against the cooler open office. */
export function LeadOfficeLight({ room }: { room: Room }) {
  const cx = (room.minX + room.maxX) / 2;
  return (
    <group>
      <pointLight position={[cx, 2.7, room.minZ + 2.9]} color="#ffc98a" intensity={9} distance={7.5} decay={2} />
      <pointLight position={[room.minX + 0.4, 1.7, room.minZ + 3.1]} color="#ffd9a0" intensity={2.2} distance={3.2} decay={2} />
      <pointLight position={[room.maxX - 0.4, 1.7, room.minZ + 2.9]} color="#ffd9a0" intensity={2.2} distance={3.2} decay={2} />
    </group>
  );
}
