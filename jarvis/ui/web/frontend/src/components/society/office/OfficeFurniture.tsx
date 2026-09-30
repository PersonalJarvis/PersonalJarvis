/**
 * The office kit: every piece is a few rounded primitives with flat, matte
 * colours in a contemporary-workplace palette — light oak on black steel,
 * felt, ceramic planters and lots of green. It needs no authored meshes; the
 * few surface patterns (felt fluting, sign faces) are canvas textures.
 * Geometries and materials are module-level singletons shared by every copy.
 */
import { useMemo } from "react";
import {
  BoxGeometry, CylinderGeometry, DoubleSide, IcosahedronGeometry, MeshStandardMaterial, RepeatWrapping, type Texture,
} from "three";
import { RoundedBoxGeometry } from "three-stdlib";
import { cachedCanvasTexture } from "./canvasMaterials";
import { DEPARTMENT_ZONES, OFFICE } from "./officePalette";
import { screenTexture, type ScreenFace } from "./screenTextures";

export const matte = (color: string, extra: Partial<ConstructorParameters<typeof MeshStandardMaterial>[0]> = {}) =>
  new MeshStandardMaterial({ color, roughness: 0.85, metalness: 0, ...extra });

export const MAT = {
  deskTop: matte(OFFICE.deskTop, { roughness: 0.6 }),
  deskEdge: matte(OFFICE.deskEdge, { roughness: 0.6 }),
  deskBody: matte(OFFICE.deskBody),
  deskLeg: matte(OFFICE.deskLeg, { roughness: 0.45, metalness: 0.35 }),
  chair: matte(OFFICE.chair, { roughness: 0.5, metalness: 0.2 }),
  chairSeat: matte(OFFICE.chairSeat),
  chairMesh: matte(OFFICE.chairMesh, { roughness: 0.95 }),
  /** White base for parts tinted per instance (seat fabric, desk screens): the instance colour is the colour. */
  tinted: matte("#ffffff", { roughness: 0.95 }),
  monitor: matte(OFFICE.monitor, { roughness: 0.35, metalness: 0.2 }),
  monitorArm: matte(OFFICE.monitorArm, { roughness: 0.3, metalness: 0.6 }),
  keyboard: matte(OFFICE.keyboard, { roughness: 0.5 }),
  mug: matte(OFFICE.mug, { roughness: 0.35 }),
  steel: matte(OFFICE.steel, { roughness: 0.45, metalness: 0.35 }),
  railing: matte(OFFICE.railing, { roughness: 0.4, metalness: 0.3 }),
  glass: new MeshStandardMaterial({ color: OFFICE.glass, transparent: true, opacity: 0.14, roughness: 0.05, side: DoubleSide, depthWrite: false }),
  wall: matte(OFFICE.wallWhite),
  sign: matte(OFFICE.signBoard, { roughness: 0.6 }),
  wood: matte(OFFICE.wood, { roughness: 0.65 }),
  woodDark: matte(OFFICE.woodDark, { roughness: 0.65 }),
  couch: matte(OFFICE.couch, { roughness: 1 }),
  cushion: matte(OFFICE.couchCushion, { roughness: 1 }),
  couchAccents: OFFICE.couchAccent.map((c) => matte(c, { roughness: 1 })),
  pot: matte(OFFICE.plantPot, { roughness: 0.7 }),
  planterLight: matte(OFFICE.planterLight, { roughness: 0.7 }),
  soil: matte("#3b2f26"),
  leaf: matte(OFFICE.leaf, { flatShading: true }),
  leafDark: matte(OFFICE.leafDark, { flatShading: true }),
  leafLight: matte(OFFICE.leafLight, { flatShading: true }),
  trunk: matte(OFFICE.trunk),
  rug: matte(OFFICE.rug, { roughness: 1 }),
  books: OFFICE.book.map((c) => matte(c)),
};

export const GEO = {
  box: new BoxGeometry(1, 1, 1),
  cyl: new CylinderGeometry(1, 1, 1, 16),
  potCyl: new CylinderGeometry(1, 0.8, 1, 16),
  // One subdivision: foliage reads as soft clusters rather than faceted low-poly rocks.
  blob: new IcosahedronGeometry(1, 1),
};

export function Box({ size, position, material, cast = true }: {
  size: [number, number, number]; position: [number, number, number]; material: MeshStandardMaterial; cast?: boolean;
}) {
  return <mesh geometry={GEO.box} material={material} position={position} scale={size} castShadow={cast} receiveShadow />;
}

