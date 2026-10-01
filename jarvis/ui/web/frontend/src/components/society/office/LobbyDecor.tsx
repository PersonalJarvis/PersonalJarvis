/**
 * The agents floor's lobby as the arrival at a startup HQ: a brand wall of
 * pale oak slats with the Jarvis ghost standing off a sage plaster panel in a
 * ring of warm light, a waiting lounge (boucle sofa, oak-and-cognac armchairs,
 * a travertine coffee table with magazines, a marble side table and a tripod
 * lamp on a wool rug), the Agent board as a standing touch-screen totem, an
 * entrance mat at the elevator, olive trees in stone planters and a lit
 * vitrine of awards.
 *
 * Its own identity next to the coding floor's walnut and amber: light
 * Scandinavian oak, sage, linen and brass. Same rules as OfficeProps: every
 * furniture piece is built in local space centred on the origin, front facing
 * +z, inside its `FURNITURE_SIZE` box. Repeats (slats, leaves) are merged into
 * one geometry per material, and canvas faces are drawn once and shared.
 */
import { memo } from "react";
import {
  AdditiveBlending, BoxGeometry, BufferGeometry, ConeGeometry, CylinderGeometry, DoubleSide, Euler, ExtrudeGeometry, Matrix4,
  MeshStandardMaterial, Path, Quaternion, Shape, SphereGeometry, TorusGeometry, Vector3,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { cachedCanvasTexture, canvasMaterial } from "./canvasMaterials";
import { Box, GEO, matte, Rounded } from "./OfficeFurniture";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind } from "./officeLayout";

type Vec3 = [number, number, number];
type Ctx = CanvasRenderingContext2D;

// ---------------------------------------------------------------------------
// Materials: pale oak, sage, linen, brass, boucle and cognac leather.
// ---------------------------------------------------------------------------

const C = {
  oak: "#d2b286",
  oakLight: "#e2c9a2",
  oakDark: "#a88660",
  sage: "#9fae93",
  sageDeep: "#74866c",
  linen: "#ece5d7",
  boucle: "#efe9de",
  cognac: "#a8653a",
  ink: "#23262b",
  stone: "#d9d3c7",
  travertine: "#e7ddcc",
  marble: "#f1eeea",
  clay: "#c4775a",
  led: "#ffe6c4",
  eye: "#ffd46b",
} as const;

const LM = {
  oak: matte(C.oak, { roughness: 0.6 }),
  oakLight: matte(C.oakLight, { roughness: 0.6 }),
  oakDark: matte(C.oakDark, { roughness: 0.6 }),
  sage: matte(C.sage, { roughness: 0.95 }),
  sageDeep: matte(C.sageDeep, { roughness: 0.9 }),
  linen: matte(C.linen, { roughness: 1 }),
  boucle: matte(C.boucle, { roughness: 1 }),
  cognac: matte(C.cognac, { roughness: 0.55 }),
  ink: matte(C.ink, { roughness: 0.35, metalness: 0.15 }),
  stone: matte(C.stone, { roughness: 0.9 }),
  travertine: matte(C.travertine, { roughness: 0.45 }),
  marble: matte(C.marble, { roughness: 0.25 }),
  clay: matte(C.clay, { roughness: 0.8 }),
  brass: matte("#c7a15e", { roughness: 0.3, metalness: 0.7 }),
  bronze: matte("#5a4a3a", { roughness: 0.4, metalness: 0.5 }),
  charcoal: matte("#2b2f33", { roughness: 0.5, metalness: 0.1 }),
  ceramic: matte("#f3efe8", { roughness: 0.35 }),
  soil: matte("#3b2f26"),
  pebbles: matte("#bdb3a3", { roughness: 1 }),
  bark: matte("#7d6b58", { roughness: 0.95 }),
  // Lit from within, so they read as light in any scene lighting.
  led: new MeshStandardMaterial({ color: C.led, emissive: C.led, emissiveIntensity: 1.3, toneMapped: false }),
  eye: new MeshStandardMaterial({ color: C.eye, emissive: C.eye, emissiveIntensity: 1.4, toneMapped: false }),
  glass: new MeshStandardMaterial({ color: "#e8f2f4", transparent: true, opacity: 0.22, roughness: 0.05, depthWrite: false }),
  crystal: new MeshStandardMaterial({ color: "#dff0f6", transparent: true, opacity: 0.6, roughness: 0.05, metalness: 0.1 }),
  shade: new MeshStandardMaterial({ color: "#f3ead8", emissive: "#ffe2b8", emissiveIntensity: 0.35, roughness: 1, side: DoubleSide }),
  olive: ["#8c9b76", "#6e805f", "#a7b390"].map((c) => matte(c, { flatShading: true, roughness: 0.8 })),
  mags: ["#c4775a", "#2f3d56", "#e9dcc4", "#74866c", "#d9a441"].map((c) => matte(c, { roughness: 0.6 })),
};

// ---------------------------------------------------------------------------
// The ghost: the mascot's own outline (MascotGigi's body path), eyes cut out
// ---------------------------------------------------------------------------

