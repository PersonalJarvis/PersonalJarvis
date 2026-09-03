/**
 * The stand-in walker: a friendly box figure in the agent's palette with
 * procedural limbs — legs and arms swing while walking, hands type at a desk,
 * the whole body breathes at rest. It is NOT the shipping character: the
 * figure pipeline (docs/agent-society/character-pipeline.md) replaces it with
 * a rigged GLB the day the first base passes the gate; the walker sim, the
 * heading and the nameplate stay exactly as they are.
 *
 * The figure faces +Z in its own space (pipeline §4.1), so `headingFor` from
 * `walkerKinematics.ts` turns it the right way.
 */
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import type { Group, Mesh } from "three";

import type { AgentPalette } from "../data";
import { FigureRig, type FigureDrive, type FigureMode } from "../figures/FigureRig";
import type { FigureRecipe } from "../figures/figureRecipe";
import { figureAssetFor } from "../figures/figureRegistry";
import { useKit } from "./WorldKit";
import { PAL } from "./worldMaterials";

/** A warm neutral for head and hands — a doll, not a skin tone. */
const SKIN = "#efe0cd";

/**
 * `carry` and `aim` belong to the retirement ceremony (`retirement.ts`): a
 * stretcher bearer walks with its arms hanging on the poles, and the lead
 * stands still behind a levelled rifle.
 */
export type WalkerMode = "rest" | "walk" | "work" | "sleep" | "carry" | "aim" | "talk";

export interface WalkerAnim {
  mode: WalkerMode;
  /** Current ground speed in m/s (drives the stride rate). */
  speed: number;
  /** How far the rifle is up, 0 (none drawn) to 1 (levelled). Ceremony only. */
  aim?: number;
  /** Muzzle-flash brightness this frame, 0..1. Ceremony only. */
  flash?: number;
}

interface Props {
  palette: AgentPalette;
  anim: { current: WalkerAnim };
  paused: boolean;
  selected: boolean;
  /** The agent's character; null keeps the box stand-in. */
  recipe?: FigureRecipe | null;
}

/**
 * Walkers are drawn larger than life: seen 50° from above at 64 m across the
 * stage a true-scale person is a 20 px sliver, and every isometric game with
 * readable characters cheats the same way. The card keeps true scale.
 */
export const WORLD_HERO_SCALE = 1.6;

const MODE_TO_CLIP: Record<WalkerMode, FigureMode> = {
  rest: "idle",
  walk: "walk",
  work: "work",
  sleep: "sleep",
  // A bearer walks; a shooter stands. Neither has a clip of its own, and
  // inventing one would mean a new figure base for a fifteen-second scene.
  carry: "walk",
  aim: "idle",
  // Authored on every base since the figure pipeline shipped, and never played
  // until the island learned that agents talk to each other.
  talk: "talk",
};

/** Shoulder height as a fraction of the whole figure — where a rifle is held. */
const SHOULDER_FRACTION = 0.7;

/** The box stand-in's overall height in metres, at world hero scale. */
const BOX_FIGURE_HEIGHT_M = 2.04;

/**
 * The shipped character when the roster row carries a recipe whose base is
 * built; the box stand-in otherwise. Both take the same sim-driven `anim`.
 */
export function WalkerFigure(props: Props) {
  const recipe = props.recipe ?? null;
  if (recipe && figureAssetFor(recipe)) {
    return <RiggedWalkerFigure {...props} recipe={recipe} />;
  }
  return <BoxWalkerFigure {...props} />;
}

/**
 * The rifle the lead carries through a retirement (`retirement.ts`).
 *
 * It is not rigged to a hand: both figure kinds — the shipped GLB and the box
 * stand-in — would need a weapon socket in the base for that, and this is a
 * fifteen-second scene, not a combat system. It is held beside the figure at
 * shoulder height instead, swinging up from slung to levelled with `aim`,
 * which reads correctly from every camera angle the island allows.
 *
 * Drawn only while `aim > 0`, and it drives itself from the same `anim` ref
 * the walker writes each frame — no React render is involved in a shot.
 */
