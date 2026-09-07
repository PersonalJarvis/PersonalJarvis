import { useMemo, useRef, useState } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import * as THREE from "three";
import { SVGLoader } from "three/examples/jsm/loaders/SVGLoader.js";
import { MASCOT } from "@/lib/deckRoom";

/**
 * Gigi in three dimensions — the same mascot, extruded.
 *
 * No modelled or generated mesh: the figure is the mascot's OWN outline
 * (the path `MascotGigi.tsx` draws) pulled into depth, so it is exactly on
 * brand — the rounded head, the zigzag hem, the eyes, the mouth, the two
 * arms, the scanlines and the glitch pixels, all at their SVG coordinates.
 * A free route, and the only one that keeps the pixel-ghost a pixel-ghost
 * (an image-to-3D generator rounds the hem and invents detail).
 *
 * The marked parts are unlit (`MeshBasicMaterial`) on purpose: the mascot
 * GLOWS, it is not lit — which also keeps it readable on the light
 * terrace. The dark body is lit, so the room's light and the floor's
 * reflection give it volume.
 *
 * It lives: a slow breath, a turn toward the pointer, a blink every few
 * seconds. Pressing it is the click-shaped wake word, exactly like the
 * orb on the flat board (`onPress`).
 */
const BODY_PATH =
  "M 58 90 Q 58 36 128 36 Q 198 36 198 90 L 198 208 L 180 186 L 160 208 L 140 186 L 120 208 L 100 186 L 80 208 L 58 186 Z";
/** SVG box 256, figure from y=36 to y=208 → MASCOT.height world units. */
const S = MASCOT.height / 172;
const DEPTH = 0.55;
/**
 * The figure's two marking values. Every glowing part of the mascot -- eyes,
 * mouth, belt line, the lit arm, the outline and the point light -- is MARK;
 * MARK_DIM carries the shaded side, so the figure keeps a light direction.
 *
 * Held identical to the same two stops in `wiki-video/public/gigi.svg`, which
 * draws the flat version of this figure.
 */
const MARK = "#FFFFFF";
const MARK_DIM = "#C9C9C9";

/** SVG (x, y) → world (x, y) with the hem on the floor and the figure centred. */
function svgToWorld(x: number, y: number): [number, number] {
  return [(x - 128) * S, (208 - y) * S];
}

function useBodyGeometry() {
  return useMemo(() => {
    const loader = new SVGLoader();
    const { paths } = loader.parse(`<svg xmlns="http://www.w3.org/2000/svg"><path d="${BODY_PATH}"/></svg>`);
    const shapes = paths.flatMap((p) => SVGLoader.createShapes(p));
    const geo = new THREE.ExtrudeGeometry(shapes, {
      depth: DEPTH / S,
      bevelEnabled: true,
      bevelThickness: 2,
      bevelSize: 2,
      bevelSegments: 2,
      curveSegments: 24,
    });
    // SVG y grows downward: flip, scale to world, hem at y=0, centred in x.
    geo.scale(S, -S, S);
    geo.translate(-128 * S, 208 * S, 0);
    geo.computeVertexNormals();
    const edges = new THREE.EdgesGeometry(geo, 28);
    return { geo, edges };
  }, []);
}

function Pixel({ x, y, w, h, z, color }: { x: number; y: number; w: number; h: number; z: number; color: string }) {
  const [wx, wy] = svgToWorld(x + w / 2, y + h / 2);
  return (
    <mesh position={[wx, wy, z]}>
      <boxGeometry args={[w * S, h * S, 0.06]} />
      <meshBasicMaterial color={color} />
    </mesh>
  );
}

function Arm({ d, z }: { d: [number, number, number, number, number, number]; z: number }) {
  const curve = useMemo(() => {
    const [ax, ay, cx, cy, bx, by] = d;
    const [x0, y0] = svgToWorld(ax, ay);
    const [x1, y1] = svgToWorld(cx, cy);
    const [x2, y2] = svgToWorld(bx, by);
    return new THREE.QuadraticBezierCurve3(
      new THREE.Vector3(x0, y0, z),
      new THREE.Vector3(x1, y1, z),
      new THREE.Vector3(x2, y2, z),
    );
  }, [d, z]);
  return (
    <mesh>
      <tubeGeometry args={[curve, 16, 2.75 * S, 12, false]} />
      <meshBasicMaterial color={MARK} />
    </mesh>
  );
}

