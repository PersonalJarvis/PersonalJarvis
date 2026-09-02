/**
 * The lobby behind a figure — the room the model card and the creator spawn
 * an agent into (maintainer, 2026-09-02: "a lobby in the background, a bit
 * like a Fortnite lobby").
 *
 * Built from primitives, no assets: a slate podium with a ring in the agent's
 * accent colour, a tiled floor that fades into a curved back wall, warm
 * light strips on that wall, two light beams from above, a large ring
 * hanging behind the figure, and a few crates at the sides for scale. All
 * of it sits inside the pixel pass the viewer already runs, so it reads as
 * the same voxel world as the island. The room stays warm and bright in
 * both themes on purpose: a near-black figure (Gigi) must read against it.
 *
 * Geometry is sized in figure heights so a 0.6 m fox and a 2.4 m spirit both
 * stand on a podium that fits, and the walls stay outside the camera's
 * farthest zoom. The ring's glow breathes only while the scene is awake.
 */
import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

export interface LobbyStageProps {
  /** The figure's rendered height; every measure here is relative to it. */
  heightM: number;
  /** The agent's accent colour — the podium ring and the halo wear it. */
  accent: string;
  /** No breathing glow while paused (reduced motion, off screen). */
  paused: boolean;
}

const WALL = 0xefe6d6;
const WALL_PANEL = 0xe4d9c5;
const FLOOR = 0xcbbfa8;
const FLOOR_LINE = 0xb9ac94;
const SLATE = 0x3a4150;
const SLATE_DARK = 0x2b3140;
const STRIP = 0xffd9a0;
const BEAM = 0xfff1d6;

