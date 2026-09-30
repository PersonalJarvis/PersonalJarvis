/**
 * The spawn point in the middle of both office floors: a round pad set into
 * the aisle crossing and, on it, a portal-shaped terminal — two pillars, a
 * lintel with the floor's crest, a screen on each side, a console with a
 * glowing button each side and an energy core under it. It spawns Jarvis
 * agents on the agents floor and coding agents on the coding floor; everyone
 * new to a floor appears on the pad in a column of light.
 *
 * Each floor dresses it in its own materials: pale oak, bronze and a mint
 * glow with the brass ghost downstairs; walnut, brass and amber with a brass
 * prompt glyph upstairs. Same rules as OfficeProps: every piece is built in
 * local space centred on the origin, front facing +z, inside its
 * `FURNITURE_SIZE` box; materials and canvas faces are made once per floor
 * and shared. The moving parts (the rings round the floor token, the glow
 * pulse and the arrival beam) live in `SpawnFittings`.
 */
import { useEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import {
  AdditiveBlending, CylinderGeometry, DoubleSide, MeshBasicMaterial, MeshStandardMaterial, RingGeometry, TorusGeometry,
  type BufferGeometry, type Group, type Mesh,
} from "three";
import { cachedCanvasTexture, canvasMaterial } from "./canvasMaterials";
import { Box, GEO, matte, Rounded } from "./OfficeFurniture";
import { drawGhost, GHOST_GEOMETRY } from "./LobbyDecor";
import { FURNITURE_SIZE, SPAWN, type Furniture, type FurnitureKind, type Point } from "./officeLayout";
import { useOfficeStore, type OfficeFloor } from "./officeStore";

type Vec3 = [number, number, number];
type Ctx = CanvasRenderingContext2D;

// ---------------------------------------------------------------------------
// The two looks
// ---------------------------------------------------------------------------

interface SpawnLook {
  /** Pillars, lintel and console; the plinth; the metal trims. */
  body: string; plinth: string; metal: string;
  /** The screen bezel, and the light every strip, button, core and ring glows in. */
  bezel: string; glow: string;
  /** Screen: background top and bottom, the text, the accent and its deep tone. */
  screenTop: string; screenBottom: string; text: string; accent: string; accentDeep: string;
  /** Pad: stone, its speckles, the outer metal band and the accent band. */
  pad: string; speck: readonly string[]; padEdge: string; padBand: string;
}

/** Downstairs the agents' Scandinavian oak, sage and bronze; upstairs the coding floor's walnut, plum and amber. */
export const SPAWN_LOOKS: Record<OfficeFloor, SpawnLook> = {
  agents: {
    body: "#dcc49c", plinth: "#a88660", metal: "#c7a15e", bezel: "#23262b", glow: "#b8f0cf",
    screenTop: "#22302a", screenBottom: "#111816", text: "#f1ebe1", accent: "#9fbf9a", accentDeep: "#16201b",
    pad: "#e9e3d8", speck: ["#ddd5c7", "#f3efe7", "#cfc6b6"], padEdge: "#a8865a", padBand: "#9fae93",
  },
  coding: {
    body: "#5d3d28", plinth: "#2e1d13", metal: "#d8ae52", bezel: "#1d1b1a", glow: "#ffc27a",
    screenTop: "#271b40", screenBottom: "#0f0a1d", text: "#fff1de", accent: "#f3b16b", accentDeep: "#241a3b",
    pad: "#2e2638", speck: ["#3b3246", "#251f2e", "#c9a24a"], padEdge: "#c9a24a", padBand: "#f3b16b",
  },
};

interface SpawnMaterials {
  body: MeshStandardMaterial; plinth: MeshStandardMaterial; metal: MeshStandardMaterial; bezel: MeshStandardMaterial;
  /** Self-lit, so it reads as light in any scene lighting; SpawnFittings pulses it. */
  glow: MeshStandardMaterial;
  glass: MeshStandardMaterial;
}

const materialCache = new Map<OfficeFloor, SpawnMaterials>();

export function spawnMaterials(floor: OfficeFloor): SpawnMaterials {
  let set = materialCache.get(floor);
  if (!set) {
    const look = SPAWN_LOOKS[floor];
    set = {
      body: matte(look.body, { roughness: floor === "coding" ? 0.5 : 0.6 }),
      plinth: matte(look.plinth, { roughness: 0.55 }),
      metal: matte(look.metal, { roughness: 0.3, metalness: 0.7 }),
      bezel: matte(look.bezel, { roughness: 0.35, metalness: 0.15 }),
      glow: new MeshStandardMaterial({ color: look.glow, emissive: look.glow, emissiveIntensity: 1.25, toneMapped: false }),
      glass: new MeshStandardMaterial({ color: "#eef6f8", transparent: true, opacity: 0.22, roughness: 0.05, depthWrite: false }),
    };
    materialCache.set(floor, set);
  }
  return set;
}

// ---------------------------------------------------------------------------
// Canvas faces
// ---------------------------------------------------------------------------

function roundRect(ctx: Ctx, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Letter spacing where the canvas supports it (Chromium does; older engines just ignore it). */
function spaced(ctx: Ctx, px: number): void {
  (ctx as Ctx & { letterSpacing?: string }).letterSpacing = `${px}px`;
}

/** A thick rounded plus, `size` px across, centred on (cx, cy). */
function plus(ctx: Ctx, cx: number, cy: number, size: number, colour: string): void {
  const bar = size * 0.24;
  ctx.fillStyle = colour;
  roundRect(ctx, cx - size / 2, cy - bar / 2, size, bar, bar / 2);
  ctx.fill();
  roundRect(ctx, cx - bar / 2, cy - size / 2, bar, size, bar / 2);
  ctx.fill();
}

const FONT = "Inter, 'Segoe UI', system-ui, sans-serif";
const MONO = "'JetBrains Mono', 'Cascadia Code', Consolas, monospace";

/** The screen's header: the floor's mark, SPAWN and a "ready" light on the right. */
function screenHeader(ctx: Ctx, w: number, look: SpawnLook, mark: (x: number, y: number) => void): void {
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  ctx.fillRect(0, 0, w, 96);
  ctx.fillStyle = look.accent;
  ctx.fillRect(0, 96, w, 4);
  mark(44, 48);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillStyle = look.text;
  spaced(ctx, 12);
  ctx.font = `800 44px ${FONT}`;
  ctx.fillText("SPAWN", 112, 50);
  spaced(ctx, 3);
  ctx.font = `700 22px ${FONT}`;
  ctx.textAlign = "right";
  ctx.fillStyle = "rgba(255,255,255,0.72)";
  ctx.fillText("READY", w - 44, 50);
  ctx.fillStyle = "#4ade80";
  ctx.beginPath(); ctx.arc(w - 146, 49, 9, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(74,222,128,0.25)";
  ctx.beginPath(); ctx.arc(w - 146, 49, 16, 0, Math.PI * 2); ctx.fill();
  spaced(ctx, 0);
  ctx.textAlign = "left";
}

/** The big "spawn" button on the left half: an accent tile with a plus and its caption. */
function spawnButton(ctx: Ctx, look: SpawnLook, caption: string, round: boolean): void {
  const cx = 250, cy = 318;
  // A soft halo, then the tile.
  const halo = ctx.createRadialGradient(cx, cy, 60, cx, cy, 230);
  halo.addColorStop(0, `${look.accent}66`);
  halo.addColorStop(1, `${look.accent}00`);
  ctx.fillStyle = halo;
  ctx.fillRect(cx - 240, cy - 240, 480, 480);
  const tile = ctx.createLinearGradient(cx, cy - 150, cx, cy + 150);
  tile.addColorStop(0, "#ffffff");
  tile.addColorStop(0.12, look.accent);
  tile.addColorStop(1, look.accent);
  ctx.fillStyle = tile;
  if (round) { ctx.beginPath(); ctx.arc(cx, cy, 142, 0, Math.PI * 2); } else roundRect(ctx, cx - 158, cy - 142, 316, 284, 44);
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.lineWidth = 5;
  ctx.stroke();
  plus(ctx, cx, cy, 150, look.accentDeep);
  ctx.fillStyle = look.text;
  ctx.textAlign = "center";
  spaced(ctx, 5);
  ctx.font = `800 30px ${FONT}`;
  ctx.fillText(caption, cx, 520);
  spaced(ctx, 0);
  ctx.textAlign = "left";
}

/** The coding floor's screen: SPAWN, a new-agent button, the terminal windows of the running agents and a prompt. */
function drawLaunchScreen(ctx: Ctx, w: number, h: number): void {
  const look = SPAWN_LOOKS.coding;
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, look.screenTop);
  bg.addColorStop(1, look.screenBottom);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);
  // A faint blueprint grid.
  ctx.strokeStyle = "rgba(243,177,107,0.07)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = 0; x <= w; x += 32) { ctx.moveTo(x + 0.5, 100); ctx.lineTo(x + 0.5, h); }
  for (let y = 100; y <= h; y += 32) { ctx.moveTo(0, y + 0.5); ctx.lineTo(w, y + 0.5); }
  ctx.stroke();
  screenHeader(ctx, w, look, (x, y) => {
    ctx.fillStyle = look.accent;
    ctx.font = `800 46px ${MONO}`;
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    ctx.fillText(">_", x + 10, y + 2);
  });
  spawnButton(ctx, look, "NEW CODING AGENT", false);
  // Three terminal windows fanning down the right: two running agents and the one about to spawn.
  const states = ["#4ade80", "#fbbf24", null] as const;
  states.forEach((dot, i) => {
    const x = 520 + i * 16, y = 138 + i * 132, ww = 460, wh = 116;
    if (dot) {
      ctx.fillStyle = "rgba(10,7,20,0.82)";
      roundRect(ctx, x, y, ww, wh, 14);
      ctx.fill();
      ctx.strokeStyle = "rgba(243,177,107,0.35)";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,0.07)";
      ctx.fillRect(x + 2, y + 2, ww - 4, 28);
      ["#f3b16b", "#8b7fb0", "#6c6384"].forEach((c, k) => {
        ctx.fillStyle = c;
        ctx.beginPath(); ctx.arc(x + 20 + k * 20, y + 16, 6, 0, Math.PI * 2); ctx.fill();
      });
      ctx.fillStyle = dot;
      ctx.beginPath(); ctx.arc(x + ww - 22, y + 16, 7, 0, Math.PI * 2); ctx.fill();
      for (let line = 0; line < 3; line += 1) {
        ctx.fillStyle = line === 0 ? look.accent : "rgba(255,241,222,0.55)";
        ctx.fillRect(x + 20, y + 46 + line * 22, line === 0 ? 18 : 12, 10);
        ctx.fillStyle = "rgba(255,241,222,0.42)";
        ctx.fillRect(x + 48, y + 46 + line * 22, 120 + ((i * 97 + line * 61) % 230), 10);
      }
    } else {
      ctx.setLineDash([14, 10]);
      ctx.strokeStyle = look.accent;
      ctx.lineWidth = 3;
      roundRect(ctx, x, y, ww, wh, 14);
      ctx.stroke();
      ctx.setLineDash([]);
      plus(ctx, x + ww / 2, y + wh / 2, 46, look.accent);
    }
  });
  // The prompt line with its cursor.
  ctx.fillStyle = "rgba(0,0,0,0.25)";
  ctx.fillRect(0, h - 92, w, 92);
  ctx.textBaseline = "middle";
  ctx.font = `700 32px ${MONO}`;
  ctx.fillStyle = look.accent;
  ctx.fillText("$", 44, h - 46);
  ctx.fillStyle = look.text;
  ctx.fillText("spawn --agent", 84, h - 46);
  const end = 84 + ctx.measureText("spawn --agent").width + 12;
  ctx.fillStyle = look.accent;
  ctx.fillRect(end, h - 64, 20, 36);
}