export function GigiFigure({
  onPress,
  pressLabel,
  pressDisabled,
  /** 0…1, the voice level — brightens the glow and the eyes. */
  levelRef,
}: {
  onPress?: () => void;
  pressLabel?: string;
  pressDisabled?: boolean;
  levelRef: { current: number };
}) {
  const { geo, edges } = useBodyGeometry();
  const group = useRef<THREE.Group>(null);
  const eyes = useRef<THREE.Group>(null);
  const glow = useRef<THREE.PointLight>(null);
  const [hover, setHover] = useState(false);
  const blink = useRef({ next: 2.5, until: 0 });
  const front = DEPTH + 0.02;

  useFrame((state, dt) => {
    const g = group.current;
    if (!g) return;
    const t = state.clock.elapsedTime;
    // breath
    g.position.y = MASCOT.position[1] + Math.sin(t * 1.3) * 0.035;
    // turn toward the pointer, gently
    const targetY = state.pointer.x * 0.32;
    g.rotation.y += (targetY - g.rotation.y) * Math.min(1, dt * 3);
    // blink
    const e = eyes.current;
    if (e) {
      if (t > blink.current.next && blink.current.until === 0) blink.current.until = t + 0.13;
      if (blink.current.until !== 0 && t > blink.current.until) {
        blink.current.until = 0;
        blink.current.next = t + 2.5 + Math.random() * 4;
      }
      const closed = blink.current.until !== 0;
      e.scale.y += ((closed ? 0.12 : 1) - e.scale.y) * Math.min(1, dt * 18);
    }
    // the glow follows the voice
    if (glow.current) glow.current.intensity = 1.4 + levelRef.current * 4.5 + (hover ? 0.4 : 0);
  });

  const press = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (!pressDisabled) onPress?.();
  };

  return (
    <group
      ref={group}
      position={[MASCOT.position[0], MASCOT.position[1], MASCOT.position[2]]}
      onClick={press}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHover(true);
        document.body.style.cursor = pressDisabled ? "default" : "pointer";
      }}
      onPointerOut={() => {
        setHover(false);
        document.body.style.cursor = "";
      }}
      name={pressLabel}
    >
      {/* body */}
      <mesh geometry={geo} castShadow receiveShadow>
        <meshStandardMaterial color="#0e0e0e" roughness={0.48} metalness={0.18} />
      </mesh>
      <lineSegments geometry={edges}>
        <lineBasicMaterial color={MARK} transparent opacity={0.9} />
      </lineSegments>

      {/* scanlines */}
      <Pixel x={58} y={131} w={140} h={2.4} z={front} color={MARK} />
      <Pixel x={58} y={159.5} w={140} h={1.4} z={front} color={MARK_DIM} />
      {/* chromatic slices */}
      <Pixel x={64} y={118} w={18} h={10} z={front} color="#5a5a5a" />
      <Pixel x={170} y={118} w={18} h={10} z={front} color="#5a5a5a" />
      {/* glitch pixels, floating just off the body */}
      {[
        [200, 104, 6, 6],
        [208, 128, 4, 4],
        [202, 146, 9, 3],
        [197, 168, 3, 5],
        [206, 176, 5, 3],
      ].map(([x, y, w, h]) => (
        <Pixel key={`r${x}${y}`} x={x} y={y} w={w} h={h} z={front * 0.6} color={MARK} />
      ))}
      {[
        [44, 96, 6, 4],
        [48, 124, 4, 6],
        [40, 148, 8, 3],
        [50, 170, 3, 5],
      ].map(([x, y, w, h]) => (
        <Pixel key={`l${x}${y}`} x={x} y={y} w={w} h={h} z={front * 0.6} color={MARK_DIM} />
      ))}

      {/* eyes (blink by scaling the group) */}
      <group ref={eyes} position={[0, svgToWorld(0, 108)[1], 0]}>
        <group position={[0, -svgToWorld(0, 108)[1], 0]}>
          <EyeDisc cx={102} cy={108} rx={10} ry={14} z={front} color={MARK} />
          <EyeDisc cx={154} cy={108} rx={10} ry={14} z={front} color={MARK} />
          <EyeDisc cx={104} cy={112} rx={4} ry={6} z={front + 0.03} color="#050505" />
          <EyeDisc cx={156} cy={112} rx={4} ry={6} z={front + 0.03} color="#050505" />
          <EyeDisc cx={106} cy={105} rx={2} ry={2} z={front + 0.05} color="#ffffff" />
          <EyeDisc cx={158} cy={105} rx={2} ry={2} z={front + 0.05} color="#ffffff" />
        </group>
      </group>
      {/* mouth */}
      <EyeDisc cx={128} cy={146} rx={7} ry={10} z={front} color={MARK} />
      <EyeDisc cx={128} cy={146} rx={3} ry={5} z={front + 0.03} color="#050505" />

      {/* arms */}
      <Arm d={[58, 140, 40, 148, 42, 162]} z={DEPTH / 2} />
      <Arm d={[198, 140, 216, 148, 214, 162]} z={DEPTH / 2} />

      {/* the glow the mascot throws onto the floor and the wall */}
      <pointLight ref={glow} color={MARK} intensity={1.4} distance={9} decay={2} position={[0, 1.6, 1.2]} />
    </group>
  );
}

/** A flat ellipse facing the viewer — an eye, a pupil, the mouth. */
function EyeDisc({ cx, cy, rx, ry, z, color }: { cx: number; cy: number; rx: number; ry: number; z: number; color: string }) {
  const [x, y] = svgToWorld(cx, cy);
  return (
    <mesh position={[x, y, z]} scale={[rx * S, ry * S, 1]}>
      <circleGeometry args={[1, 40]} />
      <meshBasicMaterial color={color} />
    </mesh>
  );
}

