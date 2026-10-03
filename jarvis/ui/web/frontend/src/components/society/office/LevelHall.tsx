/**
 * The Level Hall: the agents floor's east wing, built like the trophy hall of
 * a game. Midnight stone and brass, lit from within.
 *
 * - The Level Wall on the north wall: a live screen with the person's level,
 *   title, XP bar and the next reward, between two lit brass pillars.
 * - The Upgrade Studio's stage in front of it: a round disc with a glowing
 *   rim and a slowly turning ring of marks. Stepping on it opens the studio.
 * - The Level Road down the middle: a lit runway whose chevrons point up the
 *   levels, lined with a pedestal per reward. Each pedestal shows its reward
 *   as a small hologram in a glass case: lit once unlocked, a dark silhouette
 *   while locked, and the next unlock under a pulsing gold beam. A click on a
 *   pedestal opens that reward on the reward road.
 * - The level guide at the road's start: a free-standing screen, the same on
 *   both faces, listing how XP is earned.
 *
 * Same rules as OfficeProps: every piece is built in local space centred on
 * the origin, front facing +z, inside its `FURNITURE_SIZE` box; the light
 * shaft and the lamps over the stage live in `LevelHallFittings`. The world
 * keeps one palette in light and dark mode (office-map.md §2).
 */
import { memo, useEffect, useMemo, useRef } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import {
  AdditiveBlending, CanvasTexture, ConeGeometry, CylinderGeometry, DoubleSide, MeshBasicMaterial, MeshStandardMaterial,
  RepeatWrapping, SphereGeometry, SRGBColorSpace, TorusGeometry, type Group, type Mesh, type Texture,
} from "three";
import { useT } from "@/i18n";
import { Box, matte, Rounded } from "./OfficeFurniture";
import { cachedCanvasTexture, redrawWhenFontsLoad } from "./canvasMaterials";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind } from "./officeLayout";
import { levelFraction, nextUnlock, rewardRoad } from "../progression/cosmetics";
import { EFFECT_COLOURS, FRAME_STYLE, slotOf, type RewardId } from "../progression/levelCatalog";
import { useFrameStyle } from "../progression/LevelHud";
import { PERSON_SUBJECT, type XpRuleRow } from "../progression/progressionApi";
import { useProgression } from "../progression/progressionStore";
import { glowTexture } from "../progression/effects/flairTextures";

type Vec3 = [number, number, number];
type Ctx = CanvasRenderingContext2D;

// ---------------------------------------------------------------------------
// Materials: midnight stone, brass, cream marble and light.
// ---------------------------------------------------------------------------

const C = {
  midnight: "#141b36",
  navy: "#1d2b52",
  ink: "#0d1228",
  brass: "#d9b25c",
  gold: "#ffd76e",
  marble: "#efe8da",
  marbleVein: "#d8cfbf",
  cyan: "#8fe3ff",
  locked: "#3a4570",
} as const;

const HM = {
  midnight: matte(C.midnight, { roughness: 0.35, metalness: 0.25 }),
  navy: matte(C.navy, { roughness: 0.5 }),
  ink: matte(C.ink, { roughness: 0.3, metalness: 0.3 }),
  brass: matte(C.brass, { roughness: 0.28, metalness: 0.75 }),
  marble: matte(C.marble, { roughness: 0.32 }),
  marbleVein: matte(C.marbleVein, { roughness: 0.4 }),
  // Light strips and rings: lit from within, so they read as light in any scene lighting.
  gold: new MeshStandardMaterial({ color: C.gold, emissive: C.gold, emissiveIntensity: 1.5, toneMapped: false }),
  cyan: new MeshStandardMaterial({ color: C.cyan, emissive: C.cyan, emissiveIntensity: 1.3, toneMapped: false }),
  glass: new MeshStandardMaterial({ color: "#d6ecff", transparent: true, opacity: 0.16, roughness: 0.04, depthWrite: false, side: DoubleSide }),
  locked: new MeshStandardMaterial({ color: C.locked, roughness: 0.6, transparent: true, opacity: 0.75 }),
};

const GEO = {
  disc: new CylinderGeometry(1, 1, 1, 48),
  hex: new CylinderGeometry(1, 1, 1, 6),
  ring: new TorusGeometry(1, 0.016, 8, 64),
  ball: new SphereGeometry(1, 14, 10),
  cone: new ConeGeometry(1, 1, 5),
  rotor: new CylinderGeometry(1, 1, 1, 12),
  shaft: new CylinderGeometry(0.55, 1.6, 2.9, 32, 1, true),
  beam: new CylinderGeometry(0.2, 0.26, 1.5, 20, 1, true),
};

const FONT = '"Inter Variable", "Inter", system-ui, sans-serif';