/** The agents floor's screen: the ghost and SPAWN, a new-agent button, the team with a place for the next one. */
function drawSpawnScreen(ctx: Ctx, w: number, h: number): void {
  const look = SPAWN_LOOKS.agents;
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, look.screenTop);
  bg.addColorStop(1, look.screenBottom);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);
  screenHeader(ctx, w, look, (x, y) => drawGhost(ctx, x + 12, y - 30, 60, look.text, look.screenTop));
  spawnButton(ctx, look, "NEW AGENT", true);
  // The team: two toy figures and, dashed, the one about to spawn.
  const figures = [{ x: 620, colour: "#c4775a", shirt: "#e9dcc4" }, { x: 780, colour: "#7c8fa8", shirt: "#d9a441" }, { x: 940, colour: null, shirt: null }];
  const floorY = 480;
  ctx.fillStyle = "rgba(159,191,154,0.14)";
  roundRect(ctx, 530, floorY - 6, 480, 16, 8);
  ctx.fill();
  for (const f of figures) {
    if (f.colour && f.shirt) {
      ctx.fillStyle = f.shirt;
      roundRect(ctx, f.x - 42, floorY - 130, 84, 124, 30);
      ctx.fill();
      ctx.fillStyle = "#f1dcc3";
      ctx.beginPath(); ctx.arc(f.x, floorY - 186, 50, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = f.colour;
      ctx.beginPath(); ctx.arc(f.x, floorY - 200, 50, Math.PI, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#2b2926";
      for (const dx of [-16, 16]) { ctx.beginPath(); ctx.arc(f.x + dx, floorY - 180, 6, 0, Math.PI * 2); ctx.fill(); }
    } else {
      ctx.setLineDash([12, 9]);
      ctx.strokeStyle = look.accent;
      ctx.lineWidth = 4;
      roundRect(ctx, f.x - 42, floorY - 130, 84, 124, 30);
      ctx.stroke();
      ctx.beginPath(); ctx.arc(f.x, floorY - 186, 50, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      plus(ctx, f.x, floorY - 186, 40, look.accent);
      // Sparkles round the newcomer.
      ctx.fillStyle = look.accent;
      for (const [sx, sy, r] of [[f.x + 64, floorY - 250, 7], [f.x - 66, floorY - 226, 5], [f.x + 58, floorY - 120, 5]] as const) {
        ctx.beginPath();
        ctx.moveTo(sx, sy - r * 2); ctx.lineTo(sx + r * 0.6, sy - r * 0.6); ctx.lineTo(sx + r * 2, sy); ctx.lineTo(sx + r * 0.6, sy + r * 0.6);
        ctx.lineTo(sx, sy + r * 2); ctx.lineTo(sx - r * 0.6, sy + r * 0.6); ctx.lineTo(sx - r * 2, sy); ctx.lineTo(sx - r * 0.6, sy - r * 0.6);
        ctx.closePath(); ctx.fill();
      }
    }
  }
  // Name bars under the two who are here.
  for (const f of figures.slice(0, 2)) {
    ctx.fillStyle = "rgba(241,235,225,0.75)";
    roundRect(ctx, f.x - 50, floorY + 30, 100, 14, 7);
    ctx.fill();
  }
  // A strip of the team's colours along the bottom.
  ctx.fillStyle = "rgba(0,0,0,0.22)";
  ctx.fillRect(0, h - 92, w, 92);
  ["#c4775a", "#7c8fa8", "#d9a441", "#74866c", "#b56a4c", "#9fae93"].forEach((c, i) => {
    ctx.fillStyle = c;
    ctx.beginPath(); ctx.arc(64 + i * 52, h - 46, 17, 0, Math.PI * 2); ctx.fill();
  });
  ctx.setLineDash([8, 6]);
  ctx.strokeStyle = look.accent;
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.arc(64 + 6 * 52, h - 46, 17, 0, Math.PI * 2); ctx.stroke();
  ctx.setLineDash([]);
}

/**
 * The pad's face, drawn for a circle of the canvas' full width: speckled
 * stone, an outer metal band, a ring of tick marks, four chevrons pointing in
 * at the terminal and a fine inner circle. The glowing ring is its own mesh.
 */
function drawPad(floor: OfficeFloor) {
  return (ctx: Ctx, w: number, h: number): void => {
    const look = SPAWN_LOOKS[floor];
    const cx = w / 2, cy = h / 2, R = w / 2;
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.clip();
    ctx.fillStyle = look.pad;
    ctx.fillRect(0, 0, w, h);
    // Terrazzo / limestone speckles, deterministic.
    let seed = floor === "coding" ? 7331 : 1337;
    const rand = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 0x1_0000_0000; };
    for (let i = 0; i < 2600; i += 1) {
      const a = rand() * Math.PI * 2, r = Math.sqrt(rand()) * R;
      ctx.fillStyle = look.speck[i % look.speck.length];
      const s = 2 + rand() * (i % 9 === 0 ? 6 : 3);
      ctx.fillRect(cx + Math.cos(a) * r, cy + Math.sin(a) * r, s, s);
    }
    // The outer metal band and a thin line inside it.
    ctx.lineWidth = R * 0.05;
    ctx.strokeStyle = look.padEdge;
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.975, 0, Math.PI * 2); ctx.stroke();
    ctx.lineWidth = R * 0.008;
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.915, 0, Math.PI * 2); ctx.stroke();
    // Tick marks, longer every 45°.
    ctx.strokeStyle = look.padBand;
    ctx.lineCap = "round";
    for (let i = 0; i < 48; i += 1) {
      const a = (i / 48) * Math.PI * 2, major = i % 6 === 0;
      const r0 = R * (major ? 0.66 : 0.7), r1 = R * 0.76;
      ctx.lineWidth = major ? R * 0.018 : R * 0.008;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
      ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      ctx.stroke();
    }
    // Four chevrons pointing in at the terminal.
    ctx.fillStyle = look.padBand;
    for (let i = 0; i < 4; i += 1) {
      const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
      ctx.save();
      ctx.translate(cx + Math.cos(a) * R * 0.56, cy + Math.sin(a) * R * 0.56);
      ctx.rotate(a);
      ctx.beginPath();
      ctx.moveTo(R * 0.05, -R * 0.07); ctx.lineTo(-R * 0.02, 0); ctx.lineTo(R * 0.05, R * 0.07);
      ctx.lineTo(R * 0.02, R * 0.07); ctx.lineTo(-R * 0.05, 0); ctx.lineTo(R * 0.02, -R * 0.07);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
    ctx.lineWidth = R * 0.01;
    ctx.strokeStyle = look.padBand;
    ctx.globalAlpha = 0.6;
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.46, 0, Math.PI * 2); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.restore();
  };
}

