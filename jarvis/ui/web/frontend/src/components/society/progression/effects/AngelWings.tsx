/**
 * The level-40 wings: a pair of gold-adorned angel wings worn on the figure's
 * back. They are rendered inside the figure's torso, so they ride every hop,
 * bob and lean of the body instead of following its ground position.
 *
 * Each wing is an arm (shoulder → elbow → wrist) carrying a fan of long
 * secondaries and two rows of coverts, and a hand at the wrist carrying the
 * primaries and their coverts. Every flight feather fades from pearl white
 * into a gold tip and sits on a slightly larger gold blade that shows as a
 * gilded rim. The arm wears a gold rail with jewelled joints and filigree
 * scrolls, a warm glow sits behind each wing and gold dust drifts off them.
 * The feathers of one layer are one instanced mesh, so a wing costs a handful
 * of draw calls. The wings fold tight while the wearer sits, tuck in while it
 * walks, rest half open while it stands and spread wide and beat in the air.
 */
import { useLayoutEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import {
  AdditiveBlending, BufferAttribute, BufferGeometry, CatmullRomCurve3, Color, DoubleSide, Euler, Float32BufferAttribute,
  Matrix4, MeshStandardMaterial, Quaternion, Shape, ShapeGeometry, SphereGeometry, TubeGeometry, Vector3,
  type Group, type InstancedMesh, type Material, type Points,
} from "three";
import type { FigureMode } from "../../figures/FigureRig";
import { glowTexture, starTexture } from "./flairTextures";

/** One feather in its wing's plane: x points away from the body, y up (rig metres). */
interface Feather {
  x: number;
  y: number;
  /** Direction the quill points, radians from +x towards +y. */
  angle: number;
  length: number;
  /** Vane width as a share of the length. */
  width: number;
  /** Depth offset; more negative sits nearer a viewer behind the wearer. */
  z: number;
}

const PEARL = new Color("#fffdf6");
const GOLD = new Color("#f2bf4c");
const PALE_GOLD = new Color("#fbe3a2");
const DEG = Math.PI / 180;
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

// ------------------------------------------------------------------ shared geometry and materials
// Built once per page on first use and shared by every wearer, like the flair textures.

let shared: {
  flight: BufferGeometry; covert: BufferGeometry; feather: Material; gold: Material; gem: Material;
  knob: BufferGeometry; jewel: BufferGeometry;
} | null = null;

/** A unit-length feather along +x, tip bent slightly back, coloured from pearl into `tip` from `fadeAt` on. */
function bentFeather(shape: Shape, tip: Color, fadeAt: number): BufferGeometry {
  const geometry = new ShapeGeometry(shape, 8);
  const position = geometry.attributes.position;
  const colours = new Float32Array(position.count * 3);
  const c = new Color();
  for (let i = 0; i < position.count; i += 1) {
    const x = position.getX(i);
    position.setZ(i, -0.09 * x * x);
    const t = Math.max(0, (x - fadeAt) / (1 - fadeAt));
    c.copy(PEARL).lerp(tip, t * t * (3 - 2 * t));
    c.toArray(colours, i * 3);
  }
  geometry.setAttribute("color", new Float32BufferAttribute(colours, 3));
  geometry.computeVertexNormals();
  return geometry;
}

function wingKit() {
  if (shared) return shared;
  // Long flight feather: narrow leading vane, wider trailing vane, rounded tip.
  const flight = new Shape();
  flight.moveTo(0, 0);
  flight.bezierCurveTo(0.15, 0.06, 0.55, 0.1, 0.86, 0.07);
  flight.quadraticCurveTo(1, 0.04, 1, 0);
  flight.quadraticCurveTo(0.98, -0.08, 0.82, -0.12);
  flight.bezierCurveTo(0.55, -0.15, 0.15, -0.1, 0, 0);
  // Short, round covert feather.
  const covert = new Shape();
  covert.moveTo(0, 0);
  covert.bezierCurveTo(0.2, 0.2, 0.75, 0.24, 1, 0);
  covert.bezierCurveTo(0.75, -0.24, 0.2, -0.2, 0, 0);
  shared = {
    flight: bentFeather(flight, GOLD, 0.55),
    covert: bentFeather(covert, PALE_GOLD, 0.6),
    feather: new MeshStandardMaterial({
      color: "#ffffff", vertexColors: true, roughness: 0.5, metalness: 0.05,
      emissive: "#fff0c8", emissiveIntensity: 0.28, side: DoubleSide,
    }),
    gold: new MeshStandardMaterial({
      color: "#f3bd3f", roughness: 0.26, metalness: 0.8, emissive: "#c48a12", emissiveIntensity: 0.5, side: DoubleSide,
    }),
    gem: new MeshStandardMaterial({ color: "#fffbe8", emissive: "#ffe9a6", emissiveIntensity: 2.2, roughness: 0.1, toneMapped: false }),
    knob: new SphereGeometry(1, 16, 12),
    jewel: new SphereGeometry(1, 12, 10),
  };
  return shared;
}

// ------------------------------------------------------------------ the feather plan

/** The arm's leading edge: shoulder, elbow, wrist (wing plane, rig metres). */
const ARM: readonly [number, number][] = [[0.03, 0], [0.2, 0.3], [0.4, 0.5]];

function onArm(t: number): [number, number] {
  const k = Math.min(1, Math.max(0, t)) * (ARM.length - 1);
  const i = Math.min(ARM.length - 2, Math.floor(k));
  const f = k - i;
  return [lerp(ARM[i][0], ARM[i + 1][0], f), lerp(ARM[i][1], ARM[i + 1][1], f)];
}

function armLayers(): { secondaries: Feather[]; coverts: Feather[] } {
  const secondaries: Feather[] = [];
  for (let i = 0; i < 11; i += 1) {
    const u = i / 10;
    const [x, y] = onArm(u * 0.96);
    secondaries.push({ x, y, angle: lerp(-94, -58, u) * DEG, length: lerp(0.5, 0.56, u), width: 0.95, z: i * 0.002 });
  }
  const coverts: Feather[] = [];
  for (let i = 0; i < 12; i += 1) {
    const u = i / 11;
    const [x, y] = onArm(u);
    coverts.push({ x, y: y - 0.05, angle: lerp(-84, -50, u) * DEG, length: 0.26, width: 1, z: -0.02 + i * 0.001 });
  }
  for (let i = 0; i < 14; i += 1) {
    const u = i / 13;
    const [x, y] = onArm(u);
    coverts.push({ x, y: y - 0.005, angle: lerp(-72, -35, u) * DEG, length: 0.14, width: 1.1, z: -0.032 + i * 0.001 });
  }
  return { secondaries, coverts };
}

const PRIMARY_LENGTHS = [0.66, 0.78, 0.82, 0.8, 0.76, 0.71, 0.66, 0.62, 0.58, 0.55, 0.52];

function handLayers(): { primaries: Feather[]; coverts: Feather[] } {
  const primaries: Feather[] = PRIMARY_LENGTHS.map((length, i) => {
    const u = i / (PRIMARY_LENGTHS.length - 1);
    return { x: lerp(0.08, 0, u), y: lerp(0.025, 0, u), angle: lerp(38, -56, u) * DEG, length, width: 0.85, z: 0.012 + i * 0.002 };
  });
  const coverts: Feather[] = [];
  for (let i = 0; i < 7; i += 1) {
    const u = i / 6;
    coverts.push({ x: lerp(0.07, -0.02, u), y: lerp(0.035, -0.02, u), angle: lerp(30, -42, u) * DEG, length: 0.3, width: 1, z: -0.015 + i * 0.001 });
  }
  return { primaries, coverts };
}

/** The gold blade inside a flight feather: a touch longer and wider, so a gilded rim shows around it. */
function trimOf(feathers: readonly Feather[]): Feather[] {
  return feathers.map((f) => ({ ...f, length: f.length * 1.045, width: f.width * 1.25, z: f.z + 0.006 }));
}

/** The feather's far face: a second vane behind the gold blade, so the rim reads from both sides. */
function farFaceOf(feathers: readonly Feather[]): Feather[] {
  return feathers.map((f) => ({ ...f, z: f.z + 0.012 }));
}

const ARM_LAYERS = armLayers();
const HAND_LAYERS = handLayers();
const SECONDARY_TRIM = trimOf(ARM_LAYERS.secondaries);
const PRIMARY_TRIM = trimOf(HAND_LAYERS.primaries);
const SECONDARY_FAR = farFaceOf(ARM_LAYERS.secondaries);
const PRIMARY_FAR = farFaceOf(HAND_LAYERS.primaries);

function Feathers({ feathers, geometry, material, shadow }: {
  feathers: readonly Feather[]; geometry: BufferGeometry; material: Material; shadow: boolean;
}) {
  const mesh = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const m = mesh.current;
    if (!m) return;
    const matrix = new Matrix4();
    const q = new Quaternion();
    const e = new Euler();
    const p = new Vector3();
    const s = new Vector3();
    feathers.forEach((f, i) => {
      // Rolled a little about the quill so neighbours overlap like shingles.
      q.setFromEuler(e.set(0.28, 0, f.angle));
      matrix.compose(p.set(f.x, f.y, f.z), q, s.set(f.length, f.length * f.width, f.length));
      m.setMatrixAt(i, matrix);
    });
    m.instanceMatrix.needsUpdate = true;
  }, [feathers]);
  return <instancedMesh ref={mesh} args={[geometry, material, feathers.length]} castShadow={shadow} frustumCulled={false} />;
}