/** Rounded boxes are cached by size: every desk shares the same few geometries. */
const roundedCache = new Map<string, RoundedBoxGeometry>();
export function Rounded({ size, radius, position, material, cast = true }: {
  size: [number, number, number]; radius: number; position: [number, number, number]; material: MeshStandardMaterial; cast?: boolean;
}) {
  const key = `${size.join("x")}:${radius}`;
  let geometry = roundedCache.get(key);
  if (!geometry) {
    geometry = new RoundedBoxGeometry(size[0], size[1], size[2], 2, radius);
    roundedCache.set(key, geometry);
  }
  return <mesh geometry={geometry} material={material} position={position} castShadow={cast} receiveShadow />;
}

const screenMaterials = new Map<ScreenFace, MeshStandardMaterial>();
export function screenMaterial(face: ScreenFace): MeshStandardMaterial {
  let material = screenMaterials.get(face);
  if (!material) {
    const map = screenTexture(face);
    const lit = face !== "empty" && face !== "paused";
    material = new MeshStandardMaterial({
      color: "#ffffff", map, roughness: 0.4,
      emissive: lit ? "#ffffff" : "#000000", emissiveMap: lit ? map : null, emissiveIntensity: lit ? 0.9 : 0,
    });
    screenMaterials.set(face, material);
  }
  return material;
}

/**
 * A potted plant; `size` scales the whole plant (1 ≈ 1.45 m tall): a tall
 * charcoal planter under a full, leafy crown.
 */
export function Plant({ position, size = 1 }: { position: [number, number, number]; size?: number }) {
  return (
    <group position={position} scale={size}>
      <mesh geometry={GEO.cyl} material={MAT.pot} position={[0, 0.25, 0]} scale={[0.21, 0.5, 0.21]} castShadow receiveShadow />
      <mesh geometry={GEO.cyl} material={MAT.soil} position={[0, 0.501, 0]} scale={[0.19, 0.01, 0.19]} />
      <mesh geometry={GEO.cyl} material={MAT.trunk} position={[0, 0.64, 0]} scale={[0.025, 0.3, 0.025]} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leaf} position={[0, 1.0, 0]} scale={[0.33, 0.3, 0.33]} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leafDark} position={[0.15, 0.86, 0.1]} scale={0.19} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leafLight} position={[-0.15, 0.92, -0.08]} scale={0.19} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leafDark} position={[-0.06, 0.8, 0.16]} scale={0.17} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leaf} position={[0.1, 1.12, -0.14]} scale={0.18} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leafLight} position={[0.04, 1.28, 0.04]} scale={[0.2, 0.15, 0.2]} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leafDark} position={[-0.13, 1.16, 0.1]} scale={0.15} castShadow />
    </group>
  );
}

/** Open shelving: black steel uprights, light-oak boards, books, a plant and a few ceramics. */
export function Bookshelf({ position, rotationY = 0 }: { position: [number, number, number]; rotationY?: number }) {
  const books = useMemo(() => Array.from({ length: 13 }, (_, i) => {
    const shelf = i < 6 ? 0 : i < 10 ? 1 : 2;
    const inShelf = shelf === 0 ? i : shelf === 1 ? i - 6 : i - 10;
    const start = shelf === 0 ? -0.8 : shelf === 1 ? 0.12 : -0.78;
    return {
      x: start + inShelf * 0.1,
      y: [0.05, 0.5, 0.95][shelf],
      h: 0.26 + ((i * 7) % 4) * 0.03,
      m: MAT.books[(i * 3) % MAT.books.length],
    };
  }), []);
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      {/* Boards are shallower than the uprights and the top board sits below their tops:
          coplanar faces where steel meets oak would z-fight and flicker as the camera moves. */}
      {[-0.88, 0, 0.88].map((x) => <Box key={x} size={[0.03, 1.4, 0.34]} position={[x, 0.7, 0]} material={MAT.steel} />)}
      {[0.035, 0.485, 0.935, 1.37].map((y) => <Box key={y} size={[1.8, 0.03, 0.32]} position={[0, y, 0]} material={MAT.wood} />)}
      {books.map((b, i) => <Box key={i} size={[0.07, b.h, 0.24]} position={[b.x, b.y + b.h / 2, 0]} material={b.m} />)}
      {/* A small planter, two ceramic vases and a trailing plant. */}
      <mesh geometry={GEO.cyl} material={MAT.planterLight} position={[0.45, 0.58, 0]} scale={[0.08, 0.14, 0.08]} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leaf} position={[0.45, 0.71, 0]} scale={[0.12, 0.1, 0.12]} castShadow />
      <mesh geometry={GEO.cyl} material={MAT.planterLight} position={[-0.45, 1.05, 0]} scale={[0.06, 0.2, 0.06]} castShadow />
      <mesh geometry={GEO.cyl} material={MAT.pot} position={[-0.3, 1.02, 0.02]} scale={[0.05, 0.14, 0.05]} castShadow />
      <mesh geometry={GEO.cyl} material={MAT.pot} position={[0.55, 1.0, 0]} scale={[0.07, 0.1, 0.07]} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leafDark} position={[0.55, 1.1, 0]} scale={[0.1, 0.1, 0.1]} castShadow />
    </group>
  );
}

