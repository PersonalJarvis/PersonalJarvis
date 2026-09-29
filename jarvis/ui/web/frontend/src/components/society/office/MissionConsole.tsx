/**
 * Mission Control's console ring on the coding floor: six slanted consoles
 * facing outwards around a projector that holds a slowly turning holo-globe.
 *
 * Built in local space centred on the origin and kept inside the
 * `missionConsole` footprint (3.2 m × 3.2 m, 2.2 m high) that navigation
 * walks around. Geometries and materials are module-level singletons.
 */
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import { CylinderGeometry, MeshStandardMaterial, SphereGeometry, TorusGeometry, type Group } from "three";
import { Box, matte, Rounded } from "./OfficeFurniture";
import type { Furniture, FurnitureKind } from "./officeLayout";

const HOLO = "#38bdf8";

const MM = {
  base: matte("#2b3446", { roughness: 0.55, metalness: 0.35 }),
  trim: matte("#1b2130", { roughness: 0.45, metalness: 0.4 }),
  body: matte("#dfe4ec", { roughness: 0.6 }),
  bezel: matte("#1f2533", { roughness: 0.5 }),
  screen: new MeshStandardMaterial({ color: "#0b1a2e", emissive: HOLO, emissiveIntensity: 0.45, roughness: 0.3 }),
  bar: new MeshStandardMaterial({ color: "#7dd3fc", emissive: "#7dd3fc", emissiveIntensity: 0.9 }),
  barWarm: new MeshStandardMaterial({ color: "#fbbf24", emissive: "#fbbf24", emissiveIntensity: 0.8 }),
  holo: new MeshStandardMaterial({ color: HOLO, emissive: HOLO, emissiveIntensity: 1.1, wireframe: true, transparent: true, opacity: 0.75 }),
  holoCore: new MeshStandardMaterial({ color: HOLO, emissive: HOLO, emissiveIntensity: 0.8, transparent: true, opacity: 0.18, depthWrite: false }),
  lens: new MeshStandardMaterial({ color: "#bae6fd", emissive: HOLO, emissiveIntensity: 1.4 }),
};

const GEO = {
  base: new CylinderGeometry(1.55, 1.58, 0.1, 48),
  ring: new CylinderGeometry(1.2, 1.2, 0.02, 48, 1, true),
  pillar: new CylinderGeometry(0.16, 0.22, 1.0, 20),
  lens: new CylinderGeometry(0.24, 0.24, 0.05, 24),
  globe: new SphereGeometry(0.46, 14, 10),
  core: new SphereGeometry(0.4, 20, 14),
  orbit: new TorusGeometry(0.62, 0.012, 6, 48),
};

/** Consoles around the ring; each faces away from the centre, towards someone standing outside. */
const CONSOLES = Array.from({ length: 6 }, (_, i) => (i * Math.PI) / 3 + Math.PI / 6);
const CONSOLE_RADIUS = 1.08;

function Console({ angle }: { angle: number }) {
  // Local +z is the console's front; rotate so the front points outwards along `angle`.
  const x = Math.sin(angle) * CONSOLE_RADIUS;
  const z = Math.cos(angle) * CONSOLE_RADIUS;
  return (
    <group position={[x, 0, z]} rotation={[0, angle, 0]}>
      <Rounded size={[0.92, 0.78, 0.46]} radius={0.05} position={[0, 0.49, -0.02]} material={MM.body} />
      <Box size={[0.94, 0.04, 0.5]} position={[0, 0.9, 0]} material={MM.trim} />
      <group position={[0, 1.14, -0.08]} rotation={[-0.5, 0, 0]}>
        <Rounded size={[0.86, 0.5, 0.06]} radius={0.02} position={[0, 0, 0]} material={MM.bezel} />
        <Box size={[0.78, 0.42, 0.01]} position={[0, 0, 0.031]} material={MM.screen} cast={false} />
        {[-0.24, -0.12, 0, 0.12, 0.24].map((bx, i) => {
          const h = 0.08 + ((i * 37 + Math.round(angle * 10)) % 5) * 0.05;
          return <Box key={bx} size={[0.07, h, 0.004]} position={[bx, -0.18 + h / 2, 0.038]} material={i === 3 ? MM.barWarm : MM.bar} cast={false} />;
        })}
      </group>
    </group>
  );
}

function Hologram() {
  const spin = useRef<Group>(null);
  const reduced = useReducedMotion() ?? false;
  useFrame((_, delta) => {
    if (reduced || !spin.current) return;
    spin.current.rotation.y += delta * 0.35;
  });
  return (
    <group position={[0, 1.68, 0]}>
      <group ref={spin}>
        <mesh geometry={GEO.globe} material={MM.holo} />
        <mesh geometry={GEO.orbit} material={MM.holo} rotation={[Math.PI / 2.6, 0, 0]} />
      </group>
      <mesh geometry={GEO.core} material={MM.holoCore} />
    </group>
  );
}

/** The whole hub: base disc, glowing floor ring, six consoles, the projector and its globe. */
export function MissionConsole() {
  return (
    <group>
      <mesh geometry={GEO.base} material={MM.base} position={[0, 0.05, 0]} receiveShadow />
      <mesh geometry={GEO.ring} material={MM.lens} position={[0, 0.105, 0]} />
      {CONSOLES.map((angle) => <Console key={angle} angle={angle} />)}
      <mesh geometry={GEO.pillar} material={MM.trim} position={[0, 0.6, 0]} castShadow />
      <mesh geometry={GEO.lens} material={MM.lens} position={[0, 1.12, 0]} />
      <Hologram />
    </group>
  );
}

/** Mission Control's renderers; merged into OfficeProps' exhaustive table. */
export const MISSION_RENDERERS = {
  missionConsole: () => <MissionConsole />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;
