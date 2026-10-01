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
import { CylinderGeometry, MeshStandardMaterial, SphereGeometry } from "three";
import { canvasMaterial } from "./canvasMaterials";
import { LEAD_RENDERERS } from "./LeadSuite";
import { COMMAND_RENDERERS } from "./CommandOffice";
import { SERVER_RENDERERS } from "./ServerRoom";
import { TEAM_RENDERERS } from "./TeamRoomDecor";
import { BREAK_RENDERERS } from "./BreakLounge";
import { WARDROBE_RENDERERS } from "./WardrobeRoom";
import { LOBBY_RENDERERS } from "./LobbyDecor";
import { SPAWN_RENDERERS } from "./SpawnPoint";
import { MeetingChair } from "./OfficeChairs";
import { Bookshelf, Box, Couch, GEO, MAT, matte, Plant, Rounded, Rug } from "./OfficeFurniture";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind } from "./officeLayout";
import { PROP_COLOURS as P } from "./officePalette";

type Vec3 = [number, number, number];

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/**
 * Reception's help display: the map's main keys as key caps, each over a gold
 * pictogram of what it does (walk, run, use, map, help). Shapes only, no words,
 * so it reads the same in every language.
 */
function drawReceptionHelp(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, "#1a2742");
  bg.addColorStop(1, "#0f1628");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#f5b83d";
  ctx.fillRect(0, 0, w, 7);
  const cap = (x: number, y: number, cw: number, label: string) => {
    ctx.fillStyle = "#0a0f1c";
    roundRect(ctx, x, y + 5, cw, 46, 9);
    ctx.fill();
    ctx.fillStyle = "#eef2f8";
    roundRect(ctx, x, y, cw, 44, 9);
    ctx.fill();
    ctx.fillStyle = "#1a2742";
    ctx.font = "700 24px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, x + cw / 2, y + 23);
  };
  const gold = () => { ctx.strokeStyle = "#f5b83d"; ctx.fillStyle = "#f5b83d"; ctx.lineWidth = 5; ctx.lineCap = "round"; ctx.lineJoin = "round"; };
  const iy = 150;
  // WASD cluster over a four-way arrow.
  const kx = 26, ky = 30, kw = 44;
  cap(kx + kw + 6, ky, kw, "W");
  cap(kx, ky + 52, kw, "A"); cap(kx + kw + 6, ky + 52, kw, "S"); cap(kx + 2 * (kw + 6), ky + 52, kw, "D");
  gold();
  const cx = kx + 1.5 * kw + 6;
  ctx.beginPath();
  ctx.moveTo(cx - 22, iy); ctx.lineTo(cx + 22, iy); ctx.moveTo(cx, iy - 16); ctx.lineTo(cx, iy + 16);
  ctx.moveTo(cx - 14, iy - 7); ctx.lineTo(cx - 22, iy); ctx.lineTo(cx - 14, iy + 7);
  ctx.moveTo(cx + 14, iy - 7); ctx.lineTo(cx + 22, iy); ctx.lineTo(cx + 14, iy + 7);
  ctx.stroke();
  // Shift: a double chevron (run).
  const col = (x: number, cw: number, label: string, icon: (x: number) => void) => { cap(x, 56, cw, label); gold(); icon(x + cw / 2); };
  col(186, 92, "Shift", (x) => {
    ctx.beginPath();
    for (const dx of [-12, 6]) { ctx.moveTo(x + dx, iy - 12); ctx.lineTo(x + dx + 12, iy); ctx.lineTo(x + dx, iy + 12); }
    ctx.stroke();
  });
  // E: a filled ring (use what is nearby).
  col(298, 52, "E", (x) => {
    ctx.beginPath(); ctx.arc(x, iy, 14, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, iy, 5, 0, Math.PI * 2); ctx.fill();
  });
  // M: a folded map.
  col(370, 52, "M", (x) => {
    ctx.beginPath();
    ctx.moveTo(x - 18, iy - 10); ctx.lineTo(x - 6, iy - 15); ctx.lineTo(x + 6, iy - 10); ctx.lineTo(x + 18, iy - 15);
    ctx.lineTo(x + 18, iy + 10); ctx.lineTo(x + 6, iy + 15); ctx.lineTo(x - 6, iy + 10); ctx.lineTo(x - 18, iy + 15); ctx.closePath();
    ctx.moveTo(x - 6, iy - 15); ctx.lineTo(x - 6, iy + 10); ctx.moveTo(x + 6, iy - 10); ctx.lineTo(x + 6, iy + 15);
    ctx.stroke();
  });
  // H: a question mark in a ring (help), the key the whole guide hangs on.
  col(442, 52, "H", (x) => {
    ctx.beginPath(); ctx.arc(x, iy, 17, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, iy - 4, 6, Math.PI * 1.05, Math.PI * 2.3); ctx.lineTo(x, iy + 5); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, iy + 11, 2.6, 0, Math.PI * 2); ctx.fill();
  });
}