/** A low modular lounge sofa facing +z: plinth on slim legs, loose cushions, two accent pillows. */
export function Couch({ position, rotationY = 0 }: { position: [number, number, number]; rotationY?: number }) {
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      {[-1.05, 1.05].flatMap((x) => [-0.36, 0.36].map((z) => (
        <mesh key={`${x}:${z}`} geometry={GEO.cyl} material={MAT.steel} position={[x, 0.04, z]} scale={[0.03, 0.08, 0.03]} />
      )))}
      <Rounded size={[2.3, 0.34, 0.88]} radius={0.06} position={[0, 0.25, 0]} material={MAT.couch} />
      <Rounded size={[2.3, 0.42, 0.22]} radius={0.08} position={[0, 0.62, -0.33]} material={MAT.couch} />
      <Rounded size={[0.16, 0.3, 0.88]} radius={0.06} position={[-1.1, 0.55, 0]} material={MAT.couch} />
      <Rounded size={[0.16, 0.3, 0.88]} radius={0.06} position={[1.1, 0.55, 0]} material={MAT.couch} />
      <Rounded size={[1.0, 0.14, 0.64]} radius={0.05} position={[-0.51, 0.49, 0.08]} material={MAT.cushion} />
      <Rounded size={[1.0, 0.14, 0.64]} radius={0.05} position={[0.51, 0.49, 0.08]} material={MAT.cushion} />
      <group position={[-0.72, 0.7, -0.17]} rotation={[-0.25, 0.15, 0]}>
        <Rounded size={[0.38, 0.3, 0.1]} radius={0.04} position={[0, 0, 0]} material={MAT.couchAccents[0]} />
      </group>
      <group position={[0.74, 0.7, -0.17]} rotation={[-0.25, -0.12, 0]}>
        <Rounded size={[0.38, 0.3, 0.1]} radius={0.04} position={[0, 0, 0]} material={MAT.couchAccents[1]} />
      </group>
    </group>
  );
}

/** A frameless glass balustrade from (x1,z1) to (x2,z2): a black base shoe, the pane and a slim cap. */
export function Railing({ from, to, height = 1.05 }: { from: [number, number]; to: [number, number]; height?: number }) {
  const [x1, z1] = from;
  const [x2, z2] = to;
  const length = Math.hypot(x2 - x1, z2 - z1);
  const angle = Math.atan2(z2 - z1, x2 - x1);
  return (
    <group position={[(x1 + x2) / 2, 0, (z1 + z2) / 2]} rotation={[0, -angle, 0]}>
      <Box size={[length, 0.08, 0.08]} position={[0, 0.04, 0]} material={MAT.railing} />
      <Box size={[length, height - 0.1, 0.02]} position={[0, (height - 0.1) / 2 + 0.07, 0]} material={MAT.glass} cast={false} />
      <Box size={[length, 0.03, 0.05]} position={[0, height, 0]} material={MAT.railing} />
    </group>
  );
}

/** A department name plate: white type on charcoal, with a short rule in the zone's colour. */
function signTexture(label: string, accent: string): Texture | null {
  const text = label.length > 22 ? `${label.slice(0, 21)}…` : label;
  return cachedCanvasTexture(`dept-sign:${accent}:${text}`, 512, 112, (ctx, w, h) => {
    ctx.fillStyle = OFFICE.signBoard;
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = accent;
    ctx.fillRect(30, h - 24, 48, 6);
    ctx.fillStyle = OFFICE.signText;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    let size = 50;
    const font = (px: number) => `600 ${px}px system-ui, -apple-system, 'Segoe UI', sans-serif`;
    ctx.font = font(size);
    while (size > 30 && ctx.measureText(text).width > w - 60) {
      size -= 2;
      ctx.font = font(size);
    }
    ctx.fillText(text, 30, h / 2 - 8);
  });
}