const padCache = new Map<string, MeshStandardMaterial>();

const faces = {
  screen: (floor: OfficeFloor) => canvasMaterial(`spawn:screen:${floor}`, 1024, 690, floor === "coding" ? drawLaunchScreen : drawSpawnScreen,
    { glow: 0.9, fallback: SPAWN_LOOKS[floor].screenTop, roughness: 0.3 }),
  pad: (floor: OfficeFloor) => {
    const key = `spawn:pad:${floor}`;
    let material = padCache.get(key);
    if (!material) {
      const map = cachedCanvasTexture(key, 1024, 1024, drawPad(floor));
      material = new MeshStandardMaterial({ color: map ? "#ffffff" : SPAWN_LOOKS[floor].pad, map, roughness: floor === "coding" ? 0.35 : 0.6 });
      padCache.set(key, material);
    }
    return material;
  },
};

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

const PAD_R = FURNITURE_SIZE.spawnPad.w / 2;
/** The glowing ring set into the pad, inside the checkpoint's gold ring. */
const GLOW_RING: [number, number] = [PAD_R * 0.845, PAD_R * 0.885];

const SGEO = {
  pad: new CylinderGeometry(PAD_R, PAD_R, 0.012, 72),
  glowRing: new RingGeometry(GLOW_RING[0], GLOW_RING[1], 72),
  core: new CylinderGeometry(0.045, 0.045, 1, 16),
  tube: new CylinderGeometry(0.1, 0.1, 1, 24, 1, true),
  collar: new TorusGeometry(0.105, 0.014, 8, 28),
  button: new CylinderGeometry(0.052, 0.052, 0.012, 24),
  buttonRing: new TorusGeometry(0.066, 0.008, 6, 24),
};
SGEO.glowRing.rotateX(-Math.PI / 2);
SGEO.collar.rotateX(Math.PI / 2);
SGEO.buttonRing.rotateX(Math.PI / 2);

