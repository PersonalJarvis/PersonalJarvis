/**
 * The level-up moment in the world, kept quiet the way a ceremony is: a
 * single gold ring runs out across the floor under the figure and a soft
 * light fades beneath it, while a plate rises over the head with the new
 * insignia — "Promoted · Sergeant" on a promotion, "Level 7" between two.
 * The ring and the plate follow the figure if it keeps walking. Reduced
 * motion keeps the plate and drops the ring.
 */
import { useEffect, useMemo, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { AdditiveBlending, DoubleSide, MeshBasicMaterial, type Group, type Mesh } from "three";
import { useT } from "@/i18n";
import { glowTexture } from "./flairTextures";
import type { RankId, SubjectKind } from "../levelCatalog";
import { RankInsignia } from "../insignia/RankInsignia";
import { BURST_MS } from "../progressionStore";

/** Where something stands this frame (and how high its top is), or null while it is not drawn. */
export type FlairSource = () => { x: number; z: number; y?: number; heading?: number } | null;

const GOLD = "#e3b94f";

/** 0..1 ease-out (cubic). */
const easeOut = (x: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);

export function LevelUpBurst({ source, kind, level, rank, promoted, height, scale = 1, reduced }: {
  source: FlairSource; kind: SubjectKind; level: number; rank: RankId; promoted: boolean; height: number; scale?: number; reduced: boolean;
}) {
  const t = useT();
  const group = useRef<Group>(null);
  const ring = useRef<Mesh>(null);
  const glow = useRef<Mesh>(null);
  const clock = useRef(0);
  const materials = useMemo(() => ({
    ring: new MeshBasicMaterial({ color: GOLD, transparent: true, opacity: 0.8, blending: AdditiveBlending, depthWrite: false, side: DoubleSide }),
    glow: new MeshBasicMaterial({ map: glowTexture(), color: GOLD, transparent: true, opacity: 0.5, blending: AdditiveBlending, depthWrite: false }),
  }), []);
  useEffect(() => () => { materials.ring.dispose(); materials.glow.dispose(); }, [materials]);

  useFrame((_, rawDt) => {
    const g = group.current;
    if (!g) return;
    const at = source();
    if (at) g.position.set(at.x, 0, at.z);
    clock.current += Math.min(rawDt, 0.1);
    const s = clock.current;
    if (reduced) return;
    if (ring.current) {
      const k = s / 1.2;
      ring.current.visible = k < 1;
      ring.current.scale.setScalar((0.35 + easeOut(k) * 1.5) * scale);
      materials.ring.opacity = 0.8 * (1 - k);
    }
    if (glow.current) {
      const k = s / 1.6;
      glow.current.visible = k < 1;
      materials.glow.opacity = 0.5 * (1 - easeOut(k));
    }
  });

  const flat: [number, number, number] = [-Math.PI / 2, 0, 0];
  const word = promoted ? t("society.level.promoted") : t("society.level.level_n").replace("{0}", String(level));
  return (
    <group ref={group}>
      {!reduced && <>
        <mesh ref={glow} material={materials.glow} rotation={flat} position={[0, 0.03, 0]} scale={1.1 * scale} renderOrder={3}>
          <circleGeometry args={[0.8, 32]} />
        </mesh>
        <mesh ref={ring} material={materials.ring} rotation={flat} position={[0, 0.035, 0]} visible={false} renderOrder={3}>
          <ringGeometry args={[0.95, 1, 64]} />
        </mesh>
      </>}
      <Html center position={[0, height + 0.6, 0]} zIndexRange={[45, 40]}>
        <span className="promo-tag" data-kind={kind} style={{ animationDuration: `${BURST_MS}ms` }} aria-hidden>
          <RankInsignia rank={rank} size={30} className="promo-tag-insignia" />
          <span>
            <small>{word}</small>
            <b>{t(`society.level.title.${rank}`)}</b>
          </span>
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
