/**
 * What a levelled-up figure wears: an aura on the floor under it and a
 * gadget on or around it. The aura follows the wearer's ground position
 * every frame; a figure wears its gadget on its own body (`wornGadget.tsx`)
 * and only the pet's follows a position. Nothing here is solid, casts a
 * shadow or takes a click.
 *
 * Auras: a warm glow, a turning rune circle, a crackling storm ring and the
 * prismatic legend circle with rising motes. Gadgets: a hovering drone, a
 * halo and a crown. The wings are worn on the figure's back (AngelWings).
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import {
  AdditiveBlending, BufferAttribute, BufferGeometry, Color, DoubleSide,
  type Group, type LineSegments, type Mesh, type MeshBasicMaterial, type Points,
} from "three";
import { EFFECT_COLOURS, type RewardId } from "../levelCatalog";
import { glowTexture, runeTexture } from "./flairTextures";
import type { FlairSource } from "./CosmeticTrail";

export type AuraKind = Extract<RewardId, `aura_${string}`>;
export type GadgetKind = Extract<RewardId, `gadget_${string}`>;

function useFollow(source: FlairSource, paused: boolean, place: (g: Group, at: NonNullable<ReturnType<FlairSource>>, t: number, dt: number) => void) {
  const group = useRef<Group>(null);
  const clock = useRef(0);
  useFrame((_, rawDt) => {
    const g = group.current;
    if (!g) return;
    const at = source();
    g.visible = !!at;
    if (!at) return;
    const dt = paused ? 0 : Math.min(rawDt, 0.1);
    clock.current += dt;
    place(g, at, clock.current, dt);
  });
  return group;
}

// ------------------------------------------------------------------ auras

export function CosmeticAura({ kind, source, scale = 1, paused, reduced }: { kind: AuraKind; source: FlairSource; scale?: number; paused: boolean; reduced: boolean }) {
  const disc = useRef<Mesh>(null);
  const ringA = useRef<Mesh>(null);
  const ringB = useRef<Mesh>(null);
  const colours = useMemo(() => EFFECT_COLOURS[kind].map((c) => new Color(c)), [kind]);
  const hue = useMemo(() => new Color(), []);
  const group = useFollow(source, paused, (g, at, t) => {
    g.position.set(at.x, 0.018, at.z);
    const pulse = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(t * 2.4);
    if (disc.current) {
      const m = disc.current.material as MeshBasicMaterial;
      m.opacity = kind === "aura_glow" ? 0.35 + 0.3 * pulse : 0.22 + 0.15 * pulse;
      disc.current.scale.setScalar(1 + 0.08 * pulse);
    }
    const spin = reduced ? 0 : t;
    if (ringA.current) ringA.current.rotation.z = spin * (kind === "aura_storm" ? 1.6 : 0.5);
    if (ringB.current) ringB.current.rotation.z = -spin * 0.8;
    if (kind === "aura_legend") {
      hue.setHSL((t * 0.08) % 1, 0.9, 0.65);
      if (ringA.current) (ringA.current.material as MeshBasicMaterial).color.copy(hue);
      hue.setHSL((t * 0.08 + 0.5) % 1, 0.9, 0.65);
      if (ringB.current) (ringB.current.material as MeshBasicMaterial).color.copy(hue);
    }
  });
  const flat: [number, number, number] = [-Math.PI / 2, 0, 0];
  const r = 0.8 * scale;
  return (
    <group ref={group} renderOrder={1}>
      <mesh ref={disc} rotation={flat} renderOrder={1}>
        <circleGeometry args={[r, 40]} />
        <meshBasicMaterial map={glowTexture()} color={colours[0]} transparent opacity={0.4} blending={AdditiveBlending} depthWrite={false} />
      </mesh>
      {kind !== "aura_glow" && (
        <mesh ref={ringA} rotation={flat} position={[0, 0.004, 0]} renderOrder={1}>
          <planeGeometry args={[r * 2.1, r * 2.1]} />
          <meshBasicMaterial map={runeTexture()} color={colours[0]} transparent opacity={kind === "aura_storm" ? 0.55 : 0.8}
            blending={AdditiveBlending} depthWrite={false} side={DoubleSide} />
        </mesh>
      )}
      {kind === "aura_legend" && (
        <mesh ref={ringB} rotation={flat} position={[0, 0.006, 0]} scale={0.7} renderOrder={1}>
          <planeGeometry args={[r * 2.1, r * 2.1]} />
          <meshBasicMaterial map={runeTexture()} color={colours[1]} transparent opacity={0.75} blending={AdditiveBlending} depthWrite={false} side={DoubleSide} />
        </mesh>
      )}
      {kind === "aura_storm" && !reduced && <Lightning radius={r} colour={colours[1]} paused={paused} />}
      {kind === "aura_legend" && !reduced && <Motes radius={r * 0.7} paused={paused} />}
    </group>
  );
}

const BOLTS = 6;
const BOLT_SEGMENTS = 4;

/** Short zig-zag arcs jumping round the ring, re-struck a dozen times a second. */
function Lightning({ radius, colour, paused }: { radius: number; colour: Color; paused: boolean }) {
  const lines = useRef<LineSegments>(null);
  const geometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array(BOLTS * BOLT_SEGMENTS * 2 * 3), 3));
    return g;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  const next = useRef(0);
  const clock = useRef(0);
  useFrame((_, dt) => {
    if (paused || !lines.current) return;
    clock.current += Math.min(dt, 0.1);
    if (clock.current < next.current) return;
    next.current = clock.current + 0.08 + Math.random() * 0.06;
    const pos = geometry.attributes.position.array as Float32Array;
    let k = 0;
    for (let b = 0; b < BOLTS; b++) {
      const a0 = Math.random() * Math.PI * 2;
      let x = Math.cos(a0) * radius * 0.55, y = 0.02, z = Math.sin(a0) * radius * 0.55;
      for (let s = 0; s < BOLT_SEGMENTS; s++) {
        const nx = x + Math.cos(a0) * radius * 0.12 + (Math.random() - 0.5) * 0.12;
        const ny = y + 0.06 + Math.random() * 0.1;
        const nz = z + Math.sin(a0) * radius * 0.12 + (Math.random() - 0.5) * 0.12;
        pos[k++] = x; pos[k++] = y; pos[k++] = z; pos[k++] = nx; pos[k++] = ny; pos[k++] = nz;
        x = nx; y = ny; z = nz;
      }
    }
    geometry.attributes.position.needsUpdate = true;
    (lines.current.material as MeshBasicMaterial).opacity = 0.5 + Math.random() * 0.5;
  });
  return (
    <lineSegments ref={lines} geometry={geometry} frustumCulled={false} renderOrder={2}>
      <lineBasicMaterial color={colour} transparent blending={AdditiveBlending} depthWrite={false} />
    </lineSegments>
  );
}

