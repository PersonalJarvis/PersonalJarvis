/**
 * The trail a levelled-up figure leaves while it moves: glowing footprints,
 * sparkles, a comet tail, a neon ribbon, a rainbow or star dust. Every trail
 * lives in world space (it stays where it was drawn and fades), is one draw
 * call, and recycles a fixed pool, so a long walk never allocates.
 */
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import {
  AdditiveBlending, BufferAttribute, BufferGeometry, Color, DoubleSide, Euler, InstancedMesh, Matrix4, PlaneGeometry,
  Quaternion, Vector3, type Points,
} from "three";
import { EFFECT_COLOURS, type RewardId } from "../levelCatalog";
import { footprintTexture, glowTexture, starTexture } from "./flairTextures";
import { footprintAt, pruneSamples, pushSample, rainbowHue, RIBBON_MAX_SAMPLES, STEP_LENGTH_M, writeRibbon, type TrailSample } from "./trailModel";

export type TrailKind = Extract<RewardId, `trail_${string}`>;

/** Where the wearer is now; null hides nothing but stops new trail. */
export type FlairSource = () => { x: number; z: number; heading?: number; y?: number } | null;

const RIBBON_LIFE_S = 0.9;
const RIBBON_WIDTH: Partial<Record<TrailKind, number>> = { trail_comet: 0.34, trail_neon: 0.22, trail_rainbow: 0.4 };

export function CosmeticTrail({ kind, source, scale = 1, paused }: { kind: TrailKind; source: FlairSource; scale?: number; paused: boolean }) {
  if (kind === "trail_footprints") return <Footprints source={source} scale={scale} paused={paused} />;
  if (kind === "trail_sparkle" || kind === "trail_stardust") return <Sparks kind={kind} source={source} scale={scale} paused={paused} />;
  return <Ribbon kind={kind} source={source} scale={scale} paused={paused} />;
}

// ------------------------------------------------------------------ footprints

const PRINTS = 16;
const PRINT_LIFE_S = 2.6;