/** A canvas-drawn tile grid: one texture, repeated, filtered nearest for the pixel look. */
function makeGridTexture(): THREE.CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = `#${FLOOR.toString(16).padStart(6, "0")}`;
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = `#${FLOOR_LINE.toString(16).padStart(6, "0")}`;
  ctx.lineWidth = 3;
  ctx.strokeRect(1.5, 1.5, size - 3, size - 3);
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Vertical panels on the back wall: a lighter and a darker band, alternating. */
function makePanelTexture(): THREE.CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const w = 256;
  const h = 32;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = `#${WALL.toString(16).padStart(6, "0")}`;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = `#${WALL_PANEL.toString(16).padStart(6, "0")}`;
  for (let x = 0; x < w; x += 64) ctx.fillRect(x + 28, 0, 8, h);
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export function LobbyStage({ heightM, accent, paused }: LobbyStageProps) {
  const h = heightM;
  const ringRef = useRef<THREE.MeshStandardMaterial>(null);
  const haloRef = useRef<THREE.MeshStandardMaterial>(null);
  const grid = useMemo(makeGridTexture, []);
  const panels = useMemo(makePanelTexture, []);
  if (grid) grid.repeat.set(18, 18);
  if (panels) panels.repeat.set(9, 1);

  // The key light's target: R3F needs the object in the scene to aim at.
  const spotTarget = useMemo(() => {
    const o = new THREE.Object3D();
    o.position.set(0, h * 0.45, 0);
    return o;
  }, [h]);

  useFrame(({ clock }) => {
    if (paused) return;
    const pulse = 0.55 + Math.sin(clock.elapsedTime * 1.6) * 0.2;
    if (ringRef.current) ringRef.current.emissiveIntensity = pulse;
    if (haloRef.current) haloRef.current.emissiveIntensity = pulse * 0.6;
  });

  // The room's reach: farther than the viewer's widest zoom can look.
  const wallR = h * 5.2;
  const wallH = h * 4.2;
  const floorR = h * 8;
  const podiumR = h * 0.58;

  const strips = useMemo(() => {
    const out: { x: number; z: number; ry: number }[] = [];
    const n = 7;
    for (let i = 0; i < n; i++) {
      const a = Math.PI * (0.16 + (0.68 * i) / (n - 1)); // spread across the back half
      out.push({ x: Math.cos(a) * (wallR - 0.05), z: -Math.sin(a) * (wallR - 0.05), ry: Math.PI / 2 - a });
    }
    return out;
  }, [wallR]);

  const crates = useMemo(
    () => [
      { p: [-h * 2.2, h * 0.3, -h * 1.4], s: h * 0.6, c: SLATE },
      { p: [-h * 2.55, h * 0.18, -h * 0.7], s: h * 0.36, c: FLOOR_LINE },
      { p: [h * 2.35, h * 0.24, -h * 1.1], s: h * 0.48, c: SLATE },
      { p: [h * 2.35, h * 0.72, -h * 1.1], s: h * 0.3, c: FLOOR_LINE },
      { p: [h * 1.9, h * 0.16, -h * 2.2], s: h * 0.32, c: SLATE_DARK },
    ],
    [h],
  );

  return (
    <group>
      <fog attach="fog" args={[WALL, wallR * 0.9, wallR * 2.1]} />
      <primitive object={spotTarget} />
      <spotLight
        position={[0, h * 3.4, h * 1.2]}
        target={spotTarget}
        angle={0.42}
        penumbra={0.7}
        intensity={2.4}
        color={BEAM}
        distance={h * 8}
      />

      {/* floor */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.001, 0]} receiveShadow>
        <circleGeometry args={[floorR, 48]} />
        {grid ? <meshStandardMaterial map={grid} color={0xffffff} /> : <meshStandardMaterial color={FLOOR} />}
      </mesh>

      {/* podium: a wide base, a narrower top, the accent ring between */}
      <mesh position={[0, -h * 0.06, 0]}>
        <cylinderGeometry args={[podiumR * 1.3, podiumR * 1.38, h * 0.12, 32]} />
        <meshStandardMaterial color={SLATE_DARK} />
      </mesh>
      <mesh position={[0, -h * 0.012, 0]}>
        <cylinderGeometry args={[podiumR, podiumR, h * 0.024, 32]} />
        <meshStandardMaterial color={SLATE} />
      </mesh>
      <mesh position={[0, 0.002, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[podiumR * 1.02, podiumR * 1.14, 40]} />
        <meshStandardMaterial ref={ringRef} color={accent} emissive={accent} emissiveIntensity={0.55} />
      </mesh>

      {/* the back wall: a half cylinder seen from inside, panelled */}
      <mesh position={[0, wallH / 2 - 0.02, 0]} rotation={[0, Math.PI, 0]}>
        <cylinderGeometry args={[wallR, wallR, wallH, 48, 1, true, 0, Math.PI]} />
        {panels ? (
          <meshStandardMaterial map={panels} color={0xffffff} side={THREE.BackSide} />
        ) : (
          <meshStandardMaterial color={WALL} side={THREE.BackSide} />
        )}
      </mesh>
      {/* a dark skirting where wall meets floor */}
      <mesh position={[0, h * 0.09, 0]} rotation={[0, Math.PI, 0]}>
        <cylinderGeometry args={[wallR - 0.02, wallR - 0.02, h * 0.18, 48, 1, true, 0, Math.PI]} />
        <meshStandardMaterial color={SLATE} side={THREE.BackSide} />
      </mesh>

      {/* warm light strips on the wall */}
      {strips.map((s, i) => (
        <mesh key={i} position={[s.x, wallH * 0.52, s.z]} rotation={[0, s.ry, 0]}>
          <boxGeometry args={[h * 0.07, wallH * 0.62, h * 0.04]} />
          <meshStandardMaterial color={STRIP} emissive={STRIP} emissiveIntensity={0.9} />
        </mesh>
      ))}

      {/* the halo behind the figure */}
      <mesh position={[0, h * 0.95, -h * 1.9]}>
        <torusGeometry args={[h * 1.05, h * 0.035, 8, 48]} />
        <meshStandardMaterial ref={haloRef} color={accent} emissive={accent} emissiveIntensity={0.35} />
      </mesh>

      {/* two light beams from above */}
      {[-1, 1].map((side) => (
        <mesh key={side} position={[side * h * 1.5, h * 2.4, -h * 0.9]} rotation={[0, 0, side * 0.32]}>
          <coneGeometry args={[h * 0.95, h * 4.6, 24, 1, true]} />
          <meshBasicMaterial
            color={BEAM}
            transparent
            opacity={0.14}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
            side={THREE.DoubleSide}
          />
        </mesh>
      ))}

      {/* crates for scale */}
      {crates.map((c, i) => (
        <mesh key={i} position={c.p as [number, number, number]}>
          <boxGeometry args={[c.s, c.s, c.s]} />
          <meshStandardMaterial color={c.c} />
        </mesh>
      ))}

      {/* a soft contact blob under the feet, as before */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.004, 0]}>
        <circleGeometry args={[h * 0.26, 24]} />
        <meshBasicMaterial color={0x1a2230} transparent opacity={0.22} depthWrite={false} />
      </mesh>
    </group>
  );
}

export default LobbyStage;