/** SVG box of the mascot: the figure spans x 58–198 and y 36–208. */
const GHOST_SVG = { cx: 128, hem: 208, height: 172 };
/** Eye centres in the ghost's local space (hem at y = 0, 1 unit tall). */
const GHOST_EYES = [102, 154].map((x) => ({ x: (x - GHOST_SVG.cx) / GHOST_SVG.height, y: (GHOST_SVG.hem - 108) / GHOST_SVG.height }));
const GHOST_EYE = { rx: 10 / GHOST_SVG.height, ry: 14 / GHOST_SVG.height };

function ghostPoint(x: number, y: number): [number, number] {
  return [(x - GHOST_SVG.cx) / GHOST_SVG.height, (GHOST_SVG.hem - y) / GHOST_SVG.height];
}

/** The ghost's silhouette, one unit tall with its hem on y = 0 and centred on x = 0. */
function ghostShape(): Shape {
  const shape = new Shape();
  shape.moveTo(...ghostPoint(58, 90));
  shape.quadraticCurveTo(...ghostPoint(58, 36), ...ghostPoint(128, 36));
  shape.quadraticCurveTo(...ghostPoint(198, 36), ...ghostPoint(198, 90));
  for (const [x, y] of [[198, 208], [180, 186], [160, 208], [140, 186], [120, 208], [100, 186], [80, 208], [58, 186]]) {
    shape.lineTo(...ghostPoint(x, y));
  }
  shape.closePath();
  for (const eye of GHOST_EYES) {
    const hole = new Path();
    hole.absellipse(eye.x, eye.y, GHOST_EYE.rx, GHOST_EYE.ry, 0, Math.PI * 2, true);
    shape.holes.push(hole);
  }
  return shape;
}

/** Extruded ghost, 1 unit tall and 0.06 deep (plus a soft bevel), its back face on z = 0. */
export const GHOST_GEOMETRY = (() => {
  const geometry = new ExtrudeGeometry(ghostShape(), {
    depth: 0.06, bevelEnabled: true, bevelThickness: 0.01, bevelSize: 0.008, bevelSegments: 2, curveSegments: 18,
  });
  geometry.translate(0, 0, 0.01);
  return geometry;
})();

/** The ghost outline as a canvas path (for the screen, mat and rug faces). */
function traceGhost(ctx: Ctx, cx: number, top: number, height: number): void {
  const s = height / GHOST_SVG.height;
  const p = (x: number, y: number): [number, number] => [cx + (x - GHOST_SVG.cx) * s, top + (y - 36) * s];
  ctx.beginPath();
  ctx.moveTo(...p(58, 90));
  ctx.quadraticCurveTo(...p(58, 36), ...p(128, 36));
  ctx.quadraticCurveTo(...p(198, 36), ...p(198, 90));
  for (const [x, y] of [[198, 208], [180, 186], [160, 208], [140, 186], [120, 208], [100, 186], [80, 208], [58, 186]]) ctx.lineTo(...p(x, y));
  ctx.closePath();
}

/** The ghost, `height` px tall with its top at `top`, centred on `cx`: a filled body with filled eyes. */
export function drawGhost(ctx: Ctx, cx: number, top: number, height: number, body: string, eyes: string): void {
  traceGhost(ctx, cx, top, height);
  ctx.fillStyle = body;
  ctx.fill();
  const s = height / GHOST_SVG.height;
  ctx.fillStyle = eyes;
  for (const x of [102, 154]) {
    ctx.beginPath();
    ctx.ellipse(cx + (x - GHOST_SVG.cx) * s, top + (108 - 36) * s, 10 * s, 14 * s, 0, 0, Math.PI * 2);
    ctx.fill();
  }
}

// ---------------------------------------------------------------------------
// Canvas faces
// ---------------------------------------------------------------------------

/** Deterministic pseudo-random numbers, so drawn patterns never change between loads. */
function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

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

/** The brand wall's wordmark and strapline, white on transparent: backlit cream letters on the sage panel. */
function drawWordmark(ctx: Ctx, w: number, h: number): void {
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  spaced(ctx, 6);
  ctx.font = "700 100px Inter, 'Segoe UI', system-ui, sans-serif";
  ctx.fillText("PERSONAL JARVIS", w / 2 + 3, h * 0.36, w - 24);
  spaced(ctx, 10);
  ctx.font = "500 34px Inter, 'Segoe UI', system-ui, sans-serif";
  ctx.fillText("AGENT HEADQUARTERS", w / 2 + 5, h * 0.8, w * 0.44);
  ctx.fillRect(w * 0.12, h * 0.8 - 2, w * 0.14, 4);
  ctx.fillRect(w * 0.74, h * 0.8 - 2, w * 0.14, 4);
}

/** A soft round glow, bright in the middle and gone at the rim (drawn additively behind the ghost). */
function drawGlow(ctx: Ctx, w: number, h: number): void {
  const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
  g.addColorStop(0, "rgba(255,226,180,0.55)");
  g.addColorStop(0.55, "rgba(255,214,160,0.18)");
  g.addColorStop(1, "rgba(255,214,160,0)");
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
}

