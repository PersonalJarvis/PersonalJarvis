/**
 * The lead agent (Jarvis) as it appears in the office: not a figure but Gigi,
 * the mascot, flying at chest/head height above the walker's ground position.
 *
 * Motion comes from the pure `gigiFlight.ts`; this component only renders it
 * with a few cheap effects: an additive halo behind Gigi, a small glow under
 * its hover emitter, a recycled sparkle trail while travelling and a soft
 * shadow blob on the floor. Everything is one draw call each, no lights.
 */
import { Component, Suspense, useEffect, useMemo, useRef, type ReactNode } from "react";
import { useFrame } from "@react-three/fiber";
import {
  AdditiveBlending, BufferAttribute, BufferGeometry, CanvasTexture, CircleGeometry, Color, SRGBColorSpace,
  type Group, type Mesh, type MeshBasicMaterial, type PointLight, type Points, type Sprite, type SpriteMaterial, type Texture,
} from "three";
import { CompanionModel } from "../companion/AgentFollower";
import { defaultCompanion } from "../companion/appearance";
import { createGigiFlight, createGigiPose, followAnchor, stepGigiFlight, type GigiFlightMode } from "./gigiFlight";
import { cameraView } from "./officeStore";

/** Gigi's on-screen height in the office. */
export const GIGI_OFFICE_SIZE_M = 0.5;
/** Radius of the little orbs circling Gigi. */
const ORBIT_RADIUS_M = 0.36;
/** The authored model stands on its origin (y 0.014–0.40 of 0.4 m); lift so the flight point is its centre. */
const MODEL_CENTRE_OFFSET = -0.207 * (GIGI_OFFICE_SIZE_M / 0.4);
/** The hover emitter sits at the bottom of the shell. */
const EMITTER_OFFSET = MODEL_CENTRE_OFFSET + 0.02;
const TRAIL_SIZE = 24;
const TRAIL_LIFE_S = 0.8;
const TRAIL_RATE = 30;
const MOVING_SPEED = 0.05;
/** Closer than this to a first-person camera, Gigi would fill the view. */
const FIRST_PERSON_CLEARANCE_M = 2.5;

/** Local effect colours; the diorama keeps one palette in light and dark mode. */
const GLOW_WARM = new Color("#ffd98a");
const GLOW_SPEAK = new Color("#9ff3ff");
const SPARK = new Color("#ffe7a8");

// ---- Shared canvas textures (reference counted; GPU copies freed with the last user) ----

interface TextureCache { glow: Texture; shadow: Texture; users: number }
let cache: TextureCache | null = null;

function radialTexture(stops: [number, string][]): Texture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const context = canvas.getContext("2d");
  if (context) {
    const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
    for (const [offset, colour] of stops) gradient.addColorStop(offset, colour);
    context.fillStyle = gradient;
    context.fillRect(0, 0, 64, 64);
  }
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  return texture;
}

function sharedTextures(): TextureCache {
  if (!cache) {
    cache = {
      glow: radialTexture([[0, "rgba(255,255,255,1)"], [0.25, "rgba(255,255,255,0.55)"], [0.6, "rgba(255,255,255,0.12)"], [1, "rgba(255,255,255,0)"]]),
      shadow: radialTexture([[0, "rgba(0,0,0,1)"], [0.55, "rgba(0,0,0,0.45)"], [1, "rgba(0,0,0,0)"]]),
      users: 0,
    };
  }
  return cache;
}

/** Counted in an effect, so a StrictMode double mount never frees a texture in use. */
function acquireTextures(): void {
  sharedTextures().users++;
}

function releaseTextures(): void {
  if (!cache) return;
  cache.users--;
  if (cache.users > 0) return;
  // Frees the GPU copies; the tiny canvases stay cached and re-upload on next use,
  // so a component still holding them (StrictMode remount) never renders a dead texture.
  cache.glow.dispose();
  cache.shadow.dispose();
}

/** A missing or broken GLB hides Gigi's body; the glow still marks the lead. */
class ModelBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.warn("Gigi model unavailable", error.name); }
  render() { return this.state.failed ? null : this.props.children; }
}

/** Tiny deterministic generator for sparkle jitter. */
function nextRandom(seed: { value: number }): number {
  seed.value = (Math.imul(seed.value, 1664525) + 1013904223) >>> 0;
  return seed.value / 4294967296;
}