function Mesh3({ geometry, material, position, rotation, scale, cast = true }: {
  geometry: BufferGeometry; material: MeshStandardMaterial; position: Vec3; rotation?: Vec3; scale?: Vec3 | number; cast?: boolean;
}) {
  return <mesh geometry={geometry} material={material} position={position} rotation={rotation} scale={scale} castShadow={cast} receiveShadow />;
}

/** A bar of the prompt glyph: a unit box scaled to `size`, turned about z. */
function Bar({ size, position, angle, material }: { size: Vec3; position: Vec3; angle: number; material: MeshStandardMaterial }) {
  return <Mesh3 geometry={GEO.box} material={material} position={position} rotation={[0, 0, angle]} scale={size} />;
}

// ---------------------------------------------------------------------------
// Props (local space: centred on the origin, front faces +z)
// ---------------------------------------------------------------------------

/** Pillar centres and the console height, shared with the fittings. */
const PILLAR_X = 0.54;
const CONSOLE_Y = 0.95;
const SCREEN_Y = 1.42;
const LINTEL_Y = 1.99;

/** The floor's crest on the lintel: the brass ghost downstairs, a brass prompt glyph (>_) upstairs. */
function Crest({ floor, metal }: { floor: OfficeFloor; metal: MeshStandardMaterial }) {
  if (floor === "agents") {
    // The ghost is 1 unit tall with its back on z = 0 and ~0.08 deep: centre it on the lintel, both faces show.
    return <mesh geometry={GHOST_GEOMETRY} material={metal} position={[0, LINTEL_Y + 0.05, -0.02]} scale={[0.15, 0.15, 0.5]} castShadow />;
  }
  return (
    <group position={[-0.03, LINTEL_Y + 0.105, 0]}>
      <Bar size={[0.075, 0.022, 0.03]} position={[0, 0.022, 0]} angle={-0.6} material={metal} />
      <Bar size={[0.075, 0.022, 0.03]} position={[0, -0.022, 0]} angle={0.6} material={metal} />
      <Box size={[0.07, 0.022, 0.03]} position={[0.075, -0.042, 0]} material={metal} />
    </group>
  );
}