/** Lit material per colour, shared by every hologram. */
const litCache = new Map<string, MeshStandardMaterial>();
function lit(colour: string): MeshStandardMaterial {
  let material = litCache.get(colour);
  if (!material) {
    material = new MeshStandardMaterial({ color: colour, emissive: colour, emissiveIntensity: 0.9, roughness: 0.3, metalness: 0.2, toneMapped: false });
    litCache.set(colour, material);
  }
  return material;
}

// ---------------------------------------------------------------------------
// Live canvas faces
// ---------------------------------------------------------------------------

/**
 * A canvas texture redrawn whenever `signature` changes, and once more when
 * the hall's font has loaded (canvas text drawn before the face arrives would
 * keep the fallback face).
 */
function useLiveTexture(w: number, h: number, signature: string, draw: (ctx: Ctx, w: number, h: number) => void): Texture | null {
  const canvas = useMemo(() => {
    if (typeof document === "undefined") return null;
    const el = document.createElement("canvas");
    el.width = w;
    el.height = h;
    return el;
  }, [w, h]);
  const texture = useMemo(() => {
    if (!canvas) return null;
    const made = new CanvasTexture(canvas);
    made.colorSpace = SRGBColorSpace;
    made.anisotropy = 4;
    return made;
  }, [canvas]);
  useEffect(() => () => texture?.dispose(), [texture]);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useEffect(() => {
    let ctx: Ctx | null = null;
    try {
      ctx = canvas?.getContext("2d") ?? null;
    } catch {
      // jsdom without the canvas package throws "not implemented": the screen keeps its dark glass, which is honest.
      ctx = null;
    }
    if (!ctx || !texture) return;
    const paint = () => { drawRef.current(ctx!, w, h); texture.needsUpdate = true; };
    paint();
    if (typeof document === "undefined" || !("fonts" in document)) return;
    let live = true;
    document.fonts.load(`700 48px ${FONT}`).then(() => { if (live) paint(); }, () => {
      // A face that fails to load leaves the fallback face already drawn, which stays legible.
    });
    return () => { live = false; };
  }, [signature, canvas, texture, w, h]);
  return texture;
}

function fitText(ctx: Ctx, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let cut = text;
  while (cut.length > 1 && ctx.measureText(`${cut}…`).width > maxWidth) cut = cut.slice(0, -1);
  return `${cut}…`;
}