/** Acoustic felt with vertical fluting, drawn once per colour and repeated along the wall. */
function feltTexture(colour: string): Texture | null {
  const texture = cachedCanvasTexture(`felt:${colour}`, 64, 64, (ctx, w, h) => {
    ctx.fillStyle = colour;
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "rgba(0,0,0,0.16)";
    for (let x = 0; x < w; x += 16) ctx.fillRect(x, 0, 3, h);
    ctx.fillStyle = "rgba(255,255,255,0.07)";
    for (let x = 6; x < w; x += 16) ctx.fillRect(x, 0, 4, h);
  });
  if (texture) {
    texture.wrapS = texture.wrapT = RepeatWrapping;
    texture.anisotropy = 4;
  }
  return texture;
}

const feltMaterials = new Map<string, MeshStandardMaterial>();
function feltMaterial(colour: string, width: number): MeshStandardMaterial {
  const key = `${colour}:${width.toFixed(2)}`;
  let material = feltMaterials.get(key);
  if (!material) {
    const base = feltTexture(colour);
    let map: Texture | null = null;
    if (base) {
      map = base.clone();
      // Four flutes per texture repeat, one repeat every 20 cm.
      map.repeat.set(width / 0.2, 1);
      map.needsUpdate = true;
    }
    material = new MeshStandardMaterial({ color: map ? "#ffffff" : colour, map, roughness: 1 });
    feltMaterials.set(key, material);
  }
  return material;
}

/**
 * A department's back wall: a white partition faced with fluted acoustic felt
 * in the zone's colour, a charcoal name plate with white type, and on a wide
 * wall two slim oak ledges with small planters.
 */
export function SignWall({ label, width, position, zone = 0, tone }: {
  label: string; width: number; position: [number, number, number]; zone?: number;
  /** A coding-floor studio's own wall and panel materials, in place of the zone colours. */
  tone?: { wall: MeshStandardMaterial; slat: MeshStandardMaterial };
}) {
  const colours = DEPARTMENT_ZONES[zone % DEPARTMENT_ZONES.length];
  const panel = tone ? `#${tone.slat.color.getHexString()}` : colours.panel;
  const texture = signTexture(label, tone ? panel : colours.rug);
  const plateW = Math.min(2.6, width * 0.46);
  const ledgeX = width / 2 - 0.9;
  return (
    <group position={position}>
      <Box size={[width, 1.9, 0.12]} position={[0, 0.95, -0.01]} material={tone?.wall ?? MAT.wall} />
      <Box size={[width - 0.06, 1.6, 0.03]} position={[0, 0.98, 0.06]} material={feltMaterial(panel, width - 0.06)} cast={false} />
      <Box size={[plateW + 0.06, (plateW * 112) / 512 + 0.06, 0.03]} position={[0, 1.36, 0.09]} material={MAT.sign} />
      {texture && (
        <mesh position={[0, 1.36, 0.106]}>
          <planeGeometry args={[plateW, (plateW * 112) / 512]} />
          <meshStandardMaterial map={texture} roughness={0.7} />
        </mesh>
      )}
      {ledgeX > plateW / 2 + 0.55 && [-ledgeX, ledgeX].map((x) => (
        <group key={x} position={[x, 0, 0]}>
          <Box size={[0.9, 0.03, 0.12]} position={[0, 1.2, 0.13]} material={MAT.wood} />
          <mesh geometry={GEO.cyl} material={MAT.planterLight} position={[-0.2, 1.28, 0.13]} scale={[0.05, 0.13, 0.05]} castShadow />
          <mesh geometry={GEO.blob} material={MAT.leaf} position={[-0.2, 1.4, 0.13]} scale={[0.09, 0.08, 0.09]} castShadow />
          <mesh geometry={GEO.cyl} material={MAT.pot} position={[0.18, 1.26, 0.13]} scale={[0.045, 0.09, 0.045]} castShadow />
          <mesh geometry={GEO.blob} material={MAT.leafDark} position={[0.18, 1.35, 0.13]} scale={[0.07, 0.06, 0.07]} castShadow />
        </group>
      ))}
    </group>
  );
}

/** A flat rug under the lounge. */
export function Rug({ position, size }: { position: [number, number, number]; size: [number, number] }) {
  return <Box size={[size[0], 0.02, size[1]]} position={position} material={MAT.rug} cast={false} />;
}
