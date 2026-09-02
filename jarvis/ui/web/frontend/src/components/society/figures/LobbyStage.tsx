/**
 * The lobby behind a figure — the room the model card and the creator spawn
 * an agent into (maintainer, 2026-09-02: "a lobby in the background, a bit
 * like a Fortnite lobby", "professional, in our theme, correct from every
 * angle, from below too").
 *
 * A floating platform, not a walled room: the viewer's camera may tilt to
 * 80° below the floor, and a room with a floor and walls has nothing honest
 * to show from there. A platform does — its underside is designed (a dark
 * base cap with a glowing rim), so every angle the camera can reach shows
 * closed, finished geometry: the tiled top with the accent ring, the bevelled
 * edge, the base, a curved display wall behind the figure (two-sided), four
 * pillars carrying light strips, and the app's room around it all.
 *
 * Colours are the app's own tokens, read from the stylesheet at mount and
 * again when the theme flips (`--background`, `--secondary`, `--card`,
 * `--border`, `--border-strong`, `--accent`): the lobby is the v4 room in
 * both themes, never a colour of its own. A rim light in the accent colour
 * from behind separates a dark figure (Gigi is near-black) from the dark
 * room, which is how a lobby does it rather than by painting the room warm.
 *
 * Everything is sized in figure heights so a 0.6 m fox and a 2.4 m spirit
 * both fit; the accent ring breathes only while the scene is awake.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

export interface LobbyStageProps {
  /** The figure's rendered height; every measure here is relative to it. */
  heightM: number;
  /** No breathing glow while paused (reduced motion, off screen). */
  paused: boolean;
  /**
   * The platform's radius, when the thing on it is wider than a figure — a
   * building card passes its footprint. The wall and the pillars keep their
   * distance from the platform's edge. Default: 1.35 heights.
   */
  platformR?: number;
}

interface LobbyTokens {
  dark: boolean;
  room: THREE.Color;
  lift: THREE.Color;
  object: THREE.Color;
  line: THREE.Color;
  lineStrong: THREE.Color;
  accent: THREE.Color;
}

/** `--name: H S% L%` → a colour; a missing token falls back to `fallback`. */
function readToken(style: CSSStyleDeclaration, name: string, fallback: string): THREE.Color {
  const raw = style.getPropertyValue(name).trim();
  const m = raw.match(/^([\d.]+)\s+([\d.]+)%\s+([\d.]+)%$/);
  const c = new THREE.Color();
  if (!m) return c.set(fallback);
  return c.setHSL(Number(m[1]) / 360, Number(m[2]) / 100, Number(m[3]) / 100, THREE.SRGBColorSpace);
}

function readTokens(): LobbyTokens {
  if (typeof document === "undefined") {
    return {
      dark: true,
      room: new THREE.Color("#0a0a0a"),
      lift: new THREE.Color("#1f1f1f"),
      object: new THREE.Color("#171717"),
      line: new THREE.Color("#262626"),
      lineStrong: new THREE.Color("#383838"),
      accent: new THREE.Color("#3d8bff"),
    };
  }
  const root = document.documentElement;
  const style = getComputedStyle(root);
  return {
    dark: root.classList.contains("dark"),
    room: readToken(style, "--background", "#0a0a0a"),
    lift: readToken(style, "--secondary", "#1f1f1f"),
    object: readToken(style, "--card", "#171717"),
    line: readToken(style, "--border", "#262626"),
    lineStrong: readToken(style, "--border-strong", "#383838"),
    accent: readToken(style, "--accent", "#3d8bff"),
  };
}