const MOTES = 24;

/** Prismatic motes spiralling up out of the legend circle. */
function Motes({ radius, paused }: { radius: number; paused: boolean }) {
  const points = useRef<Points>(null);
  const pool = useMemo(() => {
    const geometry = new BufferGeometry();
    const positions = new Float32Array(MOTES * 3);
    const colours = new Float32Array(MOTES * 3);
    geometry.setAttribute("position", new BufferAttribute(positions, 3));
    geometry.setAttribute("color", new BufferAttribute(colours, 3));
    return { geometry, positions, colours, phase: Float32Array.from({ length: MOTES }, (_, i) => i / MOTES) };
  }, []);
  useEffect(() => () => pool.geometry.dispose(), [pool]);
  const c = useMemo(() => new Color(), []);
  const clock = useRef(0);
  useFrame((_, dt) => {
    if (paused) return;
    clock.current += Math.min(dt, 0.1);
    const t = clock.current;
    for (let i = 0; i < MOTES; i++) {
      const life = (pool.phase[i] + t * 0.35) % 1;
      const angle = i * 2.39996 + t * 1.2;
      const r = radius * (1 - life * 0.5);
      pool.positions[i * 3] = Math.cos(angle) * r;
      pool.positions[i * 3 + 1] = 0.05 + life * 1.6;
      pool.positions[i * 3 + 2] = Math.sin(angle) * r;
      c.setHSL((i / MOTES + t * 0.1) % 1, 0.9, 0.65).multiplyScalar(Math.sin(life * Math.PI));
      pool.colours[i * 3] = c.r; pool.colours[i * 3 + 1] = c.g; pool.colours[i * 3 + 2] = c.b;
    }
    pool.geometry.attributes.position.needsUpdate = true;
    pool.geometry.attributes.color.needsUpdate = true;
  });
  return (
    <points ref={points} geometry={pool.geometry} frustumCulled={false} renderOrder={2}>
      <pointsMaterial map={glowTexture()} size={0.09} sizeAttenuation vertexColors transparent blending={AdditiveBlending} depthWrite={false} />
    </points>
  );
}

// ------------------------------------------------------------------ gadgets

/**
 * A gadget drawn around its own origin, which is the top of the wearer's
 * head. A figure wears it in its head slot (halo, crown) or body group
 * (drone) so it rides every hop; the pet's follows its position instead.
 */