/** The Agent board totem's portrait screen: the ghost header, team counts, agent cards and the add button. */
function drawTotem(ctx: Ctx, w: number, h: number): void {
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, "#1f2925");
  bg.addColorStop(1, "#121816");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);
  // Header: the ghost, the title and a sage rule.
  drawGhost(ctx, 78, 46, 70, "#f1ebe1", "#1f2925");
  ctx.fillStyle = "#f1ebe1";
  ctx.textBaseline = "middle";
  ctx.font = "700 44px Inter, 'Segoe UI', system-ui, sans-serif";
  ctx.fillText("Agent board", 138, 72);
  ctx.fillStyle = "rgba(241,235,225,0.55)";
  ctx.font = "500 24px Inter, 'Segoe UI', system-ui, sans-serif";
  ctx.fillText("Tap to manage your team", 138, 110);
  ctx.fillStyle = C.sage;
  ctx.fillRect(40, 148, w - 80, 3);
  // Three counts: working, waiting, idle.
  const counts: [string, string][] = [["#4ade80", "4"], ["#fbbf24", "1"], ["#aab3c2", "3"]];
  counts.forEach(([colour, n], i) => {
    const x = 40 + i * ((w - 80) / 3);
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    roundRect(ctx, x + 4, 172, (w - 80) / 3 - 8, 84, 14);
    ctx.fill();
    ctx.fillStyle = colour;
    ctx.beginPath(); ctx.arc(x + 34, 214, 9, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#f1ebe1";
    ctx.font = "700 40px Inter, 'Segoe UI', system-ui, sans-serif";
    ctx.fillText(n, x + 56, 216);
  });
  // Agent cards: avatar, name, role, status.
  const avatars = ["#c4775a", "#74866c", "#7c8fa8", "#d9a441", "#8b6246", "#9fae93", "#b56a4c"];
  const status = ["#4ade80", "#4ade80", "#fbbf24", "#aab3c2", "#4ade80", "#aab3c2", "#4ade80"];
  avatars.forEach((colour, i) => {
    const y = 286 + i * 86;
    ctx.fillStyle = i === 0 ? "rgba(159,174,147,0.18)" : "rgba(255,255,255,0.05)";
    roundRect(ctx, 40, y, w - 80, 72, 14);
    ctx.fill();
    ctx.fillStyle = colour;
    ctx.beginPath(); ctx.arc(80, y + 36, 22, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "rgba(241,235,225,0.9)";
    ctx.fillRect(118, y + 20, 150 + ((i * 47) % 110), 13);
    ctx.fillStyle = "rgba(241,235,225,0.38)";
    ctx.fillRect(118, y + 44, 90 + ((i * 31) % 70), 9);
    ctx.fillStyle = status[i];
    ctx.beginPath(); ctx.arc(w - 72, y + 36, 9, 0, Math.PI * 2); ctx.fill();
  });
  // The add button.
  ctx.fillStyle = C.sage;
  roundRect(ctx, 40, h - 108, w - 80, 68, 34);
  ctx.fill();
  ctx.fillStyle = "#16201b";
  ctx.font = "700 32px Inter, 'Segoe UI', system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("+  New agent", w / 2, h - 73);
  ctx.textAlign = "left";
}

/** Charcoal coir mat: a bristly weave, an oat border band and the ghost in the middle. */
function drawMat(ctx: Ctx, w: number, h: number): void {
  const rand = lcg(4242);
  ctx.fillStyle = "#3b3a37";
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 5000; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(255,240,215,0.07)" : "rgba(0,0,0,0.16)";
    ctx.fillRect(rand() * w, rand() * h, 2, 3);
  }
  ctx.strokeStyle = "#cdbb98";
  ctx.lineWidth = 10;
  ctx.strokeRect(18, 18, w - 36, h - 36);
  drawGhost(ctx, w / 2, h / 2 - w * 0.2, w * 0.4, "#cdbb98", "#3b3a37");
}

/** Oat wool rug: a soft weave, a sage band and a fine clay line near the edge. */
function drawLobbyRug(ctx: Ctx, w: number, h: number): void {
  const rand = lcg(9001);
  ctx.fillStyle = "#e8dfcf";
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 7000; i += 1) {
    ctx.fillStyle = rand() > 0.5 ? "rgba(255,255,255,0.16)" : "rgba(120,100,76,0.08)";
    ctx.fillRect(rand() * w, rand() * h, 2, 2);
  }
  // A sage band inside the edge, then a fine clay line.
  ctx.strokeStyle = "rgba(116,134,108,0.55)";
  ctx.lineWidth = 18;
  ctx.strokeRect(44, 44, w - 88, h - 88);
  ctx.strokeStyle = C.clay;
  ctx.lineWidth = 4;
  ctx.strokeRect(26, 26, w - 52, h - 52);
}

/** A framed certificate: cream paper, a sage seal and lines of text. */
function drawCertificate(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#f6f1e7";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "#c7a15e";
  ctx.lineWidth = 4;
  ctx.strokeRect(10, 10, w - 20, h - 20);
  ctx.fillStyle = "#2f3d56";
  ctx.fillRect(w * 0.25, h * 0.22, w * 0.5, 8);
  ctx.fillStyle = "rgba(47,61,86,0.45)";
  for (let i = 0; i < 3; i += 1) ctx.fillRect(w * 0.18, h * 0.42 + i * 16, w * 0.64, 4);
  ctx.fillStyle = C.sage;
  ctx.beginPath(); ctx.arc(w * 0.5, h * 0.8, 14, 0, Math.PI * 2); ctx.fill();
}

