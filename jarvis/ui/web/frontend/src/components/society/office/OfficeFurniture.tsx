/**
 * The office kit: every piece is a few rounded primitives with flat, matte
 * colours — the "toy office" look needs no authored meshes or textures.
 * Geometries and materials are module-level singletons shared by every copy.
 */
import { useMemo } from "react";
import {
  BoxGeometry, CanvasTexture, CylinderGeometry, DoubleSide, IcosahedronGeometry,
  MeshStandardMaterial, SRGBColorSpace, type Texture,
} from "three";
import { RoundedBoxGeometry } from "three-stdlib";
import { OFFICE } from "./officePalette";
import { screenTexture, type ScreenFace } from "./screenTextures";

export const matte = (color: string, extra: Partial<ConstructorParameters<typeof MeshStandardMaterial>[0]> = {}) =>
  new MeshStandardMaterial({ color, roughness: 0.85, metalness: 0, ...extra });

export const MAT = {
  deskTop: matte(OFFICE.deskTop),
  deskBody: matte(OFFICE.deskBody),
  deskLeg: matte(OFFICE.deskLeg),
  chair: matte(OFFICE.chair),
  chairSeat: matte(OFFICE.chairSeat),
  monitor: matte(OFFICE.monitor, { roughness: 0.5 }),
  keyboard: matte(OFFICE.keyboard),
  railing: matte(OFFICE.railing, { roughness: 0.4, metalness: 0.3 }),
  glass: new MeshStandardMaterial({ color: OFFICE.glass, transparent: true, opacity: 0.18, roughness: 0.1, side: DoubleSide, depthWrite: false }),
  wall: matte(OFFICE.wallWhite),
  sign: matte(OFFICE.signBoard),
  wood: matte(OFFICE.wood),
  woodDark: matte(OFFICE.woodDark),
  couch: matte(OFFICE.couch),
  cushion: matte(OFFICE.couchCushion),
  pot: matte(OFFICE.plantPot),
  leaf: matte(OFFICE.leaf, { flatShading: true }),
  leafDark: matte(OFFICE.leafDark, { flatShading: true }),
  trunk: matte(OFFICE.trunk),
  rug: matte(OFFICE.rug),
  books: OFFICE.book.map((c) => matte(c)),
};

export const GEO = {
  box: new BoxGeometry(1, 1, 1),
  cyl: new CylinderGeometry(1, 1, 1, 16),
  potCyl: new CylinderGeometry(1, 0.8, 1, 16),
  blob: new IcosahedronGeometry(1, 0),
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
 * One workstation in local space: the agent sits at +z and looks north (-z)
 * at its monitor. The parent rotates the whole station for south-facing rows.
 */
export function Desk({ face }: { face: ScreenFace }) {
  return (
    <group>
      {/* Top and the white pedestal/legs of a bench desk. */}
      <Rounded size={[1.5, 0.06, 0.8]} radius={0.02} position={[0, 0.74, 0]} material={MAT.deskTop} />
      <Box size={[0.36, 0.7, 0.72]} position={[0.53, 0.36, 0]} material={MAT.deskBody} />
      <Box size={[0.05, 0.7, 0.7]} position={[-0.7, 0.36, 0]} material={MAT.deskLeg} />
      {/* Privacy divider between the back-to-back pair. */}
      <Box size={[1.5, 0.34, 0.03]} position={[0, 0.94, -0.41]} material={MAT.deskBody} />
      {/* Monitor on its stand. */}
      <Box size={[0.08, 0.2, 0.08]} position={[0, 0.87, -0.22]} material={MAT.monitor} />
      <Box size={[0.24, 0.02, 0.16]} position={[0, 0.78, -0.22]} material={MAT.monitor} />
      <Rounded size={[0.72, 0.44, 0.05]} radius={0.02} position={[0, 1.18, -0.24]} material={MAT.monitor} />
      <mesh position={[0, 1.18, -0.212]} material={screenMaterial(face)}>
        <planeGeometry args={[0.66, 0.38]} />
      </mesh>
      <Box size={[0.42, 0.02, 0.14]} position={[0, 0.78, 0.1]} material={MAT.keyboard} />
      <Chair />
    </group>
  );
}

/** Office swivel chair behind the desk (local +z side). */
export function Chair() {
  return (
    <group position={[0, 0, 0.72]}>
      <mesh geometry={GEO.cyl} material={MAT.chair} position={[0, 0.03, 0]} scale={[0.3, 0.04, 0.3]} castShadow />
      <mesh geometry={GEO.cyl} material={MAT.chair} position={[0, 0.25, 0]} scale={[0.03, 0.42, 0.03]} />
      <Rounded size={[0.5, 0.08, 0.48]} radius={0.03} position={[0, 0.48, 0]} material={MAT.chairSeat} />
      <Rounded size={[0.48, 0.5, 0.07]} radius={0.03} position={[0, 0.78, 0.24]} material={MAT.chairSeat} />
    </group>
  );
}

/** A potted plant; `size` scales the whole plant (1 ≈ 1.2 m tall). */
export function Plant({ position, size = 1 }: { position: [number, number, number]; size?: number }) {
  return (
    <group position={position} scale={size}>
      <mesh geometry={GEO.potCyl} material={MAT.pot} position={[0, 0.22, 0]} scale={[0.26, 0.44, 0.26]} castShadow receiveShadow />
      <mesh geometry={GEO.cyl} material={MAT.trunk} position={[0, 0.6, 0]} scale={[0.04, 0.5, 0.04]} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leaf} position={[0, 1.0, 0]} scale={[0.42, 0.36, 0.42]} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leafDark} position={[0.18, 0.86, 0.1]} scale={0.24} castShadow />
      <mesh geometry={GEO.blob} material={MAT.leaf} position={[-0.16, 0.9, -0.12]} scale={0.22} castShadow />
    </group>
  );
}

