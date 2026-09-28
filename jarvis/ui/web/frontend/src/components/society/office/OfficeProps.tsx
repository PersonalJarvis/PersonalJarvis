/**
 * Room props for the walkable office: every `FurnitureKind` in the toy-office
 * style (docs/agent-society/office-map.md §2) — rounded primitives, matte
 * colours, and a few simple canvas-drawn faces (screens, board, mirror).
 *
 * Every piece is built in local space centred on the origin, front facing +z,
 * and stays inside its `FURNITURE_SIZE` box (w along x, d along z, height h):
 * navigation walks around exactly that footprint. Geometries, materials and
 * canvas textures are module-level singletons shared by every copy.
 */
import { memo } from "react";
import {
  CanvasTexture, CylinderGeometry, MeshStandardMaterial, SphereGeometry, SRGBColorSpace, type Texture,
} from "three";
import { Bookshelf, Box, Couch, MAT, matte, Plant, Rounded, Rug } from "./OfficeFurniture";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind } from "./officeLayout";
import { PROP_COLOURS as P } from "./officePalette";

type Vec3 = [number, number, number];

// ---------------------------------------------------------------------------
// Shared canvas textures
// ---------------------------------------------------------------------------

const textureCache = new Map<string, Texture | null>();

/**
 * A canvas-drawn texture, drawn once per key and cached. Returns null where no
 * 2D canvas exists (SSR, tests without canvas); callers fall back to a colour.
 */
export function cachedCanvasTexture(
  key: string, width: number, height: number, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void,
): Texture | null {
  if (textureCache.has(key)) return textureCache.get(key) ?? null;
  const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas?.getContext("2d") ?? null;
  } catch {
    // jsdom without the canvas package throws "not implemented": no texture, the colour fallback is correct.
    ctx = null;
  }
  let texture: Texture | null = null;
  if (canvas && ctx) {
    canvas.width = width;
    canvas.height = height;
    draw(ctx, width, height);
    const made = new CanvasTexture(canvas);
    made.colorSpace = SRGBColorSpace;
    made.anisotropy = 4;
    texture = made;
  }
  textureCache.set(key, texture);
  return texture;
}

const materialCache = new Map<string, MeshStandardMaterial>();