/** The gold info sign on the reception counter's front (drawn on a disc, so the square corners never show). */
function drawInfoSign(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.clearRect(0, 0, w, h);
  const r = w / 2 - 4;
  const disc = ctx.createRadialGradient(w * 0.42, h * 0.38, r * 0.1, w / 2, h / 2, r);
  disc.addColorStop(0, "#ffe19a");
  disc.addColorStop(1, "#f0a92b");
  ctx.fillStyle = disc;
  ctx.beginPath(); ctx.arc(w / 2, h / 2, r, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.lineWidth = w * 0.025;
  ctx.beginPath(); ctx.arc(w / 2, h / 2, r * 0.86, 0, Math.PI * 2); ctx.stroke();
  // The "i": a dot over a rounded stem.
  ctx.fillStyle = "#2a1a04";
  ctx.beginPath(); ctx.arc(w / 2, h * 0.3, w * 0.075, 0, Math.PI * 2); ctx.fill();
  roundRect(ctx, w / 2 - w * 0.065, h * 0.43, w * 0.13, h * 0.34, w * 0.05);
  ctx.fill();
}

/** Arcade screen: the Asteroid Run attract screen, a rocket among faceted rocks. */
function drawArcade(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const sky = ctx.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#07060f");
  sky.addColorStop(1, "#171133");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, w, h);
  // Warp streaks running out from the vanishing point.
  const vx = w * 0.52, vy = h * 0.42;
  ctx.lineCap = "round";
  for (let i = 0; i < 46; i += 1) {
    const a = (i * 2.399) % (Math.PI * 2), r0 = 14 + ((i * 37) % 60), len = 8 + ((i * 13) % 22);
    ctx.strokeStyle = `rgba(201, 212, 255, ${0.25 + ((i * 7) % 10) / 20})`;
    ctx.lineWidth = 1 + (i % 3) * 0.5;
    ctx.beginPath();
    ctx.moveTo(vx + Math.cos(a) * r0, vy + Math.sin(a) * r0);
    ctx.lineTo(vx + Math.cos(a) * (r0 + len), vy + Math.sin(a) * (r0 + len));
    ctx.stroke();
  }
  // Low-poly rocks: a fan of facets, each triangle shaded by its facing.
  const rock = (cx: number, cy: number, r: number, seed: number) => {
    const n = 7;
    const pts = Array.from({ length: n }, (_, i) => {
      const a = (i / n) * Math.PI * 2 + seed, k = 0.75 + (((seed * 31 + i * 17) % 10) / 10) * 0.4;
      return [cx + Math.cos(a) * r * k, cy + Math.sin(a) * r * k] as const;
    });
    const hub = [cx - r * 0.15, cy - r * 0.2] as const;
    pts.forEach((p, i) => {
      const q = pts[(i + 1) % n];
      const light = 0.45 + 0.4 * Math.max(0, Math.cos((i / n) * Math.PI * 2 + seed - 2.3));
      ctx.fillStyle = `rgb(${Math.round(120 * light + 60)}, ${Math.round(128 * light + 62)}, ${Math.round(165 * light + 70)})`;
      ctx.beginPath(); ctx.moveTo(hub[0], hub[1]); ctx.lineTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.closePath(); ctx.fill();
    });
  };
  rock(w * 0.84, h * 0.2, 24, 1);
  rock(w * 0.16, h * 0.3, 15, 2);
  rock(w * 0.62, h * 0.24, 9, 3);
  rock(w * 0.3, h * 0.62, 7, 4);
  rock(w * 0.82, h * 0.7, 17, 5);
  // The rocket, flying into the screen: exhaust, fins, body, nose, window.
  const rx = w * 0.46, ry = h * 0.7;
  ctx.save();
  ctx.translate(rx, ry);
  ctx.rotate(-0.5);
  ctx.fillStyle = "#ffcc33";
  ctx.beginPath(); ctx.moveTo(-7, 14); ctx.lineTo(0, 44); ctx.lineTo(7, 14); ctx.closePath(); ctx.fill();
  ctx.fillStyle = "#e0443e";
  ctx.beginPath(); ctx.moveTo(-9, 2); ctx.lineTo(-20, 18); ctx.lineTo(-8, 14); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(9, 2); ctx.lineTo(20, 18); ctx.lineTo(8, 14); ctx.closePath(); ctx.fill();
  ctx.fillStyle = "#eef2f8";
  ctx.fillRect(-9, -16, 18, 30);
  ctx.fillStyle = "#3b82f6";
  ctx.fillRect(-9, -6, 18, 5);
  ctx.beginPath(); ctx.moveTo(-9, -16); ctx.lineTo(0, -32); ctx.lineTo(9, -16); ctx.closePath(); ctx.fill();
  ctx.restore();
  // A laser bolt and the score line.
  ctx.strokeStyle = "#ffe066";
  ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.moveTo(w * 0.55, h * 0.48); ctx.lineTo(w * 0.6, h * 0.36); ctx.stroke();
  ctx.fillStyle = "#e6ebff";
  for (let i = 0; i < 4; i += 1) ctx.fillRect(12 + i * 10, 10, 7, 7);
  ctx.fillStyle = "#facc15";
  ctx.fillRect(12, 22, 54, 3);
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
  mug: matte(P.mug),
  coffee: matte(P.coffee),
  espresso: matte(P.espresso, { roughness: 0.35, metalness: 0.25 }),
  arcadeBody: matte(P.arcadeBody),
  arcadeTrim: matte(P.arcadeTrim),
  marquee: matte(P.arcadeMarquee, { emissive: P.arcadeMarquee, emissiveIntensity: 0.8 }),
  joystick: matte(P.joystick, { roughness: 0.4 }),
  arcadeButtons: P.arcadeButtons.map((c) => matte(c, { emissive: c, emissiveIntensity: 0.35 })),
  bell: matte(P.bell, { roughness: 0.35, metalness: 0.3 }),
  boardFrame: matte(P.boardFrame),
  displayFrame: matte(P.displayFrame, { roughness: 0.5 }),
  paper: matte(P.paper),
  rugInner: matte(P.rugInner),
  gold: matte("#f5b83d", { emissive: "#f5b83d", emissiveIntensity: 0.6 }),
  brochures: P.lockers.map((c) => matte(c, { roughness: 0.8 })),
  indicatorLight: matte(P.indicator, { emissive: P.indicator, emissiveIntensity: 0.9 }),
};