// ------------------------------------------------------------------ gold ornaments

/** A flat spiral in the wing plane, for the filigree scrolls under the arm. */
function scrollCurve(cx: number, cy: number, radius: number, turns: number, start: number, z: number): CatmullRomCurve3 {
  const points: Vector3[] = [];
  for (let i = 0; i <= 24; i += 1) {
    const t = i / 24;
    const a = start + t * turns * Math.PI * 2;
    const r = radius * (1 - 0.82 * t);
    points.push(new Vector3(cx + Math.cos(a) * r, cy + Math.sin(a) * r, z));
  }
  return new CatmullRomCurve3(points);
}

/** The gold rail, jewelled joints and filigree on one face of the arm; `face` −1 is the outer side, +1 the body side. */
function ArmOrnaments({ face }: { face: 1 | -1 }) {
  const kit = wingKit();
  const z = face < 0 ? -0.04 : 0.05;
  const rail = useMemo(() => {
    const curve = new CatmullRomCurve3([
      new Vector3(-0.01, -0.04, z), new Vector3(0.1, 0.16, z), new Vector3(0.2, 0.3, z),
      new Vector3(0.31, 0.42, z), new Vector3(0.42, 0.51, z),
    ]);
    return new TubeGeometry(curve, 40, 0.024, 10, false);
  }, [z]);
  const scrolls = useMemo(() => [
    new TubeGeometry(scrollCurve(0.12, 0.08, 0.075, 1.3, 0.6, z), 48, 0.009, 6, false),
    new TubeGeometry(scrollCurve(0.29, 0.31, 0.065, 1.2, 0.9, z), 48, 0.008, 6, false),
    new TubeGeometry(scrollCurve(0.05, -0.11, 0.06, 1.1, 1.8, z), 40, 0.008, 6, false),
  ], [z]);
  useLayoutEffect(() => () => { rail.dispose(); scrolls.forEach((g) => g.dispose()); }, [rail, scrolls]);
  const joints: [number, number, number][] = [[0.0, -0.03, 0.05], [0.2, 0.3, 0.045], [0.42, 0.51, 0.05]];
  return (
    <group>
      <mesh geometry={rail} material={kit.gold} castShadow={face < 0} />
      {scrolls.map((g, i) => <mesh key={i} geometry={g} material={kit.gold} />)}
      {joints.map(([x, y, r], i) => (
        <group key={i} position={[x, y, z]}>
          <mesh geometry={kit.knob} material={kit.gold} scale={r} />
          <mesh geometry={kit.jewel} material={kit.gem} position={[0, 0, face * r * 0.8]} scale={r * 0.55} />
        </group>
      ))}
    </group>
  );
}