/**
 * The spawn terminal: a plinth with a light line, two pillars with light
 * strips, a lintel with the floor's crest, the double-sided screen hanging
 * between the pillars, and a console slab across them with a glowing spawn
 * button each side over a glass tube with the energy core.
 */
export function SpawnTerminal() {
  const floor = useOfficeStore((s) => s.floor);
  const m = spawnMaterials(floor);
  const screen = faces.screen(floor);
  return (
    <group>
      {/* Plinth with a light line along both long faces. */}
      <Rounded size={[1.12, 0.08, 0.62]} radius={0.03} position={[0, 0.04, 0]} material={m.plinth} />
      {[-1, 1].map((side) => <Box key={side} size={[1.0, 0.012, 0.01]} position={[0, 0.05, side * 0.312]} material={m.glow} cast={false} />)}

      {/* Pillars: metal feet, wood shafts, a light strip down each face. */}
      {[-PILLAR_X, PILLAR_X].map((x) => (
        <group key={x}>
          <Box size={[0.12, 0.04, 0.22]} position={[x, 0.1, 0]} material={m.metal} />
          <Rounded size={[0.12, 1.86, 0.2]} radius={0.04} position={[x, 1.01, 0]} material={m.body} />
          {[-1, 1].map((side) => <Box key={side} size={[0.014, 1.6, 0.012]} position={[x, 1.04, side * 0.101]} material={m.glow} cast={false} />)}
        </group>
      ))}

      {/* Lintel with metal trim lines and the crest. */}
      <Rounded size={[1.2, 0.1, 0.22]} radius={0.035} position={[0, LINTEL_Y, 0]} material={m.body} />
      {[-1, 1].map((side) => <Box key={side} size={[1.1, 0.012, 0.01]} position={[0, LINTEL_Y, side * 0.111]} material={m.metal} cast={false} />)}
      <Crest floor={floor} metal={m.metal} />

      {/* The screen: a bezel between the pillars with a face on each side and metal brackets. */}
      <group position={[0, SCREEN_Y, 0]}>
        <Rounded size={[0.94, 0.66, 0.07]} radius={0.03} position={[0, 0, 0]} material={m.bezel} />
        <Box size={[0.9, 0.012, 0.074]} position={[0, 0.324, 0]} material={m.metal} cast={false} />
        <mesh position={[0, 0, 0.0355]} material={screen}><planeGeometry args={[0.86, 0.58]} /></mesh>
        <mesh position={[0, 0, -0.0355]} rotation={[0, Math.PI, 0]} material={screen}><planeGeometry args={[0.86, 0.58]} /></mesh>
        {[-0.475, 0.475].map((x) => <Box key={x} size={[0.02, 0.1, 0.05]} position={[x, 0, 0]} material={m.metal} />)}
      </group>

      {/* The console across the pillars, a spawn button each side. */}
      <Rounded size={[0.96, 0.035, 0.46]} radius={0.012} position={[0, CONSOLE_Y, 0]} material={m.body} />
      {[-1, 1].map((side) => (
        <group key={side} position={[0, CONSOLE_Y + 0.018, side * 0.14]}>
          <Mesh3 geometry={SGEO.button} material={m.glow} position={[0, 0.004, 0]} cast={false} />
          <Mesh3 geometry={SGEO.buttonRing} material={m.metal} position={[0, 0.004, 0]} cast={false} />
        </group>
      ))}

      {/* Under it the glass tube with the energy core and a metal collar at each end. */}
      <Mesh3 geometry={SGEO.core} material={m.glow} position={[0, 0.515, 0]} scale={[1, 0.83, 1]} cast={false} />
      <Mesh3 geometry={SGEO.tube} material={m.glass} position={[0, 0.515, 0]} scale={[1, 0.83, 1]} cast={false} />
      <Mesh3 geometry={SGEO.collar} material={m.metal} position={[0, 0.105, 0]} />
      <Mesh3 geometry={SGEO.collar} material={m.metal} position={[0, 0.925, 0]} />
    </group>
  );
}