/** A see-through canvas material (alpha from the texture); invisible where no canvas exists. */
const alphaCache = new Map<string, MeshStandardMaterial>();
function alphaMaterial(key: string, w: number, h: number, draw: (ctx: Ctx, w: number, h: number) => void,
  extra: Partial<ConstructorParameters<typeof MeshStandardMaterial>[0]>): MeshStandardMaterial {
  let material = alphaCache.get(key);
  if (!material) {
    const map = cachedCanvasTexture(key, w, h, draw);
    material = new MeshStandardMaterial({ map, transparent: true, depthWrite: false, opacity: map ? 1 : 0, ...extra });
    alphaCache.set(key, material);
  }
  return material;
}

const faces = {
  wordmark: () => alphaMaterial("lobby:wordmark", 1024, 256, drawWordmark,
    { color: "#fbf6ec", roughness: 0.5, emissive: "#fff0d4", emissiveIntensity: 0.55 }),
  glow: () => alphaMaterial("lobby:glow", 256, 256, drawGlow, { color: "#ffffff", blending: AdditiveBlending, toneMapped: false }),
  totem: () => canvasMaterial("lobby:totem", 512, 1024, drawTotem, { glow: 0.9, fallback: "#1a2320", roughness: 0.3 }),
  mat: () => canvasMaterial("lobby:mat", 256, 384, drawMat, { fallback: "#3b3a37", roughness: 1 }),
  rug: () => canvasMaterial("lobby:rug", 512, 320, drawLobbyRug, { fallback: "#e8dfcf", roughness: 1 }),
  certificate: () => canvasMaterial("lobby:certificate", 128, 160, drawCertificate, { fallback: "#f6f1e7", roughness: 0.8 }),
};

// ---------------------------------------------------------------------------
// Shared geometry
// ---------------------------------------------------------------------------

const LGEO = {
  cyl: new CylinderGeometry(1, 1, 1, 24),
  sphere: new SphereGeometry(1, 18, 12),
  dome: new SphereGeometry(1, 18, 8, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2),
  planter: new CylinderGeometry(0.42, 0.34, 0.62, 32),
  shade: new CylinderGeometry(0.19, 0.225, 0.3, 32, 1, true),
  cup: new CylinderGeometry(0.075, 0.03, 0.13, 24),
  handle: new TorusGeometry(0.035, 0.008, 8, 16, Math.PI),
  crystal: new ConeGeometry(0.06, 0.22, 4),
  ring: new TorusGeometry(0.62, 0.012, 6, 72),
};

function Cyl({ radius, height, position, material, rotation, cast = true }: {
  radius: number; height: number; position: Vec3; material: MeshStandardMaterial; rotation?: Vec3; cast?: boolean;
}) {
  return <mesh geometry={LGEO.cyl} material={material} position={position} rotation={rotation} scale={[radius, height, radius]} castShadow={cast} receiveShadow />;
}

function Panel({ size, position, material, rotation }: { size: [number, number]; position: Vec3; material: MeshStandardMaterial; rotation?: Vec3 }) {
  return <mesh position={position} rotation={rotation} material={material}><planeGeometry args={size} /></mesh>;
}

/** The brand wall's oak slats, merged into one geometry. */
const BRAND = { w: FURNITURE_SIZE.brandWall.w, pitch: 0.08 };
const BRAND_SLATS = (() => {
  const count = Math.floor((BRAND.w - 0.06) / BRAND.pitch);
  const parts = Array.from({ length: count }, (_, i) =>
    new BoxGeometry(0.042, 2.64, 0.032).translate(-((count - 1) * BRAND.pitch) / 2 + i * BRAND.pitch, 1.44, -0.002));
  const merged = mergeGeometries(parts, false);
  parts.forEach((p) => p.dispose());
  return merged;
})();

/**
 * An olive tree's canopy: small silvery leaf clumps in a loose, lopsided
 * crown round three branch tips; merged per leaf tone, three draw calls a tree.
 */
