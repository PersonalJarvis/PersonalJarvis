/**
 * The break room as a cosy startup lounge (it stands on both floors): an L of
 * oatmeal bouclé sofa modules with plump cushions and knitted throws round a
 * solid-oak coffee table laid with magazines, a board game and a chess game;
 * a large kilim rug; a reading nook with a sage armchair and a linen floor
 * lamp by the dog's basket; a bookcase wall with sage-backed bays, books and
 * objects under warm shelf lights; a foosball table; beanbags; a
 * filtered-water station; monsteras in woven baskets; a pixel mat in front
 * of the arcade; globe pendants over the table, a "take five" neon over the
 * coffee bar and a small gallery of prints on the west wall.
 *
 * Same rules as OfficeProps: every furniture piece is built in local space
 * centred on the origin, front facing +z, inside its `FURNITURE_SIZE` box.
 * Repeated parts (books, leaves, foosball men, chess pieces) are merged into
 * one geometry per material, and canvas faces are drawn once and shared.
 */
import { memo } from "react";
import {
  BoxGeometry, BufferGeometry, CylinderGeometry, DoubleSide, Euler, Matrix4, MeshBasicMaterial, MeshStandardMaterial, Quaternion,
  SphereGeometry, Vector3,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { cachedCanvasTexture, canvasMaterial } from "./canvasMaterials";
import { Box, GEO, matte, Rounded } from "./OfficeFurniture";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind, type Room } from "./officeLayout";

type Vec3 = [number, number, number];

// ---------------------------------------------------------------------------
// Materials: light Scandinavian oak, oatmeal bouclé, sage, linen, brass,
// with terracotta and mustard accents.
// ---------------------------------------------------------------------------

const C = {
  oak: "#d6b98f",
  oakLight: "#e2c9a2",
  oakDark: "#a88660",
  boucle: "#e3d9c8",
  boucleShade: "#cfc3ae",
  sage: "#93a58a",
  sageDeep: "#6f8468",
  linen: "#efe7d8",
  terracotta: "#c0714f",
  mustard: "#d4a24c",
  charcoal: "#34363a",
  cream: "#f4eee3",
  navy: "#2f3d56",
  led: "#ffdcae",
} as const;

const BM = {
  oak: matte(C.oak, { roughness: 0.6 }),
  oakLight: matte(C.oakLight, { roughness: 0.6 }),
  oakDark: matte(C.oakDark, { roughness: 0.65 }),
  boucle: matte(C.boucle, { roughness: 1 }),
  boucleShade: matte(C.boucleShade, { roughness: 1 }),
  sage: matte(C.sage, { roughness: 0.95 }),
  sagePaint: matte(C.sageDeep, { roughness: 0.8 }),
  linen: matte(C.linen, { roughness: 1 }),
  terracotta: matte(C.terracotta, { roughness: 0.95 }),
  mustard: matte(C.mustard, { roughness: 0.95 }),
  charcoal: matte(C.charcoal, { roughness: 0.7 }),
  cream: matte(C.cream, { roughness: 0.45 }),
  navy: matte(C.navy, { roughness: 0.7 }),
  brass: matte("#c9a25a", { roughness: 0.35, metalness: 0.6 }),
  chrome: matte("#d5d8dc", { roughness: 0.25, metalness: 0.7 }),
  black: matte("#1d1e21", { roughness: 0.5 }),
  marble: matte("#ece9e4", { roughness: 0.3 }),
  soil: matte("#3b2f26"),
  stem: matte("#58703f", { roughness: 0.8 }),
  leaves: ["#3d7444", "#2c5a35", "#5a9654"].map((c) => matte(c, { flatShading: true, roughness: 0.7 })),
  beanbags: [C.sage, C.mustard, C.terracotta].map((c) => matte(c, { roughness: 1 })),
  books: ["#c0714f", "#e9dcc4", "#51705c", "#2f3d56", "#d4a24c", "#8b6246", "#7c8fa8", "#b9c7b0"].map((c) => matte(c, { roughness: 0.8 })),
  // Lit from within, so they read as light in any scene lighting.
  led: new MeshStandardMaterial({ color: C.led, emissive: C.led, emissiveIntensity: 1.2, toneMapped: false }),
  bulb: new MeshStandardMaterial({ color: "#fff4e0", emissive: "#ffe6c2", emissiveIntensity: 1.1, roughness: 0.3, toneMapped: false }),
  shade: new MeshStandardMaterial({ color: "#f3ead9", emissive: "#ffdcae", emissiveIntensity: 0.35, roughness: 0.9, side: DoubleSide }),
  glass: new MeshStandardMaterial({ color: "#e4f1f4", transparent: true, opacity: 0.35, roughness: 0.05, depthWrite: false }),
  water: new MeshStandardMaterial({ color: "#a9d6e6", transparent: true, opacity: 0.6, roughness: 0.1, depthWrite: false }),
  acrylic: new MeshStandardMaterial({ color: "#ffffff", transparent: true, opacity: 0.12, roughness: 0.05, depthWrite: false }),
  pixelGlow: new MeshStandardMaterial({ color: "#c77dff", emissive: "#c77dff", emissiveIntensity: 1.1, toneMapped: false }),
};

// ---------------------------------------------------------------------------
// Canvas faces
// ---------------------------------------------------------------------------

type Ctx = CanvasRenderingContext2D;

/** Deterministic pseudo-random numbers, so drawn patterns and book runs never change between loads. */
function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/** A hand-woven kilim: cream field, stepped diamonds in sage, terracotta and mustard, charcoal borders, fringed ends. */
function drawKilim(ctx: Ctx, w: number, h: number): void {
  const rand = lcg(2024);
  ctx.fillStyle = "#efe4d0";
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 9000; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.14)" : "rgba(110,80,50,0.07)";
    ctx.fillRect(rand() * w, rand() * h, 3, 2);
  }
  const fringe = 26;
  // Fringe along the short ends.
  ctx.strokeStyle = "#e6dac4";
  ctx.lineWidth = 3;
  for (let x = 4; x < w; x += 9) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x + (rand() - 0.5) * 4, fringe); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x, h); ctx.lineTo(x + (rand() - 0.5) * 4, h - fringe); ctx.stroke();
  }
  const x0 = 0, y0 = fringe, x1 = w, y1 = h - fringe;
  // Border bands: charcoal, terracotta with a zigzag, charcoal.
  ctx.fillStyle = C.charcoal;
  ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
  ctx.fillStyle = C.terracotta;
  ctx.fillRect(x0 + 14, y0 + 14, x1 - x0 - 28, y1 - y0 - 28);
  ctx.fillStyle = "#efe4d0";
  ctx.fillRect(x0 + 56, y0 + 56, x1 - x0 - 112, y1 - y0 - 112);
  ctx.strokeStyle = "#efe4d0";
  ctx.lineWidth = 6;
  const zig = (ax: number, ay: number, bx: number, by: number) => {
    const horizontal = ay === by;
    const len = horizontal ? bx - ax : by - ay;
    ctx.beginPath();
    for (let s = 0; s <= len; s += 20) {
      const off = (s / 20) % 2 === 0 ? -8 : 8;
      if (horizontal) ctx.lineTo(ax + s, ay + off); else ctx.lineTo(ax + off, ay + s);
    }
    ctx.stroke();
  };
  zig(x0 + 40, y0 + 35, x1 - 40, y0 + 35);
  zig(x0 + 40, y1 - 35, x1 - 40, y1 - 35);
  zig(x0 + 35, y0 + 40, x0 + 35, y1 - 40);
  zig(x1 - 35, y0 + 40, x1 - 35, y1 - 40);
  // Field: a row of stepped diamonds, small crosses between them.
  const fx0 = x0 + 80, fx1 = x1 - 80, fy0 = y0 + 80, fy1 = y1 - 80;
  const cy = (fy0 + fy1) / 2;
  const count = 3;
  const pitch = (fx1 - fx0) / count;
  const tones = [C.sageDeep, C.terracotta, C.mustard];
  const diamond = (cx: number, r: number, colour: string) => {
    const step = r / 5;
    ctx.fillStyle = colour;
    for (let i = 0; i < 5; i += 1) {
      const half = r - i * step;
      ctx.fillRect(cx - half, cy - (i + 1) * step * 1.3, half * 2, step * 1.3);
      ctx.fillRect(cx - half, cy + i * step * 1.3, half * 2, step * 1.3);
    }
  };
  for (let i = 0; i < count; i += 1) {
    const cx = fx0 + pitch * (i + 0.5);
    const r = Math.min(pitch * 0.42, (fy1 - fy0) * 0.36);
    diamond(cx, r, C.charcoal);
    diamond(cx, r * 0.78, tones[i]);
    diamond(cx, r * 0.42, "#efe4d0");
    diamond(cx, r * 0.2, tones[(i + 1) % 3]);
  }
  ctx.fillStyle = C.sageDeep;
  for (let i = 0; i <= count; i += 1) {
    const cx = fx0 + pitch * i;
    for (const y of [fy0 + 40, fy1 - 40]) {
      ctx.fillRect(cx - 18, y - 5, 36, 10);
      ctx.fillRect(cx - 5, y - 18, 10, 36);
    }
  }
  ctx.fillStyle = C.terracotta;
  for (let i = 0; i < count; i += 1) {
    const cx = fx0 + pitch * (i + 0.5);
    for (const y of [fy0 + 30, fy1 - 30]) ctx.fillRect(cx - 40, y - 4, 80, 8);
  }
}