function Footprints({ source, scale, paused }: { source: FlairSource; scale: number; paused: boolean }) {
  const mesh = useRef<InstancedMesh>(null);
  const state = useRef({ travelled: 0, left: true, last: null as { x: number; z: number } | null, next: 0, clock: 0 });
  const born = useMemo(() => new Float32Array(PRINTS).fill(-99), []);
  const geometry = useMemo(() => new PlaneGeometry(0.13 * scale, 0.2 * scale), [scale]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  const tmp = useMemo(() => ({ m: new Matrix4(), q: new Quaternion(), e: new Euler(), p: new Vector3(), s: new Vector3(1, 1, 1), c: new Color() }), []);
  const base = useMemo(() => new Color(EFFECT_COLOURS.trail_footprints[0]), []);

  // Before the first frame every print is hidden (zero scale, black), so none flashes at the origin.
  useLayoutEffect(() => {
    const m = mesh.current;
    if (!m) return;
    tmp.c.setRGB(0, 0, 0);
    for (let i = 0; i < PRINTS; i++) { m.setColorAt(i, tmp.c); tmp.m.makeScale(0, 0, 0); m.setMatrixAt(i, tmp.m); }
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [tmp, geometry]);

  useFrame((_, rawDt) => {
    const m = mesh.current;
    if (!m || paused) return;
    const s = state.current;
    s.clock += Math.min(rawDt, 0.1);
    const at = source();
    if (at) {
      if (s.last) {
        const step = Math.hypot(at.x - s.last.x, at.z - s.last.z);
        s.travelled += step < 2.5 ? step : 0;
      }
      s.last = { x: at.x, z: at.z };
      if (s.travelled >= STEP_LENGTH_M * scale) {
        s.travelled = 0;
        const print = footprintAt(at.x, at.z, at.heading ?? 0, s.left);
        s.left = !s.left;
        const i = s.next;
        s.next = (i + 1) % PRINTS;
        born[i] = s.clock;
        tmp.e.set(-Math.PI / 2, print.rot + Math.PI, 0, "YXZ");
        tmp.q.setFromEuler(tmp.e);
        tmp.p.set(print.x, 0.022, print.z);
        tmp.m.compose(tmp.p, tmp.q, tmp.s);
        m.setMatrixAt(i, tmp.m);
        m.instanceMatrix.needsUpdate = true;
      }
    }
    for (let i = 0; i < PRINTS; i++) {
      const fade = Math.max(0, 1 - (s.clock - born[i]) / PRINT_LIFE_S);
      tmp.c.copy(base).multiplyScalar(fade * 0.85);
      m.setColorAt(i, tmp.c);
    }
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  });

  return (
    <instancedMesh ref={mesh} args={[geometry, undefined, PRINTS]} frustumCulled={false} renderOrder={2}>
      <meshBasicMaterial map={footprintTexture()} transparent blending={AdditiveBlending} depthWrite={false} side={DoubleSide} />
    </instancedMesh>
  );
}

// ------------------------------------------------------------------ sparks

const SPARKS = 48;

function nextRandom(seed: { value: number }): number {
  seed.value = (Math.imul(seed.value, 1664525) + 1013904223) >>> 0;
  return seed.value / 4294967296;
}

function Sparks({ kind, source, scale, paused }: { kind: "trail_sparkle" | "trail_stardust"; source: FlairSource; scale: number; paused: boolean }) {
  const points = useRef<Points>(null);
  const stardust = kind === "trail_stardust";
  const life = stardust ? 1.4 : 0.9;
  const pool = useMemo(() => {
    const geometry = new BufferGeometry();
    const positions = new Float32Array(SPARKS * 3);
    const colours = new Float32Array(SPARKS * 3);
    geometry.setAttribute("position", new BufferAttribute(positions, 3));
    geometry.setAttribute("color", new BufferAttribute(colours, 3));
    return { geometry, positions, colours, age: new Float32Array(SPARKS).fill(99), rise: new Float32Array(SPARKS), tint: new Float32Array(SPARKS), next: 0 };
  }, []);
  useEffect(() => () => pool.geometry.dispose(), [pool]);
  const palette = useMemo(() => EFFECT_COLOURS[kind].map((c) => new Color(c)), [kind]);
  const state = useRef({ last: null as { x: number; z: number } | null, debt: 0, clock: 0, seed: { value: 0x2545f491 } });

  useFrame((_, rawDt) => {
    if (paused) return;
    const dt = Math.min(rawDt, 0.1);
    const s = state.current;
    s.clock += dt;
    const at = source();
    const { positions, colours, age, rise, tint } = pool;
    if (at) {
      const moved = s.last ? Math.hypot(at.x - s.last.x, at.z - s.last.z) : 0;
      s.last = { x: at.x, z: at.z };
      if (moved > 0.004 && moved < 2.5) {
        s.debt += dt * (stardust ? 34 : 26);
        while (s.debt >= 1) {
          s.debt -= 1;
          const i = pool.next;
          pool.next = (i + 1) % SPARKS;
          const spread = (stardust ? 0.5 : 0.3) * scale;
          positions[i * 3] = at.x + (nextRandom(s.seed) - 0.5) * spread;
          positions[i * 3 + 1] = (0.08 + nextRandom(s.seed) * (stardust ? 1.0 : 0.6)) * scale + (at.y ?? 0);
          positions[i * 3 + 2] = at.z + (nextRandom(s.seed) - 0.5) * spread;
          rise[i] = (stardust ? 0.05 : 0.25) + nextRandom(s.seed) * 0.2;
          tint[i] = Math.floor(nextRandom(s.seed) * palette.length);
          age[i] = 0;
        }
      } else s.debt = 0;
    }
    let alive = false;
    for (let i = 0; i < SPARKS; i++) {
      if (age[i] >= life) { colours[i * 3] = colours[i * 3 + 1] = colours[i * 3 + 2] = 0; continue; }
      age[i] += dt;
      positions[i * 3 + 1] += rise[i] * dt;
      let fade = Math.max(0, 1 - age[i] / life);
      if (stardust) fade *= 0.55 + 0.45 * Math.sin(s.clock * 14 + i * 1.7);
      const c = palette[tint[i]] ?? palette[0];
      colours[i * 3] = c.r * fade; colours[i * 3 + 1] = c.g * fade; colours[i * 3 + 2] = c.b * fade;
      alive = true;
    }
    if (points.current) points.current.visible = alive;
    if (alive) {
      pool.geometry.attributes.position.needsUpdate = true;
      pool.geometry.attributes.color.needsUpdate = true;
    }
  });

  return (
    <points ref={points} geometry={pool.geometry} frustumCulled={false} visible={false} renderOrder={2}>
      <pointsMaterial map={stardust ? starTexture() : glowTexture()} size={(stardust ? 0.13 : 0.08) * Math.max(0.6, scale)} sizeAttenuation
        vertexColors transparent blending={AdditiveBlending} depthWrite={false} />
    </points>
  );
}

// ------------------------------------------------------------------ ribbons

function Ribbon({ kind, source, scale, paused }: { kind: Exclude<TrailKind, "trail_footprints" | "trail_sparkle" | "trail_stardust">; source: FlairSource; scale: number; paused: boolean }) {
  const strip = useMemo(() => {
    const geometry = new BufferGeometry();
    const positions = new Float32Array(RIBBON_MAX_SAMPLES * 6);
    const colours = new Float32Array(RIBBON_MAX_SAMPLES * 6);
    const index: number[] = [];
    for (let i = 0; i < RIBBON_MAX_SAMPLES - 1; i++) {
      const a = i * 2;
      index.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
    geometry.setIndex(index);
    geometry.setAttribute("position", new BufferAttribute(positions, 3));
    geometry.setAttribute("color", new BufferAttribute(colours, 3));
    geometry.setDrawRange(0, 0);
    return { geometry, positions, colours };
  }, []);
  useEffect(() => () => strip.geometry.dispose(), [strip]);
  const samples = useRef<TrailSample[]>([]);
  const clock = useRef(0);
  const colourA = useMemo(() => new Color(EFFECT_COLOURS[kind][0]), [kind]);
  const colourB = useMemo(() => new Color(EFFECT_COLOURS[kind][1] ?? EFFECT_COLOURS[kind][0]), [kind]);
  const mix = useMemo(() => new Color(), []);
  const width = (RIBBON_WIDTH[kind] ?? 0.3) * scale;

  useFrame((_, rawDt) => {
    if (paused) return;
    clock.current += Math.min(rawDt, 0.1);
    const t = clock.current;
    const at = source();
    const list = samples.current;
    if (at) pushSample(list, at.x, at.z, t);
    pruneSamples(list, t, RIBBON_LIFE_S);
    const n = list.length;
    if (n < 2) { strip.geometry.setDrawRange(0, 0); return; }
    const bright = writeRibbon(list, t, RIBBON_LIFE_S, width, 0.025, strip.positions);
    for (let i = 0; i < n; i++) {
      const along = i / (n - 1);
      if (kind === "trail_rainbow") mix.setHSL(rainbowHue(along, t), 0.95, 0.6);
      else if (kind === "trail_neon") mix.copy(colourA).lerp(colourB, 0.5 + 0.5 * Math.sin(t * 4 + along * 6));
      else mix.copy(colourA).lerp(colourB, along * along);
      const b = bright[i] * 0.9;
      for (const v of [i * 6, i * 6 + 3]) { strip.colours[v] = mix.r * b; strip.colours[v + 1] = mix.g * b; strip.colours[v + 2] = mix.b * b; }
    }
    strip.geometry.setDrawRange(0, (n - 1) * 6);
    strip.geometry.attributes.position.needsUpdate = true;
    strip.geometry.attributes.color.needsUpdate = true;
  });

  return (
    <mesh geometry={strip.geometry} frustumCulled={false} renderOrder={2}>
      <meshBasicMaterial vertexColors transparent blending={AdditiveBlending} depthWrite={false} side={DoubleSide} />
    </mesh>
  );
}