const OLIVE_CROWNS: Vec3[] = [[-0.18, 1.9, 0.08], [0.18, 2.05, -0.06], [0.0, 1.72, -0.16], [0.04, 2.14, 0.12]];
const OLIVE_GEOMETRIES: BufferGeometry[] = (() => {
  const base = new SphereGeometry(1, 8, 5);
  const tones: BufferGeometry[][] = [[], [], []];
  const rand = lcg(31337);
  const matrix = new Matrix4(), rotation = new Quaternion(), euler = new Euler();
  const at = new Vector3(), scale = new Vector3();
  for (let i = 0; i < 168; i += 1) {
    const [cx, cy, cz] = OLIVE_CROWNS[i % OLIVE_CROWNS.length];
    const angle = rand() * Math.PI * 2;
    const r = Math.sqrt(rand()) * 0.24;
    at.set(cx + Math.cos(angle) * r, cy + (rand() - 0.35) * 0.3, cz + Math.sin(angle) * r);
    euler.set((rand() - 0.5) * 0.8, rand() * Math.PI, (rand() - 0.5) * 0.8);
    rotation.setFromEuler(euler);
    const size = 0.06 + rand() * 0.04;
    scale.set(size * 1.4, size * 0.5, size);
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

/**
 * The brand wall: pale oak slats on a linen backing between an oak plinth and
 * a cap with a warm cove light; in front a sage plaster panel between brass
 * fins carrying the ghost (ink, its eyes lit from behind) in a ring of light,
 * and the backlit wordmark under it.
 */
function BrandWall() {
  const w = BRAND.w;
  const ghost = { y: 1.34, size: 0.92, z: 0.09 };
  const halo = ghost.y + ghost.size * 0.52;
  return (
    <group>
      <Box size={[w, 2.84, 0.15]} position={[0, 1.42, -0.1]} material={LM.linen} />
      <mesh geometry={BRAND_SLATS} material={LM.oakLight} castShadow receiveShadow />
      <Box size={[w, 0.1, 0.3]} position={[0, 0.05, -0.03]} material={LM.oakDark} />
      <Box size={[w, 0.06, 0.32]} position={[0, 2.87, -0.02]} material={LM.oakDark} />
      <Box size={[w - 0.1, 0.008, 0.02]} position={[0, 2.836, 0.12]} material={LM.led} cast={false} />
      {/* The sage panel and its brass fins. */}
      <Rounded size={[1.9, 2.3, 0.06]} radius={0.02} position={[0, 1.52, 0.05]} material={LM.sage} />
      {[-0.99, 0.99].map((x) => <Box key={x} size={[0.025, 2.36, 0.07]} position={[x, 1.52, 0.055]} material={LM.brass} />)}
      {/* The glow and the lit ring behind the ghost. */}
      <Panel size={[1.7, 1.7]} position={[0, halo, 0.081]} material={faces.glow()} />
      <mesh geometry={LGEO.ring} material={LM.led} position={[0, halo, 0.085]} />
      {GHOST_EYES.map((eye) => (
        <mesh key={eye.x} position={[eye.x * ghost.size, ghost.y + eye.y * ghost.size, 0.083]} material={LM.eye}
          scale={[GHOST_EYE.rx * ghost.size * 1.3, GHOST_EYE.ry * ghost.size * 1.2, 1]}>
          <circleGeometry args={[1, 20]} />
        </mesh>
      ))}
      {/* Four brass stand-off pins hold the ghost off the panel. */}
      {[[-0.2, 0.35], [0.2, 0.35], [-0.2, 0.8], [0.2, 0.8]].map(([x, y]) => (
        <Cyl key={`${x}:${y}`} radius={0.01} height={0.02} position={[x * ghost.size, ghost.y + y * ghost.size, 0.09]} rotation={[Math.PI / 2, 0, 0]} material={LM.brass} cast={false} />
      ))}
      <mesh geometry={GHOST_GEOMETRY} material={LM.ink} position={[0, ghost.y, ghost.z]} scale={[ghost.size, ghost.size, 0.9]} castShadow receiveShadow />
      <Panel size={[1.72, 0.43]} position={[0, 0.74, 0.082]} material={faces.wordmark()} />
    </group>
  );
}

/**
 * The Agent board as a standing touch-screen totem: an ink body between pale
 * oak cheeks with warm light lines, a portrait screen of the team, the ghost
 * in brass on top and an oak plinth.
 */
function AgentTotem() {
  return (
    <group>
      <Rounded size={[0.88, 0.06, 0.48]} radius={0.02} position={[0, 0.03, 0]} material={LM.oakDark} />
      <Rounded size={[0.76, 1.94, 0.12]} radius={0.03} position={[0, 1.03, -0.02]} material={LM.charcoal} />
      {[-0.405, 0.405].map((x) => (
        <group key={x}>
          <Rounded size={[0.05, 1.96, 0.18]} radius={0.015} position={[x, 1.04, -0.02]} material={LM.oakLight} />
          <Box size={[0.008, 1.7, 0.006]} position={[x, 1.1, 0.072]} material={LM.led} cast={false} />
        </group>
      ))}
      <Panel size={[0.66, 1.32]} position={[0, 1.2, 0.0405]} material={faces.totem()} />
      <Box size={[0.66, 0.01, 0.01]} position={[0, 1.9, 0.045]} material={LM.brass} cast={false} />
      <Box size={[0.4, 0.035, 0.004]} position={[0, 0.34, 0.041]} material={LM.bronze} cast={false} />
      <mesh geometry={GHOST_GEOMETRY} material={LM.brass} position={[0, 1.92, -0.03]} scale={[0.1, 0.1, 0.6]} castShadow />
    </group>
  );
}

/** A low boucle sofa on an oak plinth, deep seat cushions, a sage and a clay pillow. */
function LobbySofa() {
  return (
    <group>
      <Box size={[2.1, 0.06, 0.75]} position={[0, 0.03, 0]} material={LM.charcoal} />
      <Rounded size={[2.26, 0.08, 0.9]} radius={0.02} position={[0, 0.1, 0]} material={LM.oak} />
      <Rounded size={[2.26, 0.64, 0.24]} radius={0.1} position={[0, 0.46, -0.35]} material={LM.boucle} />
      {[-1.07, 1.07].map((x) => <Rounded key={x} size={[0.16, 0.5, 0.9]} radius={0.07} position={[x, 0.39, 0]} material={LM.boucle} />)}
      {[-0.51, 0.51].map((x) => (
        <group key={x}>
          <Rounded size={[1.0, 0.2, 0.68]} radius={0.08} position={[x, 0.24, 0.08]} material={LM.boucle} />
          <group position={[x, 0.52, -0.17]} rotation={[-0.14, 0, 0]}>
            <Rounded size={[0.98, 0.36, 0.14]} radius={0.06} position={[0, 0, 0]} material={LM.boucle} />
          </group>
        </group>
      ))}
      <group position={[-0.72, 0.52, -0.04]} rotation={[-0.25, 0.25, 0.12]}>
        <Rounded size={[0.4, 0.36, 0.12]} radius={0.05} position={[0, 0, 0]} material={LM.sage} />
      </group>
      <group position={[0.74, 0.5, -0.04]} rotation={[-0.22, -0.3, -0.1]}>
        <Rounded size={[0.36, 0.32, 0.11]} radius={0.05} position={[0, 0, 0]} material={LM.clay} />
      </group>
    </group>
  );
}

/** An oak lounge chair with cognac leather cushions and a tilted back. */
function LobbyArmchair() {
  return (
    <group>
      {[-0.33, 0.33].flatMap((x) => [-0.33, 0.33].map((z) => (
        <Box key={`${x}:${z}`} size={[0.045, 0.58, 0.045]} position={[x, 0.29, z]} material={LM.oak} />
      )))}
      {[-0.34, 0.34].map((x) => <Rounded key={x} size={[0.075, 0.035, 0.8]} radius={0.012} position={[x, 0.595, 0]} material={LM.oak} />)}
      <Box size={[0.62, 0.05, 0.66]} position={[0, 0.3, 0]} material={LM.oak} />
      <Rounded size={[0.6, 0.12, 0.62]} radius={0.05} position={[0, 0.385, 0.04]} material={LM.cognac} />
      <group position={[0, 0.36, -0.25]} rotation={[-0.22, 0, 0]}>
        <Rounded size={[0.58, 0.42, 0.1]} radius={0.045} position={[0, 0.21, 0]} material={LM.cognac} />
        <Box size={[0.62, 0.04, 0.05]} position={[0, 0.43, -0.03]} material={LM.oak} />
      </group>
    </group>
  );
}

/** An oval travertine coffee table on two oak drums, magazines, a sage bowl and an olive sprig. */
function LobbyTable() {
  return (
    <group>
      <mesh geometry={LGEO.cyl} material={LM.travertine} position={[0, 0.3825, 0]} scale={[0.59, 0.035, 0.365]} castShadow receiveShadow />
      {[-0.26, 0.26].map((x) => <Cyl key={x} radius={0.1} height={0.365} position={[x, 0.1825, 0]} material={LM.oak} />)}
      {/* A stack of magazines and one fanned out beside it. */}
      <group position={[-0.2, 0.4, 0.04]} rotation={[0, 0.22, 0]}>
        {[0, 1, 2].map((i) => (
          <Box key={i} size={[0.22 - i * 0.01, 0.009, 0.29 - i * 0.01]} position={[0, 0.0045 + i * 0.009, 0]} material={LM.mags[i]} cast={false} />
        ))}
      </group>
      <group position={[-0.02, 0.4, -0.08]} rotation={[0, -0.5, 0]}>
        <Box size={[0.21, 0.008, 0.28]} position={[0, 0.004, 0]} material={LM.mags[3]} cast={false} />
      </group>
      <mesh geometry={LGEO.dome} material={LM.sageDeep} position={[0.26, 0.47, 0.05]} scale={[0.1, 0.07, 0.1]} castShadow />
      {[[-0.03, 0.01], [0.03, -0.02], [0.0, 0.035]].map(([x, z], i) => (
        <mesh key={i} geometry={LGEO.sphere} material={LM.mags[4]} position={[0.26 + x, 0.435, 0.05 + z]} scale={0.028} />
      ))}
      <Cyl radius={0.025} height={0.09} position={[0.38, 0.445, -0.12]} material={LM.ceramic} />
      {[-0.3, 0.1, 0.4].map((tilt, i) => (
        <group key={i} position={[0.38, 0.49, -0.12]} rotation={[tilt * 0.3, i * 1.9, tilt]}>
          <Box size={[0.005, 0.07, 0.005]} position={[0, 0.035, 0]} material={LM.bark} cast={false} />
          <mesh geometry={GEO.blob} material={LM.olive[i]} position={[0, 0.06, 0]} scale={[0.022, 0.012, 0.022]} />
        </group>
      ))}
    </group>
  );
}

/** A round marble side table on a brass stem, a book and a glass. */
function SideTable() {
  return (
    <group>
      <Cyl radius={0.18} height={0.02} position={[0, 0.01, 0]} material={LM.brass} />
      <Cyl radius={0.018} height={0.52} position={[0, 0.28, 0]} material={LM.brass} />
      <Cyl radius={0.235} height={0.025} position={[0, 0.5525, 0]} material={LM.marble} />
      <Box size={[0.16, 0.025, 0.22]} position={[-0.05, 0.5775, 0.02]} material={LM.mags[1]} cast={false} />
      <Box size={[0.15, 0.02, 0.2]} position={[-0.05, 0.6, 0.02]} material={LM.mags[2]} cast={false} />
      <Cyl radius={0.03} height={0.08} position={[0.12, 0.605, -0.06]} material={LM.glass} cast={false} />
    </group>
  );
}

/** Tripod floor lamp: three oak legs, a brass neck and a glowing linen drum. */
const LAMP_LEG = { spread: 0.2, apex: 1.24 };
/** Height of the bulb inside the shade; the lounge's light sits there. */
const LAMP_LIGHT_Y = 1.5;
function LobbyLamp() {
  const tilt = Math.atan2(LAMP_LEG.spread, LAMP_LEG.apex);
  const length = Math.hypot(LAMP_LEG.spread, LAMP_LEG.apex);
  return (
    <group>
      {[0, 1, 2].map((i) => (
        <group key={i} rotation={[0, (i * Math.PI * 2) / 3 + 0.4, 0]}>
          <Cyl radius={0.014} height={length} position={[LAMP_LEG.spread / 2, LAMP_LEG.apex / 2, 0]} rotation={[0, 0, tilt]} material={LM.oak} />
        </group>
      ))}
      <Cyl radius={0.012} height={0.26} position={[0, 1.36, 0]} material={LM.brass} />
      <mesh geometry={LGEO.shade} material={LM.shade} position={[0, 1.56, 0]} castShadow />
      <mesh geometry={LGEO.sphere} material={LM.led} position={[0, LAMP_LIGHT_Y, 0]} scale={0.045} />
    </group>
  );
}

/** The three branches from the trunk's fork to the crowns, as placed cylinders. */
const OLIVE_BRANCHES = OLIVE_CROWNS.map(([x, y, z]) => {
  const from = new Vector3(0.04, 1.3, -0.02);
  const dir = new Vector3(x, y - 0.1, z).sub(from);
  const length = dir.length();
  return {
    position: from.clone().addScaledVector(dir, 0.5).toArray() as Vec3,
    quaternion: new Quaternion().setFromUnitVectors(new Vector3(0, 1, 0), dir.normalize()),
    scale: [0.028, length, 0.028] as Vec3,
  };
});

/** An olive tree in a pale stone planter: a twisting trunk, three branches and a silvery crown. */
function OliveTree() {
  return (
    <group>
      <mesh geometry={LGEO.planter} material={LM.stone} position={[0, 0.31, 0]} castShadow receiveShadow />
      <Cyl radius={0.43} height={0.03} position={[0, 0.62, 0]} material={LM.stone} />
      <Cyl radius={0.39} height={0.01} position={[0, 0.631, 0]} material={LM.pebbles} cast={false} />
      <Cyl radius={0.07} height={0.62} position={[0.02, 0.93, 0]} rotation={[0.06, 0, -0.08]} material={LM.bark} />
      <Cyl radius={0.05} height={0.5} position={[0.06, 1.38, -0.02]} rotation={[-0.1, 0, 0.18]} material={LM.bark} />
      {OLIVE_BRANCHES.map((branch, i) => (
        <mesh key={i} geometry={LGEO.cyl} material={LM.bark} position={branch.position} quaternion={branch.quaternion} scale={branch.scale} castShadow />
      ))}
      {OLIVE_GEOMETRIES.map((geometry, i) => <mesh key={i} geometry={geometry} material={LM.olive[i]} castShadow receiveShadow />)}
    </group>
  );
}

/** A brass cup on a round foot with two handles. */
function Cup({ position, scale = 1 }: { position: Vec3; scale?: number }) {
  return (
    <group position={position} scale={scale}>
      <Cyl radius={0.05} height={0.03} position={[0, 0.015, 0]} material={LM.oakDark} />
      <Cyl radius={0.012} height={0.08} position={[0, 0.07, 0]} material={LM.brass} />
      <mesh geometry={LGEO.cup} material={LM.brass} position={[0, 0.175, 0]} castShadow />
      {[-1, 1].map((side) => (
        <mesh key={side} geometry={LGEO.handle} material={LM.brass} position={[side * 0.07, 0.18, 0]} rotation={[0, 0, side * -Math.PI / 2]} />
      ))}
    </group>
  );
}

/**
 * A lit oak vitrine of awards: glass shelves on a sage back behind a glass
 * front — brass cups, a crystal obelisk, a framed certificate and the ghost
 * in brass on an oak block.
 */
function AwardCase() {
  const shelves = [0.56, 0.98, 1.4];
  return (
    <group>
      <Box size={[1.3, 0.12, 0.42]} position={[0, 0.06, 0]} material={LM.oakDark} />
      <Box size={[1.3, 1.83, 0.03]} position={[0, 1.035, -0.195]} material={LM.oak} />
      <Box size={[1.22, 1.72, 0.01]} position={[0, 1.02, -0.175]} material={LM.sage} />
      {[-0.63, 0.63].map((x) => <Box key={x} size={[0.04, 1.83, 0.42]} position={[x, 1.035, 0]} material={LM.oak} />)}
      <Box size={[1.3, 0.04, 0.42]} position={[0, 1.93, 0]} material={LM.oak} />
      <Box size={[1.18, 0.008, 0.02]} position={[0, 1.905, 0.12]} material={LM.led} cast={false} />
      {shelves.map((y) => <Box key={y} size={[1.22, 0.012, 0.36]} position={[0, y, -0.01]} material={LM.glass} cast={false} />)}
      <Box size={[1.22, 1.78, 0.008]} position={[0, 1.01, 0.2]} material={LM.glass} cast={false} />
      {/* Floor level: the big cup and a certificate leaning back. */}
      <Cup position={[-0.3, 0.12, 0]} scale={1.3} />
      <group position={[0.25, 0.12, -0.08]} rotation={[-0.14, 0, 0]}>
        <Box size={[0.3, 0.37, 0.02]} position={[0, 0.185, 0]} material={LM.oakDark} />
        <Panel size={[0.25, 0.32]} position={[0, 0.185, 0.0105]} material={faces.certificate()} />
      </group>
      {/* First shelf: two cups. */}
      <Cup position={[-0.35, shelves[0] + 0.006, 0]} />
      <Cup position={[0.1, shelves[0] + 0.006, -0.02]} scale={0.85} />
      <Box size={[0.2, 0.05, 0.1]} position={[0.4, shelves[0] + 0.031, 0]} material={LM.oakDark} />
      <Box size={[0.14, 0.03, 0.004]} position={[0.4, shelves[0] + 0.031, 0.051]} material={LM.brass} cast={false} />
      {/* Second shelf: the crystal obelisk and the brass ghost. */}
      <mesh geometry={LGEO.crystal} material={LM.crystal} position={[-0.25, shelves[1] + 0.116, 0]} rotation={[0, Math.PI / 4, 0]} />
      <Box size={[0.14, 0.05, 0.1]} position={[0.22, shelves[1] + 0.031, 0]} material={LM.oakDark} />
      <mesh geometry={GHOST_GEOMETRY} material={LM.brass} position={[0.22, shelves[1] + 0.056, -0.02]} scale={[0.16, 0.16, 0.5]} castShadow />
      {/* Top shelf: books and a small cup. */}
      {[0.04, 0.03, 0.045, 0.035].map((w, i) => (
        <Box key={i} size={[w, 0.22 + (i % 2) * 0.03, 0.17]} position={[-0.45 + i * 0.05, shelves[2] + 0.12 + (i % 2) * 0.015, -0.03]} material={LM.mags[i]} />
      ))}
      <Cup position={[0.3, shelves[2] + 0.006, 0]} scale={0.8} />
    </group>
  );
}

/** A flat textured floor piece (mat, rug), sized per instance. */
function FloorPiece({ w, d, material, lift }: { w: number; d: number; material: MeshStandardMaterial; lift: number }) {
  return <Box size={[w, lift, d]} position={[0, lift / 2, 0]} material={material} cast={false} />;
}

/** Renderers of the lobby's furniture kinds; merged into OfficeProps' exhaustive table. */
export const LOBBY_RENDERERS = {
  brandWall: () => <BrandWall />,
  agentTotem: () => <AgentTotem />,
  lobbySofa: () => <LobbySofa />,
  lobbyArmchair: () => <LobbyArmchair />,
  lobbyTable: () => <LobbyTable />,
  sideTable: () => <SideTable />,
  lobbyLamp: () => <LobbyLamp />,
  oliveTree: () => <OliveTree />,
  awardCase: () => <AwardCase />,
  entranceMat: ({ item }: { item: Furniture }) => {
    const size = item.size ?? FURNITURE_SIZE.entranceMat;
    return <FloorPiece w={size.w} d={size.d} material={faces.mat()} lift={0.014} />;
  },
  lobbyRug: ({ item }: { item: Furniture }) => {
    const size = item.size ?? FURNITURE_SIZE.lobbyRug;
    return <FloorPiece w={size.w} d={size.d} material={faces.rug()} lift={0.016} />;
  },
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;

// ---------------------------------------------------------------------------
// Fittings: the lounge's light
// ---------------------------------------------------------------------------

/**
 * The lounge's one real light: warm, from inside the tripod lamp's linen
 * shade, so the pool of light round the sofa has a visible source. (The lobby
 * has no ceiling to hang a pendant from; one floating in the night read as a
 * hoop in mid-air from the high camera.)
 */
export const LobbyFittings = memo(function LobbyFittings({ lamp }: { lamp: Furniture }) {
  return <pointLight position={[lamp.x, LAMP_LIGHT_Y, lamp.z]} color="#ffd9ad" intensity={4} distance={6} decay={2} />;
});