/** A dark game mat of pixel invaders and stars in front of the arcade. */
function drawPixelMat(ctx: Ctx, w: number, h: number): void {
  const rand = lcg(77);
  ctx.fillStyle = "#16142a";
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 70; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.5)" : "rgba(199,125,255,0.5)";
    ctx.fillRect(Math.floor(rand() * w / 4) * 4, Math.floor(rand() * h / 4) * 4, 4, 4);
  }
  const invader = ["00100000100", "00010001000", "00111111100", "01101110110", "11111111111", "10111111101", "10100000101", "00011011000"];
  const draw = (cx: number, cy: number, px: number, colour: string) => {
    ctx.fillStyle = colour;
    invader.forEach((row, r) => [...row].forEach((bit, c) => {
      if (bit === "1") ctx.fillRect(cx + (c - 5.5) * px, cy + (r - 4) * px, px, px);
    }));
  };
  draw(w * 0.28, h * 0.3, 9, "#ff5fa2");
  draw(w * 0.72, h * 0.3, 9, "#4fd1c5");
  draw(w * 0.5, h * 0.62, 12, "#ffd166");
  ctx.strokeStyle = "#c77dff";
  ctx.lineWidth = 8;
  ctx.strokeRect(10, 10, w - 20, h - 20);
  ctx.fillStyle = "#ffffff";
  ctx.font = "700 30px ui-monospace, 'Cascadia Mono', monospace";
  ctx.textAlign = "center";
  ctx.fillText("PLAYER 1", w / 2, h - 34);
}

/** Neon tubes on a transparent sheet: a soft halo under a bright core. */
function drawNeon(text: string, colour: string) {
  return (ctx: Ctx, w: number, h: number) => {
    ctx.clearRect(0, 0, w, h);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = "italic 600 132px 'Segoe Script', 'Brush Script MT', 'Snell Roundhand', cursive";
    ctx.lineJoin = "round";
    ctx.shadowColor = colour;
    ctx.shadowBlur = 44;
    ctx.strokeStyle = colour;
    ctx.lineWidth = 20;
    ctx.strokeText(text, w / 2, h / 2 + 6);
    ctx.shadowBlur = 14;
    ctx.lineWidth = 12;
    ctx.strokeText(text, w / 2, h / 2 + 6);
    ctx.shadowBlur = 0;
    ctx.strokeStyle = "#fff1f5";
    ctx.lineWidth = 5;
    ctx.strokeText(text, w / 2, h / 2 + 6);
  };
}

/** Three prints for the gallery wall. */
function drawArches(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#f4eee3";
  ctx.fillRect(0, 0, w, h);
  const bands = [C.terracotta, C.mustard, C.sage, "#f4eee3"];
  bands.forEach((colour, i) => {
    ctx.fillStyle = colour;
    const r = w * (0.38 - i * 0.09);
    ctx.beginPath();
    ctx.moveTo(w / 2 - r, h * 0.82);
    ctx.arc(w / 2, h * 0.82, r, Math.PI, 0);
    ctx.closePath();
    ctx.fill();
  });
  ctx.fillStyle = C.navy;
  ctx.fillRect(w * 0.1, h * 0.82, w * 0.8, 4);
}