function hexPath(ctx: Ctx, cx: number, cy: number, r: number): void {
  ctx.beginPath();
  for (let i = 0; i < 6; i += 1) {
    const a = Math.PI / 6 + (i * Math.PI) / 3;
    const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

/** The screen's dark glass: a deep gradient, a faint grid and a soft vignette. */
function screenGround(ctx: Ctx, w: number, h: number): void {
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, "#1b2552");
  bg.addColorStop(1, "#0b1030");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(143,227,255,0.06)";
  ctx.lineWidth = 1;
  for (let x = 0; x < w; x += 48) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  for (let y = 0; y < h; y += 48) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  const glow = ctx.createRadialGradient(w * 0.3, h * 0.45, 10, w * 0.3, h * 0.45, w * 0.6);
  glow.addColorStop(0, "rgba(255,215,110,0.16)");
  glow.addColorStop(1, "rgba(255,215,110,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, w, h);
}

interface WallLines {
  level: number;
  levelWord: string;
  title: string;
  xp: string;
  toNext: string;
  nextLabel: string;
  nextName: string;
  nextAt: string;
  nextColours: [string, string];
  hall: string;
}

function drawWall(ctx: Ctx, w: number, h: number, lines: WallLines, fraction: number, frame: { ring: string; fill: string; ink: string }): void {
  screenGround(ctx, w, h);
  ctx.textBaseline = "middle";
  // The hall's name across the top.
  ctx.fillStyle = "rgba(255,215,110,0.85)";
  ctx.font = `700 34px ${FONT}`;
  ctx.textAlign = "center";
  ctx.fillText(fitText(ctx, lines.hall.toUpperCase(), w - 120), w / 2, 52);
  ctx.textAlign = "left";
  // The level badge: a hexagon in the person's frame colours.
  const bx = 250, by = 330, br = 170;
  ctx.save();
  ctx.shadowColor = frame.ring;
  ctx.shadowBlur = 40;
  hexPath(ctx, bx, by, br);
  ctx.fillStyle = frame.fill;
  ctx.fill();
  ctx.restore();
  hexPath(ctx, bx, by, br);
  ctx.lineWidth = 14;
  ctx.strokeStyle = frame.ring;
  ctx.stroke();
  ctx.fillStyle = frame.ink;
  ctx.textAlign = "center";
  ctx.font = `600 34px ${FONT}`;
  ctx.fillText(lines.levelWord.toUpperCase(), bx, by - 78);
  ctx.font = `800 150px ${FONT}`;
  ctx.fillText(String(lines.level), bx, by + 22);
  ctx.textAlign = "left";
  // Title, XP bar and what is missing.
  const x0 = 480, barW = 600;
  ctx.fillStyle = "#ffffff";
  ctx.font = `800 76px ${FONT}`;
  ctx.fillText(fitText(ctx, lines.title, w - x0 - 60), x0, 230);
  const barY = 318, barH = 34;
  ctx.fillStyle = "rgba(255,255,255,0.12)";
  ctx.beginPath(); ctx.roundRect(x0, barY, barW, barH, barH / 2); ctx.fill();
  const grad = ctx.createLinearGradient(x0, 0, x0 + barW, 0);
  grad.addColorStop(0, "#ffb347");
  grad.addColorStop(1, "#ffe08a");
  ctx.fillStyle = grad;
  ctx.beginPath(); ctx.roundRect(x0, barY, Math.max(barH, barW * fraction), barH, barH / 2); ctx.fill();
  ctx.fillStyle = "#ffe9b0";
  ctx.font = `700 38px ${FONT}`;
  ctx.fillText(lines.xp, x0 + barW + 30, barY + barH / 2);
  ctx.fillStyle = "rgba(255,255,255,0.78)";
  ctx.font = `500 36px ${FONT}`;
  ctx.fillText(fitText(ctx, lines.toNext, w - x0 - 60), x0, 400);
  // The next reward.
  const cardY = 460, cardH = 120, cardW = w - x0 - 60;
  ctx.fillStyle = "rgba(255,255,255,0.07)";
  ctx.beginPath(); ctx.roundRect(x0, cardY, cardW, cardH, 22); ctx.fill();
  ctx.strokeStyle = "rgba(255,215,110,0.45)";
  ctx.lineWidth = 3;
  ctx.stroke();
  const tile = ctx.createLinearGradient(x0 + 24, cardY + 20, x0 + 104, cardY + 100);
  tile.addColorStop(0, lines.nextColours[0]);
  tile.addColorStop(1, lines.nextColours[1]);
  ctx.fillStyle = tile;
  ctx.beginPath(); ctx.roundRect(x0 + 24, cardY + 20, 80, 80, 18); ctx.fill();
  ctx.fillStyle = "rgba(255,215,110,0.95)";
  ctx.font = `700 28px ${FONT}`;
  ctx.fillText(fitText(ctx, lines.nextLabel.toUpperCase(), cardW - 160), x0 + 130, cardY + 38);
  ctx.fillStyle = "#ffffff";
  ctx.font = `700 40px ${FONT}`;
  const name = fitText(ctx, lines.nextName, cardW - 160 - (lines.nextAt ? 220 : 0));
  ctx.fillText(name, x0 + 130, cardY + 82);
  if (lines.nextAt) {
    ctx.textAlign = "right";
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.font = `600 32px ${FONT}`;
    ctx.fillText(lines.nextAt, x0 + cardW - 28, cardY + 82);
    ctx.textAlign = "left";
  }
}

/** A reward's two tile colours: its frame ring and fill, or its effect's first and last colour. */
export function rewardColours(reward: RewardId): [string, string] {
  if (slotOf(reward) === "frame") {
    const style = FRAME_STYLE[reward as keyof typeof FRAME_STYLE];
    return [style.ring, style.fill];
  }
  const colours = EFFECT_COLOURS[reward as keyof typeof EFFECT_COLOURS];
  return [colours[0], colours[colours.length - 1]];
}

// ---------------------------------------------------------------------------
// The Level Wall
// ---------------------------------------------------------------------------

const WALL = { w: FURNITURE_SIZE.levelWall.w, h: FURNITURE_SIZE.levelWall.h, d: FURNITURE_SIZE.levelWall.d, screenW: 5.0, screenH: 2.08, screenY: 1.72 };

function LevelWall() {
  const t = useT();
  const person = useProgression((s) => s.subjects[PERSON_SUBJECT]);
  const rewards = useProgression((s) => s.snapshot?.rewards);
  const level = person?.level ?? 1;
  const frame = useFrameStyle("person", level);
  const fraction = levelFraction(person);
  const next = rewards ? nextUnlock(rewards, "person", level) : undefined;
  const lines: WallLines = {
    level,
    levelWord: t("society.level.level_n").replace("{0}", "").trim(),
    title: t(`society.level.title.${person?.title || "newcomer"}`),
    xp: person && person.xpForNext > 0 ? t("society.level.xp_of").replace("{0}", String(person.xpIntoLevel)).replace("{1}", String(person.xpForNext)) : "",
    toNext: person && person.xpForNext <= 0 ? t("society.level.max")
      : t("society.level.to_next").replace("{0}", String(person ? person.xpForNext - person.xpIntoLevel : 40)).replace("{1}", String(level + 1)),
    nextLabel: t("society.hall.next_reward"),
    nextName: next ? t(`society.level.reward.${next.rewardId}`) : t("society.hall.all_unlocked"),
    nextAt: next ? t("society.level.locked_at").replace("{0}", String(next.levels.person)) : "",
    nextColours: next ? rewardColours(next.rewardId) : [C.gold, C.brass],
    hall: t("society.office.room_levels"),
  };
  const signature = JSON.stringify([lines, Math.round(fraction * 200), frame]);
  const face = useLiveTexture(1536, 640, signature, (ctx, w, h) => drawWall(ctx, w, h, lines, fraction, frame));
  return (
    <group>
      {/* Backing panel with a brass frame round the screen. */}
      <Box size={[WALL.w, WALL.h, 0.2]} position={[0, WALL.h / 2, -WALL.d / 2 + 0.1]} material={HM.midnight} />
      <Box size={[WALL.screenW + 0.24, WALL.screenH + 0.24, 0.06]} position={[0, WALL.screenY, 0.03]} material={HM.brass} />
      <mesh position={[0, WALL.screenY, 0.065]}>
        <planeGeometry args={[WALL.screenW, WALL.screenH]} />
        {face
          ? <meshStandardMaterial map={face} emissiveMap={face} emissive="#ffffff" emissiveIntensity={0.85} roughness={0.4} />
          : <meshStandardMaterial color={C.ink} roughness={0.3} />}
      </mesh>
      {/* Two lit brass pillars either side, a gold strip along the top and the bottom. */}
      {[-1, 1].map((side) => (
        <group key={side} position={[side * (WALL.w / 2 - 0.3), 0, 0]}>
          <Box size={[0.4, WALL.h, 0.3]} position={[0, WALL.h / 2, -0.02]} material={HM.navy} />
          <Box size={[0.07, WALL.h - 0.4, 0.04]} position={[0, WALL.h / 2, 0.14]} material={HM.gold} cast={false} />
          <Box size={[0.46, 0.08, 0.34]} position={[0, WALL.h - 0.04, -0.02]} material={HM.brass} />
          <Box size={[0.46, 0.12, 0.34]} position={[0, 0.06, -0.02]} material={HM.brass} />
        </group>
      ))}
      <Box size={[WALL.screenW, 0.04, 0.04]} position={[0, WALL.screenY + WALL.screenH / 2 + 0.22, 0.05]} material={HM.gold} cast={false} />
      <Box size={[WALL.screenW, 0.04, 0.04]} position={[0, WALL.screenY - WALL.screenH / 2 - 0.22, 0.05]} material={HM.gold} cast={false} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// The Upgrade Studio's stage
// ---------------------------------------------------------------------------

const STAGE_R = FURNITURE_SIZE.studioStage.w / 2 - 0.04;

/** The stage disc; the Upgrade Studio's preview stands its figure on the same one. */
export function StudioStage() {
  const marks = useRef<Group>(null);
  useFrame((_, dt) => { if (marks.current) marks.current.rotation.y += Math.min(dt, 0.1) * 0.22; });
  const glow = useMemo(() => new MeshBasicMaterial({
    map: glowTexture(), color: C.gold, transparent: true, opacity: 0.35, blending: AdditiveBlending, depthWrite: false,
  }), []);
  useEffect(() => () => glow.dispose(), [glow]);
  return (
    <group>
      {/* Flat on purpose: figures stand at y 0 and auras glow at 0.018 m, so nothing here rises above ~0.015 m. */}
      <mesh geometry={GEO.disc} material={HM.midnight} position={[0, 0.004, 0]} scale={[STAGE_R, 0.008, STAGE_R]} receiveShadow />
      <mesh geometry={GEO.disc} material={HM.brass} position={[0, 0.009, 0]} scale={[STAGE_R * 0.98, 0.002, STAGE_R * 0.98]} />
      <mesh geometry={GEO.disc} material={HM.navy} position={[0, 0.011, 0]} scale={[STAGE_R * 0.9, 0.002, STAGE_R * 0.9]} receiveShadow />
      <mesh geometry={GEO.ring} material={HM.gold} position={[0, 0.013, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[STAGE_R * 0.95, STAGE_R * 0.95, 0.5]} />
      <mesh geometry={GEO.ring} material={HM.brass} position={[0, 0.012, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[STAGE_R * 0.55, STAGE_R * 0.55, 0.4]} />
      <mesh material={glow} position={[0, 0.014, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[STAGE_R * 0.75, 40]} />
      </mesh>
      <group ref={marks} position={[0, 0.013, 0]}>
        {Array.from({ length: 16 }, (_, i) => {
          const a = (i / 16) * Math.PI * 2, r = STAGE_R * 0.74;
          return <Box key={i} size={[0.14, 0.008, 0.035]} position={[Math.cos(a) * r, 0, Math.sin(a) * r]} material={i % 4 === 0 ? HM.gold : HM.cyan} cast={false} />;
        })}
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Reward pedestals and their holograms
// ---------------------------------------------------------------------------

/** A reward as a small model, about 0.36 m across: lit in its own colours, or a dark silhouette while locked. */
function RewardMini({ reward, open }: { reward: RewardId; open: boolean }) {
  const slot = slotOf(reward);
  const [a, b] = rewardColours(reward);
  const effect = slot === "frame" ? [a, b] : EFFECT_COLOURS[reward as keyof typeof EFFECT_COLOURS];
  const m = (colour: string) => (open ? lit(colour) : HM.locked);
  if (slot === "frame") {
    return (
      <group rotation={[Math.PI / 2, 0, 0]}>
        <mesh geometry={GEO.hex} material={m(a)} scale={[0.17, 0.05, 0.17]} />
        <mesh geometry={GEO.hex} material={m(b)} position={[0, 0.03, 0]} scale={[0.12, 0.02, 0.12]} />
        <mesh geometry={GEO.hex} material={m(b)} position={[0, -0.03, 0]} scale={[0.12, 0.02, 0.12]} />
      </group>
    );
  }
  if (slot === "trail") {
    // A rising spiral of beads, each in the trail's colours.
    return (
      <group>
        {Array.from({ length: 7 }, (_, i) => {
          const angle = i * 0.95, r = 0.05 + i * 0.018;
          return <mesh key={i} geometry={GEO.ball} material={m(effect[i % effect.length])}
            position={[Math.cos(angle) * r, -0.15 + i * 0.05, Math.sin(angle) * r]} scale={0.022 + i * 0.006} />;
        })}
      </group>
    );
  }
  if (slot === "aura") {
    return (
      <group position={[0, -0.1, 0]}>
        <mesh geometry={GEO.ring} material={m(effect[0])} rotation={[Math.PI / 2, 0, 0]} scale={[0.17, 0.17, 2.4]} />
        <mesh geometry={GEO.ring} material={m(effect[effect.length - 1])} rotation={[Math.PI / 2, 0, 0]} position={[0, 0.03, 0]} scale={[0.11, 0.11, 2]} />
        <mesh geometry={GEO.disc} material={m(effect[0])} scale={[0.07, 0.012, 0.07]} />
      </group>
    );
  }
  if (reward === "gadget_halo") {
    return <mesh geometry={GEO.ring} material={m(effect[0])} rotation={[Math.PI / 2.4, 0, 0]} scale={[0.14, 0.14, 3]} />;
  }
  if (reward === "gadget_crown") {
    return (
      <group position={[0, -0.06, 0]}>
        <mesh geometry={GEO.disc} material={m(effect[0])} position={[0, 0.04, 0]} scale={[0.13, 0.08, 0.13]} />
        {[0, 1, 2, 3, 4].map((i) => {
          const ang = (i / 5) * Math.PI * 2;
          return <mesh key={i} geometry={GEO.cone} material={m(effect[0])} position={[Math.sin(ang) * 0.11, 0.12, Math.cos(ang) * 0.11]} scale={[0.028, 0.08, 0.028]} />;
        })}
        <mesh geometry={GEO.ball} material={m(effect[1] ?? effect[0])} position={[0, 0.05, 0.13]} scale={0.022} />
      </group>
    );
  }
  if (reward === "gadget_wings") {
    // Two fans of long feathers.
    return (
      <group>
        {[-1, 1].map((side) => (
          <group key={side} scale={[side, 1, 1]}>
            {[0, 1, 2, 3].map((i) => (
              <mesh key={i} geometry={GEO.ball} material={m(i % 2 === 0 ? effect[0] : effect[1] ?? effect[0])}
                position={[0.06 + i * 0.035, 0.02 + i * 0.02, 0]} rotation={[0, 0, -0.5 + i * 0.35]} scale={[0.11, 0.022, 0.012]} />
            ))}
          </group>
        ))}
      </group>
    );
  }
  // The helper drone: a body, four rotors and a lens.
  return (
    <group>
      <mesh geometry={GEO.disc} material={m(effect[0])} scale={[0.07, 0.035, 0.07]} />
      {[[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([sx, sz]) => (
        <group key={`${sx}${sz}`} position={[sx * 0.09, 0.02, sz * 0.09]}>
          <Box size={[0.012, 0.012, 0.012]} position={[-sx * 0.04, 0, -sz * 0.04]} material={m(effect[0])} cast={false} />
          <mesh geometry={GEO.rotor} material={m(effect[1] ?? effect[0])} scale={[0.04, 0.006, 0.04]} />
        </group>
      ))}
      <mesh geometry={GEO.ball} material={m(effect[1] ?? effect[0])} position={[0, -0.02, 0.06]} scale={0.016} />
    </group>
  );
}

const PEDESTAL = { holoY: 1.32, caseY: 1.31, caseH: 0.62, caseW: 0.56 };

/** The level plate on a pedestal's front: the unlock level, the reward's name, a padlock while locked. */
function plateMaterial(at: number, name: string, open: boolean, next: boolean, levelWord: string): MeshStandardMaterial {
  const key = `level-plate:${at}:${name}:${open}:${next}:${levelWord}`;
  const draw = (ctx: Ctx, w: number, h: number) => {
    ctx.fillStyle = open ? "#1d2b52" : "#151b33";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = next ? C.gold : open ? C.brass : "#4a5480";
    ctx.lineWidth = 8;
    ctx.strokeRect(4, 4, w - 8, h - 8);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = next ? C.gold : open ? "#ffe9b0" : "#9aa3c7";
    ctx.font = `800 54px ${FONT}`;
    ctx.fillText(`${levelWord} ${at}`.trim(), w / 2, 56);
    ctx.fillStyle = open ? "#ffffff" : "#aab2d4";
    ctx.font = `600 30px ${FONT}`;
    ctx.fillText(fitText(ctx, name, w - 40), w / 2, 120);
    if (!open) {
      // A small padlock in the corner.
      ctx.strokeStyle = "#9aa3c7";
      ctx.lineWidth = 5;
      ctx.beginPath(); ctx.arc(w - 40, 34, 11, Math.PI, 0); ctx.stroke();
      ctx.fillStyle = "#9aa3c7";
      ctx.fillRect(w - 56, 34, 32, 24);
    }
  };
  const map = cachedCanvasTexture(key, 384, 168, draw);
  redrawWhenFontsLoad(key, map, [`800 54px ${FONT}`, `600 30px ${FONT}`], draw);
  let material = plateCache.get(key);
  if (!material) {
    material = new MeshStandardMaterial({ color: map ? "#ffffff" : "#1d2b52", map, emissive: "#ffffff", emissiveMap: map, emissiveIntensity: map ? 0.55 : 0, roughness: 0.5 });
    plateCache.set(key, material);
  }
  return material;
}
const plateCache = new Map<string, MeshStandardMaterial>();

function RewardPedestal({ item }: { item: Furniture }) {
  const t = useT();
  const index = Number(item.id.slice("level-pedestal-".length));
  const rewards = useProgression((s) => s.snapshot?.rewards);
  const level = useProgression((s) => s.subjects[PERSON_SUBJECT]?.level ?? 1);
  const road = useMemo(() => (rewards ? rewardRoad(rewards, "person") : []), [rewards]);
  const reward = road[index];
  const at = reward?.levels.person ?? 0;
  const open = !!reward && level >= at;
  const next = !!reward && !!rewards && nextUnlock(rewards, "person", level)?.rewardId === reward.rewardId;
  const holo = useRef<Group>(null);
  const beam = useRef<Mesh>(null);
  useFrame((state, dt) => {
    const step = Math.min(dt, 0.1);
    if (holo.current) {
      holo.current.rotation.y += step * (open ? 0.9 : 0.3);
      holo.current.position.y = PEDESTAL.holoY + (open ? Math.sin(state.clock.elapsedTime * 1.6 + index) * 0.025 : 0);
    }
    if (beam.current) (beam.current.material as MeshBasicMaterial).opacity = 0.18 + 0.14 * Math.sin(state.clock.elapsedTime * 2.6);
  });
  const glow = useMemo(() => new MeshBasicMaterial({
    map: glowTexture(), color: next ? C.gold : open && reward ? rewardColours(reward.rewardId)[0] : "#6b78b0",
    transparent: true, opacity: open || next ? 0.55 : 0.2, blending: AdditiveBlending, depthWrite: false,
  }), [next, open, reward]);
  const beamMaterial = useMemo(() => new MeshBasicMaterial({
    color: C.gold, transparent: true, opacity: 0.25, blending: AdditiveBlending, depthWrite: false, side: DoubleSide,
  }), []);
  useEffect(() => () => { glow.dispose(); beamMaterial.dispose(); }, [glow, beamMaterial]);
  const onClick = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 6 || !reward) return;
    event.stopPropagation();
    useProgression.getState().openPanel("rewards", { subject: "person", reward: reward.rewardId });
  };
  const hover = (on: boolean) => (event: ThreeEvent<PointerEvent>) => {
    if (!reward) return;
    event.stopPropagation();
    document.body.style.cursor = on ? "pointer" : "";
  };
  useEffect(() => () => { document.body.style.cursor = ""; }, []);
  const name = reward ? t(`society.level.reward.${reward.rewardId}`) : "";
  const levelWord = t("society.level.lv").replace("{0}", "").trim();
  return (
    <group onClick={onClick} onPointerOver={hover(true)} onPointerOut={hover(false)}>
      {/* Plinth: a brass-banded cream marble column on a midnight base. */}
      <Rounded size={[0.74, 0.12, 0.74]} radius={0.03} position={[0, 0.06, 0]} material={HM.midnight} />
      <Rounded size={[0.56, 0.82, 0.56]} radius={0.04} position={[0, 0.53, 0]} material={HM.marble} />
      <Box size={[0.585, 0.05, 0.585]} position={[0, 0.2, 0]} material={HM.brass} />
      <Box size={[0.585, 0.05, 0.585]} position={[0, 0.86, 0]} material={HM.brass} />
      <Rounded size={[0.68, 0.07, 0.68]} radius={0.02} position={[0, 0.975, 0]} material={HM.midnight} />
      {reward && (
        <mesh position={[0, 0.56, 0.282]} material={plateMaterial(at, name, open, next, levelWord)}>
          <planeGeometry args={[0.46, 0.2]} />
        </mesh>
      )}
      {/* The glass case, its brass cap and the light under the reward. */}
      <mesh position={[0, PEDESTAL.caseY, 0]} material={HM.glass} renderOrder={2}>
        <boxGeometry args={[PEDESTAL.caseW, PEDESTAL.caseH, PEDESTAL.caseW]} />
      </mesh>
      <Box size={[0.6, 0.04, 0.6]} position={[0, PEDESTAL.caseY + PEDESTAL.caseH / 2 + 0.02, 0]} material={HM.brass} />
      <mesh material={glow} position={[0, 1.02, 0]} rotation={[-Math.PI / 2, 0, 0]} renderOrder={1}>
        <circleGeometry args={[0.3, 24]} />
      </mesh>
      {reward && <group ref={holo} position={[0, PEDESTAL.holoY, 0]}><RewardMini reward={reward.rewardId} open={open} /></group>}
      {next && <mesh ref={beam} geometry={GEO.beam} material={beamMaterial} position={[0, 1.02 + 0.75, 0]} renderOrder={3} />}
    </group>
  );
}

// ---------------------------------------------------------------------------
// The Level Road and the level guide
// ---------------------------------------------------------------------------

function roadTexture(): Texture | null {
  return cachedCanvasTexture("level-road", 256, 512, (ctx, w, h) => {
    ctx.fillStyle = "#24336a";
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    ctx.fillRect(w * 0.1, 0, w * 0.8, h);
    // Brass edges.
    ctx.fillStyle = "#d9b25c";
    ctx.fillRect(0, 0, 10, h);
    ctx.fillRect(w - 10, 0, 10, h);
    // Chevrons pointing up the levels (north).
    ctx.strokeStyle = "rgba(255,215,110,0.75)";
    ctx.lineWidth = 12;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const y of [140, 380]) {
      ctx.beginPath();
      ctx.moveTo(w * 0.28, y + 50);
      ctx.lineTo(w * 0.5, y);
      ctx.lineTo(w * 0.72, y + 50);
      ctx.stroke();
    }
  });
}

function LevelRoad({ w, d }: { w: number; d: number }) {
  const material = useMemo(() => {
    const base = roadTexture();
    const map = base ? base.clone() : null;
    if (map) {
      map.wrapS = RepeatWrapping;
      map.wrapT = RepeatWrapping;
      map.repeat.set(1, Math.max(1, Math.round(d / 2.2)));
      map.needsUpdate = true;
    }
    return new MeshStandardMaterial({
      color: map ? "#ffffff" : "#24336a", map, emissive: "#ffffff", emissiveMap: map, emissiveIntensity: map ? 0.35 : 0, roughness: 0.35,
    });
  }, [d]);
  useEffect(() => () => { material.map?.dispose(); material.dispose(); }, [material]);
  return (
    <mesh material={material} position={[0, 0.008, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
      <planeGeometry args={[w, d]} />
    </mesh>
  );
}

interface GuideLines { title: string; subtitle: string; rows: { xp: string; text: string }[]; footer: string }

function drawGuide(ctx: Ctx, w: number, h: number, lines: GuideLines): void {
  screenGround(ctx, w, h);
  ctx.textBaseline = "middle";
  ctx.fillStyle = C.gold;
  ctx.font = `800 64px ${FONT}`;
  ctx.fillText(fitText(ctx, lines.title, w - 100), 56, 74);
  ctx.fillStyle = "rgba(255,255,255,0.72)";
  ctx.font = `500 32px ${FONT}`;
  ctx.fillText(fitText(ctx, lines.subtitle, w - 100), 56, 128);
  const rowH = 70;
  lines.rows.forEach((row, i) => {
    const y = 200 + i * rowH;
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    ctx.beginPath(); ctx.roundRect(48, y - rowH / 2 + 6, w - 96, rowH - 12, 14); ctx.fill();
    ctx.fillStyle = "#ffd76e";
    ctx.font = `800 36px ${FONT}`;
    ctx.fillText(row.xp, 72, y);
    ctx.fillStyle = "#ffffff";
    ctx.font = `500 34px ${FONT}`;
    ctx.fillText(fitText(ctx, row.text, w - 330), 230, y);
  });
  ctx.fillStyle = "rgba(143,227,255,0.85)";
  ctx.font = `600 30px ${FONT}`;
  ctx.fillText(fitText(ctx, lines.footer, w - 100), 56, h - 46);
}

const GUIDE = { w: FURNITURE_SIZE.levelGuide.w, h: FURNITURE_SIZE.levelGuide.h, screenW: 3.08, screenH: 1.7, screenY: 1.5 };
/** The guide lists the person's best-paying rules, at most this many. */
const GUIDE_ROWS = 6;
const NO_RULES: XpRuleRow[] = [];

function LevelGuide() {
  const t = useT();
  const rules = useProgression((s) => s.snapshot?.rules ?? NO_RULES);
  const rows = useMemo(() => rules.filter((r) => r.kind === "person").sort((a, b) => b.xp - a.xp).slice(0, GUIDE_ROWS)
    .map((r) => ({ xp: `+${r.xp} XP`, text: t(`society.level.source.${r.source}`) })), [rules, t]);
  const lines: GuideLines = {
    title: t("society.hall.guide_title"), subtitle: t("society.hall.guide_subtitle"), rows, footer: t("society.hall.guide_footer"),
  };
  const face = useLiveTexture(1280, 704, JSON.stringify(lines), (ctx, w, h) => drawGuide(ctx, w, h, lines));
  const screen = face
    ? <meshStandardMaterial map={face} emissiveMap={face} emissive="#ffffff" emissiveIntensity={0.85} roughness={0.4} />
    : <meshStandardMaterial color={C.ink} roughness={0.3} />;
  return (
    <group>
      {[-1, 1].map((side) => (
        <group key={side} position={[side * (GUIDE.w / 2 - 0.1), 0, 0]}>
          <Box size={[0.1, GUIDE.h - 0.05, 0.12]} position={[0, (GUIDE.h - 0.05) / 2, 0]} material={HM.brass} />
          <Rounded size={[0.2, 0.06, 0.34]} radius={0.02} position={[0, 0.03, 0]} material={HM.midnight} />
        </group>
      ))}
      <Rounded size={[GUIDE.screenW + 0.18, GUIDE.screenH + 0.18, 0.16]} radius={0.04} position={[0, GUIDE.screenY, 0]} material={HM.midnight} />
      <Box size={[GUIDE.screenW + 0.18, 0.03, 0.17]} position={[0, GUIDE.screenY + GUIDE.screenH / 2 + 0.105, 0]} material={HM.gold} cast={false} />
      {/* The same screen on both faces. */}
      <mesh position={[0, GUIDE.screenY, 0.085]}><planeGeometry args={[GUIDE.screenW, GUIDE.screenH]} />{screen}</mesh>
      <mesh position={[0, GUIDE.screenY, -0.085]} rotation={[0, Math.PI, 0]}><planeGeometry args={[GUIDE.screenW, GUIDE.screenH]} />{screen}</mesh>
    </group>
  );
}

/** Renderers of the Level Hall's furniture kinds; merged into OfficeProps' exhaustive table. */
export const LEVEL_HALL_RENDERERS = {
  levelWall: () => <LevelWall />,
  studioStage: () => <StudioStage />,
  rewardPedestal: ({ item }: { item: Furniture }) => <RewardPedestal item={item} />,
  levelRoad: ({ item }: { item: Furniture }) => {
    const size = item.size ?? FURNITURE_SIZE.levelRoad;
    return <LevelRoad w={size.w} d={size.d} />;
  },
  levelGuide: () => <LevelGuide />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;

/**
 * What hangs over the hall rather than stands in it: a brass ring lamp over
 * the stage with a soft shaft of light down to it, and warm lights over the
 * stage and the guide.
 */
export const LevelHallFittings = memo(function LevelHallFittings({ stage, guide }: { stage: Pick<Furniture, "x" | "z">; guide: Pick<Furniture, "x" | "z"> | null }) {
  const shaft = useMemo(() => new MeshBasicMaterial({
    color: "#ffe2a0", transparent: true, opacity: 0.09, blending: AdditiveBlending, depthWrite: false, side: DoubleSide,
  }), []);
  useEffect(() => () => shaft.dispose(), [shaft]);
  const at: Vec3 = [stage.x, 0, stage.z];
  return (
    <>
      <group position={at}>
        <mesh geometry={GEO.ring} material={HM.brass} position={[0, 3.05, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[1.05, 1.05, 3]} />
        <mesh geometry={GEO.ring} material={HM.gold} position={[0, 3.0, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[0.95, 0.95, 2]} />
        <mesh geometry={GEO.shaft} material={shaft} position={[0, 1.5, 0]} renderOrder={1} />
        <pointLight position={[0, 2.6, 0.4]} color="#ffdca0" intensity={6} distance={7} decay={2} />
      </group>
      {guide && <pointLight position={[guide.x, 2.8, guide.z - 1.4]} color="#cfe3ff" intensity={3.5} distance={6} decay={2} />}
    </>
  );
});