/** The app's tokens, re-read whenever the root's class list (the theme) changes. */
function useLobbyTokens(): LobbyTokens {
  const [tokens, setTokens] = useState<LobbyTokens>(readTokens);
  useEffect(() => {
    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(() => setTokens(readTokens()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return tokens;
}

function pixelTexture(canvas: HTMLCanvasElement, repeatX: number, repeatY: number): THREE.CanvasTexture {
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(repeatX, repeatY);
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** The platform's top: tiles in the lift tone, hairlines in the border tone. */
function makeTileTexture(t: LobbyTokens): THREE.CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = t.lift.getStyle();
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = t.lineStrong.getStyle();
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, size - 2, size - 2);
  return pixelTexture(canvas, 14, 14);
}

/** The pool of light under the figure: accent-tinted at the centre, gone at the rim. */
function makePoolTexture(t: LobbyTokens): THREE.CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const grad = ctx.createRadialGradient(size / 2, size / 2, 2, size / 2, size / 2, size / 2);
  const a = t.accent;
  const rgb = `${Math.round(a.r * 255)}, ${Math.round(a.g * 255)}, ${Math.round(a.b * 255)}`;
  grad.addColorStop(0, `rgba(${rgb}, ${t.dark ? 0.35 : 0.22})`);
  grad.addColorStop(0.6, `rgba(${rgb}, ${t.dark ? 0.1 : 0.06})`);
  grad.addColorStop(1, `rgba(${rgb}, 0)`);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export function LobbyStage({ heightM, paused, platformR }: LobbyStageProps) {
  const h = heightM;
  const t = useLobbyTokens();
  const ringRef = useRef<THREE.MeshStandardMaterial>(null);
  const underRef = useRef<THREE.MeshStandardMaterial>(null);

  const tiles = useMemo(() => makeTileTexture(t), [t]);
  const pool = useMemo(() => makePoolTexture(t), [t]);
  useEffect(
    () => () => {
      tiles?.dispose();
      pool?.dispose();
    },
    [tiles, pool],
  );

  useFrame(({ clock }) => {
    if (paused) return;
    const pulse = 0.7 + Math.sin(clock.elapsedTime * 1.4) * 0.25;
    if (ringRef.current) ringRef.current.emissiveIntensity = pulse;
    if (underRef.current) underRef.current.emissiveIntensity = pulse * 0.8;
  });

  // Measures, in figure heights.
  const platR = platformR ?? h * 1.35;
  const platH = h * 0.3;
  const wallR = Math.max(h * 3.0, platR * 2.2);
  const wallH = h * 2.4;
  const pillarR = Math.max(h * 2.35, platR * 1.75);

  const accentHex = `#${t.accent.getHexString()}`;
  const glow = t.dark ? 1.0 : 0.7;

  const pillars = useMemo(() => {
    const angles = [-1.25, -0.45, 0.45, 1.25]; // around the back half, 0 = straight behind
    return angles.map((a) => ({
      x: Math.sin(a) * pillarR,
      z: -Math.cos(a) * pillarR,
      ry: -a,
    }));
  }, [pillarR]);

  // The display wall: seven flat panels on an arc behind the figure. Flat
  // slabs, not a curved sheet — the pixel pass draws every facet edge, and
  // on a smooth cylinder those read as spokes from above; on panels they
  // are the seams a panelled wall has.
  const panels = useMemo(() => {
    const n = 7;
    const span = 1.9;
    return Array.from({ length: n }, (_, i) => {
      const a = -span / 2 + (span * i) / (n - 1);
      return { x: Math.sin(a) * wallR, z: -Math.cos(a) * wallR, ry: -a, lifted: i % 2 === 0 };
    });
  }, [wallR]);
  const panelW = (wallR * 1.9) / 7 + h * 0.02;

  return (
    <group>
      {/* Lights: a key from the front, a soft fill, and the accent rim from behind. */}
      <hemisphereLight args={[t.dark ? 0xaab3c2 : 0xffffff, t.dark ? 0x1a1a1a : 0x8a8a8a, t.dark ? 0.9 : 1.0]} />
      <directionalLight position={[2.2 * h, 3.4 * h, 2.8 * h]} intensity={t.dark ? 1.6 : 1.8} />
      <directionalLight position={[-2.4 * h, 1.6 * h, 1.2 * h]} intensity={0.35} />
      <directionalLight position={[0.6 * h, 2.2 * h, -3 * h]} intensity={t.dark ? 1.4 : 0.9} color={accentHex} />

      {/* The platform: one closed body (side, plain caps) … */}
      <mesh position={[0, -platH / 2, 0]}>
        <cylinderGeometry args={[platR, platR, platH, 48]} />
        <meshStandardMaterial attach="material-0" color={t.lift} />
        <meshStandardMaterial attach="material-1" color={t.lift} />
        <meshStandardMaterial attach="material-2" color={t.line} />
      </mesh>
      {/* … and the tiled top as a flat disc: a circle's UVs are planar, so the
          grid stays a grid; a cylinder cap's are polar and turned it into spokes. */}
      <mesh position={[0, 0.001, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[platR, 48]} />
        {tiles ? (
          <meshStandardMaterial map={tiles} color={0xffffff} />
        ) : (
          <meshStandardMaterial color={t.lift} />
        )}
      </mesh>
      {/* the bevel lip on the top edge */}
      <mesh position={[0, -h * 0.02, 0]}>
        <cylinderGeometry args={[platR * 1.03, platR * 1.03, h * 0.04, 48]} />
        <meshStandardMaterial color={t.lineStrong} />
      </mesh>
      {/* the accent ring inset in the top */}
      <mesh position={[0, 0.003, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[platR * 0.86, platR * 0.9, 64]} />
        <meshStandardMaterial
          ref={ringRef}
          color={accentHex}
          emissive={accentHex}
          emissiveIntensity={0.7 * glow}
          side={THREE.DoubleSide}
        />
      </mesh>
      {/* the glowing rim under the platform — what a view from below is for */}
      <mesh position={[0, -platH + h * 0.03, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[platR * 0.98, h * 0.022, 8, 64]} />
        <meshStandardMaterial ref={underRef} color={accentHex} emissive={accentHex} emissiveIntensity={0.6 * glow} />
      </mesh>
      {/* the pool of light the figure stands in, then a soft contact shadow */}
      {pool ? (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.004, 0]}>
          <planeGeometry args={[h * 1.9, h * 1.9]} />
          <meshBasicMaterial map={pool} transparent depthWrite={false} />
        </mesh>
      ) : null}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.006, 0]}>
        <circleGeometry args={[h * 0.26, 24]} />
        <meshBasicMaterial color={0x000000} transparent opacity={t.dark ? 0.35 : 0.18} depthWrite={false} />
      </mesh>

      {/* The display wall: seven panels on an arc, each with its accent line low down. */}
      {panels.map((p, i) => (
        <group key={i} position={[p.x, 0, p.z]} rotation={[0, p.ry, 0]}>
          <mesh position={[0, wallH / 2 - h * 0.25, 0]}>
            <boxGeometry args={[panelW, wallH, h * 0.08]} />
            <meshStandardMaterial color={p.lifted ? t.lineStrong : t.lift} />
          </mesh>
          <mesh position={[0, h * 0.12, h * 0.045]}>
            <boxGeometry args={[panelW * 0.92, h * 0.025, h * 0.01]} />
            <meshStandardMaterial color={accentHex} emissive={accentHex} emissiveIntensity={0.8 * glow} />
          </mesh>
        </group>
      ))}
      {/* the top rail that ties the panels together */}
      <mesh position={[0, wallH - h * 0.22, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[wallR, h * 0.035, 6, 64, 2.0]} />
        <meshStandardMaterial color={t.lineStrong} />
      </mesh>

      {/* Four pillars, each carrying a light strip. */}
      {pillars.map((p, i) => (
        <group key={i} position={[p.x, 0, p.z]} rotation={[0, p.ry, 0]}>
          <mesh position={[0, h * 1.1 - h * 0.3, 0]}>
            <boxGeometry args={[h * 0.16, h * 2.2, h * 0.16]} />
            <meshStandardMaterial color={t.lift} />
          </mesh>
          <mesh position={[0, h * 1.1 - h * 0.3, h * 0.085]}>
            <boxGeometry args={[h * 0.04, h * 1.6, h * 0.01]} />
            <meshStandardMaterial color={accentHex} emissive={accentHex} emissiveIntensity={0.9 * glow} />
          </mesh>
          <mesh position={[0, -h * 0.3 - h * 0.05, 0]}>
            <boxGeometry args={[h * 0.3, h * 0.1, h * 0.3]} />
            <meshStandardMaterial color={t.lineStrong} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

export default LobbyStage;