function drawSunset(ctx: Ctx, w: number, h: number): void {
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#f3d9b8");
  grad.addColorStop(1, "#e6a57e");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#fbe7c6";
  ctx.beginPath(); ctx.arc(w * 0.5, h * 0.45, w * 0.2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = C.sageDeep;
  ctx.beginPath(); ctx.moveTo(0, h * 0.72); ctx.lineTo(w * 0.35, h * 0.52); ctx.lineTo(w * 0.62, h * 0.66); ctx.lineTo(w, h * 0.5); ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.fill();
  ctx.fillStyle = "#4d6149";
  ctx.beginPath(); ctx.moveTo(0, h * 0.85); ctx.lineTo(w * 0.5, h * 0.7); ctx.lineTo(w, h * 0.82); ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.fill();
}

function drawBotanical(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#efe9dc";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "#51705c";
  ctx.fillStyle = "#6f8468";
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(w / 2, h * 0.9); ctx.quadraticCurveTo(w * 0.45, h * 0.5, w / 2, h * 0.12); ctx.stroke();
  for (let i = 0; i < 7; i += 1) {
    const y = h * (0.8 - i * 0.1);
    const side = i % 2 === 0 ? 1 : -1;
    ctx.beginPath();
    ctx.ellipse(w / 2 + side * w * 0.14, y, w * 0.15, h * 0.035, side * -0.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

/** A clock face: cream dial, charcoal ticks, hands at ten past ten. */
function drawClock(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#f6f1e8";
  ctx.beginPath(); ctx.arc(w / 2, h / 2, w / 2, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = C.charcoal;
  for (let i = 0; i < 12; i += 1) {
    const a = (i / 12) * Math.PI * 2;
    ctx.lineWidth = i % 3 === 0 ? 7 : 3;
    ctx.beginPath();
    ctx.moveTo(w / 2 + Math.sin(a) * w * 0.36, h / 2 - Math.cos(a) * h * 0.36);
    ctx.lineTo(w / 2 + Math.sin(a) * w * 0.44, h / 2 - Math.cos(a) * h * 0.44);
    ctx.stroke();
  }
  ctx.lineCap = "round";
  ctx.lineWidth = 8;
  ctx.beginPath(); ctx.moveTo(w / 2, h / 2); ctx.lineTo(w / 2 - w * 0.2, h / 2 - h * 0.12); ctx.stroke();
  ctx.lineWidth = 5;
  ctx.beginPath(); ctx.moveTo(w / 2, h / 2); ctx.lineTo(w / 2 + w * 0.3, h / 2 - h * 0.17); ctx.stroke();
  ctx.fillStyle = C.terracotta;
  ctx.beginPath(); ctx.arc(w / 2, h / 2, 7, 0, Math.PI * 2); ctx.fill();
}

/** A board game lid: a hex map in island colours under a title band. */
function drawGameLid(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#2b6f8f";
  ctx.fillRect(0, 0, w, h);
  const tiles = ["#d4a24c", "#5a9654", "#c0714f", "#e9dcc4", "#3d7444", "#8b6246"];
  const r = 18;
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < 5; col += 1) {
      const cx = 46 + col * r * 1.75 + (row % 2) * r * 0.88;
      const cy = 64 + row * r * 1.5;
      ctx.fillStyle = tiles[(row * 5 + col) % tiles.length];
      ctx.beginPath();
      for (let k = 0; k < 6; k += 1) {
        const a = (k / 6) * Math.PI * 2 + Math.PI / 6;
        ctx.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
      }
      ctx.closePath();
      ctx.fill();
    }
  }
  ctx.fillStyle = "#f4eee3";
  ctx.fillRect(0, 0, w, 30);
  ctx.fillStyle = C.navy;
  ctx.font = "800 20px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("SETTLERS", w / 2, 22);
}

/** Two magazine covers side by side: a big cover line over a photo block. */
function drawMagazines(ctx: Ctx, w: number, h: number): void {
  const covers = [["#f4eee3", C.terracotta, "KINFOLK"], ["#dfe7da", C.navy, "WIRED"]] as const;
  covers.forEach(([paper, ink, title], i) => {
    const x = (i * w) / 2;
    ctx.fillStyle = paper;
    ctx.fillRect(x, 0, w / 2, h);
    ctx.fillStyle = ink;
    ctx.font = "800 26px Georgia, serif";
    ctx.textAlign = "center";
    ctx.fillText(title, x + w / 4, 34);
    ctx.fillStyle = i === 0 ? C.sage : C.mustard;
    ctx.fillRect(x + 16, 50, w / 2 - 32, h * 0.52);
    ctx.fillStyle = "rgba(40,40,40,0.5)";
    for (let l = 0; l < 3; l += 1) ctx.fillRect(x + 16, h - 50 + l * 12, w / 2 - 50 - l * 14, 5);
  });
}

/** A chess board: eight by eight in oak tones with a walnut rim. */
function drawChess(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#6a4631";
  ctx.fillRect(0, 0, w, h);
  const pad = w * 0.06, cell = (w - pad * 2) / 8;
  for (let r = 0; r < 8; r += 1) {
    for (let c = 0; c < 8; c += 1) {
      ctx.fillStyle = (r + c) % 2 === 0 ? "#efdcbc" : "#9c7650";
      ctx.fillRect(pad + c * cell, pad + r * cell, cell, cell);
    }
  }
}

/** The foosball pitch: felt green with white lines. */
function drawPitch(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#3f7d4a";
  ctx.fillRect(0, 0, w, h);
  for (let x = 0; x < w; x += w / 8) {
    ctx.fillStyle = "rgba(255,255,255,0.05)";
    ctx.fillRect(x, 0, w / 16, h);
  }
  ctx.strokeStyle = "rgba(255,255,255,0.85)";
  ctx.lineWidth = 4;
  ctx.strokeRect(8, 8, w - 16, h - 16);
  ctx.beginPath(); ctx.moveTo(w / 2, 8); ctx.lineTo(w / 2, h - 8); ctx.stroke();
  ctx.beginPath(); ctx.arc(w / 2, h / 2, h * 0.18, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeRect(8, h * 0.28, w * 0.1, h * 0.44);
  ctx.strokeRect(w - 8 - w * 0.1, h * 0.28, w * 0.1, h * 0.44);
}

/** A woven seagrass basket. */
function drawWeave(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#c9ab7c";
  ctx.fillRect(0, 0, w, h);
  for (let y = 0; y < h; y += 8) {
    for (let x = 0; x < w; x += 12) {
      ctx.fillStyle = ((x / 12 + y / 8) % 2) === 0 ? "rgba(90,60,30,0.22)" : "rgba(255,240,210,0.18)";
      ctx.fillRect(x, y, 12, 6);
    }
  }
}

const faces = {
  kilim: () => canvasMaterial("break:kilim", 1024, 832, drawKilim, { fallback: "#efe4d0", roughness: 1 }),
  pixelMat: () => canvasMaterial("break:pixel-mat", 256, 280, drawPixelMat, { glow: 0.25, fallback: "#16142a", roughness: 1 }),
  arches: () => canvasMaterial("break:print-arches", 160, 200, drawArches, { fallback: "#f4eee3", roughness: 0.8 }),
  sunset: () => canvasMaterial("break:print-sunset", 240, 300, drawSunset, { fallback: "#f3d9b8", roughness: 0.8 }),
  botanical: () => canvasMaterial("break:print-botanical", 160, 200, drawBotanical, { fallback: "#efe9dc", roughness: 0.8 }),
  clock: () => canvasMaterial("break:clock", 128, 128, drawClock, { fallback: "#f6f1e8", roughness: 0.5 }),
  gameLid: () => canvasMaterial("break:game-lid", 256, 160, drawGameLid, { fallback: "#2b6f8f", roughness: 0.7 }),
  magazines: () => canvasMaterial("break:magazines", 256, 170, drawMagazines, { fallback: "#f4eee3", roughness: 0.7 }),
  chess: () => canvasMaterial("break:chess", 128, 128, drawChess, { fallback: "#9c7650", roughness: 0.6 }),
  pitch: () => canvasMaterial("break:pitch", 256, 136, drawPitch, { fallback: "#3f7d4a", roughness: 0.95 }),
  weave: () => canvasMaterial("break:weave", 128, 64, drawWeave, { fallback: "#c9ab7c", roughness: 1 }),
};

/** The neon sign glows on its own and lets the wall show through between the tubes. */
let neonMaterial: MeshBasicMaterial | null = null;
function neon(): MeshBasicMaterial {
  if (!neonMaterial) {
    const map = cachedCanvasTexture("break:neon", 1024, 300, drawNeon("take five", "#ff6f91"));
    neonMaterial = map
      ? new MeshBasicMaterial({ map, transparent: true, depthWrite: false, toneMapped: false })
      : new MeshBasicMaterial({ color: "#ff6f91", transparent: true, opacity: 0, depthWrite: false });
  }
  return neonMaterial;
}

// ---------------------------------------------------------------------------
// Shared geometry
// ---------------------------------------------------------------------------

const BGEO = {
  cyl: new CylinderGeometry(1, 1, 1, 20),
  sphere: new SphereGeometry(1, 24, 16),
  basket: new CylinderGeometry(0.24, 0.2, 0.42, 28, 1, true),
  lampShade: new CylinderGeometry(0.09, 0.11, 0.2, 24, 1, true),
  globe: new SphereGeometry(0.13, 24, 16),
  bag: new SphereGeometry(1, 28, 18),
};

function Cyl({ radius, height, position, material, rotation, cast = true }: {
  radius: number; height: number; position: Vec3; material: MeshStandardMaterial; rotation?: Vec3; cast?: boolean;
}) {
  return <mesh geometry={BGEO.cyl} material={material} position={position} rotation={rotation} scale={[radius, height, radius]} castShadow={cast} receiveShadow />;
}

function Panel({ size, position, material, rotation }: {
  size: [number, number]; position: Vec3; material: MeshStandardMaterial | MeshBasicMaterial; rotation?: Vec3;
}) {
  return <mesh position={position} rotation={rotation} material={material}><planeGeometry args={size} /></mesh>;
}

/** Merge parts into one geometry and free the parts. */
function merge(parts: BufferGeometry[]): BufferGeometry {
  const merged = mergeGeometries(parts, false);
  parts.forEach((p) => p.dispose());
  return merged;
}

/** A box part placed at `at`, for merging. */
function boxPart(size: Vec3, at: Vec3, rotZ = 0): BufferGeometry {
  const g = new BoxGeometry(size[0], size[1], size[2]);
  if (rotZ) g.rotateZ(rotZ);
  return g.translate(at[0], at[1], at[2]);
}

/**
 * The bookcase's books, one merged geometry per spine colour: runs of upright
 * books with a leaning one at the end, and a few flat stacks.
 */
const BOOKCASE = { w: FURNITURE_SIZE.breakBookcase.w, bays: [-1.035, -0.345, 0.345, 1.035], bayW: 0.64, shelves: [0.64, 1.1, 1.56] };
const BOOK_GEOMETRIES: BufferGeometry[] = (() => {
  const rand = lcg(31337);
  const byTone: BufferGeometry[][] = BM.books.map(() => []);
  const add = (size: Vec3, at: Vec3, rotZ = 0) => byTone[Math.floor(rand() * byTone.length)].push(boxPart(size, at, rotZ));
  // Which part of each bay holds books, as [from, to] fractions of its width; the rest holds objects.
  const runs: Record<string, [number, number]> = {
    "0:0": [0, 1], "0:2": [0, 0.55], "0:3": [0.05, 0.95],
    "1:0": [0.4, 1], "1:1": [0, 1], "1:3": [0, 0.6],
    "2:1": [0.45, 1], "2:2": [0, 1],
  };
  BOOKCASE.shelves.forEach((y, level) => {
    BOOKCASE.bays.forEach((bx, bay) => {
      const run = runs[`${level}:${bay}`];
      if (!run) return;
      let x = bx - BOOKCASE.bayW / 2 + run[0] * BOOKCASE.bayW + 0.01;
      const end = bx - BOOKCASE.bayW / 2 + run[1] * BOOKCASE.bayW - 0.01;
      while (x < end - 0.06) {
        const bw = 0.024 + rand() * 0.026;
        const bh = 0.22 + rand() * 0.14;
        const bd = 0.2 + rand() * 0.06;
        add([bw, bh, bd], [x + bw / 2, y + bh / 2, 0.02]);
        x += bw + 0.002;
      }
      // One book leaning on the run's end, where it fits.
      const lh = 0.26;
      if (x + 0.08 < end) add([0.03, lh, 0.22], [x + 0.04, y + (lh / 2) * Math.cos(0.3) + 0.004, 0.02], -0.3);
    });
  });
  // Flat stacks on two shelves.
  for (const [x, y] of [[0.53, 0.64], [1.23, 1.1]] as const) {
    let top = y;
    for (let i = 0; i < 4; i += 1) {
      const t = 0.03 + rand() * 0.015;
      add([0.2 - i * 0.015, t, 0.18 - i * 0.01], [x + (rand() - 0.5) * 0.02, top + t / 2, 0.03]);
      top += t;
    }
  }
  return byTone.map((parts) => merge(parts));
})();

/** A monstera's split leaves on arching stems, merged per leaf tone plus one stem geometry. */
const MONSTERA: { leaves: BufferGeometry[]; stems: BufferGeometry } = (() => {
  const base = new SphereGeometry(1, 12, 8);
  const tones: BufferGeometry[][] = [[], [], []];
  const stems: BufferGeometry[] = [];
  const matrix = new Matrix4(), rotation = new Quaternion(), euler = new Euler(0, 0, 0, "YXZ");
  const at = new Vector3(), scale = new Vector3();
  const count = 13;
  for (let i = 0; i < count; i += 1) {
    const t = i / (count - 1);
    const angle = i * 2.39996;
    const tip = 0.95 + (1 - Math.abs(t - 0.45)) * 0.75;
    const reach = 0.04 + 0.05 * Math.sin(Math.PI * t);
    const half = 0.15 - 0.03 * Math.abs(t - 0.5);
    // Stem: a thin cylinder from the soil to under the leaf.
    const stem = new CylinderGeometry(0.008, 0.011, 1, 6);
    const dx = Math.sin(angle) * reach, dz = Math.cos(angle) * reach;
    const length = Math.hypot(dx, tip - 0.5, dz);
    matrix.lookAt(new Vector3(0, 0, 0), new Vector3(dx, tip - 0.5, dz), new Vector3(0, 0, 1));
    stem.rotateX(Math.PI / 2);
    stem.scale(1, 1, length);
    stem.applyMatrix4(new Matrix4().extractRotation(matrix));
    stem.translate(dx / 2, 0.5 + (tip - 0.5) / 2, dz / 2);
    stems.push(stem);
    // Leaf: a broad flattened ellipsoid tipping outward and down.
    const droop = 0.35 + 0.35 * (1 - t);
    at.set(dx + Math.sin(angle) * half * 0.5, tip + 0.02, dz + Math.cos(angle) * half * 0.5);
    euler.set(droop, angle, ((i % 3) - 1) * 0.25);
    rotation.setFromEuler(euler);
    scale.set(half * 0.85, 0.012, half);
    matrix.compose(at, rotation, scale);
    tones[i % 3].push(base.clone().applyMatrix4(matrix));
  }
  base.dispose();
  return { leaves: tones.map((parts) => merge(parts)), stems: merge(stems) };
})();

/** Foosball: which team each of the eight rods belongs to and how many men it carries (goalie to goalie). */
const FOOS_RODS: readonly [team: 0 | 1, men: number][] = [[0, 1], [0, 2], [1, 3], [0, 5], [1, 5], [0, 3], [1, 2], [1, 1]];
const FOOS = { pitchY: 0.52, rodY: 0.6, pitch: 0.13, halfZ: 0.25 };
const FOOS_GEOMETRIES: { men: BufferGeometry[]; rods: BufferGeometry; handles: BufferGeometry } = (() => {
  const men: BufferGeometry[][] = [[], []];
  const rods: BufferGeometry[] = [];
  const handles: BufferGeometry[] = [];
  FOOS_RODS.forEach(([team, count], i) => {
    const x = -((FOOS_RODS.length - 1) * FOOS.pitch) / 2 + i * FOOS.pitch;
    // Team 0 works its rods from the +z side, team 1 from the -z side.
    const side = team === 0 ? 1 : -1;
    rods.push(new CylinderGeometry(0.007, 0.007, 0.86, 8).rotateX(Math.PI / 2).translate(x, FOOS.rodY, side * 0.06));
    handles.push(new CylinderGeometry(0.017, 0.017, 0.1, 12).rotateX(Math.PI / 2).translate(x, FOOS.rodY, side * 0.46));
    for (let m = 0; m < count; m += 1) {
      const z = -FOOS.halfZ + ((m + 0.5) * FOOS.halfZ * 2) / count;
      men[team].push(new BoxGeometry(0.022, 0.07, 0.032).translate(x, FOOS.rodY - 0.03, z));
      men[team].push(new SphereGeometry(0.017, 10, 8).translate(x, FOOS.rodY + 0.022, z));
    }
  });
  return { men: men.map((parts) => merge(parts)), rods: merge(rods), handles: merge(handles) };
})();

/** Chess pieces mid-game, merged per side: a base disc, a body and a head. */
const CHESS_GEOMETRIES: BufferGeometry[] = (() => {
  const cell = 0.3 * 0.88 / 8;
  const origin = -0.3 * 0.44 + cell / 2;
  const squares: [number, number, number, boolean][] = [
    // [file, rank, height, tall]
    [0, 0, 0.034, false], [4, 0, 0.05, true], [5, 1, 0.026, false], [6, 1, 0.026, false], [2, 2, 0.04, false], [4, 3, 0.026, false], [3, 0, 0.046, true],
    [0, 7, 0.034, false], [4, 7, 0.05, true], [3, 6, 0.026, false], [5, 5, 0.04, false], [6, 6, 0.026, false], [4, 4, 0.026, false], [7, 7, 0.034, false],
  ];
  const sides: BufferGeometry[][] = [[], []];
  squares.forEach(([file, rank, height, tall], i) => {
    const x = origin + file * cell, z = origin + rank * cell;
    const side = i < 7 ? 0 : 1;
    sides[side].push(new CylinderGeometry(0.011, 0.013, 0.006, 12).translate(x, 0.003, z));
    sides[side].push(new CylinderGeometry(tall ? 0.006 : 0.007, 0.01, height, 12).translate(x, height / 2, z));
    sides[side].push(new SphereGeometry(tall ? 0.009 : 0.008, 10, 8).translate(x, height + 0.004, z));
  });
  return sides.map((parts) => merge(parts));
})();

// ---------------------------------------------------------------------------
// Furniture
// ---------------------------------------------------------------------------

/**
 * The sofa modules' open end, by furniture id: +1 = the +x end, -1 = the -x end.
 * The two long modules meet the corner module there, so that end has no arm.
 */
const SOFA_OPEN_END: Record<string, 1 | -1> = { "break-sofa-a": 1, "break-sofa-b": -1 };

/** Seat cushion top: the couch seat height figures sit at. */
const SEAT_TOP = 0.56;

/** An accent pillow, leaning back against the back cushions. */
function Pillow({ position, material, rotationY = 0, tilt = -0.28 }: { position: Vec3; material: MeshStandardMaterial; rotationY?: number; tilt?: number }) {
  return (
    <group position={position} rotation={[tilt, rotationY, 0]}>
      <Rounded size={[0.38, 0.34, 0.11]} radius={0.05} position={[0, 0, 0]} material={material} />
    </group>
  );
}

/**
 * A long bouclé sofa module facing +z: a recessed oak plinth, a deep base,
 * two seat and two back cushions, an arm on the closed end, accent pillows
 * and — on the module with an arm facing the room — a sage throw over it.
 */
function SofaModule({ id }: { id: string }) {
  const open = SOFA_OPEN_END[id] ?? 1;
  const armX = -open * 1.01;
  const seatCentre = open * 0.09;
  return (
    <group>
      <Box size={[2.1, 0.07, 0.78]} position={[0, 0.035, 0]} material={BM.oakDark} />
      <Rounded size={[2.2, 0.28, 0.9]} radius={0.05} position={[0, 0.21, 0]} material={BM.boucleShade} />
      <Rounded size={[2.2, 0.38, 0.2]} radius={0.06} position={[0, 0.53, -0.35]} material={BM.boucleShade} />
      <Rounded size={[0.18, 0.36, 0.9]} radius={0.07} position={[armX, 0.52, 0]} material={BM.boucleShade} />
      {[-0.5, 0.5].map((dx) => (
        <group key={dx}>
          <Rounded size={[0.98, 0.2, 0.68]} radius={0.07} position={[seatCentre + dx, SEAT_TOP - 0.1, 0.1]} material={BM.boucle} />
          <group position={[seatCentre + dx, 0.66, -0.23]} rotation={[-0.12, 0, 0]}>
            <Rounded size={[0.96, 0.36, 0.2]} radius={0.09} position={[0, 0, 0]} material={BM.boucle} />
          </group>
        </group>
      ))}
      <Pillow position={[seatCentre - 0.72, 0.67, -0.1]} material={BM.terracotta} rotationY={0.18} />
      <Pillow position={[seatCentre + 0.2, 0.66, -0.1]} material={BM.mustard} rotationY={-0.1} />
      {/* The knitted throw over the arm: the top fold and the drape down the outside. */}
      <Box size={[0.2, 0.014, 0.52]} position={[armX, 0.707, 0.05]} material={BM.sage} />
      <Box size={[0.014, 0.34, 0.52]} position={[armX - open * 0.093, 0.54, 0.05]} material={BM.sage} />
    </group>
  );
}

/** The sectional's corner module: backs along -z and +x, open towards -x and +z, a throw folded on the seat. */
function SofaCorner() {
  return (
    <group>
      <Box size={[0.8, 0.07, 0.8]} position={[0, 0.035, 0]} material={BM.oakDark} />
      <Rounded size={[0.9, 0.28, 0.9]} radius={0.05} position={[0, 0.21, 0]} material={BM.boucleShade} />
      <Rounded size={[0.9, 0.38, 0.2]} radius={0.06} position={[0, 0.53, -0.35]} material={BM.boucleShade} />
      <Rounded size={[0.2, 0.38, 0.9]} radius={0.06} position={[0.35, 0.53, 0]} material={BM.boucleShade} />
      <Rounded size={[0.68, 0.2, 0.68]} radius={0.07} position={[-0.1, SEAT_TOP - 0.1, 0.1]} material={BM.boucle} />
      <group position={[-0.1, 0.66, -0.23]} rotation={[-0.12, 0, 0]}>
        <Rounded size={[0.66, 0.36, 0.2]} radius={0.09} position={[0, 0, 0]} material={BM.boucle} />
      </group>
      <group position={[0.23, 0.66, 0.1]} rotation={[0, 0, 0.12]}>
        <Rounded size={[0.2, 0.36, 0.66]} radius={0.09} position={[0, 0, 0]} material={BM.boucle} />
      </group>
      <Pillow position={[0.05, 0.67, -0.05]} material={BM.sage} rotationY={-Math.PI / 4} tilt={-0.2} />
      {/* A folded throw in terracotta on the seat. */}
      <Rounded size={[0.42, 0.06, 0.3]} radius={0.02} position={[-0.18, SEAT_TOP + 0.03, 0.22]} material={BM.terracotta} />
      <Box size={[0.42, 0.004, 0.02]} position={[-0.18, SEAT_TOP + 0.061, 0.3]} material={BM.cream} cast={false} />
    </group>
  );
}

/**
 * Solid-oak coffee table on chunky legs with a lower shelf of magazines. On
 * top: a chess game in progress, a board game box on a second one, fanned
 * magazines, a mug and a small succulent bowl.
 */
function BreakTable() {
  return (
    <group>
      {[-0.56, 0.56].flatMap((x) => [-0.29, 0.29].map((z) => (
        <Box key={`${x}:${z}`} size={[0.07, 0.34, 0.07]} position={[x, 0.17, z]} material={BM.oak} />
      )))}
      <Box size={[1.18, 0.025, 0.6]} position={[0, 0.11, 0]} material={BM.oakLight} />
      <Rounded size={[1.3, 0.06, 0.75]} radius={0.025} position={[0, 0.37, 0]} material={BM.oak} />
      {/* Magazines on the shelf. */}
      <Box size={[0.3, 0.02, 0.22]} position={[-0.3, 0.132, 0.04]} material={BM.books[2]} cast={false} />
      <Box size={[0.29, 0.02, 0.21]} position={[-0.29, 0.152, 0.04]} material={BM.books[1]} cast={false} />
      <Box size={[0.26, 0.05, 0.26]} position={[0.3, 0.148, -0.04]} material={BM.navy} />
      {/* The chess game. */}
      <group position={[-0.32, 0.4, 0.02]}>
        <Box size={[0.3, 0.014, 0.3]} position={[0, 0.007, 0]} material={faces.chess()} />
        <group position={[0, 0.014, 0]}>
          <mesh geometry={CHESS_GEOMETRIES[0]} material={BM.cream} castShadow />
          <mesh geometry={CHESS_GEOMETRIES[1]} material={BM.black} castShadow />
        </group>
      </group>
      {/* Board game boxes. */}
      <Box size={[0.3, 0.05, 0.2]} position={[0.3, 0.425, -0.14]} material={BM.terracotta} />
      <group position={[0.3, 0.4725, -0.14]} rotation={[0, 0.12, 0]}>
        <Box size={[0.26, 0.045, 0.17]} position={[0, 0, 0]} material={BM.navy} />
        <Panel size={[0.26, 0.17]} position={[0, 0.0231, 0]} rotation={[-Math.PI / 2, 0, 0]} material={faces.gameLid()} />
      </group>
      {/* Fanned magazines, a mug and a succulent. */}
      <group position={[0.22, 0.401, 0.16]} rotation={[0, -0.25, 0]}>
        <Panel size={[0.34, 0.225]} position={[0, 0.002, 0]} rotation={[-Math.PI / 2, 0, 0]} material={faces.magazines()} />
      </group>
      <Cyl radius={0.04} height={0.08} position={[0.5, 0.44, 0.08]} material={BM.cream} />
      <Box size={[0.012, 0.04, 0.012]} position={[0.545, 0.44, 0.08]} material={BM.cream} cast={false} />
      <Cyl radius={0.06} height={0.04} position={[0.02, 0.42, -0.24]} material={BM.terracotta} />
      {[[-0.02, 0, -0.24], [0.04, 0, -0.22], [0.01, 0, -0.28]].map(([x, , z], i) => (
        <mesh key={i} geometry={GEO.blob} material={BM.leaves[i]} position={[x, 0.45, z]} scale={[0.025, 0.02, 0.025]} castShadow />
      ))}
    </group>
  );
}

/** The kilim rug, sized per instance. */
function BreakRug({ w, d }: { w: number; d: number }) {
  return <Box size={[w, 0.014, d]} position={[0, 0.007, 0]} material={faces.kilim()} cast={false} />;
}

/** The pixel game mat in front of the arcade, with a violet glow strip along its edge. */
function ArcadeMat({ w, d }: { w: number; d: number }) {
  return (
    <group>
      <Box size={[w, 0.012, d]} position={[0, 0.006, 0]} material={faces.pixelMat()} cast={false} />
      <Box size={[0.02, 0.006, d - 0.04]} position={[-w / 2 + 0.03, 0.015, 0]} material={BM.pixelGlow} cast={false} />
    </group>
  );
}

/**
 * The bookcase wall: an oak carcass with sage-painted back panels, four bays
 * over a cabinet with sage doors and brass pulls, warm light strips under the
 * shelves, runs of books and objects — vases, a trailing pothos, a framed
 * photo, stacked board games, a little brass trophy.
 */
function BreakBookcase() {
  const w = BOOKCASE.w;
  return (
    <group>
      {/* Carcass: back, ends, top, cabinet top and shelves, bay dividers. */}
      <Box size={[w - 0.04, 1.98, 0.015]} position={[0, 1.0, -0.185]} material={BM.sagePaint} cast={false} />
      {[-w / 2 + 0.015, w / 2 - 0.015].map((x) => <Box key={x} size={[0.03, 2.03, 0.38]} position={[x, 1.015, 0]} material={BM.oak} />)}
      <Box size={[w, 0.035, 0.4]} position={[0, 2.0125, 0]} material={BM.oak} />
      <Box size={[w - 0.06, 0.03, 0.38]} position={[0, 0.625, 0]} material={BM.oak} />
      {BOOKCASE.shelves.slice(1).map((y) => <Box key={y} size={[w - 0.06, 0.025, 0.36]} position={[0, y - 0.0125, 0]} material={BM.oak} />)}
      {[-0.69, 0, 0.69].map((x) => <Box key={x} size={[0.025, 1.36, 0.36]} position={[x, 1.315, 0]} material={BM.oak} />)}
      {/* Cabinet: body, four sage doors with brass pulls, a recessed kick plinth. */}
      <Box size={[w - 0.06, 0.52, 0.34]} position={[0, 0.35, -0.01]} material={BM.oakLight} />
      <Box size={[w - 0.06, 0.08, 0.3]} position={[0, 0.04, -0.03]} material={BM.oakDark} />
      {BOOKCASE.bays.map((x) => (
        <group key={x}>
          <Box size={[0.64, 0.5, 0.014]} position={[x, 0.35, 0.167]} material={BM.sage} />
          <Box size={[0.12, 0.014, 0.02]} position={[x, 0.54, 0.18]} material={BM.brass} cast={false} />
        </group>
      ))}
      {/* Warm light strips under each shelf, at the front edge. */}
      {[1.075, 1.535, 1.99].map((y) => (
        <Box key={y} size={[w - 0.12, 0.006, 0.014]} position={[0, y - 0.005, 0.16]} material={BM.led} cast={false} />
      ))}
      {BOOK_GEOMETRIES.map((geometry, i) => <mesh key={i} geometry={geometry} material={BM.books[i]} castShadow receiveShadow />)}
      {/* Bottom shelf: stacked board games in bay 1. */}
      <Box size={[0.4, 0.07, 0.28]} position={[-0.345, 0.675, 0.01]} material={BM.navy} />
      <Box size={[0.38, 0.06, 0.26]} position={[-0.34, 0.74, 0.01]} material={BM.terracotta} />
      <Box size={[0.36, 0.08, 0.27]} position={[-0.35, 0.81, 0.01]} material={BM.mustard} />
      <Box size={[0.3, 0.05, 0.22]} position={[-0.34, 0.875, 0.01]} material={BM.cream} />
      {/* Middle shelf: a pothos in bay 0, a framed photo and a sculpture in bay 2. */}
      <Cyl radius={0.065} height={0.12} position={[-1.2, 1.16, 0.02]} material={BM.cream} />
      {[[-1.22, 1.25, 0.03, 0.09], [-1.15, 1.23, 0.07, 0.07], [-1.26, 1.2, 0.1, 0.06], [-1.18, 1.15, 0.13, 0.05]].map(([x, y, z, s], i) => (
        <mesh key={i} geometry={GEO.blob} material={BM.leaves[i % 3]} position={[x, y, z]} scale={[s, s * 0.8, s]} castShadow />
      ))}
      <group position={[0.24, 1.1, -0.06]} rotation={[-0.1, 0.15, 0]}>
        <Box size={[0.24, 0.3, 0.02]} position={[0, 0.15, 0]} material={BM.oakDark} />
        <Panel size={[0.19, 0.25]} position={[0, 0.15, 0.0105]} material={faces.sunset()} />
      </group>
      <mesh geometry={BGEO.sphere} material={BM.cream} position={[0.5, 1.2, 0.02]} scale={[0.06, 0.06, 0.06]} castShadow />
      <Cyl radius={0.03} height={0.04} position={[0.5, 1.12, 0.02]} material={BM.charcoal} />
      {/* Top shelf: vases in bay 0, a brass trophy in bay 1, a trailing plant in bay 3. */}
      <Cyl radius={0.05} height={0.24} position={[-1.2, 1.68, 0.02]} material={BM.sage} />
      <Cyl radius={0.04} height={0.15} position={[-1.06, 1.635, 0.03]} material={BM.cream} />
      <mesh geometry={BGEO.sphere} material={BM.terracotta} position={[-0.9, 1.61, 0.03]} scale={[0.05, 0.05, 0.05]} castShadow />
      <Cyl radius={0.035} height={0.03} position={[-0.47, 1.575, 0.02]} material={BM.charcoal} />
      <Cyl radius={0.008} height={0.08} position={[-0.47, 1.63, 0.02]} material={BM.brass} />
      <mesh geometry={BGEO.sphere} material={BM.brass} position={[-0.47, 1.69, 0.02]} scale={[0.04, 0.035, 0.04]} castShadow />
      <Cyl radius={0.07} height={0.1} position={[1.05, 1.61, 0.02]} material={BM.terracotta} />
      {[[1.0, 1.68, 0.05, 0.08], [1.1, 1.66, 0.08, 0.07], [1.05, 1.56, 0.14, 0.05], [0.98, 1.46, 0.15, 0.045], [1.12, 1.5, 0.15, 0.045], [1.04, 1.36, 0.16, 0.04]]
        .map(([x, y, z, s], i) => (
          <mesh key={i} geometry={GEO.blob} material={BM.leaves[(i + 1) % 3]} position={[x, y, z]} scale={[s, s * 0.8, s * 0.6]} castShadow />
        ))}
    </group>
  );
}

/**
 * The reading nook: a sage bouclé tub armchair on oak legs with a terracotta
 * lumbar pillow and an open book on the arm, and a linen-shaded floor lamp
 * on a marble foot in the corner behind it.
 */
const NOOK_CHAIR = { x: 0.08, z: 0.08 } as const;
function ReadingNook() {
  return (
    <group>
      <group position={[NOOK_CHAIR.x, 0, NOOK_CHAIR.z]}>
        {[-0.27, 0.27].flatMap((x) => [-0.26, 0.26].map((z) => (
          <Cyl key={`${x}:${z}`} radius={0.018} height={0.22} position={[x, 0.11, z]} material={BM.oak} />
        )))}
        <Rounded size={[0.7, 0.2, 0.7]} radius={0.06} position={[0, 0.31, 0]} material={BM.sage} />
        <Rounded size={[0.5, 0.15, 0.54]} radius={0.06} position={[0, SEAT_TOP - 0.075, 0.06]} material={BM.linen} />
        <group position={[0, 0.62, -0.28]} rotation={[-0.14, 0, 0]}>
          <Rounded size={[0.7, 0.48, 0.14]} radius={0.07} position={[0, 0, 0]} material={BM.sage} />
        </group>
        {[-0.295, 0.295].map((x) => <Rounded key={x} size={[0.11, 0.26, 0.66]} radius={0.05} position={[x, 0.5, 0]} material={BM.sage} />)}
        <Pillow position={[0, 0.7, -0.17]} material={BM.terracotta} tilt={-0.22} />
        <group position={[0.295, 0.636, 0.12]} rotation={[0, 0.3, 0]}>
          <Box size={[0.16, 0.012, 0.11]} position={[0, 0, 0]} material={BM.cream} cast={false} />
          <Box size={[0.004, 0.014, 0.11]} position={[0, 0.001, 0]} material={BM.navy} cast={false} />
        </group>
      </group>
      {/* Floor lamp: marble foot, brass pole, linen drum shade lit from inside. */}
      <group position={[-0.34, 0, -0.34]}>
        <Cyl radius={0.09} height={0.03} position={[0, 0.015, 0]} material={BM.marble} />
        <Cyl radius={0.01} height={1.42} position={[0, 0.74, 0]} material={BM.brass} />
        <mesh geometry={BGEO.lampShade} material={BM.shade} position={[0, 1.52, 0]} castShadow />
        <mesh geometry={BGEO.sphere} material={BM.bulb} position={[0, 1.48, 0]} scale={0.03} />
      </group>
    </group>
  );
}

/** A pear-shaped beanbag with a dimpled seat, a raised back on the -z side and piping round its middle. */
function Beanbag({ id }: { id: string }) {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const material = BM.beanbags[hash % BM.beanbags.length];
  return (
    <group>
      <mesh geometry={BGEO.bag} material={material} position={[0, 0.22, 0.02]} scale={[0.43, 0.22, 0.42]} castShadow receiveShadow />
      <mesh geometry={BGEO.bag} material={material} position={[0, 0.4, -0.18]} scale={[0.36, 0.2, 0.22]} castShadow receiveShadow />
      <mesh geometry={BGEO.bag} material={material} position={[0, 0.33, 0.06]} scale={[0.3, 0.1, 0.28]} castShadow receiveShadow />
      <mesh geometry={BGEO.cyl} material={BM.charcoal} position={[0, 0.2, 0.02]} scale={[0.425, 0.012, 0.415]} />
      <Box size={[0.12, 0.03, 0.02]} position={[0, 0.5, -0.33]} material={BM.charcoal} cast={false} />
    </group>
  );
}

/**
 * Filtered-water station: an oak-clad cabinet, a dispensing niche with
 * chrome taps over a drip tray, a glass bottle on a ceramic collar and a
 * paper-cup tube on the side.
 */
function WaterStation() {
  return (
    <group>
      <Rounded size={[0.34, 0.86, 0.34]} radius={0.03} position={[0, 0.43, 0]} material={BM.oakLight} />
      <Box size={[0.26, 0.26, 0.02]} position={[0, 0.68, 0.162]} material={BM.charcoal} cast={false} />
      <Box size={[0.22, 0.015, 0.08]} position={[0, 0.56, 0.2]} material={BM.chrome} />
      <Cyl radius={0.014} height={0.05} position={[-0.05, 0.77, 0.19]} material={BM.chrome} />
      <Cyl radius={0.014} height={0.05} position={[0.05, 0.77, 0.19]} material={BM.chrome} />
      <Cyl radius={0.016} height={0.012} position={[-0.05, 0.8, 0.19]} material={BM.navy} cast={false} />
      <Cyl radius={0.016} height={0.012} position={[0.05, 0.8, 0.19]} material={BM.terracotta} cast={false} />
      <Box size={[0.26, 0.012, 0.02]} position={[0, 0.2, 0.171]} material={BM.brass} cast={false} />
      <Cyl radius={0.13} height={0.04} position={[0, 0.88, 0]} material={BM.cream} />
      <Cyl radius={0.05} height={0.04} position={[0, 0.915, 0]} material={BM.glass} cast={false} />
      <Cyl radius={0.15} height={0.3} position={[0, 1.085, 0]} material={BM.glass} cast={false} />
      <Cyl radius={0.14} height={0.24} position={[0, 1.06, 0]} material={BM.water} cast={false} />
      <Cyl radius={0.13} height={0.03} position={[0, 1.25, 0]} material={BM.glass} cast={false} />
      <Cyl radius={0.032} height={0.3} position={[0.2, 0.6, 0.06]} material={BM.cream} />
      <Cyl radius={0.028} height={0.03} position={[0.2, 0.44, 0.06]} material={BM.chrome} cast={false} />
    </group>
  );
}

/**
 * Foosball table, long side along x: an oak cabinet on four legs, the green
 * pitch with its lines, eight chrome rods with black handles, terracotta men
 * against navy men, a ball and bead score counters.
 */
function Foosball() {
  const y = FOOS.pitchY;
  return (
    <group>
      {[-0.52, 0.52].flatMap((x) => [-0.25, 0.25].map((z) => (
        <Box key={`${x}:${z}`} size={[0.07, y - 0.06, 0.07]} position={[x, (y - 0.06) / 2, z]} material={BM.oakDark} />
      )))}
      <Box size={[1.0, 0.03, 0.04]} position={[0, 0.14, -0.25]} material={BM.oakDark} />
      <Box size={[1.0, 0.03, 0.04]} position={[0, 0.14, 0.25]} material={BM.oakDark} />
      <Box size={[1.2, 0.05, 0.66]} position={[0, y - 0.035, 0]} material={BM.oak} />
      <Panel size={[1.12, 0.58]} position={[0, y - 0.008, 0]} rotation={[-Math.PI / 2, 0, 0]} material={faces.pitch()} />
      {[-0.31, 0.31].map((z) => <Box key={z} size={[1.2, 0.18, 0.04]} position={[0, y + 0.07, z]} material={BM.oak} />)}
      {[-0.58, 0.58].map((x) => (
        <group key={x}>
          <Box size={[0.04, 0.18, 0.58]} position={[x, y + 0.07, 0]} material={BM.oak} />
          <Box size={[0.012, 0.06, 0.18]} position={[x * 0.97, y + 0.03, 0]} material={BM.black} cast={false} />
        </group>
      ))}
      <mesh geometry={FOOS_GEOMETRIES.rods} material={BM.chrome} castShadow />
      <mesh geometry={FOOS_GEOMETRIES.handles} material={BM.black} castShadow />
      <mesh geometry={FOOS_GEOMETRIES.men[0]} material={BM.terracotta} castShadow />
      <mesh geometry={FOOS_GEOMETRIES.men[1]} material={BM.navy} castShadow />
      <mesh geometry={BGEO.sphere} material={BM.cream} position={[0.07, y + 0.012, 0.05]} scale={0.014} castShadow />
      {/* Bead score counters on the rails. */}
      {[-1, 1].map((s) => (
        <group key={s} position={[s * 0.5, y + 0.165, s * 0.31]}>
          <Cyl radius={0.003} height={0.14} position={[0, 0, 0]} rotation={[0, 0, Math.PI / 2]} material={BM.chrome} cast={false} />
          {[0, 1, 2].map((i) => <mesh key={i} geometry={BGEO.sphere} material={s < 0 ? BM.terracotta : BM.navy} position={[-0.05 + i * 0.022, 0, 0]} scale={0.009} />)}
        </group>
      ))}
    </group>
  );
}

/** A monstera in a woven seagrass basket. */
function BreakPlant() {
  return (
    <group>
      <mesh geometry={BGEO.basket} material={faces.weave()} position={[0, 0.21, 0]} castShadow receiveShadow />
      <Cyl radius={0.2} height={0.02} position={[0, 0.01, 0]} material={BM.oakDark} />
      <Cyl radius={0.225} height={0.012} position={[0, 0.39, 0]} material={BM.soil} cast={false} />
      <Box size={[0.5, 0.02, 0.02]} position={[0, 0.41, 0]} material={BM.oakDark} cast={false} />
      <mesh geometry={MONSTERA.stems} material={BM.stem} castShadow />
      {MONSTERA.leaves.map((geometry, i) => <mesh key={i} geometry={geometry} material={BM.leaves[i]} castShadow receiveShadow />)}
    </group>
  );
}

/** Renderers of the break room's furniture kinds; merged into OfficeProps' exhaustive table. */
export const BREAK_RENDERERS = {
  breakSofa: ({ item }: { item: Furniture }) => <SofaModule id={item.id} />,
  breakSofaCorner: () => <SofaCorner />,
  breakTable: () => <BreakTable />,
  breakRug: ({ item }: { item: Furniture }) => {
    const size = item.size ?? FURNITURE_SIZE.breakRug;
    return <BreakRug w={size.w} d={size.d} />;
  },
  arcadeMat: ({ item }: { item: Furniture }) => {
    const size = item.size ?? FURNITURE_SIZE.arcadeMat;
    return <ArcadeMat w={size.w} d={size.d} />;
  },
  breakBookcase: () => <BreakBookcase />,
  readingNook: () => <ReadingNook />,
  foosball: () => <Foosball />,
  breakPlant: () => <BreakPlant />,
  beanbag: ({ item }: { item: Furniture }) => <Beanbag id={item.id} />,
  waterCooler: () => <WaterStation />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;

// ---------------------------------------------------------------------------
// Room fittings: what hangs rather than stands
// ---------------------------------------------------------------------------

/** A globe pendant on a long cord: brass canopy cap, opal glass lit from within. */
function GlobePendant({ position }: { position: Vec3 }) {
  return (
    <group position={position}>
      <Box size={[0.006, 0.6, 0.006]} position={[0, 0.44, 0]} material={BM.black} cast={false} />
      <Cyl radius={0.035} height={0.05} position={[0, 0.14, 0]} material={BM.brass} cast={false} />
      <mesh geometry={BGEO.globe} material={BM.bulb} />
    </group>
  );
}

/**
 * The lounge's hanging and wall-mounted pieces: two globe pendants over the
 * coffee table with one warm light, the "take five" neon on a sage board over
 * the coffee bar, a gallery of prints and a clock
 * on the west wall.
 */
export const BreakLoungeFittings = memo(function BreakLoungeFittings({ room, furniture }: { room: Room; furniture: readonly Furniture[] }) {
  const table = furniture.find((f) => f.kind === "breakTable");
  const bar = furniture.find((f) => f.kind === "coffeeBar" && f.room === "break");
  const wallX = room.minX + 0.075;
  return (
    <group>
      {table && (
        <group position={[table.x, 0, table.z]}>
          <GlobePendant position={[-0.38, 1.82, 0]} />
          <GlobePendant position={[0.38, 1.72, 0]} />
          <pointLight position={[0, 1.55, 0]} color="#ffd9ad" intensity={5} distance={5.5} decay={2} />
        </group>
      )}
      {bar && (
        <group position={[bar.x, 0, room.minZ + 0.085]}>
          <Box size={[1.5, 0.5, 0.03]} position={[0, 1.6, 0]} material={BM.oak} />
          <Box size={[1.44, 0.44, 0.006]} position={[0, 1.6, 0.016]} material={BM.sagePaint} cast={false} />
          <Box size={[1.36, 0.4, 0.01]} position={[0, 1.6, 0.03]} material={BM.acrylic} cast={false} />
          <Panel size={[1.36, 0.4]} position={[0, 1.6, 0.036]} material={neon()} />
        </group>
      )}
      {/* West wall: a gallery of three prints south of the door, a clock north of it. */}
      <group position={[wallX, 0, 0]} rotation={[0, Math.PI / 2, 0]}>
        {([
          [room.minZ + 5.55, 1.42, 0.36, 0.45, faces.arches()],
          [room.minZ + 6.25, 1.3, 0.56, 0.7, faces.sunset()],
          [room.minZ + 6.95, 1.48, 0.36, 0.45, faces.botanical()],
        ] as const).map(([z, y, fw, fh, face]) => (
          <group key={z} position={[-z, y, 0]}>
            <Box size={[fw + 0.05, fh + 0.05, 0.025]} position={[0, 0, 0]} material={BM.oak} />
            <Box size={[fw - 0.01, fh - 0.01, 0.004]} position={[0, 0, 0.013]} material={BM.cream} cast={false} />
            <Panel size={[fw - 0.08, fh - 0.08]} position={[0, 0, 0.0155]} material={face} />
          </group>
        ))}
        <group position={[-(room.minZ + 2.35), 1.55, 0]}>
          <Cyl radius={0.17} height={0.03} position={[0, 0, 0]} rotation={[Math.PI / 2, 0, 0]} material={BM.oak} />
          <mesh position={[0, 0, 0.0155]} material={faces.clock()}><circleGeometry args={[0.15, 32]} /></mesh>
        </group>
      </group>
    </group>
  );
});