export function GadgetModel({ kind, scale = 1, paused, reduced }: {
  kind: GadgetKind; scale?: number; paused: boolean; reduced: boolean;
}) {
  const spinner = useRef<Group>(null);
  const extra = useRef<Group>(null);
  const blink = useRef<Mesh>(null);
  const clock = useRef(Math.random() * 10);
  useFrame((_, rawDt) => {
    clock.current += paused ? 0 : Math.min(rawDt, 0.1);
    const t = clock.current;
    const calm = reduced ? 0 : 1;
    if (kind === "gadget_halo" && spinner.current) {
      spinner.current.position.y = 0.14 * scale + 0.025 * Math.sin(t * 2.2) * calm;
      spinner.current.rotation.z = 0.15 * Math.sin(t * 0.9) * calm;
    }
    if (kind === "gadget_crown" && spinner.current) spinner.current.rotation.y = t * 0.4 * calm;
    if (kind === "gadget_drone" && spinner.current) {
      spinner.current.rotation.y = t * 1.1 * calm;
      if (extra.current) {
        extra.current.position.y = 0.04 * Math.sin(t * 3) * calm;
        extra.current.children.forEach((rotor, i) => { if (i < 4) rotor.rotation.y = t * 40 * calm; });
      }
      if (blink.current) (blink.current.material as MeshBasicMaterial).opacity = Math.sin(t * 6) > 0.2 ? 1 : 0.2;
    }
  });
  const [main, accent] = EFFECT_COLOURS[kind];
  return (
    <group>
      {kind === "gadget_halo" && (
        <group ref={spinner}>
          <mesh rotation={[Math.PI / 2, 0, 0]}>
            <torusGeometry args={[0.17 * scale, 0.022 * scale, 10, 36]} />
            <meshBasicMaterial color={main} toneMapped={false} />
          </mesh>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <circleGeometry args={[0.32 * scale, 24]} />
            <meshBasicMaterial map={glowTexture()} color={main} transparent opacity={0.6} blending={AdditiveBlending} depthWrite={false} />
          </mesh>
        </group>
      )}
      {kind === "gadget_crown" && (
        <group ref={spinner} position={[0, -0.05 * scale, 0]} scale={scale}>
          <mesh position={[0, 0.045, 0]}>
            <cylinderGeometry args={[0.13, 0.12, 0.09, 20, 1, true]} />
            <meshStandardMaterial color={main} metalness={0.7} roughness={0.25} emissive={main} emissiveIntensity={0.25} side={DoubleSide} />
          </mesh>
          {[0, 1, 2, 3, 4].map((i) => {
            const a = (i / 5) * Math.PI * 2;
            return (
              <mesh key={i} position={[Math.sin(a) * 0.125, 0.12, Math.cos(a) * 0.125]}>
                <coneGeometry args={[0.03, 0.08, 6]} />
                <meshStandardMaterial color={main} metalness={0.7} roughness={0.25} emissive={main} emissiveIntensity={0.25} />
              </mesh>
            );
          })}
          <mesh position={[0, 0.05, 0.128]}>
            <sphereGeometry args={[0.022, 10, 8]} />
            <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.6} roughness={0.2} />
          </mesh>
        </group>
      )}
      {kind === "gadget_drone" && (
        <group ref={spinner} position={[0, 0.2 * scale, 0]}>
          <group ref={extra} position={[0.55 * scale, 0, 0]} scale={scale}>
            {[[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([sx, sz], i) => (
              <mesh key={i} position={[sx * 0.075, 0.03, sz * 0.075]}>
                <cylinderGeometry args={[0.045, 0.045, 0.006, 12]} />
                <meshBasicMaterial color={accent} transparent opacity={0.45} />
              </mesh>
            ))}
            <mesh>
              <boxGeometry args={[0.13, 0.045, 0.13]} />
              <meshStandardMaterial color={main} roughness={0.35} metalness={0.4} />
            </mesh>
            <mesh ref={blink} position={[0, -0.03, 0.066]}>
              <sphereGeometry args={[0.014, 8, 6]} />
              <meshBasicMaterial color={accent} transparent toneMapped={false} />
            </mesh>
          </group>
        </group>
      )}
    </group>
  );
}

/** A gadget that follows a ground position; `top` is the wearer's head-top height above the floor. */
export function CosmeticGadget({ kind, source, top, scale = 1, paused, reduced }: {
  kind: GadgetKind; source: FlairSource; top: number; scale?: number; paused: boolean; reduced: boolean;
}) {
  const group = useFollow(source, paused, (g, at) => { g.position.set(at.x, at.y ?? top, at.z); });
  return (
    <group ref={group}>
      <GadgetModel kind={kind} scale={scale} paused={paused} reduced={reduced} />
    </group>
  );
}