// ------------------------------------------------------------------ gold dust

const DUST = 44;

function GoldDust({ spread, paused }: { spread: { current: number }; paused: boolean }) {
  const points = useRef<Points>(null);
  const pool = useMemo(() => {
    const geometry = new BufferGeometry();
    const position = new Float32Array(DUST * 3);
    const colour = new Float32Array(DUST * 3);
    geometry.setAttribute("position", new BufferAttribute(position, 3));
    geometry.setAttribute("color", new BufferAttribute(colour, 3));
    const age = new Float32Array(DUST).map(() => Math.random() * 2.4);
    const life = new Float32Array(DUST).map(() => 1.6 + Math.random() * 1.2);
    const sway = new Float32Array(DUST).map(() => Math.random() * Math.PI * 2);
    return { geometry, position, colour, age, life, sway, tint: new Color() };
  }, []);
  useLayoutEffect(() => () => pool.geometry.dispose(), [pool]);
  const spawn = (i: number) => {
    const side = Math.random() < 0.5 ? -1 : 1;
    const reach = lerp(0.35, 1.05, spread.current);
    pool.position[i * 3] = side * (0.12 + Math.random() * reach);
    pool.position[i * 3 + 1] = -0.45 + Math.random() * 0.95;
    pool.position[i * 3 + 2] = -0.18 - Math.random() * 0.25;
    pool.age[i] = 0;
  };
  useFrame((_, rawDt) => {
    const dt = paused ? 0 : Math.min(rawDt, 0.1);
    const { position, colour, age, life, sway, tint } = pool;
    for (let i = 0; i < DUST; i += 1) {
      age[i] += dt;
      if (age[i] >= life[i]) spawn(i);
      const t = age[i] / life[i];
      position[i * 3] += Math.sin(age[i] * 2.4 + sway[i]) * 0.03 * dt;
      position[i * 3 + 1] -= 0.16 * dt;
      // Additive points fade by darkening: in, hold, out.
      const fade = Math.min(1, t * 5) * (1 - t);
      tint.copy(i % 3 === 0 ? PEARL : GOLD).multiplyScalar(fade * 1.4);
      tint.toArray(colour, i * 3);
    }
    pool.geometry.attributes.position.needsUpdate = true;
    pool.geometry.attributes.color.needsUpdate = true;
  });
  return (
    <points ref={points} geometry={pool.geometry} frustumCulled={false} renderOrder={2}>
      <pointsMaterial map={starTexture()} size={0.07} sizeAttenuation vertexColors transparent blending={AdditiveBlending} depthWrite={false} toneMapped={false} />
    </points>
  );
}