/** A low wooden bookshelf with a few coloured books. */
export function Bookshelf({ position, rotationY = 0 }: { position: [number, number, number]; rotationY?: number }) {
  const books = useMemo(() => Array.from({ length: 14 }, (_, i) => ({
    x: -0.72 + (i % 7) * 0.22 + ((i * 13) % 5) * 0.01,
    y: i < 7 ? 0.42 : 0.98,
    h: 0.3 + ((i * 7) % 4) * 0.04,
    m: MAT.books[i % MAT.books.length],
  })), []);
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      <Box size={[1.8, 1.4, 0.36]} position={[0, 0.7, 0]} material={MAT.woodDark} />
      <Box size={[1.7, 0.03, 0.34]} position={[0, 0.72, 0.02]} material={MAT.wood} cast={false} />
      {books.map((b, i) => <Box key={i} size={[0.14, b.h, 0.26]} position={[b.x, b.y + b.h / 2 - 0.12, 0.06]} material={b.m} />)}
    </group>
  );
}

/** A two-seat lounge couch facing +z. */
export function Couch({ position, rotationY = 0 }: { position: [number, number, number]; rotationY?: number }) {
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      <Rounded size={[2.2, 0.42, 0.9]} radius={0.08} position={[0, 0.25, 0]} material={MAT.couch} />
      <Rounded size={[2.2, 0.55, 0.22]} radius={0.08} position={[0, 0.6, -0.36]} material={MAT.couch} />
      <Rounded size={[0.2, 0.36, 0.9]} radius={0.06} position={[-1.08, 0.5, 0]} material={MAT.couch} />
      <Rounded size={[0.2, 0.36, 0.9]} radius={0.06} position={[1.08, 0.5, 0]} material={MAT.couch} />
      <Rounded size={[0.96, 0.12, 0.66]} radius={0.05} position={[-0.5, 0.5, 0.06]} material={MAT.cushion} />
      <Rounded size={[0.96, 0.12, 0.66]} radius={0.05} position={[0.5, 0.5, 0.06]} material={MAT.cushion} />
    </group>
  );
}

/** A glass railing run from (x1,z1) to (x2,z2): posts, a top rail and a pane. */
export function Railing({ from, to, height = 1.05 }: { from: [number, number]; to: [number, number]; height?: number }) {
  const [x1, z1] = from;
  const [x2, z2] = to;
  const length = Math.hypot(x2 - x1, z2 - z1);
  const angle = Math.atan2(z2 - z1, x2 - x1);
  const posts = Math.max(2, Math.round(length / 2.4) + 1);
  return (
    <group position={[(x1 + x2) / 2, 0, (z1 + z2) / 2]} rotation={[0, -angle, 0]}>
      <Box size={[length, 0.06, 0.08]} position={[0, height, 0]} material={MAT.railing} />
      <Box size={[length, height - 0.1, 0.02]} position={[0, (height - 0.1) / 2 + 0.05, 0]} material={MAT.glass} cast={false} />
      {Array.from({ length: posts }, (_, i) => (
        <Box key={i} size={[0.06, height, 0.06]} position={[-length / 2 + (length * i) / (posts - 1), height / 2, 0]} material={MAT.railing} />
      ))}
    </group>
  );
}

const signTextures = new Map<string, Texture | null>();
function signTexture(label: string): Texture | null {
  if (signTextures.has(label)) return signTextures.get(label) ?? null;
  const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
  const ctx = canvas?.getContext("2d") ?? null;
  let texture: Texture | null = null;
  if (canvas && ctx) {
    canvas.width = 512;
    canvas.height = 96;
    ctx.fillStyle = OFFICE.signBoard;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = OFFICE.signText;
    ctx.font = "600 52px system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label.length > 18 ? `${label.slice(0, 17)}…` : label, canvas.width / 2, canvas.height / 2 + 2);
    const made = new CanvasTexture(canvas);
    made.colorSpace = SRGBColorSpace;
    made.anisotropy = 4;
    texture = made;
  }
  signTextures.set(label, texture);
  return texture;
}

/** A department's back wall: white partition, dark-wood name board, slatted wood panels. */
export function SignWall({ label, width, position }: { label: string; width: number; position: [number, number, number] }) {
  const texture = signTexture(label);
  const slats = Math.max(2, Math.floor(width / 0.5));
  return (
    <group position={position}>
      <Box size={[width, 1.9, 0.14]} position={[0, 0.95, 0]} material={MAT.wall} />
      <Box size={[width * 0.52, 0.5, 0.05]} position={[0, 1.35, 0.1]} material={MAT.sign} />
      {texture && (
        <mesh position={[0, 1.35, 0.13]}>
          <planeGeometry args={[width * 0.5, (width * 0.5 * 96) / 512]} />
          <meshStandardMaterial map={texture} roughness={0.8} />
        </mesh>
      )}
      {Array.from({ length: slats }, (_, i) => {
        const x = -width / 2 + 0.25 + i * ((width - 0.5) / (slats - 1));
        if (Math.abs(x) < width * 0.3) return null;
        return <Box key={i} size={[0.12, 1.7, 0.05]} position={[x, 0.95, 0.1]} material={MAT.woodDark} />;
      })}
    </group>
  );
}

/** A flat rug under the lounge. */
export function Rug({ position, size }: { position: [number, number, number]; size: [number, number] }) {
  return <Box size={[size[0], 0.02, size[1]]} position={position} material={MAT.rug} cast={false} />;
}