/** The spawn pad: a thin stone disc with its drawn face and the glowing ring set into it. */
export function SpawnPad() {
  const floor = useOfficeStore((s) => s.floor);
  const m = spawnMaterials(floor);
  return (
    <group>
      <Mesh3 geometry={SGEO.pad} material={faces.pad(floor)} position={[0, 0.006, 0]} cast={false} />
      <Mesh3 geometry={SGEO.glowRing} material={m.glow} position={[0, 0.0135, 0]} cast={false} />
    </group>
  );
}

export const SPAWN_RENDERERS: Record<Extract<FurnitureKind, "spawnPad" | "spawnTerminal">, (props: { item: Furniture }) => JSX.Element> = {
  spawnPad: () => <SpawnPad />,
  spawnTerminal: () => <SpawnTerminal />,
};

// ---------------------------------------------------------------------------
// Fittings: the moving parts
// ---------------------------------------------------------------------------

/** How long a newcomer's column of light lasts. */
const BEAM_S = 1.8;
const BEAM_H = 2.8;

const beamGeometry = new CylinderGeometry(0.42, 0.42, BEAM_H, 32, 1, true);
const burstGeometry = new RingGeometry(0.3, 0.42, 48);
burstGeometry.rotateX(-Math.PI / 2);
const gyroGeometry = [new TorusGeometry(0.52, 0.011, 6, 64), new TorusGeometry(0.6, 0.009, 6, 64)];