// ------------------------------------------------------------------ the wings

/** How open the wings rest in each figure mode (0 folded, 1 spread). */
const SPREAD: Record<FigureMode, number> = {
  idle: 0.8, walk: 0.45, work: 0, talk: 0.75, sit: 0, sleep: 0, celebrate: 1, wave: 0.95,
};

/** The whole pair's size on the rig; the plan above is drawn at 1. */
const WING_SCALE = 1.3;

export interface WingMotion {
  /** True while the wearer is off the floor (a jump): the wings spread and beat. */
  airborne?: () => boolean;
}

export function AngelWings({ drive, airborne, paused, reduced }: {
  drive: { current: { mode: FigureMode } };
  paused: boolean;
  reduced: boolean;
} & WingMotion) {
  const kit = wingKit();
  const arms = useRef<(Group | null)[]>([]);
  const hands = useRef<(Group | null)[]>([]);
  const state = useRef({ clock: Math.random() * 10, beat: 0 });
  const spread = useRef(SPREAD.idle);

  useFrame((_, rawDt) => {
    const s = state.current;
    const dt = paused ? 0 : Math.min(rawDt, 0.1);
    const flying = !!airborne?.();
    const target = flying ? 1 : SPREAD[drive.current.mode] ?? SPREAD.idle;
    spread.current += (target - spread.current) * (1 - Math.exp(-(flying ? 14 : 6) * dt));
    s.clock += dt;
    // A strong beat in the air that fades out after landing; a slow breath on the ground.
    s.beat += ((flying ? 1 : 0) - s.beat) * (1 - Math.exp(-8 * dt));
    const calm = reduced ? 0 : 1;
    const breath = 0.05 * Math.sin(s.clock * 1.7) * calm;
    const stroke = 0.5 * s.beat * Math.sin(s.clock * 15) * calm;
    const lag = 0.4 * s.beat * Math.sin(s.clock * 15 - 0.7) * calm;
    const k = spread.current;
    for (let i = 0; i < 2; i += 1) {
      const arm = arms.current[i];
      const hand = hands.current[i];
      if (arm) {
        arm.rotation.y = lerp(1.25, 0.12, k);
        arm.rotation.z = lerp(-0.3, 0.1, k) + breath + stroke;
      }
      if (hand) {
        hand.rotation.y = lerp(1.1, 0, k);
        hand.rotation.z = lerp(-0.75, 0, k) + breath * 0.6 + lag;
      }
    }
  });

  return (
    <group scale={WING_SCALE}>
      {[1, -1].map((side, i) => (
        // Leaned back a little so the arch clears the head; the right wing mirrors the left.
        <group key={side} position={[side * 0.04, 0, 0]} scale={[side, 1, 1]} rotation={[-0.28, 0, 0]}>
          <group ref={(g) => { arms.current[i] = g; }}>
            <mesh position={[0.4, 0.15, 0.08]} scale={1.6}>
              <planeGeometry args={[1, 1]} />
              <meshBasicMaterial map={glowTexture()} color="#ffe6a8" transparent opacity={0.24} blending={AdditiveBlending} depthWrite={false} toneMapped={false} />
            </mesh>
            <Feathers feathers={SECONDARY_FAR} geometry={kit.flight} material={kit.feather} shadow={false} />
            <Feathers feathers={SECONDARY_TRIM} geometry={kit.flight} material={kit.gold} shadow={false} />
            <Feathers feathers={ARM_LAYERS.secondaries} geometry={kit.flight} material={kit.feather} shadow />
            <Feathers feathers={ARM_LAYERS.coverts} geometry={kit.covert} material={kit.feather} shadow={false} />
            <ArmOrnaments face={-1} />
            <ArmOrnaments face={1} />
            <group ref={(g) => { hands.current[i] = g; }} position={[ARM[2][0], ARM[2][1], 0]}>
              <Feathers feathers={PRIMARY_FAR} geometry={kit.flight} material={kit.feather} shadow={false} />
              <Feathers feathers={PRIMARY_TRIM} geometry={kit.flight} material={kit.gold} shadow={false} />
              <Feathers feathers={HAND_LAYERS.primaries} geometry={kit.flight} material={kit.feather} shadow />
              <Feathers feathers={HAND_LAYERS.coverts} geometry={kit.covert} material={kit.feather} shadow={false} />
            </group>
          </group>
        </group>
      ))}
      {!reduced && <GoldDust spread={spread} paused={paused} />}
    </group>
  );
}
