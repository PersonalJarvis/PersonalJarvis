/**
 * The team room as a meeting room people like walking into: a solid walnut
 * table with a cable-tray spine and an inset power rail, a walnut slat wall
 * carrying the live team board, a video-conference display and felt panels,
 * a travertine-topped credenza with books, fiddle-leaf figs in clay planters,
 * a wool rug, and a pair of linear pendants that pool warm light on the table.
 *
 * Same rules as OfficeProps: every furniture piece is built in local space
 * centred on the origin, front facing +z, inside its `FURNITURE_SIZE` box.
 * Repeated parts (slats, leaves) are merged into one geometry per material,
 * and canvas faces are drawn once and shared.
 */
import { memo } from "react";
import {
  BoxGeometry, BufferGeometry, CylinderGeometry, DoubleSide, Euler, Matrix4, MeshStandardMaterial, Quaternion, SphereGeometry, Vector3,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { canvasMaterial } from "./canvasMaterials";
import { Box, GEO, MAT, matte, Rounded } from "./OfficeFurniture";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind, type Room } from "./officeLayout";

type Vec3 = [number, number, number];

// ---------------------------------------------------------------------------
// Materials: warm walnut, travertine, clay, sage and oat, soft warm light.
// ---------------------------------------------------------------------------

const C = {
  walnut: "#6a4631",
  walnutDark: "#3f2a1d",
  walnutLight: "#8b6246",
  travertine: "#e8dfd0",
  clay: "#c4775a",
  sage: "#8d9f86",
  oat: "#ddd0ba",
  cream: "#f1ebe1",
  terracotta: "#b56a4c",
  led: "#ffe2bd",
} as const;

const TM = {
  walnut: matte(C.walnut, { roughness: 0.55 }),
  walnutDark: matte(C.walnutDark, { roughness: 0.6 }),
  walnutLight: matte(C.walnutLight, { roughness: 0.55 }),
  travertine: matte(C.travertine, { roughness: 0.45 }),
  alu: matte("#c3c7cc", { roughness: 0.35, metalness: 0.5 }),
  brass: matte("#c9a25a", { roughness: 0.35, metalness: 0.6 }),
  ceramic: matte(C.cream, { roughness: 0.4 }),
  clay: matte(C.clay, { roughness: 0.8 }),
  terracotta: matte(C.terracotta, { roughness: 0.75 }),
  soil: matte("#3b2f26"),
  trunk: matte("#6b5440"),
  // Light strips: lit from within, so they read as light in any scene lighting.
  led: new MeshStandardMaterial({ color: C.led, emissive: C.led, emissiveIntensity: 1.2, toneMapped: false }),
  lens: matte("#0c0d10", { roughness: 0.15, metalness: 0.3 }),
  glass: new MeshStandardMaterial({ color: "#e4f1f4", transparent: true, opacity: 0.35, roughness: 0.05, depthWrite: false }),
  water: new MeshStandardMaterial({ color: "#bfe0ea", transparent: true, opacity: 0.55, roughness: 0.1, depthWrite: false }),
  frosted: new MeshStandardMaterial({ color: "#f5f7f8", transparent: true, opacity: 0.55, roughness: 0.9, side: DoubleSide, depthWrite: false }),
  frostedLine: new MeshStandardMaterial({ color: "#ffffff", transparent: true, opacity: 0.8, roughness: 0.9, side: DoubleSide, depthWrite: false }),
  leaves: ["#3f7a45", "#2e5e37", "#5d9a58"].map((c) => matte(c, { flatShading: true, roughness: 0.7 })),
  books: ["#c4775a", "#e9dcc4", "#51705c", "#2f3d56", "#d9a441", "#8b6246", "#7c8fa8"].map((c) => matte(c)),
};

// ---------------------------------------------------------------------------
// Canvas faces
// ---------------------------------------------------------------------------

type Ctx = CanvasRenderingContext2D;