export function GigiFlyer({ owner, mode, speaking, paused, reduced, clear }: {
  /** The walker's mover (or, in "follow" mode, the person's character): ground position and facing (0 = +z). */
  owner: { current: { x: number; z: number; heading: number } };
  mode: GigiFlightMode;
  speaking: boolean;
  paused: boolean;
  reduced: boolean;
  /** Free airspace test; "follow" mode keeps Gigi out of walls with it. */
  clear?: (x: number, z: number) => boolean;
}) {
  const root = useRef<Group>(null);
  const body = useRef<Group>(null);
  const halo = useRef<Sprite>(null);
  const emitter = useRef<Sprite>(null);
  const shadow = useRef<Mesh>(null);
  const trail = useRef<Points>(null);
  const light = useRef<PointLight>(null);
  const orbit = useRef<Group>(null);
  const flight = useRef(createGigiFlight(owner.current.x, owner.current.z, owner.current.heading));
  const pose = useMemo(() => createGigiPose(), []);
  const clock = useRef(0);
  const last = useRef({ x: owner.current.x, z: owner.current.z });
  const emitDebt = useRef(0);
  const seed = useRef({ value: 0x9e3779b9 });
  const side = useRef<1 | -1>(1);
  const tint = useMemo(() => new Color(), []);
  const appearance = useMemo(() => ({ ...defaultCompanion("jarvis"), sizeM: GIGI_OFFICE_SIZE_M }), []);

  const textures = useMemo(() => sharedTextures(), []);
  useEffect(() => { acquireTextures(); return releaseTextures; }, []);

  const particles = useMemo(() => {
    const geometry = new BufferGeometry();
    const positions = new Float32Array(TRAIL_SIZE * 3);
    const colours = new Float32Array(TRAIL_SIZE * 3);
    geometry.setAttribute("position", new BufferAttribute(positions, 3));
    geometry.setAttribute("color", new BufferAttribute(colours, 3));
    const shadowGeometry = new CircleGeometry(0.28, 28);
    return { geometry, shadowGeometry, positions, colours, age: new Float32Array(TRAIL_SIZE).fill(TRAIL_LIFE_S), rise: new Float32Array(TRAIL_SIZE), next: 0 };
  }, []);
  useEffect(() => () => { particles.geometry.dispose(); particles.shadowGeometry.dispose(); }, [particles]);

  useFrame((_, rawDt) => {
    if (paused) return;
    const dt = Math.min(rawDt, 0.1);
    clock.current += dt;
    const m = owner.current;
    const travelled = Math.hypot(m.x - last.current.x, m.z - last.current.z);
    const moving = dt > 0 && travelled / dt > MOVING_SPEED && travelled < 4;
    last.current.x = m.x; last.current.z = m.z;
    let targetX = m.x, targetZ = m.z;
    if (mode === "follow") {
      const anchor = followAnchor(m.x, m.z, m.heading, side.current, clear);
      side.current = anchor.side;
      targetX = anchor.x; targetZ = anchor.z;
    }
    stepGigiFlight(flight.current, {
      targetX, targetZ, moving, mode, speaking, t: clock.current, dt, heading: m.heading, reduced, clear,
    }, pose);

    // Gigi hovers at head height beside the person: seated in first person that is right in
    // front of the eyes, so close by it steps out of the picture until the person stands up.
    const hidden = cameraView.firstPerson && Math.hypot(pose.x - cameraView.x, pose.z - cameraView.z) < FIRST_PERSON_CLEARANCE_M;
    if (root.current) root.current.visible = !hidden;
    if (shadow.current) shadow.current.visible = !hidden;
    if (root.current) root.current.position.set(pose.x, pose.y, pose.z);
    if (body.current) {
      body.current.rotation.set(pose.pitch, pose.yaw, pose.roll, "YXZ");
      body.current.scale.setScalar(pose.scale);
    }
    tint.copy(GLOW_WARM).lerp(GLOW_SPEAK, speaking ? 1 : mode === "talk" ? 0.5 : 0);
    if (halo.current) {
      const material = halo.current.material as SpriteMaterial;
      material.color.copy(tint);
      material.opacity = 0.18 + 0.5 * pose.glow;
      halo.current.scale.setScalar(0.75 + 0.35 * pose.glow);
    }
    if (emitter.current) {
      const material = emitter.current.material as SpriteMaterial;
      material.color.copy(tint);
      material.opacity = 0.3 + 0.5 * pose.glow;
      emitter.current.scale.setScalar(0.16 + 0.1 * pose.glow);
    }
    // A warm light that tints the floor and furniture around Gigi, brighter when it talks.
    if (light.current) {
      light.current.color.copy(tint);
      light.current.intensity = reduced ? 0.5 : 0.45 + 0.9 * pose.glow;
    }
    // Three small orbs circle Gigi; they spin up while it speaks or works.
    if (orbit.current) {
      const spin = reduced ? 0 : speaking ? 3.2 : mode === "work" ? 2.2 : 1.1;
      orbit.current.rotation.y += spin * dt;
      orbit.current.rotation.z = 0.35 * Math.sin(clock.current * 0.7);
      orbit.current.children.forEach((child, i) => {
        const orb = child as Sprite;
        (orb.material as SpriteMaterial).color.copy(tint);
        orb.scale.setScalar(0.07 + 0.025 * Math.sin(clock.current * 3 + i * 2.1) + 0.03 * pose.glow);
      });
    }
    if (shadow.current) {
      const lift = Math.max(0, pose.y - 0.4);
      (shadow.current.material as MeshBasicMaterial).opacity = Math.max(0.08, 0.34 - lift * 0.22);
      shadow.current.position.set(pose.x, 0.012, pose.z);
      shadow.current.scale.setScalar(0.85 + lift * 0.35);
    }

    // Sparkle trail: a fixed pool recycled round-robin, faded by age.
    const { positions, colours, age, rise } = particles;
    if (moving && !reduced && pose.speed > 0.1) {
      emitDebt.current += dt * TRAIL_RATE;
      while (emitDebt.current >= 1) {
        emitDebt.current -= 1;
        const i = particles.next;
        particles.next = (i + 1) % TRAIL_SIZE;
        positions[i * 3] = pose.x + (nextRandom(seed.current) - 0.5) * 0.12;
        positions[i * 3 + 1] = pose.y + EMITTER_OFFSET + (nextRandom(seed.current) - 0.5) * 0.06;
        positions[i * 3 + 2] = pose.z + (nextRandom(seed.current) - 0.5) * 0.12;
        rise[i] = (nextRandom(seed.current) - 0.65) * 0.25;
        age[i] = 0;
      }
    } else emitDebt.current = 0;
    let alive = false;
    for (let i = 0; i < TRAIL_SIZE; i++) {
      if (age[i] >= TRAIL_LIFE_S) { colours[i * 3] = colours[i * 3 + 1] = colours[i * 3 + 2] = 0; continue; }
      age[i] += dt;
      positions[i * 3 + 1] += rise[i] * dt;
      const fade = Math.max(0, 1 - age[i] / TRAIL_LIFE_S);
      // Additive blending: fading the colour to black fades the sparkle out.
      colours[i * 3] = SPARK.r * fade; colours[i * 3 + 1] = SPARK.g * fade; colours[i * 3 + 2] = SPARK.b * fade;
      alive = true;
    }
    if (trail.current) trail.current.visible = alive;
    if (alive) {
      particles.geometry.attributes.position.needsUpdate = true;
      particles.geometry.attributes.color.needsUpdate = true;
    }
  });

  return (
    <>
      <group ref={root} position={[owner.current.x, pose.y, owner.current.z]}>
        <sprite ref={halo} scale={0.9} renderOrder={1}>
          <spriteMaterial map={textures.glow} color={GLOW_WARM} transparent opacity={0.35} blending={AdditiveBlending} depthWrite={false} />
        </sprite>
        <sprite ref={emitter} position={[0, EMITTER_OFFSET, 0]} scale={0.2} renderOrder={1}>
          <spriteMaterial map={textures.glow} color={GLOW_WARM} transparent opacity={0.5} blending={AdditiveBlending} depthWrite={false} />
        </sprite>
        <pointLight ref={light} color={GLOW_WARM} intensity={0.6} distance={3.2} decay={2} />
        <group ref={orbit}>
          {[0, 1, 2].map((i) => (
            <sprite key={i} position={[Math.cos((i / 3) * Math.PI * 2) * ORBIT_RADIUS_M, 0, Math.sin((i / 3) * Math.PI * 2) * ORBIT_RADIUS_M]} scale={0.08} renderOrder={2}>
              <spriteMaterial map={textures.glow} color={GLOW_WARM} transparent opacity={0.9} blending={AdditiveBlending} depthWrite={false} />
            </sprite>
          ))}
        </group>
        <group ref={body}>
          <group position={[0, MODEL_CENTRE_OFFSET, 0]}>
            <ModelBoundary>
              <Suspense fallback={null}><CompanionModel appearance={appearance} lead /></Suspense>
            </ModelBoundary>
          </group>
        </group>
      </group>
      <mesh ref={shadow} geometry={particles.shadowGeometry} rotation={[-Math.PI / 2, 0, 0]} position={[owner.current.x, 0.012, owner.current.z]} renderOrder={1}>
        <meshBasicMaterial map={textures.shadow} color="#000000" transparent opacity={0.3} depthWrite={false} />
      </mesh>
      <points ref={trail} geometry={particles.geometry} frustumCulled={false} visible={false} renderOrder={2}>
        <pointsMaterial map={textures.glow} size={0.07} sizeAttenuation vertexColors transparent blending={AdditiveBlending} depthWrite={false} />
      </points>
    </>
  );
}
