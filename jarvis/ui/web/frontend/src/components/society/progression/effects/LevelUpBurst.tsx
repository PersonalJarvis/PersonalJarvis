/**
 * The level-up moment in the world, in three beats that overlap:
 *
 * 1. a column of light shoots up from the floor around the figure (0–0.3 s),
 * 2. two shockwave rings race out across the floor (0.05–1.1 s),
 * 3. a fountain of sparks bursts up and rains back down (0.1–2.2 s),
 *
 * while a "LEVEL n" tag rises over the head. The column and sparks follow
 * the figure if it keeps walking. Reduced motion keeps only the tag.
 */
import { useEffect, useMemo, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { AdditiveBlending, BufferAttribute, BufferGeometry, Color, DoubleSide, type Group, type Mesh, type MeshBasicMaterial, type Points } from "three";
import { beamTexture, glowTexture } from "./flairTextures";
import type { FlairSource } from "./CosmeticTrail";
import type { SubjectKind } from "../levelCatalog";
import { BURST_MS } from "../progressionStore";

/** Gold for the person, the pet's teal, an agent's green: the same three as the HUD. */
export const BURST_COLOURS: Record<SubjectKind, [string, string]> = {
  person: ["#ffd25e", "#fff3c4"],
  pet: ["#5eead4", "#e0fffa"],
  agent: ["#86efac", "#f0fff4"],
};

const SPARKS = 90;
const GRAVITY = 5.5;

/** 0..1 ease-out (cubic). */
const easeOut = (x: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);

export function LevelUpBurst({ source, kind, level, label, height, scale = 1, reduced }: {
  source: FlairSource; kind: SubjectKind; level: number; label: string; height: number; scale?: number; reduced: boolean;
}) {
  const group = useRef<Group>(null);
  const column = useRef<Mesh>(null);
  const ring1 = useRef<Mesh>(null);
  const ring2 = useRef<Mesh>(null);
  const flash = useRef<Mesh>(null);
  const sparks = useRef<Points>(null);
  const t = useRef(0);
  const [main, light] = BURST_COLOURS[kind];
  const pool = useMemo(() => {
    const geometry = new BufferGeometry();
    const positions = new Float32Array(SPARKS * 3);
    const colours = new Float32Array(SPARKS * 3);
    const velocity = new Float32Array(SPARKS * 3);
    const a = new Color(main), b = new Color(light);
    for (let i = 0; i < SPARKS; i++) {
      const angle = (i / SPARKS) * Math.PI * 2 * 7.3;
      const out = (0.8 + ((i * 37) % 11) / 11 * 1.6) * scale;
      velocity[i * 3] = Math.cos(angle) * out;
      velocity[i * 3 + 1] = (2.6 + ((i * 53) % 13) / 13 * 2.8) * scale;
      velocity[i * 3 + 2] = Math.sin(angle) * out;
      const c = i % 3 === 0 ? b : a;
      colours[i * 3] = c.r; colours[i * 3 + 1] = c.g; colours[i * 3 + 2] = c.b;
    }
    geometry.setAttribute("position", new BufferAttribute(positions, 3));
    geometry.setAttribute("color", new BufferAttribute(colours.slice(), 3));
    return { geometry, positions, base: colours, velocity };
  }, [main, light, scale]);
  useEffect(() => () => pool.geometry.dispose(), [pool]);

  useFrame((_, rawDt) => {
    const g = group.current;
    if (!g) return;
    const at = source();
    if (at) g.position.set(at.x, 0, at.z);
    t.current += Math.min(rawDt, 0.1);
    const s = t.current;
    if (reduced) return;
    if (column.current) {
      const grow = easeOut(s / 0.3);
      const fade = s < 0.9 ? 1 : Math.max(0, 1 - (s - 0.9) / 1.2);
      column.current.scale.set(1 - 0.35 * easeOut((s - 0.9) / 1.2), grow, 1 - 0.35 * easeOut((s - 0.9) / 1.2));
      (column.current.material as MeshBasicMaterial).opacity = 0.75 * fade;
      column.current.rotation.y = s * 1.5;
    }
    for (const [ring, delay, span] of [[ring1.current, 0.05, 1.0], [ring2.current, 0.25, 1.1]] as const) {
      if (!ring) continue;
      const k = (s - delay) / span;
      ring.visible = k > 0 && k < 1;
      ring.scale.setScalar(0.3 + easeOut(k) * 3.4 * scale);
      (ring.material as MeshBasicMaterial).opacity = 0.9 * (1 - Math.min(1, Math.max(0, k)));
    }
    if (flash.current) {
      const k = s / 0.5;
      flash.current.visible = k < 1;
      flash.current.scale.setScalar((0.6 + k * 2.4) * scale);
      (flash.current.material as MeshBasicMaterial).opacity = 0.85 * (1 - k);
    }
    const st = s - 0.1;
    if (sparks.current) sparks.current.visible = st > 0 && st < 2.1;
    if (st > 0) {
      const { positions, velocity, base } = pool;
      const colours = pool.geometry.attributes.color.array as Float32Array;
      const fade = Math.max(0, 1 - st / 2.1);
      for (let i = 0; i < SPARKS; i++) {
        positions[i * 3] = velocity[i * 3] * st;
        positions[i * 3 + 1] = Math.max(0.02, 0.4 * scale + velocity[i * 3 + 1] * st - 0.5 * GRAVITY * scale * st * st);
        positions[i * 3 + 2] = velocity[i * 3 + 2] * st;
        const twinkle = 0.6 + 0.4 * Math.sin(st * 20 + i);
        colours[i * 3] = base[i * 3] * fade * twinkle; colours[i * 3 + 1] = base[i * 3 + 1] * fade * twinkle; colours[i * 3 + 2] = base[i * 3 + 2] * fade * twinkle;
      }
      pool.geometry.attributes.position.needsUpdate = true;
      pool.geometry.attributes.color.needsUpdate = true;
    }
  });

  const flat: [number, number, number] = [-Math.PI / 2, 0, 0];
  return (
    <group ref={group}>
      {!reduced && <>
        <mesh ref={column} position={[0, 3 * scale, 0]} renderOrder={3}>
          <cylinderGeometry args={[0.55 * scale, 0.7 * scale, 6 * scale, 28, 1, true]} />
          <meshBasicMaterial map={beamTexture()} color={main} transparent opacity={0.7} blending={AdditiveBlending} depthWrite={false} side={DoubleSide} />
        </mesh>
        <mesh ref={flash} rotation={flat} position={[0, 0.03, 0]} renderOrder={3}>
          <circleGeometry args={[0.6, 32]} />
          <meshBasicMaterial map={glowTexture()} color={light} transparent opacity={0.8} blending={AdditiveBlending} depthWrite={false} />
        </mesh>
        {[ring1, ring2].map((ref, i) => (
          <mesh key={i} ref={ref} rotation={flat} position={[0, 0.035, 0]} visible={false} renderOrder={3}>
            <ringGeometry args={[0.86, 1, 48]} />
            <meshBasicMaterial color={i === 0 ? main : light} transparent opacity={0.9} blending={AdditiveBlending} depthWrite={false} side={DoubleSide} />
          </mesh>
        ))}
        <points ref={sparks} geometry={pool.geometry} frustumCulled={false} visible={false} renderOrder={3}>
          <pointsMaterial map={glowTexture()} size={0.14 * scale} sizeAttenuation vertexColors transparent blending={AdditiveBlending} depthWrite={false} />
        </points>
      </>}
      <Html center position={[0, height + 0.55, 0]} zIndexRange={[45, 40]}>
        <span className="level-burst-tag" data-kind={kind} style={{ animationDuration: `${BURST_MS}ms` }} aria-hidden>
          <small>{label}</small><b>{level}</b>
        </span>
      </Html>
    </group>
  );
}

/** "+5 XP", rising and fading over the wearer's head (CSS does the motion). */
export function XpPopup({ x, z, y, xp, kind }: { x: number; z: number; y: number; xp: number; kind: SubjectKind }) {
  return (
    <Html center position={[x, y, z]} zIndexRange={[44, 40]}>
      <span className="level-xp-pop" data-kind={kind} aria-hidden>+{xp} XP</span>
    </Html>
  );
}