/** Deterministic pseudo-random numbers, so drawn patterns never change between loads. */
function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/** Team board without any team yet: an org chart on the left, sticky notes on the right. */
function drawTeamBoard(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#fbfbf8";
  ctx.fillRect(0, 0, w, h);
  // Org chart: one box on top, three below, joined by marker lines.
  const node = (x: number, y: number, colour: string) => {
    ctx.fillStyle = colour;
    ctx.beginPath(); ctx.roundRect(x - 62, y - 26, 124, 52, 10); ctx.fill();
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

/** Acoustic felt with vertical fluting in one colour. */
function drawFelt(colour: string) {
  return (ctx: Ctx, w: number, h: number) => {
    ctx.fillStyle = colour;
    ctx.fillRect(0, 0, w, h);
    for (let x = 0; x < w; x += 16) {
      ctx.fillStyle = "rgba(0,0,0,0.16)";
      ctx.fillRect(x, 0, 3, h);
      ctx.fillStyle = "rgba(255,255,255,0.08)";
      ctx.fillRect(x + 6, 0, 4, h);
    }
  };
}

/** A felt pinboard full of sticky notes with marker scribbles. */
function drawPinboard(ctx: Ctx, w: number, h: number): void {
  const rand = lcg(4242);
  ctx.fillStyle = C.oat;
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 900; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.12)" : "rgba(90,70,50,0.08)";
    ctx.fillRect(rand() * w, rand() * h, 2, 2);
  }
  const notes = ["#fde68a", "#fbcfe8", "#bbf7d0", "#bfdbfe", "#fed7aa"];
  const cols = 2, rows = 4, cw = w / cols, ch = h / rows;
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      if ((r * cols + c) % 5 === 3) continue;
      const size = 78 + rand() * 12;
      ctx.save();
      ctx.translate(c * cw + cw / 2 + (rand() - 0.5) * 16, r * ch + ch / 2 + (rand() - 0.5) * 16);
      ctx.rotate((rand() - 0.5) * 0.18);
      ctx.fillStyle = "rgba(60,40,20,0.18)";
      ctx.fillRect(-size / 2 + 3, -size / 2 + 4, size, size);
      ctx.fillStyle = notes[(r * cols + c) % notes.length];
      ctx.fillRect(-size / 2, -size / 2, size, size);
      ctx.strokeStyle = "#3b4250";
      ctx.lineWidth = 3;
      ctx.lineCap = "round";
      for (let l = 0; l < 3; l += 1) {
        ctx.beginPath();
        ctx.moveTo(-size / 2 + 12, -size / 2 + 22 + l * 18);
        ctx.lineTo(-size / 2 + 12 + (size - 30) * (0.5 + rand() * 0.5), -size / 2 + 22 + l * 18);
        ctx.stroke();
      }
      ctx.fillStyle = "#b8412f";
      ctx.beginPath(); ctx.arc(0, -size / 2 + 7, 4, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }
  }
}

