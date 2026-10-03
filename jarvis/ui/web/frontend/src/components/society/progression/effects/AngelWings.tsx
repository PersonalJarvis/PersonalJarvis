/**
 * The level-40 wings: a pair of feathered angel wings worn on the figure's
 * back. They are rendered inside the figure's torso, so they ride every hop,
 * bob and lean of the body instead of following its ground position.
 *
 * Each wing is an arm (shoulder → elbow → wrist) carrying a fan of long
 * secondaries and two rows of coverts, and a hand at the wrist carrying the
 * primaries and their coverts. The feathers of one layer are one instanced
 * mesh, so a whole wing costs a handful of draw calls. The wings fold tight
 * while the wearer sits, tuck in while it walks, rest half open while it
 * stands and spread wide and beat while it is in the air.
 */
import { useLayoutEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import {
  AdditiveBlending, Color, DoubleSide, Euler, Matrix4, Quaternion, Shape, ShapeGeometry, Vector3,
  type BufferGeometry, type Group, type InstancedMesh,
} from "three";
import type { FigureMode } from "../../figures/FigureRig";
import { useProgression } from "../progressionStore";
import { equippedFor } from "../cosmetics";
import { glowTexture } from "./flairTextures";

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
  colour: string;
}

const PEARL = "#fbf8ef";
const WHITE = "#ffffff";
const GOLD = "#f6dfa4";

let featherGeometry: BufferGeometry | null = null;
let covertGeometry: BufferGeometry | null = null;

/** A unit-length feather along +x, its tip bent slightly back like a real vane. */
function bentFeather(shape: Shape): BufferGeometry {
  const geometry = new ShapeGeometry(shape, 8);
  const position = geometry.attributes.position;
  for (let i = 0; i < position.count; i += 1) {
    const x = position.getX(i);
    position.setZ(i, -0.09 * x * x);
  }
  geometry.computeVertexNormals();
  return geometry;
}

/** Long flight feather: narrow leading vane, wider trailing vane, rounded tip. Shared by every wearer. */
function flightFeather(): BufferGeometry {
  if (featherGeometry) return featherGeometry;
  const s = new Shape();
  s.moveTo(0, 0);
  s.bezierCurveTo(0.15, 0.06, 0.55, 0.1, 0.86, 0.07);
  s.quadraticCurveTo(1, 0.04, 1, 0);
  s.quadraticCurveTo(0.98, -0.08, 0.82, -0.12);
  s.bezierCurveTo(0.55, -0.15, 0.15, -0.1, 0, 0);
  featherGeometry = bentFeather(s);
  return featherGeometry;
}

/** Short, round covert feather. Shared by every wearer. */
function covertFeather(): BufferGeometry {
  if (covertGeometry) return covertGeometry;
  const s = new Shape();
  s.moveTo(0, 0);
  s.bezierCurveTo(0.2, 0.2, 0.75, 0.24, 1, 0);
  s.bezierCurveTo(0.75, -0.24, 0.2, -0.2, 0, 0);
  covertGeometry = bentFeather(s);
  return covertGeometry;
}

const DEG = Math.PI / 180;
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

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
  for (let i = 0; i < 10; i += 1) {
    const u = i / 9;
    const [x, y] = onArm(u * 0.96);
    secondaries.push({ x, y, angle: lerp(-93, -58, u) * DEG, length: lerp(0.44, 0.52, u), width: 0.95, z: i * 0.002, colour: PEARL });
  }
  const coverts: Feather[] = [];
  for (let i = 0; i < 11; i += 1) {
    const u = i / 10;
    const [x, y] = onArm(u);
    coverts.push({ x, y: y - 0.04, angle: lerp(-82, -50, u) * DEG, length: 0.22, width: 1, z: -0.02 + i * 0.001, colour: WHITE });
  }
  for (let i = 0; i < 13; i += 1) {
    const u = i / 12;
    const [x, y] = onArm(u);
    coverts.push({ x, y: y + 0.01, angle: lerp(-70, -35, u) * DEG, length: 0.12, width: 1.1, z: -0.032 + i * 0.001, colour: WHITE });
  }
  return { secondaries, coverts };
}

const PRIMARY_LENGTHS = [0.6, 0.7, 0.73, 0.71, 0.67, 0.63, 0.59, 0.56, 0.53, 0.5];

function handLayers(): { primaries: Feather[]; coverts: Feather[] } {
  const primaries: Feather[] = PRIMARY_LENGTHS.map((length, i) => {
    const u = i / (PRIMARY_LENGTHS.length - 1);
    const gold = new Color(GOLD).lerp(new Color(PEARL), u).getStyle();
    return { x: lerp(0.07, 0, u), y: lerp(0.02, 0, u), angle: lerp(34, -54, u) * DEG, length, width: 0.85, z: 0.01 + i * 0.002, colour: gold };
  });
  const coverts: Feather[] = [];
  for (let i = 0; i < 6; i += 1) {
    const u = i / 5;
    coverts.push({ x: lerp(0.06, -0.02, u), y: lerp(0.03, -0.02, u), angle: lerp(26, -40, u) * DEG, length: 0.26, width: 1, z: -0.015 + i * 0.001, colour: WHITE });
  }
  return { primaries, coverts };
}