function Rifle({ anim, heightM }: { anim: { current: WalkerAnim }; heightM: number }) {
  const kit = useKit();
  const group = useRef<Group>(null);
  const flash = useRef<Group>(null);
  const spark = useRef<Mesh>(null);
  const shoulder = heightM * SHOULDER_FRACTION;

  useFrame(() => {
    const g = group.current;
    if (!g) return;
    const a = Math.max(0, Math.min(1, anim.current.aim ?? 0));
    g.visible = a > 0.001;
    if (!g.visible) return;
    // Slung: low and pointing at the ground. Levelled: shoulder high and flat.
    g.position.set(0.34, shoulder - 0.34 * (1 - a), 0.06 + 0.1 * a);
    g.rotation.x = 0.95 * (1 - a);
    const f = Math.max(0, Math.min(1, anim.current.flash ?? 0));
    if (flash.current) {
      flash.current.visible = f > 0.02;
      const s = 0.16 + f * 0.5;
      flash.current.scale.set(s, s, s);
    }
    if (spark.current) spark.current.rotation.z += 0.6;
  });

  return (
    <group ref={group} visible={false}>
      <mesh geometry={kit.g.box} material={kit.m.lit(PAL.metal)} position={[0, 0, 0.55]} scale={[0.07, 0.07, 1.06]} />
      <mesh geometry={kit.g.box} material={kit.m.lit(PAL.woodDark)} position={[0, -0.02, -0.12]} scale={[0.11, 0.17, 0.52]} />
      <mesh geometry={kit.g.box} material={kit.m.lit(PAL.woodDark)} position={[0, -0.16, 0.14]} scale={[0.09, 0.22, 0.11]} />
      <mesh geometry={kit.g.box} material={kit.m.lit(PAL.metal)} position={[0, 0.08, 0.18]} scale={[0.05, 0.06, 0.34]} />
      <group ref={flash} position={[0, 0, 1.14]} visible={false}>
        <mesh ref={spark} geometry={kit.g.blob} material={kit.m.glow(PAL.muzzle)} />
        <mesh
          geometry={kit.g.disc}
          material={kit.m.halo(PAL.muzzle, 0.6)}
          rotation={[Math.PI / 2, 0, 0]}
          scale={[2.4, 1, 2.4]}
        />
      </group>
    </group>
  );
}

function RiggedWalkerFigure({ palette, anim, paused, selected, recipe }: Props & { recipe: FigureRecipe }) {
  const kit = useKit();
  const ring = useRef<Group>(null);
  const drive = useRef<FigureDrive>({ mode: "idle", speed: 0 });

  useFrame(({ clock }) => {
    const a = anim.current;
    drive.current.mode = MODE_TO_CLIP[a.mode];
    drive.current.speed = a.speed;
    if (ring.current && !paused) {
      const s = 1 + Math.sin(clock.getElapsedTime() * 3) * 0.06;
      ring.current.scale.set(s, 1, s);
    }
  });

  const height = (recipe.heightM ?? 1.75) * WORLD_HERO_SCALE;

  return (
    <group>
      {selected && (
        <group ref={ring} position={[0, 0.04, 0]}>
          <mesh geometry={kit.g.ring} material={kit.m.glow(palette.accent)} rotation={[Math.PI / 2, 0, 0]} scale={[1.9, 1.9, 1.2]} />
        </group>
      )}
      <FigureRig recipe={recipe} drive={drive} paused={paused} heightM={height} />
      <Rifle anim={anim} heightM={height} />
    </group>
  );
}

/**
 * The box stand-in, exported because the retirement ceremony's two stretcher
 * bearers are drawn with it: they are scenery, not roster rows, so they have
 * no figure recipe of their own.
 */