/** A video call in progress: six participants in a grid, the speaker ringed in green, the call controls below. */
function drawCall(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#101216";
  ctx.fillRect(0, 0, w, h);
  const tiles = [
    ["#3a4a5c", "#e0b48a", "#2d3440"], ["#5c4a3a", "#c98f6f", "#403428"], ["#3f5446", "#f0c9a4", "#2b3a30"],
    ["#4b3f5c", "#a8745a", "#312a3e"], ["#5a5446", "#e8c29c", "#3c382e"], ["#3a5058", "#8d5a44", "#28383e"],
  ];
  const pad = 14, bar = 62, cols = 3, rows = 2;
  const tw = (w - pad * (cols + 1)) / cols;
  const th = (h - bar - pad * (rows + 1)) / rows;
  tiles.forEach(([bg, skin, shirt], i) => {
    const x = pad + (i % cols) * (tw + pad);
    const y = pad + Math.floor(i / cols) * (th + pad);
    const grad = ctx.createLinearGradient(x, y, x, y + th);
    grad.addColorStop(0, bg);
    grad.addColorStop(1, "#15181d");
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.roundRect(x, y, tw, th, 12); ctx.fill();
    ctx.save();
    ctx.beginPath(); ctx.roundRect(x, y, tw, th, 12); ctx.clip();
    const cx = x + tw / 2;
    ctx.fillStyle = shirt;
    ctx.beginPath(); ctx.ellipse(cx, y + th + 10, tw * 0.28, th * 0.38, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = skin;
    ctx.beginPath(); ctx.arc(cx, y + th * 0.46, th * 0.17, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.beginPath(); ctx.roundRect(x + 10, y + th - 30, 70 + (i % 3) * 18, 20, 10); ctx.fill();
    if (i === 1) {
      ctx.strokeStyle = "#4ade80";
      ctx.lineWidth = 5;
      ctx.beginPath(); ctx.roundRect(x + 2.5, y + 2.5, tw - 5, th - 5, 11); ctx.stroke();
    }
  });
  const by = h - bar / 2 - 4;
  [-96, -32, 32].forEach((dx) => {
    ctx.fillStyle = "#2b2f36";
    ctx.beginPath(); ctx.arc(w / 2 + dx, by, 20, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#c9ced6";
    ctx.beginPath(); ctx.arc(w / 2 + dx, by, 6, 0, Math.PI * 2); ctx.fill();
  });
  ctx.fillStyle = "#e5484d";
  ctx.beginPath(); ctx.roundRect(w / 2 + 64, by - 20, 64, 40, 20); ctx.fill();
}

/** A calm laptop screen: a sidebar and a few lines of a document. */
function drawLaptop(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#eef0f2";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#d9dde2";
  ctx.fillRect(0, 0, w * 0.26, h);
  ctx.fillStyle = "#b9c0c8";
  for (let i = 0; i < 5; i += 1) ctx.fillRect(w * 0.34, 14 + i * 12, w * (0.5 - (i % 2) * 0.15), 5);
  ctx.fillStyle = C.clay;
  ctx.fillRect(w * 0.34, h - 20, 26, 8);
}

/** Walnut door front with vertical fluting and a darker rim. */
function drawFluting(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = C.walnutLight;
  ctx.fillRect(0, 0, w, h);
  for (let x = 4; x < w; x += 10) {
    ctx.fillStyle = "rgba(30,18,10,0.28)";
    ctx.fillRect(x, 0, 2, h);
    ctx.fillStyle = "rgba(255,230,200,0.08)";
    ctx.fillRect(x + 3, 0, 3, h);
  }
  ctx.strokeStyle = "rgba(30,18,10,0.55)";
  ctx.lineWidth = 4;
  ctx.strokeRect(0, 0, w, h);
}

/** An abstract print: a clay sun over a sage hill on cream paper. */
function drawPrint(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#f4efe6";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = C.clay;
  ctx.beginPath(); ctx.arc(w * 0.58, h * 0.4, w * 0.22, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = C.sage;
  ctx.beginPath(); ctx.ellipse(w * 0.35, h * 0.95, w * 0.6, h * 0.35, 0, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "#2f3d56";
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(w * 0.12, h * 0.2); ctx.lineTo(w * 0.4, h * 0.2); ctx.stroke();
}

/** Oat wool rug: a soft weave, a clay border band and a fine charcoal line. */
function drawRug(ctx: Ctx, w: number, h: number): void {
  const rand = lcg(777);
  ctx.fillStyle = "#e6dccb";
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 6000; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.16)" : "rgba(120,96,70,0.09)";
    ctx.fillRect(rand() * w, rand() * h, 2, 2);
  }
  // A faint diamond lattice across the field.
  ctx.strokeStyle = "rgba(150,120,90,0.14)";
  ctx.lineWidth = 2;
  for (let x = -h; x < w + h; x += 48) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x + h, h); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x + h, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  ctx.strokeStyle = C.clay;
  ctx.lineWidth = 16;
  ctx.strokeRect(22, 22, w - 44, h - 44);
  ctx.strokeStyle = "#3a3632";
  ctx.lineWidth = 3;
  ctx.strokeRect(44, 44, w - 88, h - 88);
  ctx.strokeStyle = "#e6dccb";
  ctx.lineWidth = 12;
  ctx.strokeRect(6, 6, w - 12, h - 12);
}

const faces = {
  board: () => canvasMaterial("prop:teamBoard", 1024, 478, drawTeamBoard, { fallback: "#fbfbf8", roughness: 0.5 }),
  feltClay: () => canvasMaterial("team:felt-clay", 128, 256, drawFelt(C.clay), { fallback: C.clay, roughness: 1 }),
  feltSage: () => canvasMaterial("team:felt-sage", 128, 256, drawFelt(C.sage), { fallback: C.sage, roughness: 1 }),
  pinboard: () => canvasMaterial("team:pinboard", 256, 512, drawPinboard, { fallback: C.oat, roughness: 0.95 }),
  call: () => canvasMaterial("team:call", 1024, 576, drawCall, { glow: 0.9, fallback: "#1b2230", roughness: 0.35 }),
  laptop: () => canvasMaterial("team:laptop", 160, 100, drawLaptop, { glow: 0.55, fallback: "#dde2e8", roughness: 0.4 }),
  fluting: () => canvasMaterial("team:fluting", 128, 128, drawFluting, { fallback: C.walnutLight, roughness: 0.6 }),
  print: () => canvasMaterial("team:print", 128, 160, drawPrint, { fallback: "#f4efe6", roughness: 0.8 }),
  rug: () => canvasMaterial("team:rug", 512, 384, drawRug, { fallback: "#e6dccb", roughness: 1 }),
};

// ---------------------------------------------------------------------------
// Shared geometry
// ---------------------------------------------------------------------------

const TGEO = {
  cyl: new CylinderGeometry(1, 1, 1, 20),
  sphere: new SphereGeometry(1, 16, 12),
  planter: new CylinderGeometry(0.27, 0.2, 0.52, 28),
  vase: new CylinderGeometry(0.045, 0.07, 0.24, 20),
};

function Cyl({ radius, height, position, material, rotation, cast = true }: {
  radius: number; height: number; position: Vec3; material: MeshStandardMaterial; rotation?: Vec3; cast?: boolean;
}) {
  return <mesh geometry={TGEO.cyl} material={material} position={position} rotation={rotation} scale={[radius, height, radius]} castShadow={cast} receiveShadow />;
}

function Panel({ size, position, material, rotation }: { size: [number, number]; position: Vec3; material: MeshStandardMaterial; rotation?: Vec3 }) {
  return <mesh position={position} rotation={rotation} material={material}><planeGeometry args={size} /></mesh>;
}

/** The slat wall's walnut slats, merged into one geometry: one draw call for 80 slats. */
const WALL = { w: FURNITURE_SIZE.teamWall.w, pitch: 0.1 };
const SLAT_GEOMETRY = (() => {
  const count = Math.floor((WALL.w - 0.1) / WALL.pitch);
  const parts = Array.from({ length: count }, (_, i) =>
    new BoxGeometry(0.045, 1.9, 0.035).translate(-((count - 1) * WALL.pitch) / 2 + i * WALL.pitch, 1.03, -0.0175));
  const merged = mergeGeometries(parts, false);
  parts.forEach((p) => p.dispose());
  return merged;
})();

/**
 * A fiddle-leaf fig's leaves, spiralling up the stem on the golden angle and
 * lifting towards the top; merged per leaf tone, three draw calls a plant.
 */
const LEAF_GEOMETRIES: BufferGeometry[] = (() => {
  const base = new SphereGeometry(1, 10, 6);
  const tones: BufferGeometry[][] = [[], [], []];
  const matrix = new Matrix4(), rotation = new Quaternion(), euler = new Euler(0, 0, 0, "YXZ");
  const at = new Vector3(), scale = new Vector3();
  const count = 38;
  for (let i = 0; i < count; i += 1) {
    const t = i / (count - 1);
    const angle = i * 2.39996;
    const half = 0.125 - 0.035 * t;
    const radius = 0.05 + 0.09 * Math.sin(Math.PI * (0.2 + t * 0.7));
    const lift = -0.2 + 0.75 * t;
    const reach = radius + half * Math.cos(lift);
    at.set(Math.sin(angle) * reach, 0.98 + t * 0.9 + half * Math.sin(lift), Math.cos(angle) * reach);
    euler.set(-lift, angle, ((i % 3) - 1) * 0.35);
    rotation.setFromEuler(euler);
    scale.set(half * 0.72, 0.012, half);
    matrix.compose(at, rotation, scale);
    tones[i % 3].push(base.clone().applyMatrix4(matrix));
  }
  base.dispose();
  return tones.map((parts) => {
    const merged = mergeGeometries(parts, false);
    parts.forEach((p) => p.dispose());
    return merged;
  });
})();

// ---------------------------------------------------------------------------
// Furniture
// ---------------------------------------------------------------------------

/** An open laptop; the hinge is on its -z side and the screen faces +z. */
function Laptop({ position, rotationY = 0 }: { position: Vec3; rotationY?: number }) {
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      <Box size={[0.32, 0.012, 0.22]} position={[0, 0.006, 0]} material={TM.alu} />
      <Box size={[0.26, 0.002, 0.09]} position={[0, 0.013, 0.02]} material={MAT.keyboard} cast={false} />
      <group position={[0, 0.012, -0.108]} rotation={[-0.28, 0, 0]}>
        <Box size={[0.32, 0.21, 0.008]} position={[0, 0.105, 0]} material={TM.alu} />
        <Panel size={[0.3, 0.19]} position={[0, 0.105, 0.0045]} material={faces.laptop()} />
      </group>
    </group>
  );
}

/**
 * Solid walnut conference table: a thick top with a flush power rail down the
 * middle, a black cable-tray spine under it fed by a riser from the floor, and
 * two slab legs on steel shoes. On top: laptops for either side, notebooks, a
 * speakerphone puck, a carafe with glasses and a bud vase.
 */
function TeamTable() {
  return (
    <group>
      {/* Legs and the cable spine. */}
      {[-1.42, 1.42].map((x) => (
        <group key={x}>
          <Box size={[0.08, 0.68, 1.16]} position={[x, 0.37, 0]} material={TM.walnutDark} />
          <Box size={[0.12, 0.03, 1.24]} position={[x, 0.015, 0]} material={MAT.steel} />
        </group>
      ))}
      <Box size={[2.76, 0.07, 0.18]} position={[0, 0.665, 0]} material={MAT.steel} />
      <Box size={[0.1, 0.63, 0.1]} position={[0, 0.315, 0]} material={MAT.steel} />
      <Box size={[0.3, 0.012, 0.2]} position={[0, 0.006, 0]} material={MAT.steel} cast={false} />
      {/* The top and its inset power rail with five sockets. */}
      <Rounded size={[3.56, 0.056, 1.52]} radius={0.025} position={[0, 0.732, 0]} material={TM.walnut} />
      <Box size={[2.0, 0.004, 0.12]} position={[0, 0.7605, 0]} material={MAT.monitor} cast={false} />
      {[-0.8, -0.4, 0, 0.4, 0.8].map((x) => (
        <Box key={x} size={[0.09, 0.004, 0.06]} position={[x, 0.7625, 0]} material={TM.alu} cast={false} />
      ))}
      {/* Laptops face the chairs on either side; notebooks with pens. */}
      <Laptop position={[-0.55, 0.76, 0.42]} />
      <Laptop position={[0.55, 0.76, -0.42]} rotationY={Math.PI} />
      <Box size={[0.22, 0.01, 0.3]} position={[0.62, 0.765, 0.44]} material={MAT.mug} cast={false} />
      <Box size={[0.012, 0.008, 0.16]} position={[0.77, 0.775, 0.44]} material={TM.books[3]} cast={false} />
      <Box size={[0.22, 0.01, 0.3]} position={[-0.62, 0.765, -0.44]} material={TM.books[1]} cast={false} />
      {/* Speakerphone puck with a lit ring. */}
      <Cyl radius={0.1} height={0.028} position={[-1.3, 0.774, 0]} material={MAT.monitor} />
      <Cyl radius={0.06} height={0.004} position={[-1.3, 0.79, 0]} material={TM.led} cast={false} />
      {/* Carafe and two glasses, a bud vase with a sprig. */}
      <Cyl radius={0.05} height={0.2} position={[1.25, 0.86, 0.14]} material={TM.glass} cast={false} />
      <Cyl radius={0.045} height={0.13} position={[1.25, 0.825, 0.14]} material={TM.water} cast={false} />
      <Cyl radius={0.032} height={0.09} position={[1.08, 0.805, -0.12]} material={TM.glass} cast={false} />
      <Cyl radius={0.032} height={0.09} position={[1.4, 0.805, -0.14]} material={TM.glass} cast={false} />
      <Cyl radius={0.028} height={0.1} position={[0.05, 0.81, 0.24]} material={TM.ceramic} />
      <mesh geometry={GEO.blob} material={MAT.leaf} position={[0.05, 0.9, 0.24]} scale={[0.05, 0.05, 0.05]} castShadow />
    </group>
  );
}

/**
 * The team whiteboard, hung on the slat wall: a white face in a thin walnut
 * frame, an aluminium marker tray and a picture light washing it from above.
 * The face stays where `TeamBoardFace` expects it.
 */
function TeamBoard() {
  return (
    <group>
      <Rounded size={[2.52, 1.24, 0.05]} radius={0.015} position={[0, 1.25, -0.04]} material={TM.walnutLight} />
      <Panel size={[2.4, 1.12]} position={[0, 1.25, -0.0135]} material={faces.board()} />
      <Box size={[1.7, 0.025, 0.07]} position={[0, 0.615, -0.01]} material={TM.alu} />
      {[TM.books[0], TM.books[3], TM.books[2]].map((material, i) => (
        <Box key={i} size={[0.13, 0.02, 0.02]} position={[-0.25 + i * 0.18, 0.637, 0]} material={material} cast={false} />
      ))}
      {/* Picture light: two slim arms and a bar with a warm strip underneath. */}
      {[-0.6, 0.6].map((x) => <Box key={x} size={[0.015, 0.015, 0.12]} position={[x, 1.9, -0.02]} material={MAT.steel} cast={false} />)}
      <Rounded size={[1.6, 0.035, 0.06]} radius={0.012} position={[0, 1.9, 0.05]} material={MAT.steel} />
      <Box size={[1.54, 0.006, 0.03]} position={[0, 1.8795, 0.05]} material={TM.led} cast={false} />
    </group>
  );
}

/**
 * The north wall: a walnut slat wall under a warm cove light. West of the
 * board three panels — clay felt, a pinboard of sticky notes, sage felt; east
 * of it a video-conference display with its camera bar on top.
 */
function TeamWall() {
  return (
    <group>
      <Box size={[WALL.w, 1.98, 0.025]} position={[0, 1.01, -0.0475]} material={TM.walnutDark} />
      <mesh geometry={SLAT_GEOMETRY} material={TM.walnut} castShadow receiveShadow />
      <Box size={[WALL.w, 0.08, 0.1]} position={[0, 0.04, -0.01]} material={TM.walnutDark} />
      <Box size={[WALL.w, 0.04, 0.1]} position={[0, 2.0, -0.01]} material={TM.walnutDark} />
      <Box size={[WALL.w - 0.16, 0.008, 0.02]} position={[0, 1.976, 0.028]} material={TM.led} cast={false} />
      {/* Felt, pinboard, felt. */}
      {[faces.feltClay(), faces.pinboard(), faces.feltSage()].map((material, i) => (
        <group key={i} position={[-3.44 + i * 0.74, 1.12, 0.012]}>
          <Box size={[0.68, 1.36, 0.024]} position={[0, 0, 0]} material={material} />
        </group>
      ))}
      {/* The display and the camera bar. */}
      <Rounded size={[2.02, 1.16, 0.035]} radius={0.012} position={[2.7, 1.36, 0.0225]} material={MAT.monitor} />
      <Panel size={[1.96, 1.1]} position={[2.7, 1.36, 0.0405]} material={faces.call()} />
      <Rounded size={[0.62, 0.055, 0.05]} radius={0.015} position={[2.7, 1.975, 0.035]} material={MAT.monitor} />
      <mesh position={[2.7, 1.975, 0.0605]} material={TM.lens}><circleGeometry args={[0.016, 20]} /></mesh>
      <mesh position={[2.79, 1.975, 0.0605]} material={TM.led}><circleGeometry args={[0.004, 8]} /></mesh>
    </group>
  );
}

/**
 * Low walnut credenza on black steel legs: four fluted doors with brass pulls
 * under a travertine top carrying books, a leaning print, a small clay
 * sculpture and a ceramic vase with branches.
 */
function Credenza() {
  return (
    <group>
      {[-1.0, 1.0].flatMap((x) => [-0.17, 0.17].map((z) => (
        <Box key={`${x}:${z}`} size={[0.03, 0.12, 0.03]} position={[x, 0.06, z]} material={MAT.steel} />
      )))}
      <Box size={[2.16, 0.46, 0.42]} position={[0, 0.35, -0.01]} material={TM.walnutDark} />
      {[-0.81, -0.27, 0.27, 0.81].map((x) => (
        <Panel key={x} size={[0.52, 0.42]} position={[x, 0.35, 0.2005]} material={faces.fluting()} />
      ))}
      {[-0.56, -0.02, 0.02, 0.56].map((x) => <Box key={x} size={[0.012, 0.12, 0.014]} position={[x, 0.4, 0.212]} material={TM.brass} cast={false} />)}
      <Box size={[2.2, 0.03, 0.46]} position={[0, 0.595, 0]} material={TM.travertine} />
      {/* Books upright between two bookends. */}
      {[0.045, 0.035, 0.05, 0.04, 0.038, 0.048].reduce<{ x: number; w: number; h: number; i: number }[]>((acc, w, i) => {
        const prev = acc[acc.length - 1];
        acc.push({ x: prev ? prev.x + prev.w / 2 + w / 2 + 0.004 : -0.96, w, h: 0.2 + ((i * 5) % 4) * 0.02, i });
        return acc;
      }, []).map((b) => (
        <Box key={b.i} size={[b.w, b.h, 0.17]} position={[b.x, 0.61 + b.h / 2, -0.02]} material={TM.books[b.i % TM.books.length]} />
      ))}
      <Box size={[0.012, 0.14, 0.12]} position={[-0.992, 0.68, -0.02]} material={MAT.steel} />
      <Box size={[0.012, 0.14, 0.12]} position={[-0.68, 0.68, -0.02]} material={MAT.steel} />
      {/* A stack of books under a clay sphere. */}
      <Box size={[0.28, 0.035, 0.2]} position={[-0.32, 0.6275, 0.02]} material={TM.books[3]} />
      <Box size={[0.25, 0.03, 0.18]} position={[-0.31, 0.66, 0.02]} material={TM.books[1]} />
      <Box size={[0.23, 0.04, 0.17]} position={[-0.33, 0.695, 0.02]} material={TM.books[0]} />
      <mesh geometry={TGEO.sphere} material={TM.clay} position={[-0.32, 0.765, 0.02]} scale={0.05} castShadow />
      {/* A print leaning on the wall. */}
      <group position={[0.3, 0.61, -0.12]} rotation={[-0.12, 0, 0]}>
        <Box size={[0.3, 0.38, 0.02]} position={[0, 0.19, 0]} material={TM.walnutDark} />
        <Panel size={[0.25, 0.33]} position={[0, 0.19, 0.0105]} material={faces.print()} />
      </group>
      {/* Ceramic vase with three branches. */}
      <mesh geometry={TGEO.vase} material={TM.ceramic} position={[0.86, 0.73, 0]} castShadow receiveShadow />
      {[-0.35, 0.05, 0.4].map((tilt, i) => (
        <group key={i} position={[0.86, 0.84, 0]} rotation={[tilt * 0.4, i * 1.2, tilt]}>
          <Box size={[0.008, 0.26, 0.008]} position={[0, 0.13, 0]} material={TM.trunk} cast={false} />
          <mesh geometry={GEO.blob} material={TM.leaves[i]} position={[0, 0.24, 0]} scale={[0.045, 0.03, 0.045]} castShadow />
          <mesh geometry={GEO.blob} material={TM.leaves[(i + 1) % 3]} position={[0, 0.16, 0]} scale={[0.035, 0.022, 0.035]} castShadow />
        </group>
      ))}
    </group>
  );
}

/** A fiddle-leaf fig in a tapered clay planter, nearly two metres tall. */
function DesignerPlant() {
  return (
    <group>
      <mesh geometry={TGEO.planter} material={TM.terracotta} position={[0, 0.26, 0]} castShadow receiveShadow />
      <Cyl radius={0.275} height={0.03} position={[0, 0.52, 0]} material={TM.terracotta} />
      <Cyl radius={0.25} height={0.01} position={[0, 0.531, 0]} material={TM.soil} cast={false} />
      <Cyl radius={0.022} height={1.3} position={[0, 1.18, 0]} material={TM.trunk} />
      <Cyl radius={0.016} height={0.7} position={[0.06, 1.0, 0.02]} rotation={[0, 0, -0.18]} material={TM.trunk} />
      {LEAF_GEOMETRIES.map((geometry, i) => (
        <mesh key={i} geometry={geometry} material={TM.leaves[i]} castShadow receiveShadow />
      ))}
    </group>
  );
}

/** A wool rug with a clay border, sized per instance. */
function TeamRug({ w, d }: { w: number; d: number }) {
  return <Box size={[w, 0.016, d]} position={[0, 0.008, 0]} material={faces.rug()} cast={false} />;
}

/** Renderers of the team room's furniture kinds; merged into OfficeProps' exhaustive table. */
export const TEAM_RENDERERS = {
  meetingTable: () => <TeamTable />,
  teamBoard: () => <TeamBoard />,
  teamWall: () => <TeamWall />,
  credenza: () => <Credenza />,
  designerPlant: () => <DesignerPlant />,
  teamRug: ({ item }: { item: Furniture }) => {
    const size = item.size ?? FURNITURE_SIZE.teamRug;
    return <TeamRug w={size.w} d={size.d} />;
  },
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;

// ---------------------------------------------------------------------------
// Room fittings: pendants and their light, the frosted band on the glass
// ---------------------------------------------------------------------------

/** Height of the frosted band's centre on the glass, and its height. */
const BAND = { y: 1.2, h: 0.46 };

/**
 * What hangs in the room rather than stands in it: two linear pendants over
 * the table with one warm light between them, and a frosted privacy band on
 * the glass front either side of the door.
 */
export const TeamRoomFittings = memo(function TeamRoomFittings({ room, table }: { room: Room; table: Furniture }) {
  const door = room.doors.find((d) => d.side === "south");
  const bands = door
    ? [[room.minX, door.at - door.width / 2], [door.at + door.width / 2, room.maxX]]
    : [[room.minX, room.maxX]];
  return (
    <group>
      <group position={[table.x, 0, table.z]} rotation={[0, table.rotationY, 0]}>
        {[-0.78, 0.78].map((x) => (
          <group key={x} position={[x, 0, 0]}>
            <Rounded size={[1.3, 0.04, 0.07]} radius={0.015} position={[0, 1.97, 0]} material={MAT.steel} />
            <Box size={[1.24, 0.006, 0.045]} position={[0, 1.948, 0]} material={TM.led} cast={false} />
            {[-0.55, 0.55].map((cx) => <Box key={cx} size={[0.006, 0.8, 0.006]} position={[cx, 2.39, 0]} material={MAT.steel} cast={false} />)}
          </group>
        ))}
        <pointLight position={[0, 1.8, 0]} color="#ffd9ad" intensity={5} distance={5.5} decay={2} />
      </group>
      {bands.map(([from, to]) => {
        const length = to - from - 0.08;
        if (length <= 0) return null;
        const x = (from + to) / 2;
        return (
          <group key={from} position={[x, 0, room.maxZ]}>
            <Box size={[length, BAND.h, 0.03]} position={[0, BAND.y, 0]} material={TM.frosted} cast={false} />
            <Box size={[length, 0.012, 0.032]} position={[0, BAND.y + BAND.h / 2 + 0.03, 0]} material={TM.frostedLine} cast={false} />
          </group>
        );
      })}
    </group>
  );
});