const beamMaterials = new Map<OfficeFloor, { beam: MeshBasicMaterial; burst: MeshBasicMaterial }>();
function beamMaterialsFor(floor: OfficeFloor) {
  let set = beamMaterials.get(floor);
  if (!set) {
    // Bright at the floor, gone at the top.
    const fade = cachedCanvasTexture("spawn:beam", 4, 128, (ctx, w, h) => {
      const g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, "rgba(0,0,0,1)");
      g.addColorStop(1, "rgba(255,255,255,1)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    });
    const colour = SPAWN_LOOKS[floor].glow;
    set = {
      beam: new MeshBasicMaterial({ color: colour, map: fade, transparent: true, opacity: 0, blending: AdditiveBlending, depthWrite: false, side: DoubleSide, toneMapped: false }),
      burst: new MeshBasicMaterial({ color: colour, transparent: true, opacity: 0, blending: AdditiveBlending, depthWrite: false, side: DoubleSide, toneMapped: false }),
    };
    beamMaterials.set(floor, set);
  }
  return set;
}

/**
 * The spawn point's life: two gyroscope rings turning round the floor token,
 * the glow breathing, and a column of light on the pad whenever someone new
 * arrives on the floor (a newcomer walks off the pad to its desk). Reduced
 * motion keeps the rings still and skips the beam.
 */
export function SpawnFittings({ terminal, arrival, floor, newcomers, animate }: {
  terminal: Point; arrival: Point; floor: OfficeFloor; newcomers: ReadonlySet<string>; animate: boolean;
}) {
  const m = spawnMaterials(floor);
  const beams = beamMaterialsFor(floor);
  const gyroA = useRef<Group>(null);
  const gyroB = useRef<Group>(null);
  const beam = useRef<Mesh>(null);
  const burst = useRef<Mesh>(null);
  // -1 = start on the next frame; null = no beam running.
  const beamStart = useRef<number | null>(null);
  const seen = useRef<ReadonlySet<string>>(newcomers);
  useEffect(() => {
    const fresh = [...newcomers].some((id) => !seen.current.has(id));
    seen.current = newcomers;
    if (fresh && animate) beamStart.current = -1;
  }, [newcomers, animate]);
  // The glow is shared: leave it at rest when the fittings go.
  useEffect(() => () => { m.glow.emissiveIntensity = 1.25; }, [m]);

  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    if (gyroA.current && gyroB.current) {
      gyroA.current.rotation.set(animate ? t * 0.9 : 1.1, animate ? t * 0.35 : 0.3, 0);
      gyroB.current.rotation.set(animate ? -t * 0.55 : 0.4, 0, animate ? t * 0.7 : 1.2);
    }
    m.glow.emissiveIntensity = animate ? 1.2 + Math.sin(t * 2.1) * 0.25 : 1.25;
    if (beamStart.current === -1) beamStart.current = t;
    const start = beamStart.current;
    const p = start === null ? 1 : (t - start) / BEAM_S;
    const running = p >= 0 && p < 1;
    if (!running) beamStart.current = null;
    if (beam.current && burst.current) {
      beam.current.visible = running;
      burst.current.visible = running;
      if (running) {
        const rise = Math.min(1, p / 0.15);
        beams.beam.opacity = 0.75 * rise * (1 - Math.max(0, (p - 0.15) / 0.85));
        beam.current.scale.set(1.15 - 0.5 * p, 0.4 + 0.6 * rise, 1.15 - 0.5 * p);
        beam.current.position.y = (BEAM_H * beam.current.scale.y) / 2;
        burst.current.scale.setScalar(1 + p * 2.2);
        beams.burst.opacity = 0.8 * (1 - p);
      }
    }
  });

  return (
    <>
      <group position={[terminal.x, SPAWN.tokenY, terminal.z]}>
        <group ref={gyroA}><mesh geometry={gyroGeometry[0]} material={m.glow} /></group>
        <group ref={gyroB}><mesh geometry={gyroGeometry[1]} material={m.glow} /></group>
      </group>
      <group position={[arrival.x, 0, arrival.z]}>
        <mesh ref={beam} geometry={beamGeometry} material={beams.beam} visible={false} renderOrder={2} />
        <mesh ref={burst} geometry={burstGeometry} material={beams.burst} position={[0, 0.03, 0]} visible={false} renderOrder={2} />
      </group>
    </>
  );
}