export function BoxWalkerFigure({ palette, anim, paused, selected }: Props) {
  const kit = useKit();
  const body = useRef<Group>(null);
  const legL = useRef<Group>(null);
  const legR = useRef<Group>(null);
  const armL = useRef<Group>(null);
  const armR = useRef<Group>(null);
  const ring = useRef<Group>(null);
  const phase = useRef(Math.random() * Math.PI * 2);

  useFrame(({ clock }, dt) => {
    if (paused) return;
    const a = anim.current;
    const t = clock.getElapsedTime();
    const step = Math.min(dt, 0.1);
    let legSwing = 0;
    let armSwing = 0;
    let armLift = 0;
    let bob = 0;
    if (a.mode === "walk") {
      // Stride rate follows speed: ~1.28 m per full cycle at nominal pace.
      phase.current += step * a.speed * (Math.PI * 2) / 1.28;
      legSwing = Math.sin(phase.current) * 0.65;
      armSwing = -Math.sin(phase.current) * 0.5;
      bob = Math.abs(Math.sin(phase.current)) * 0.05;
    } else if (a.mode === "work") {
      armLift = -1.25;
      armSwing = Math.sin(t * 9) * 0.08;
      bob = Math.sin(t * 2.2) * 0.012;
    } else if (a.mode === "carry") {
      // A bearer strides with the stretcher's poles in both hands: the legs
      // walk, the arms hang still on the wood.
      phase.current += (step * a.speed * (Math.PI * 2)) / 1.28;
      legSwing = Math.sin(phase.current) * 0.5;
      bob = Math.abs(Math.sin(phase.current)) * 0.035;
      armLift = 0.12;
    } else if (a.mode === "aim") {
      // Behind a levelled rifle: both arms come up with the weapon.
      armLift = -1.35 * (a.aim ?? 1);
      bob = Math.sin(t * 1.4) * 0.008;
    } else if (a.mode === "talk") {
      // Standing and speaking: one hand comes up and the body carries the
      // sentence. No steps — a conversation never moves a figure.
      armLift = -0.25 + Math.sin(t * 6.2) * 0.18;
      bob = Math.sin(t * 3.1) * 0.014;
    } else if (a.mode === "sleep") {
      bob = Math.sin(t * 0.9) * 0.01;
      armLift = 0.15;
    } else {
      bob = Math.sin(t * 1.6 + phase.current) * 0.02;
    }
    if (body.current) body.current.position.y = bob;
    if (legL.current) legL.current.rotation.x = legSwing;
    if (legR.current) legR.current.rotation.x = -legSwing;
    if (armL.current) armL.current.rotation.x = armLift + armSwing;
    if (armR.current) armR.current.rotation.x = armLift - armSwing;
    if (ring.current) {
      const s = 1 + Math.sin(t * 3) * 0.06;
      ring.current.scale.set(s, 1, s);
    }
  });

  const lit = kit.m.lit;
  const glow = kit.m.glow;
  const box = kit.g.box;

  return (
    <group>
      {selected && (
        <group ref={ring} position={[0, 0.04, 0]}>
          <mesh geometry={kit.g.ring} material={glow(palette.accent)} rotation={[Math.PI / 2, 0, 0]} scale={[1.9, 1.9, 1.2]} />
        </group>
      )}
      <group ref={body}>
        {/* legs, pivoting at the hip */}
        <group ref={legL} position={[-0.14, 0.86, 0]}>
          <mesh geometry={box} material={lit(palette.secondary)} position={[0, -0.4, 0]} scale={[0.22, 0.8, 0.24]} />
          <mesh geometry={box} material={lit(palette.secondary)} position={[0, -0.8, 0.04]} scale={[0.24, 0.12, 0.32]} />
        </group>
        <group ref={legR} position={[0.14, 0.86, 0]}>
          <mesh geometry={box} material={lit(palette.secondary)} position={[0, -0.4, 0]} scale={[0.22, 0.8, 0.24]} />
          <mesh geometry={box} material={lit(palette.secondary)} position={[0, -0.8, 0.04]} scale={[0.24, 0.12, 0.32]} />
        </group>
        {/* torso and belt */}
        <mesh geometry={box} material={lit(palette.primary)} position={[0, 1.16, 0]} scale={[0.56, 0.62, 0.32]} />
        <mesh geometry={box} material={lit(palette.accent)} position={[0, 0.88, 0]} scale={[0.58, 0.08, 0.34]} />
        {/* arms, pivoting at the shoulder */}
        <group ref={armL} position={[-0.38, 1.42, 0]}>
          <mesh geometry={box} material={lit(palette.primary)} position={[0, -0.28, 0]} scale={[0.16, 0.56, 0.2]} />
          <mesh geometry={box} material={lit(SKIN)} position={[0, -0.6, 0]} scale={[0.15, 0.14, 0.18]} />
        </group>
        <group ref={armR} position={[0.38, 1.42, 0]}>
          <mesh geometry={box} material={lit(palette.primary)} position={[0, -0.28, 0]} scale={[0.16, 0.56, 0.2]} />
          <mesh geometry={box} material={lit(SKIN)} position={[0, -0.6, 0]} scale={[0.15, 0.14, 0.18]} />
        </group>
        {/* head and visor (the visor marks the front: +Z) */}
        <mesh geometry={box} material={lit(SKIN)} position={[0, 1.72, 0]} scale={[0.44, 0.44, 0.4]} />
        <mesh geometry={box} material={glow(palette.accent)} position={[0, 1.76, 0.2]} scale={[0.36, 0.12, 0.06]} />
        <mesh geometry={box} material={lit(palette.secondary)} position={[0, 1.96, -0.02]} scale={[0.46, 0.08, 0.42]} />
      </group>
      <Rifle anim={anim} heightM={BOX_FIGURE_HEIGHT_M} />
    </group>
  );
}