const lazy = {
  receptionHelp: () => canvasMaterial("prop:receptionHelp", 512, 192, drawReceptionHelp, { glow: 0.85, fallback: "#1a2742", roughness: 0.4 }),
  infoSign: () => canvasMaterial("prop:infoSign", 256, 256, drawInfoSign, { glow: 0.7, fallback: "#f5b83d", roughness: 0.4 }),
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

/**
 * Reception counter: the visitor side (+z) is a high white counter with a
 * wood top and a service bell; the receptionist side (-z) is a lower work
 * surface whose monitor faces -z.
 */
/** Vertical slat positions across the reception back wall. */
const RECEPTION_SLATS = Array.from({ length: 15 }, (_, i) => -1.26 + i * 0.18);

/**
 * Reception counter, the office's help desk. Visitor side (+z): a white
 * counter on a dark plinth with wood end caps, a wood feature panel carrying
 * a glowing gold info sign, gold light lines under the lip and along the
 * floor, and on the wood top a service bell, a brochure stand and a small
 * plant. Behind it a slatted wood wall holds the help display with the map's
 * main keys, so the desk reads as "ask here" from across the floor.
 */
function ReceptionDesk() {
  return (
    <group>
      {/* Back wall: dark panel, light slats, a gold light line on top. */}
      <Box size={[2.8, 2.1, 0.05]} position={[0, 1.05, -0.42]} material={MAT.woodDark} />
      {RECEPTION_SLATS.map((x) => <Box key={x} size={[0.07, 2.02, 0.03]} position={[x, 1.05, -0.38]} material={MAT.wood} />)}
      <Box size={[2.8, 0.05, 0.1]} position={[0, 2.125, -0.405]} material={MAT.woodDark} />
      <Box size={[2.7, 0.018, 0.02]} position={[0, 2.09, -0.345]} material={PM.gold} cast={false} />
      {/* Help display on the wall, facing the visitors. */}
      <Rounded size={[1.56, 0.64, 0.05]} radius={0.03} position={[0, 1.62, -0.34]} material={PM.displayFrame} />
      <Panel size={[1.46, 0.548]} position={[0, 1.62, -0.3135]} material={lazy.receptionHelp()} />
      <Box size={[0.6, 0.02, 0.012]} position={[0, 1.95, -0.32]} material={PM.gold} cast={false} />

      {/* Receptionist side: a low work surface on two pedestals and a keyboard. */}
      <Rounded size={[2.5, 0.04, 0.3]} radius={0.015} position={[0, 0.74, -0.17]} material={MAT.deskTop} />
      <Box size={[0.36, 0.72, 0.26]} position={[-1.05, 0.36, -0.17]} material={MAT.deskBody} />
      <Box size={[0.36, 0.72, 0.26]} position={[1.05, 0.36, -0.17]} material={MAT.deskBody} />
      <Box size={[0.4, 0.02, 0.12]} position={[0.2, 0.77, -0.2]} material={MAT.keyboard} />

      {/* Visitor counter: plinth, body and wood end caps (body stops short of the caps: no coplanar faces). */}
      <Box size={[2.64, 0.08, 0.34]} position={[0, 0.04, 0.22]} material={MAT.woodDark} />
      <Box size={[2.5, 0.012, 0.012]} position={[0, 0.07, 0.395]} material={PM.gold} cast={false} />
      <Rounded size={[2.6, 0.9, 0.38]} radius={0.12} position={[0, 0.53, 0.24]} material={MAT.deskBody} />
      <Rounded size={[0.1, 0.96, 0.42]} radius={0.03} position={[-1.35, 0.5, 0.23]} material={MAT.wood} />
      <Rounded size={[0.1, 0.96, 0.42]} radius={0.03} position={[1.35, 0.5, 0.23]} material={MAT.wood} />
      <Rounded size={[2.8, 0.05, 0.46]} radius={0.02} position={[0, 1.005, 0.22]} material={MAT.wood} />
      <Box size={[2.5, 0.016, 0.012]} position={[0, 0.965, 0.432]} material={PM.gold} cast={false} />
      {/* Front feature panel with the info sign. */}
      <Rounded size={[0.92, 0.62, 0.03]} radius={0.02} position={[0, 0.52, 0.43]} material={MAT.wood} />
      <mesh position={[0, 0.54, 0.448]} material={lazy.infoSign()}>
        <circleGeometry args={[0.2, 40]} />
      </mesh>

      {/* On the counter: service bell, brochure stand, a small plant. */}
      <Cyl radius={0.07} height={0.012} position={[0.95, 1.036, 0.26]} material={MAT.monitor} />
      <mesh geometry={PGEO.dome} material={PM.bell} position={[0.95, 1.042, 0.26]} scale={0.055} castShadow />
      <mesh geometry={PGEO.sphere} material={PM.bell} position={[0.95, 1.1, 0.26]} scale={0.012} />
      <Box size={[0.34, 0.02, 0.12]} position={[-0.9, 1.04, 0.24]} material={PM.displayFrame} />
      {P.lockers.slice(0, 3).map((colour, i) => (
        <group key={colour} position={[-1.01 + i * 0.11, 1.11, 0.24]} rotation={[-0.2, 0, 0]}>
          <Box size={[0.085, 0.13, 0.01]} position={[0, 0, 0]} material={PM.brochures[i]!} />
        </group>
      ))}
      <Cyl radius={0.05} height={0.07} position={[-0.35, 1.065, 0.3]} material={MAT.pot} />
      <mesh geometry={GEO.blob} material={MAT.leaf} position={[-0.35, 1.13, 0.3]} scale={[0.07, 0.06, 0.07]} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leafDark} position={[-0.32, 1.17, 0.29]} scale={0.035} castShadow />
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
      {/* The back stops 3 cm short of the slats' outer face: coplanar faces z-fight into stripes. */}
      <Rounded size={[2.2, 2.4, 0.27]} radius={0.04} position={[0, 1.2, -0.035]} material={PM.shaft} />
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
  receptionDesk: () => <ReceptionDesk />,
  lockers: () => <Lockers />,
  mirror: () => <Mirror />,
  coffeeBar: () => <CoffeeBar />,
  // The shared couch's armrests reach 2.36 m; squeeze it into the 2.2 m footprint.
  couch: () => <group scale={[COUCH_FIT_X, 1, 1]}><Couch position={[0, 0, 0]} /></group>,
  coffeeTable: () => <CoffeeTable />,
  arcade: () => <Arcade />,
  bookshelf: () => <Bookshelf position={[0, 0, 0]} />,
  plant: () => <Plant position={[0, 0, 0]} size={PROP_PLANT_SIZE} />,
  rug: ({ item }) => {
    const size = item.size ?? FURNITURE_SIZE.rug;
    return <RugPiece w={size.w} d={size.d} />;
  },
  elevator: () => <Elevator />,
  ...LEAD_RENDERERS,
  ...COMMAND_RENDERERS,
  ...SERVER_RENDERERS,
  ...TEAM_RENDERERS,
  ...BREAK_RENDERERS,
  ...WARDROBE_RENDERERS,
  ...LOBBY_RENDERERS,
  ...SPAWN_RENDERERS,
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