const ARM_LAYERS = armLayers();
const HAND_LAYERS = handLayers();

function Feathers({ feathers, geometry, shadow }: { feathers: readonly Feather[]; geometry: BufferGeometry; shadow: boolean }) {
  const mesh = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const m = mesh.current;
    if (!m) return;
    const matrix = new Matrix4();
    const q = new Quaternion();
    const e = new Euler();
    const p = new Vector3();
    const s = new Vector3();
    const c = new Color();
    feathers.forEach((f, i) => {
      // Rolled a little about the quill so neighbours overlap like shingles.
      q.setFromEuler(e.set(0.28, 0, f.angle));
      matrix.compose(p.set(f.x, f.y, f.z), q, s.set(f.length, f.length * f.width, f.length));
      m.setMatrixAt(i, matrix);
      m.setColorAt(i, c.set(f.colour));
    });
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [feathers]);
  return (
    <instancedMesh ref={mesh} args={[geometry, undefined, feathers.length]} castShadow={shadow} frustumCulled={false}>
      <meshStandardMaterial color={WHITE} roughness={0.55} metalness={0} emissive="#fff1cf" emissiveIntensity={0.22} side={DoubleSide} />
    </instancedMesh>
  );
}

/** How open the wings rest in each figure mode (0 folded, 1 spread). */
const SPREAD: Record<FigureMode, number> = {
  idle: 0.5, walk: 0.3, work: 0, talk: 0.45, sit: 0, sleep: 0, celebrate: 0.95, wave: 0.85,
};

export interface WingMotion {
  /** True while the wearer is off the floor (a jump): the wings spread and beat. */
  airborne?: () => boolean;
}

export function AngelWings({ drive, airborne, paused, reduced }: {
  drive: { current: { mode: FigureMode } };
  paused: boolean;
  reduced: boolean;
} & WingMotion) {
  const arms = useRef<(Group | null)[]>([]);
  const hands = useRef<(Group | null)[]>([]);
  const state = useRef({ spread: SPREAD.idle, clock: Math.random() * 10, beat: 0 });

  useFrame((_, rawDt) => {
    const s = state.current;
    const dt = paused ? 0 : Math.min(rawDt, 0.1);
    const flying = !!airborne?.();
    const target = flying ? 1 : SPREAD[drive.current.mode] ?? SPREAD.idle;
    s.spread += (target - s.spread) * (1 - Math.exp(-(flying ? 14 : 6) * dt));
    s.clock += dt;
    // A strong beat in the air that fades out after landing; a slow breath on the ground.
    s.beat += ((flying ? 1 : 0) - s.beat) * (1 - Math.exp(-8 * dt));
    const calm = reduced ? 0 : 1;
    const breath = 0.05 * Math.sin(s.clock * 1.7) * calm;
    const stroke = 0.5 * s.beat * Math.sin(s.clock * 15) * calm;
    const lag = 0.4 * s.beat * Math.sin(s.clock * 15 - 0.7) * calm;
    const k = s.spread;
    for (let i = 0; i < 2; i += 1) {
      const arm = arms.current[i];
      const hand = hands.current[i];
      if (arm) {
        arm.rotation.y = lerp(1.25, 0.12, k);
        arm.rotation.z = lerp(-0.3, 0.08, k) + breath + stroke;
      }
      if (hand) {
        hand.rotation.y = lerp(1.1, 0, k);
        hand.rotation.z = lerp(-0.75, 0, k) + breath * 0.6 + lag;
      }
    }
  });

  return (
    <group>
      {[1, -1].map((side, i) => (
        // Leaned back a little so the arch clears the head; the right wing mirrors the left.
        <group key={side} position={[side * 0.04, 0, 0]} scale={[side, 1, 1]} rotation={[-0.28, 0, 0]}>
          <group ref={(g) => { arms.current[i] = g; }}>
            <mesh position={[0.36, 0.12, 0.06]} scale={1.25}>
              <planeGeometry args={[1, 1]} />
              <meshBasicMaterial map={glowTexture()} color="#ffe7a8" transparent opacity={0.3} blending={AdditiveBlending} depthWrite={false} toneMapped={false} />
            </mesh>
            <Feathers feathers={ARM_LAYERS.secondaries} geometry={flightFeather()} shadow />
            <Feathers feathers={ARM_LAYERS.coverts} geometry={covertFeather()} shadow={false} />
            <group ref={(g) => { hands.current[i] = g; }} position={[ARM[2][0], ARM[2][1], 0]}>
              <Feathers feathers={HAND_LAYERS.primaries} geometry={flightFeather()} shadow />
              <Feathers feathers={HAND_LAYERS.coverts} geometry={covertFeather()} shadow={false} />
            </group>
          </group>
        </group>
      ))}
    </group>
  );
}

/** True when this subject currently wears the wings (unlocked, and not swapped out by the person). */
export function useWearsWings(kind: "person" | "agent", subjectId: string): boolean {
  return useProgression((s) => {
    const rewards = s.snapshot?.rewards;
    const level = s.subjects[subjectId]?.level;
    if (!rewards || !level) return false;
    return equippedFor(rewards, kind, level, kind === "person" ? s.choices.person : undefined).gadget === "gadget_wings";
  });
}