/** A material showing a cached canvas texture; `glow` > 0 makes it self-lit like a screen. */
function canvasMaterial(
  key: string, width: number, height: number, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void,
  { glow = 0, fallback = "#ffffff", roughness = 0.6 }: { glow?: number; fallback?: string; roughness?: number } = {},
): MeshStandardMaterial {
  let material = materialCache.get(key);
  if (!material) {
    const map = cachedCanvasTexture(key, width, height, draw);
    material = new MeshStandardMaterial({
      color: map ? "#ffffff" : fallback, map, roughness,
      emissive: glow > 0 ? "#ffffff" : "#000000", emissiveMap: glow > 0 ? map : null, emissiveIntensity: glow,
    });
    materialCache.set(key, material);
  }
  return material;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Team board: an org chart on the left, sticky notes on the right. */
function drawTeamBoard(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = P.boardWhite;
  ctx.fillRect(0, 0, w, h);
  // Org chart: one box on top, three below, joined by marker lines.
  const node = (x: number, y: number, colour: string) => {
    ctx.fillStyle = colour;
    roundRect(ctx, x - 62, y - 26, 124, 52, 10);
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fillRect(x - 40, y - 5, 80, 10);
  };
  ctx.strokeStyle = "#3b4250";
  ctx.lineWidth = 5;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(250, 120); ctx.lineTo(250, 200);
  ctx.moveTo(90, 200); ctx.lineTo(410, 200);
  for (const x of [90, 250, 410]) { ctx.moveTo(x, 200); ctx.lineTo(x, 262); }
  ctx.stroke();
  node(250, 100, "#4f7cac");
  node(90, 288, "#5e9c76");
  node(250, 288, "#c8553d");
  node(410, 288, "#8d6cab");
  // Marker scribbles under the chart.
  ctx.strokeStyle = "#6b7280";
  ctx.lineWidth = 4;
  for (let i = 0; i < 3; i += 1) {
    ctx.beginPath();
    ctx.moveTo(40, 380 + i * 30);
    ctx.lineTo(40 + 180 + ((i * 70) % 160), 380 + i * 30);
    ctx.stroke();
  }
  // Sticky notes, slightly askew.
  const notes = ["#fde68a", "#fbcfe8", "#bbf7d0", "#bfdbfe", "#fed7aa", "#fde68a"];
  notes.forEach((colour, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    ctx.save();
    ctx.translate(620 + col * 135, 110 + row * 170);
    ctx.rotate(((i * 37) % 9 - 4) * 0.02);
    ctx.fillStyle = "rgba(0,0,0,0.12)";
    ctx.fillRect(-52, -48, 110, 110);
    ctx.fillStyle = colour;
    ctx.fillRect(-55, -55, 110, 110);
    ctx.fillStyle = "rgba(60,60,70,0.55)";
    for (let line = 0; line < 3; line += 1) ctx.fillRect(-40, -30 + line * 22, 50 + ((i + line) * 17) % 30, 6);
    ctx.restore();
  });
}

/** Kiosk screen: a list of agents with a gold header and an add button. */
function drawKioskList(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#22345a");
  grad.addColorStop(1, "#16223b");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#f5b83d";
  ctx.fillRect(0, 0, w, 56);
  ctx.fillStyle = "#2a1d0a";
  ctx.fillRect(24, 22, 150, 12);
  const dots = ["#4ade80", "#fbbf24", "#e8ecf2", "#4ade80"];
  dots.forEach((colour, i) => {
    const y = 92 + i * 62;
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    roundRect(ctx, 20, y - 24, w - 40, 48, 10);
    ctx.fill();
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.arc(48, y, 10, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fillRect(72, y - 6, 180 + ((i * 53) % 120), 12);
  });
  ctx.fillStyle = "#f5b83d";
  ctx.beginPath();
  ctx.arc(w - 50, h - 42, 24, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#2a1d0a";
  ctx.fillRect(w - 62, h - 45, 24, 6);
  ctx.fillRect(w - 53, h - 54, 6, 24);
}

/** A generic desktop UI for the reception monitor. */
function drawDeskScreen(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#1b2a44";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#7dd3fc";
  ctx.fillRect(12, 12, w - 24, 16);
  ctx.fillStyle = "rgba(255,255,255,0.75)";
  for (let i = 0; i < 5; i += 1) ctx.fillRect(16, 44 + i * 20, 60 + ((i * 41) % 110), 8);
  ctx.fillStyle = "#a7f3d0";
  ctx.fillRect(w - 80, 44, 60, 90);
}

const INVADER = [
  "..X.....X..", "...X...X...", "..XXXXXXX..", ".XX.XXX.XX.",
  "XXXXXXXXXXX", "X.XXXXXXX.X", "X.X.....X.X", "...XX.XX...",
];

/** Arcade screen: two rows of pixel invaders, a ship and a score. */
function drawArcade(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#07060f";
  ctx.fillRect(0, 0, w, h);
  const px = 4;
  for (let row = 0; row < 2; row += 1) {
    for (let col = 0; col < 4; col += 1) {
      ctx.fillStyle = row === 0 ? "#ff7ab8" : "#7cfc9a";
      const ox = 22 + col * 58, oy = 34 + row * 44;
      INVADER.forEach((line, y) => {
        for (let x = 0; x < line.length; x += 1) if (line[x] === "X") ctx.fillRect(ox + x * px, oy + y * px, px, px);
      });
    }
  }
  ctx.fillStyle = "#7dd3fc";
  ctx.fillRect(w / 2 - 18, h - 30, 36, 10);
  ctx.fillRect(w / 2 - 4, h - 40, 8, 10);
  ctx.fillStyle = "#fde68a";
  ctx.fillRect(w / 2 - 1, h - 90, 3, 14);
  ctx.fillStyle = "#ffffff";
  for (let i = 0; i < 5; i += 1) ctx.fillRect(14 + i * 12, 10, 8, 10);
}

/** Elevator floor indicator: an amber "up" arrow and a dim "down" arrow. */
function drawIndicator(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = "#111318";
  ctx.fillRect(0, 0, w, h);
  const arrow = (cx: number, up: boolean, colour: string) => {
    ctx.fillStyle = colour;
    ctx.beginPath();
    const s = up ? -1 : 1;
    ctx.moveTo(cx, h / 2 + s * 14);
    ctx.lineTo(cx - 14, h / 2 - s * 10);
    ctx.lineTo(cx + 14, h / 2 - s * 10);
    ctx.closePath();
    ctx.fill();
  };
  arrow(w * 0.3, true, P.indicator);
  arrow(w * 0.7, false, "#4b5160");
}

/** Mirror glass: a cool gradient with two soft diagonal highlights. */
function drawMirror(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const grad = ctx.createLinearGradient(0, 0, w, h);
  grad.addColorStop(0, "#f1f9fe");
  grad.addColorStop(1, "#b9d7ea");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "rgba(255,255,255,0.55)";
  for (const [x, width] of [[0.15, 0.18], [0.5, 0.08]] as const) {
    ctx.beginPath();
    ctx.moveTo(w * x, h);
    ctx.lineTo(w * (x + width), h);
    ctx.lineTo(w * (x + width + 0.6), 0);
    ctx.lineTo(w * (x + 0.6), 0);
    ctx.closePath();
    ctx.fill();
  }
}

/** Coffee menu chalkboard. */
function drawMenu(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = P.chalkboard;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#f8fafc";
  ctx.font = "700 36px system-ui, -apple-system, 'Segoe UI', sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("MENU", w / 2, 34);
  for (let i = 0; i < 4; i += 1) {
    const y = 78 + i * 28;
    ctx.fillStyle = "rgba(248,250,252,0.8)";
    ctx.fillRect(24, y, 90 + ((i * 29) % 50), 7);
    ctx.fillStyle = "#fde68a";
    ctx.fillRect(w - 58, y, 34, 7);
  }
}

// ---------------------------------------------------------------------------
// Shared materials and geometries
// ---------------------------------------------------------------------------

const PM = {
  steel: matte(P.steel, { roughness: 0.35, metalness: 0.25 }),
  steelDark: matte(P.steelDark, { roughness: 0.45, metalness: 0.2 }),
  shaft: matte(P.elevatorShaft),
  lockers: P.lockers.map((c) => matte(c)),
  lockerVent: matte(P.lockerVent),
  bottle: new MeshStandardMaterial({ color: P.waterBottle, roughness: 0.2, transparent: true, opacity: 0.8 }),
  taps: P.waterTap.map((c) => matte(c)),
  mug: matte(P.mug),
  coffee: matte(P.coffee),
  espresso: matte(P.espresso, { roughness: 0.35, metalness: 0.25 }),
  arcadeBody: matte(P.arcadeBody),
  arcadeTrim: matte(P.arcadeTrim),
  marquee: matte(P.arcadeMarquee, { emissive: P.arcadeMarquee, emissiveIntensity: 0.8 }),
  joystick: matte(P.joystick, { roughness: 0.4 }),
  arcadeButtons: P.arcadeButtons.map((c) => matte(c, { emissive: c, emissiveIntensity: 0.35 })),
  beanbags: P.beanbag.map((c) => matte(c, { roughness: 0.95 })),
  bell: matte(P.bell, { roughness: 0.35, metalness: 0.3 }),
  boardFrame: matte(P.boardFrame),
  kioskBody: matte(P.kioskBody),
  kioskHead: matte(P.kioskHead, { roughness: 0.5 }),
  paper: matte(P.paper),
  rugInner: matte(P.rugInner),
  gold: matte("#f5b83d", { emissive: "#f5b83d", emissiveIntensity: 0.6 }),
  indicatorLight: matte(P.indicator, { emissive: P.indicator, emissiveIntensity: 0.9 }),
};

const lazy = {
  board: () => canvasMaterial("prop:teamBoard", 1024, 478, drawTeamBoard, { fallback: P.boardWhite, roughness: 0.7 }),
  kiosk: () => canvasMaterial("prop:kiosk", 512, 360, drawKioskList, { glow: 0.85, fallback: "#1b2a44", roughness: 0.4 }),
  deskScreen: () => canvasMaterial("prop:deskScreen", 256, 160, drawDeskScreen, { glow: 0.8, fallback: "#1b2a44", roughness: 0.4 }),
  arcade: () => canvasMaterial("prop:arcade", 256, 200, drawArcade, { glow: 1, fallback: "#07060f", roughness: 0.4 }),
  indicator: () => canvasMaterial("prop:indicator", 128, 48, drawIndicator, { glow: 0.9, fallback: "#111318", roughness: 0.4 }),
  mirror: () => canvasMaterial("prop:mirror", 128, 256, drawMirror, { glow: 0.25, fallback: P.mirror, roughness: 0.1 }),
  menu: () => canvasMaterial("prop:menu", 256, 192, drawMenu, { glow: 0.15, fallback: P.chalkboard, roughness: 0.9 }),
};

const PGEO = {
  cyl: new CylinderGeometry(1, 1, 1, 20),
  sphere: new SphereGeometry(1, 20, 14),
  dome: new SphereGeometry(1, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2),
};

function Cyl({ radius, height, position, material, cast = true }: {
  radius: number; height: number; position: Vec3; material: MeshStandardMaterial; cast?: boolean;
}) {
  return <mesh geometry={PGEO.cyl} material={material} position={position} scale={[radius, height, radius]} castShadow={cast} receiveShadow />;
}

function Panel({ size, position, material, rotation }: {
  size: [number, number]; position: Vec3; material: MeshStandardMaterial; rotation?: Vec3;
}) {
  return (
    <mesh position={position} rotation={rotation} material={material}>
      <planeGeometry args={size} />
    </mesh>
  );
}

// ---------------------------------------------------------------------------
// Props (local space: centred on the origin, front faces +z)
// ---------------------------------------------------------------------------

/** Long light-wood meeting table on two white pedestals. Chairs are `MeetingChairs`. */
function MeetingTable() {
  return (
    <group>
      <Rounded size={[3.6, 0.07, 1.6]} radius={0.03} position={[0, 0.725, 0]} material={MAT.deskTop} />
      <Rounded size={[0.22, 0.69, 1.1]} radius={0.03} position={[-1.3, 0.345, 0]} material={MAT.deskBody} />
      <Rounded size={[0.22, 0.69, 1.1]} radius={0.03} position={[1.3, 0.345, 0]} material={MAT.deskBody} />
      <Box size={[2.4, 0.06, 0.08]} position={[0, 0.3, 0]} material={MAT.deskLeg} />
      <Box size={[0.3, 0.006, 0.42]} position={[-0.9, 0.763, 0.35]} material={PM.paper} cast={false} />
      <Box size={[0.3, 0.006, 0.42]} position={[0.6, 0.763, -0.35]} material={PM.paper} cast={false} />
      <Box size={[0.42, 0.02, 0.3]} position={[0.1, 0.77, 0.3]} material={MAT.monitor} />
    </group>
  );
}

/** A meeting chair centred on its seat, facing +z (backrest on the -z side). */
function MeetingChair() {
  return (
    <group>
      <Cyl radius={0.26} height={0.04} position={[0, 0.02, 0]} material={MAT.chair} />
      <Cyl radius={0.03} height={0.4} position={[0, 0.24, 0]} material={MAT.chair} cast={false} />
      <Rounded size={[0.48, 0.08, 0.46]} radius={0.03} position={[0, 0.46, 0]} material={MAT.chairSeat} />
      <Rounded size={[0.46, 0.46, 0.07]} radius={0.03} position={[0, 0.74, -0.21]} material={MAT.chairSeat} />
    </group>
  );
}

/**
 * Six chairs around a meeting table: three per long side at local x = -1.1, 0,
 * +1.1 and z = ∓1.25, each facing the table. They sit outside the table's
 * footprint and are not navigation obstacles (they match the `meeting-*` spots).
 */
export function MeetingChairs({ table }: { table: Furniture }) {
  return (
    <group position={[table.x, 0, table.z]} rotation={[0, table.rotationY, 0]}>
      {[-1.1, 0, 1.1].map((x) => (
        <group key={x}>
          <group position={[x, 0, -1.25]}><MeetingChair /></group>
          <group position={[x, 0, 1.25]} rotation={[0, Math.PI, 0]}><MeetingChair /></group>
        </group>
      ))}
    </group>
  );
}

/** Mobile whiteboard (org chart + sticky notes) on two slim legs, board face towards +z. */
function TeamBoard() {
  return (
    <group>
      {[-1.26, 1.26].map((x) => (
        <group key={x}>
          <Box size={[0.05, 1.86, 0.05]} position={[x, 0.93, -0.04]} material={MAT.railing} />
          <Box size={[0.06, 0.03, 0.2]} position={[x, 0.015, 0]} material={MAT.railing} />
        </group>
      ))}
      <Rounded size={[2.5, 1.22, 0.05]} radius={0.02} position={[0, 1.25, -0.04]} material={PM.boardFrame} />
      <Panel size={[2.4, 1.12]} position={[0, 1.25, -0.0135]} material={lazy.board()} />
      <Box size={[2.1, 0.03, 0.08]} position={[0, 0.66, 0]} material={PM.boardFrame} />
      {[PM.joystick, MAT.books[2], MAT.keyboard].map((material, i) => (
        <Box key={i} size={[0.12, 0.02, 0.02]} position={[-0.3 + i * 0.18, 0.685, 0.01]} material={material} cast={false} />
      ))}
    </group>
  );
}

/**
 * Reception counter: the visitor side (+z) is a high white counter with a
 * wood top and a service bell; the receptionist side (-z) is a lower work
 * surface whose monitor faces -z.
 */
function ReceptionDesk() {
  return (
    <group>
      <Rounded size={[2.8, 0.98, 0.4]} radius={0.15} position={[0, 0.49, 0.25]} material={MAT.deskBody} />
      <Rounded size={[2.8, 0.05, 0.46]} radius={0.02} position={[0, 1.005, 0.22]} material={MAT.wood} />
      <Box size={[2.3, 0.1, 0.02]} position={[0, 0.55, 0.44]} material={MAT.wood} cast={false} />
      <Rounded size={[2.5, 0.04, 0.44]} radius={0.015} position={[0, 0.74, -0.2]} material={MAT.deskTop} />
      <Box size={[0.36, 0.72, 0.42]} position={[-1.05, 0.36, -0.2]} material={MAT.deskBody} />
      <Box size={[0.36, 0.72, 0.42]} position={[1.05, 0.36, -0.2]} material={MAT.deskBody} />
      {/* Monitor facing the receptionist. */}
      <Box size={[0.2, 0.015, 0.14]} position={[-0.3, 0.768, -0.05]} material={MAT.monitor} />
      <Box size={[0.06, 0.12, 0.05]} position={[-0.3, 0.83, -0.05]} material={MAT.monitor} />
      <Rounded size={[0.56, 0.32, 0.035]} radius={0.012} position={[-0.3, 0.93, -0.08]} material={MAT.monitor} />
      <Panel size={[0.52, 0.28]} position={[-0.3, 0.93, -0.0985]} rotation={[0, Math.PI, 0]} material={lazy.deskScreen()} />
      <Box size={[0.4, 0.02, 0.13]} position={[-0.3, 0.77, -0.3]} material={MAT.keyboard} />
      {/* Service bell on the visitor counter. */}
      <Cyl radius={0.07} height={0.012} position={[0.9, 1.036, 0.26]} material={MAT.monitor} />
      <mesh geometry={PGEO.dome} material={PM.bell} position={[0.9, 1.042, 0.26]} scale={0.055} castShadow />
      <mesh geometry={PGEO.sphere} material={PM.bell} position={[0.9, 1.1, 0.26]} scale={0.012} />
    </group>
  );
}

/** Standing touch terminal; the screen (a list of agents) faces +z, tilted slightly up. */
function Kiosk() {
  return (
    <group>
      <Rounded size={[0.6, 0.05, 0.42]} radius={0.02} position={[0, 0.025, 0]} material={PM.kioskBody} />
      <Rounded size={[0.24, 1.1, 0.18]} radius={0.05} position={[0, 0.6, -0.04]} material={PM.kioskBody} />
      <group position={[0, 1.43, 0]} rotation={[-0.12, 0, 0]}>
        <Rounded size={[0.9, 0.66, 0.1]} radius={0.04} position={[0, 0, 0]} material={PM.kioskHead} />
        <Panel size={[0.8, 0.56]} position={[0, 0, 0.051]} material={lazy.kiosk()} />
        <Box size={[0.5, 0.03, 0.04]} position={[0, 0.345, 0]} material={PM.gold} cast={false} />
      </group>
    </group>
  );
}

/** A row of four coloured lockers, doors facing +z. */
function Lockers() {
  return (
    <group>
      <Box size={[2.4, 0.05, 0.46]} position={[0, 0.025, 0]} material={MAT.deskLeg} />
      {PM.lockers.map((material, i) => {
        const x = -0.9 + i * 0.6;
        return (
          <group key={i}>
            <Rounded size={[0.57, 1.8, 0.46]} radius={0.03} position={[x, 0.95, 0]} material={material} />
            {[1.6, 1.66, 1.72].map((y) => <Box key={y} size={[0.3, 0.025, 0.01]} position={[x, y, 0.232]} material={PM.lockerVent} cast={false} />)}
            <Box size={[0.03, 0.14, 0.03]} position={[x + 0.2, 1.0, 0.235]} material={PM.steelDark} cast={false} />
          </group>
        );
      })}
      <Box size={[2.4, 0.04, 0.48]} position={[0, 1.87, 0]} material={MAT.deskBody} />
    </group>
  );
}

/** Tall standing mirror in a wood frame; the glass faces +z. */
function Mirror() {
  return (
    <group>
      <Box size={[0.14, 0.05, 0.12]} position={[-0.33, 0.025, 0]} material={MAT.woodDark} />
      <Box size={[0.14, 0.05, 0.12]} position={[0.33, 0.025, 0]} material={MAT.woodDark} />
      <Rounded size={[0.9, 1.84, 0.07]} radius={0.03} position={[0, 0.97, -0.02]} material={MAT.wood} />
      <Panel size={[0.74, 1.66]} position={[0, 0.97, 0.0155]} material={lazy.mirror()} />
    </group>
  );
}

/** Coffee counter with an espresso machine, cups and a small menu board; service side faces +z. */
function CoffeeBar() {
  return (
    <group>
      <Rounded size={[2.4, 0.68, 0.62]} radius={0.04} position={[0, 0.34, 0.03]} material={MAT.deskBody} />
      {[-0.78, 0, 0.78].map((x) => <Box key={x} size={[0.7, 0.5, 0.012]} position={[x, 0.33, 0.342]} material={MAT.wood} cast={false} />)}
      <Rounded size={[2.4, 0.04, 0.68]} radius={0.015} position={[0, 0.7, 0.01]} material={MAT.woodDark} />
      {/* Espresso machine. */}
      <Rounded size={[0.5, 0.3, 0.34]} radius={0.04} position={[-0.65, 0.87, -0.1]} material={PM.espresso} />
      <Box size={[0.4, 0.08, 0.01]} position={[-0.65, 0.95, 0.072]} material={MAT.monitor} cast={false} />
      <Box size={[0.08, 0.05, 0.08]} position={[-0.72, 0.82, 0.1]} material={PM.steelDark} />
      <Box size={[0.08, 0.05, 0.08]} position={[-0.58, 0.82, 0.1]} material={PM.steelDark} />
      <Box size={[0.32, 0.015, 0.12]} position={[-0.65, 0.728, 0.12]} material={PM.steelDark} cast={false} />
      <Cyl radius={0.035} height={0.06} position={[-0.72, 0.765, 0.12]} material={PM.mug} />
      {/* Cups. */}
      {[0.05, 0.17, 0.29, 0.41].map((x, i) => (
        <group key={x}>
          <Cyl radius={0.04} height={0.08} position={[x, 0.76, 0.14]} material={PM.mug} />
          {i === 1 && <Cyl radius={0.034} height={0.004} position={[x, 0.8, 0.14]} material={PM.coffee} cast={false} />}
        </group>
      ))}
      {/* Menu board leaning back on the counter. */}
      <group position={[0.92, 0.72, -0.18]} rotation={[-0.18, 0, 0]}>
        <Box size={[0.44, 0.33, 0.02]} position={[0, 0.165, -0.005]} material={MAT.wood} />
        <Panel size={[0.38, 0.28]} position={[0, 0.165, 0.0055]} material={lazy.menu()} />
      </group>
    </group>
  );
}

/** Water cooler: white stand, taps on the +z side, a blue bottle on top. */
function WaterCooler() {
  return (
    <group>
      <Rounded size={[0.36, 0.9, 0.36]} radius={0.04} position={[0, 0.45, 0]} material={MAT.deskBody} />
      <Box size={[0.26, 0.2, 0.01]} position={[0, 0.72, 0.181]} material={PM.steelDark} cast={false} />
      <Box size={[0.04, 0.05, 0.04]} position={[-0.06, 0.74, 0.2]} material={PM.taps[0]} />
      <Box size={[0.04, 0.05, 0.04]} position={[0.06, 0.74, 0.2]} material={PM.taps[1]} />
      <Box size={[0.22, 0.02, 0.06]} position={[0, 0.6, 0.2]} material={PM.steelDark} />
      <Cyl radius={0.12} height={0.04} position={[0, 0.92, 0]} material={MAT.deskBody} />
      <Cyl radius={0.16} height={0.3} position={[0, 1.09, 0]} material={PM.bottle} />
      <Cyl radius={0.15} height={0.03} position={[0, 1.255, 0]} material={PM.bottle} />
    </group>
  );
}

/** Round low wood table with a mug and a magazine. */
function CoffeeTable() {
  return (
    <group>
      <Cyl radius={0.28} height={0.03} position={[0, 0.015, 0]} material={MAT.woodDark} />
      <Cyl radius={0.06} height={0.24} position={[0, 0.135, 0]} material={MAT.woodDark} />
      <Cyl radius={0.55} height={0.05} position={[0, 0.275, 0]} material={MAT.wood} />
      <Box size={[0.22, 0.01, 0.3]} position={[-0.15, 0.305, -0.05]} material={MAT.books[2]} cast={false} />
      <Cyl radius={0.045} height={0.085} position={[0.18, 0.3425, 0.1]} material={PM.mug} />
      <Cyl radius={0.038} height={0.004} position={[0.18, 0.386, 0.1]} material={PM.coffee} cast={false} />
      <Box size={[0.015, 0.045, 0.012]} position={[0.232, 0.345, 0.1]} material={PM.mug} cast={false} />
    </group>
  );
}

/** Retro arcade cabinet; screen, controls and marquee face +z. */
function Arcade() {
  return (
    <group>
      <Rounded size={[0.7, 0.95, 0.62]} radius={0.03} position={[0, 0.475, -0.04]} material={PM.arcadeBody} />
      <Box size={[0.6, 0.12, 0.01]} position={[0, 0.12, 0.272]} material={PM.arcadeTrim} cast={false} />
      <Box size={[0.2, 0.22, 0.01]} position={[0, 0.6, 0.272]} material={PM.arcadeTrim} cast={false} />
      <group position={[0, 1.0, 0.2]} rotation={[0.25, 0, 0]}>
        <Rounded size={[0.7, 0.08, 0.3]} radius={0.02} position={[0, 0, 0]} material={PM.arcadeTrim} />
        <Cyl radius={0.012} height={0.08} position={[-0.15, 0.08, 0]} material={MAT.monitor} cast={false} />
        <mesh geometry={PGEO.sphere} material={PM.joystick} position={[-0.15, 0.13, 0]} scale={0.035} castShadow />
        {PM.arcadeButtons.map((material, i) => (
          <Cyl key={i} radius={0.025} height={0.02} position={[0.05 + i * 0.08, 0.045, 0]} material={material} cast={false} />
        ))}
      </group>
      <Rounded size={[0.7, 0.72, 0.42]} radius={0.03} position={[0, 1.4, -0.15]} material={PM.arcadeBody} />
      <Box size={[0.62, 0.5, 0.01]} position={[0, 1.36, 0.062]} material={PM.arcadeTrim} cast={false} />
      <Panel size={[0.56, 0.44]} position={[0, 1.36, 0.068]} material={lazy.arcade()} />
      <Box size={[0.7, 0.14, 0.14]} position={[0, 1.72, 0]} material={PM.marquee} />
      <Box size={[0.012, 1.6, 0.5]} position={[-0.356, 0.9, -0.05]} material={PM.arcadeTrim} cast={false} />
      <Box size={[0.012, 1.6, 0.5]} position={[0.356, 0.9, -0.05]} material={PM.arcadeTrim} cast={false} />
    </group>
  );
}

/** Stable small hash of an id (colour picks, variations). */
export function idHash(id: string): number {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return hash;
}

/** A squashed soft beanbag; the raised back is on the -z side, so it faces +z. */
function Beanbag({ id }: { id: string }) {
  const material = PM.beanbags[idHash(id) % PM.beanbags.length];
  return (
    <group>
      <mesh geometry={PGEO.sphere} material={material} position={[0, 0.26, 0]} scale={[0.44, 0.26, 0.44]} castShadow receiveShadow />
      <mesh geometry={PGEO.sphere} material={material} position={[0, 0.42, -0.16]} scale={[0.36, 0.18, 0.24]} castShadow receiveShadow />
    </group>
  );
}

/** A flat rug with an inner field, sized per instance. */
function RugPiece({ w, d }: { w: number; d: number }) {
  const inset = Math.min(0.3, Math.min(w, d) * 0.15);
  return (
    <group>
      <Rug position={[0, 0.01, 0]} size={[w, d]} />
      <Box size={[w - inset, 0.022, d - inset]} position={[0, 0.011, 0]} material={PM.rugInner} cast={false} />
    </group>
  );
}

/** Steel elevator doors in a free-standing shaft block; doors face +z, wood slats on the back. */
function Elevator() {
  return (
    <group>
      <Rounded size={[2.2, 2.4, 0.3]} radius={0.04} position={[0, 1.2, -0.05]} material={PM.shaft} />
      <Box size={[0.1, 2.08, 0.06]} position={[-0.66, 1.04, 0.12]} material={PM.steelDark} />
      <Box size={[0.1, 2.08, 0.06]} position={[0.66, 1.04, 0.12]} material={PM.steelDark} />
      <Box size={[1.42, 0.1, 0.06]} position={[0, 2.13, 0.12]} material={PM.steelDark} />
      <Box size={[0.6, 2.0, 0.03]} position={[-0.305, 1.0, 0.11]} material={PM.steel} />
      <Box size={[0.6, 2.0, 0.03]} position={[0.305, 1.0, 0.11]} material={PM.steel} />
      <Box size={[0.012, 2.0, 0.035]} position={[0, 1.0, 0.112]} material={PM.steelDark} cast={false} />
      <Box size={[1.3, 0.02, 0.08]} position={[0, 0.01, 0.15]} material={PM.steelDark} cast={false} />
      <Box size={[0.34, 0.14, 0.02]} position={[0, 2.28, 0.11]} material={MAT.monitor} cast={false} />
      <Panel size={[0.3, 0.1]} position={[0, 2.28, 0.1205]} material={lazy.indicator()} />
      <Box size={[0.1, 0.22, 0.02]} position={[0.9, 1.1, 0.11]} material={PM.steel} cast={false} />
      <Box size={[0.035, 0.035, 0.01]} position={[0.9, 1.15, 0.122]} material={PM.indicatorLight} cast={false} />
      <Box size={[0.035, 0.035, 0.01]} position={[0.9, 1.05, 0.122]} material={PM.steelDark} cast={false} />
      {[-0.9, -0.54, -0.18, 0.18, 0.54, 0.9].map((x) => (
        <Box key={x} size={[0.1, 2.1, 0.03]} position={[x, 1.1, -0.185]} material={MAT.woodDark} cast={false} />
      ))}
    </group>
  );
}

/** Plant scale that keeps the canopy inside the 0.6 m plant footprint. */
export const PROP_PLANT_SIZE = 0.82;

const COUCH_FIT_X = FURNITURE_SIZE.couch.w / 2.36;

/** Every furniture kind maps to exactly one renderer (the Record type keeps this exhaustive). */
export const PROP_RENDERERS: Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element> = {
  meetingTable: () => <MeetingTable />,
  teamBoard: () => <TeamBoard />,
  receptionDesk: () => <ReceptionDesk />,
  kiosk: () => <Kiosk />,
  lockers: () => <Lockers />,
  mirror: () => <Mirror />,
  coffeeBar: () => <CoffeeBar />,
  waterCooler: () => <WaterCooler />,
  // The shared couch's armrests reach 2.36 m; squeeze it into the 2.2 m footprint.
  couch: () => <group scale={[COUCH_FIT_X, 1, 1]}><Couch position={[0, 0, 0]} /></group>,
  coffeeTable: () => <CoffeeTable />,
  arcade: () => <Arcade />,
  beanbag: ({ item }) => <Beanbag id={item.id} />,
  bookshelf: () => <Bookshelf position={[0, 0, 0]} />,
  plant: () => <Plant position={[0, 0, 0]} size={PROP_PLANT_SIZE} />,
  rug: ({ item }) => {
    const size = item.size ?? FURNITURE_SIZE.rug;
    return <RugPiece w={size.w} d={size.d} />;
  },
  elevator: () => <Elevator />,
};

/** One furniture item, placed at (x, 0, z) and turned by `rotationY` (0 = front faces +z). */
export const FurniturePiece = memo(function FurniturePiece({ item }: { item: Furniture }) {
  const Render = PROP_RENDERERS[item.kind];
  return (
    <group position={[item.x, 0, item.z]} rotation={[0, item.rotationY, 0]} name={`prop:${item.id}`}>
      <Render item={item} />
    </group>
  );
});
