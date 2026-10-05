/**
 * The Level Hall: the agents floor's east wing, a hall of honour. Cream and
 * charcoal marble underfoot, walnut and brass, a red runner up the middle,
 * and every piece on show is the real one — the same geometry the figures
 * wear.
 *
 * - The rank wall on the north wall: a walnut-framed shadow box holding all
 *   24 rank insignia on navy velvet, in two rows like a service chart —
 *   enlisted and NCO ranks above, officers and generals below. Ranks the
 *   person holds or held are struck in gold and silver, the current one is
 *   framed in brass, the ones ahead are pewter silhouettes. A brass plaque
 *   below reads the person's rank, level, XP and next promotion.
 * - The studio dais in front of it: a round walnut platform with a brass
 *   ring and star inlay. Stepping on it opens the studio.
 * - Display cases along the runner, one per uniform piece, lowest unlock at
 *   the south end: a mannequin in the uniform (wearing the person's own rank
 *   insignia), a cap on a velvet head form, or ribbons and medals on a slanted
 *   velvet board — exactly the pieces the figure wears. An unlocked case is
 *   lit; a locked one stays dim behind smoked glass. A brass plaque on each
 *   names the piece and the rank that brings it. A click opens it on the
 *   promotion road.
 * - The service guide at the runner's start: a framed, double-sided board
 *   listing how XP is earned.
 *
 * Same rules as OfficeProps: every piece is built in local space centred on
 * the origin, front facing +z, inside its `FURNITURE_SIZE` box; the lights
 * over the dais live in `LevelHallFittings`. The world keeps one palette in
 * light and dark mode (office-map.md §2).
 */
import { memo, useEffect, useMemo, useRef } from "react";
import { type ThreeEvent } from "@react-three/fiber";
import {
  CanvasTexture, CylinderGeometry, DoubleSide, MeshStandardMaterial, RepeatWrapping, Shape, ShapeGeometry, SRGBColorSpace, Vector2,
  type Texture,
} from "three";
import { useT } from "@/i18n";
import { Box, matte, Rounded } from "./OfficeFurniture";
import { cachedCanvasTexture, redrawWhenFontsLoad } from "./canvasMaterials";
import { FURNITURE_SIZE, type Furniture, type FurnitureKind } from "./officeLayout";
import { ToyFigure } from "./ToyFigure";
import { TOY, toyLookFor, type ToyLook } from "./toyFigureModel";
import { equippedFor, levelFraction, rewardRoad } from "../progression/cosmetics";
import { RANK_INFO, rankAt, slotOf, TITLE_IDS, type RankId, type RewardId } from "../progression/levelCatalog";
import { PERSON_SUBJECT, type XpRuleRow } from "../progression/progressionApi";
import { useProgression } from "../progression/progressionStore";
import { insigniaHeight, RankInsignia3D } from "../progression/regalia/insignia3d";
import { Aiguillette, BreastDecoration } from "../progression/regalia/decorations3d";
import { dressedLook, regaliaFor, type UniformId } from "../progression/regalia/dress";
import { Headwear, type HeadwearId } from "../progression/regalia/headwear3d";

type Ctx = CanvasRenderingContext2D;

// ---------------------------------------------------------------------------
// Materials: walnut, brass, navy velvet, glass.
// ---------------------------------------------------------------------------

const C = {
  walnut: "#4a2f1f",
  walnutDark: "#2c1b12",
  brass: "#c9a24a",
  velvet: "#1a2240",
  ink: "#141a2e",
  cream: "#efe8d8",
} as const;

const HM = {
  walnut: matte(C.walnut, { roughness: 0.45 }),
  walnutDark: matte(C.walnutDark, { roughness: 0.4 }),
  brass: matte(C.brass, { roughness: 0.3, metalness: 0.7 }),
  velvet: matte(C.velvet, { roughness: 1 }),
  cream: matte(C.cream, { roughness: 0.5 }),
  ink: matte(C.ink, { roughness: 0.5 }),
  glass: new MeshStandardMaterial({ color: "#e4f0ff", transparent: true, opacity: 0.12, roughness: 0.05, metalness: 0.1, depthWrite: false, side: DoubleSide }),
  /** A locked case: smoked glass. */
  smoked: new MeshStandardMaterial({ color: "#20242e", transparent: true, opacity: 0.42, roughness: 0.1, depthWrite: false, side: DoubleSide }),
  /** The light panel inside the top of a lit case. */
  caseLight: new MeshStandardMaterial({ color: "#fff4dc", emissive: "#fff1d0", emissiveIntensity: 1.6, toneMapped: false }),
  caseDark: matte("#191b22", { roughness: 0.8 }),
};

const FONT = '"Inter Variable", "Inter", system-ui, sans-serif';
const SERIF = 'Georgia, "Times New Roman", serif';

// ---------------------------------------------------------------------------
// Canvas faces
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
      // jsdom without the canvas package throws "not implemented": the plaque keeps its plain face, which is honest.
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

/** Polished brass: a vertical sheen, a bevelled edge and engraved (dark, inset) text. */
function brassGround(ctx: Ctx, w: number, h: number): void {
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, "#e6c97f");
  g.addColorStop(0.45, "#c9a24a");
  g.addColorStop(1, "#9c7a2e");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(60,40,8,0.55)";
  ctx.lineWidth = Math.max(2, h * 0.04);
  ctx.strokeRect(ctx.lineWidth, ctx.lineWidth, w - ctx.lineWidth * 2, h - ctx.lineWidth * 2);
}

function engrave(ctx: Ctx, text: string, x: number, y: number): void {
  ctx.fillStyle = "rgba(255,240,200,0.45)";
  ctx.fillText(text, x, y + 1.5);
  ctx.fillStyle = "#3a2a0c";
  ctx.fillText(text, x, y);
}

const plateCache = new Map<string, MeshStandardMaterial>();

/** A brass plaque material with up to two engraved lines, cached by its text. */
function plaqueMaterial(lines: readonly string[], w = 512, h = 128): MeshStandardMaterial {
  const key = `hall-plaque:${w}x${h}:${lines.join("|")}`;
  const draw = (ctx: Ctx, cw: number, ch: number) => {
    brassGround(ctx, cw, ch);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (lines.length === 1) {
      ctx.font = `700 ${Math.round(ch * 0.42)}px ${SERIF}`;
      engrave(ctx, fitText(ctx, lines[0], cw - 40), cw / 2, ch / 2);
    } else {
      ctx.font = `700 ${Math.round(ch * 0.3)}px ${SERIF}`;
      engrave(ctx, fitText(ctx, lines[0], cw - 36), cw / 2, ch * 0.36);
      ctx.font = `600 ${Math.round(ch * 0.2)}px ${FONT}`;
      engrave(ctx, fitText(ctx, lines[1], cw - 36), cw / 2, ch * 0.72);
    }
  };
  let material = plateCache.get(key);
  if (!material) {
    const map = cachedCanvasTexture(key, w, h, draw);
    redrawWhenFontsLoad(key, map, [`700 40px ${FONT}`], draw);
    material = new MeshStandardMaterial({ color: map ? "#ffffff" : C.brass, map, roughness: 0.35, metalness: 0.45 });
    plateCache.set(key, material);
  }
  return material;
}

// ---------------------------------------------------------------------------
// The rank wall
// ---------------------------------------------------------------------------

const WALL = { w: FURNITURE_SIZE.levelWall.w, h: FURNITURE_SIZE.levelWall.h, d: FURNITURE_SIZE.levelWall.d };
/** The velvet field inside the frame, and the two rows of ranks in it. */
const FIELD = { w: 6.0, h: 1.72, y: 1.83 };
const ROWS: readonly (readonly RankId[])[] = [TITLE_IDS.slice(0, 13), TITLE_IDS.slice(13)];
const CELL = FIELD.w / 13;

function RankCell({ rank, reached, current, x, y, levelAt }: { rank: RankId; reached: boolean; current: boolean; x: number; y: number; levelAt: number }) {
  const t = useT();
  // As wide as the cell allows, unless that would make a tall chevron set overrun the row.
  const fitW = CELL * 0.64, maxH = 0.36;
  const width = Math.min(fitW, (fitW * maxH) / insigniaHeight(rank, fitW));
  const label = plaqueMaterial([t(`society.level.title.${rank}`), `${RANK_INFO[rank].grade} · ${t("society.level.lv").replace("{0}", String(levelAt))}`], 320, 112);
  return (
    <group position={[x, y, 0]}>
      {current && (
        <group>
          <Box size={[CELL - 0.03, 0.8, 0.012]} position={[0, -0.02, 0.006]} material={HM.brass} cast={false} />
          <Box size={[CELL - 0.07, 0.76, 0.014]} position={[0, -0.02, 0.008]} material={HM.velvet} cast={false} />
        </group>
      )}
      <group position={[0, 0.12, 0.02]}>
        {rank === "private"
          ? <mesh material={reached ? HM.brass : HM.ink}><torusGeometry args={[0.07, 0.006, 6, 32]} /></mesh>
          : <RankInsignia3D rank={rank} width={width} muted={!reached} />}
      </group>
      <mesh material={label} position={[0, -0.27, 0.018]}>
        <planeGeometry args={[CELL - 0.06, (CELL - 0.06) * (112 / 320)]} />
      </mesh>
    </group>
  );
}

interface StatusLines { hall: string; rank: string; grade: string; level: string; xp: string; next: string }

function drawStatus(ctx: Ctx, w: number, h: number, lines: StatusLines, fraction: number): void {
  brassGround(ctx, w, h);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.font = `700 ${Math.round(h * 0.2)}px ${SERIF}`;
  engrave(ctx, fitText(ctx, lines.rank, w * 0.5), 40, h * 0.32);
  ctx.font = `600 ${Math.round(h * 0.12)}px ${FONT}`;
  engrave(ctx, fitText(ctx, `${lines.grade}  ·  ${lines.level}`, w * 0.5), 40, h * 0.62);
  // The XP bar: an engraved channel filled with dark enamel.
  const bx = w * 0.56, bw = w * 0.4, by = h * 0.26, bh = h * 0.13;
  ctx.fillStyle = "rgba(60,40,8,0.35)";
  ctx.fillRect(bx, by, bw, bh);
  ctx.fillStyle = "#1a2240";
  ctx.fillRect(bx + 3, by + 3, Math.max(0, (bw - 6) * fraction), bh - 6);
  ctx.textAlign = "right";
  ctx.font = `600 ${Math.round(h * 0.12)}px ${FONT}`;
  engrave(ctx, fitText(ctx, lines.xp, bw), bx + bw, h * 0.56);
  engrave(ctx, fitText(ctx, lines.next, bw), bx + bw, h * 0.78);
}

function RankWall() {
  const t = useT();
  const person = useProgression((s) => s.subjects[PERSON_SUBJECT]);
  const bands = useProgression((s) => s.snapshot?.titles.person);
  const level = person?.level ?? 1;
  const held = rankAt(bands, level);
  const heldIndex = TITLE_IDS.indexOf(held);
  const startOf = (rank: RankId) => bands?.find((b) => b.title === rank)?.level ?? 1;
  const next = bands?.find((band) => band.level > level);
  const fraction = levelFraction(person);
  const lines: StatusLines = {
    hall: t("society.office.room_levels"),
    rank: t(`society.level.title.${held}`),
    grade: RANK_INFO[held].grade,
    level: t("society.level.level_n").replace("{0}", String(level)),
    xp: person && person.xpForNext > 0
      ? t("society.level.xp_of").replace("{0}", String(person.xpIntoLevel)).replace("{1}", String(person.xpForNext)) : t("society.level.max"),
    next: next ? t("society.level.next_promotion").replace("{0}", t(`society.level.title.${next.title}`)).replace("{1}", String(next.level))
      : t("society.hall.top_title"),
  };
  const status = useLiveTexture(1024, 220, JSON.stringify([lines, Math.round(fraction * 200)]), (ctx, w, h) => drawStatus(ctx, w, h, lines, fraction));
  const header = plaqueMaterial([t("society.office.room_levels").toUpperCase()], 768, 112);
  return (
    <group>
      {/* Walnut panelling, then the brass-framed velvet field. */}
      <Box size={[WALL.w, WALL.h, 0.18]} position={[0, WALL.h / 2, -WALL.d / 2 + 0.09]} material={HM.walnut} />
      <Box size={[WALL.w, 0.12, 0.24]} position={[0, WALL.h - 0.06, -WALL.d / 2 + 0.12]} material={HM.walnutDark} />
      <Box size={[WALL.w, 0.16, 0.22]} position={[0, 0.08, -WALL.d / 2 + 0.11]} material={HM.walnutDark} />
      <Box size={[FIELD.w + 0.16, FIELD.h + 0.16, 0.08]} position={[0, FIELD.y, 0.0]} material={HM.walnutDark} />
      <Box size={[FIELD.w + 0.06, FIELD.h + 0.06, 0.084]} position={[0, FIELD.y, 0.002]} material={HM.brass} />
      <Box size={[FIELD.w, FIELD.h, 0.088]} position={[0, FIELD.y, 0.004]} material={HM.velvet} />
      <group position={[0, 0, 0.05]}>
        {ROWS.map((row, r) => row.map((rank, i) => {
          const index = TITLE_IDS.indexOf(rank);
          const x = (i - (row.length - 1) / 2) * CELL;
          const y = FIELD.y + (r === 0 ? 0.43 : -0.43);
          return <RankCell key={rank} rank={rank} reached={index <= heldIndex} current={index === heldIndex} x={x} y={y} levelAt={startOf(rank)} />;
        }))}
      </group>
      {/* The hall's name above, the person's record below. */}
      <mesh material={header} position={[0, 2.88, 0.05]}>
        <planeGeometry args={[2.2, 2.2 * (112 / 768)]} />
      </mesh>
      <Box size={[3.0, 0.62, 0.05]} position={[0, 0.56, -0.02]} material={HM.walnutDark} />
      <mesh position={[0, 0.56, 0.008]}>
        <planeGeometry args={[2.84, 2.84 * (220 / 1024)]} />
        {status ? <meshStandardMaterial map={status} roughness={0.35} metalness={0.4} /> : <meshStandardMaterial color={C.brass} roughness={0.35} metalness={0.5} />}
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------------------
// The studio dais
// ---------------------------------------------------------------------------

const STAGE_R = FURNITURE_SIZE.studioStage.w / 2 - 0.04;
const GEO = { disc: new CylinderGeometry(1, 1, 1, 64) };

/** The dais's inlaid star, its points `r` from the centre. */
function starShape(r: number): Shape {
  return new Shape(Array.from({ length: 10 }, (_, i) => {
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const rr = i % 2 === 0 ? r : r * 0.4;
    return new Vector2(Math.cos(a) * rr, Math.sin(a) * rr);
  }));
}

const STAR = new ShapeGeometry(starShape(STAGE_R * 0.32));

/** The studio dais; the studio's preview stands its figure on the same one. */
export function StudioStage() {
  return (
    <group>
      {/* Flat on purpose: figures stand at y 0, so nothing here rises above ~0.016 m. */}
      <mesh geometry={GEO.disc} material={HM.walnutDark} position={[0, 0.005, 0]} scale={[STAGE_R, 0.01, STAGE_R]} receiveShadow />
      <mesh geometry={GEO.disc} material={HM.brass} position={[0, 0.0105, 0]} scale={[STAGE_R * 0.97, 0.001, STAGE_R * 0.97]} />
      <mesh geometry={GEO.disc} material={HM.walnut} position={[0, 0.0115, 0]} scale={[STAGE_R * 0.94, 0.002, STAGE_R * 0.94]} receiveShadow />
      <mesh geometry={GEO.disc} material={HM.brass} position={[0, 0.0128, 0]} scale={[STAGE_R * 0.56, 0.001, STAGE_R * 0.56]} />
      <mesh geometry={GEO.disc} material={HM.walnut} position={[0, 0.0136, 0]} scale={[STAGE_R * 0.54, 0.0012, STAGE_R * 0.54]} receiveShadow />
      <mesh geometry={STAR} material={HM.brass} position={[0, 0.0146, 0]} rotation={[-Math.PI / 2, 0, 0]} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Display cases
// ---------------------------------------------------------------------------

const CASE = { w: 0.7, d: 0.7 };

/** A neutral display mannequin: stone-grey, no hair, no face. */
const MANNEQUIN: ToyLook = { ...toyLookFor(null, "hall-mannequin"), skin: "#cfc9bd", hairStyle: "bald", blush: false, eyewear: "none" };
const STILL = { current: { mode: "idle" as const, speed: 0 } };

/** What a case shows: the piece as the person would wear it at their own rank. */
function CaseContent({ reward, rank, open }: { reward: RewardId; rank: RankId; open: boolean }) {
  const slot = slotOf(reward);
  if (slot === "uniform") {
    const regalia = regaliaFor(rank, {});
    return (
      <group position={[0, 0.3, 0]}>
        <ToyFigure look={dressedLook(MANNEQUIN, reward as UniformId, rank)} drive={STILL} paused heightM={1.2} regalia={regalia} mannequin />
        {/* The mannequin's stand under its feet. */}
        <mesh geometry={GEO.disc} material={HM.walnutDark} position={[0, -0.005, 0]} scale={[0.2, 0.01, 0.2]} />
      </group>
    );
  }
  if (slot === "headwear") {
    const k = 0.55;
    return (
      <group position={[0, 0.98, 0]}>
        {/* A velvet head form on a turned walnut neck. */}
        <mesh material={HM.walnut} position={[0, 0.06, 0]}>
          <cylinderGeometry args={[0.035, 0.06, 0.12, 20]} />
        </mesh>
        <group position={[0, 0.12 - (TOY.head.y - TOY.head.ry) * k, 0]} scale={k}>
          <mesh material={HM.velvet} position={[0, TOY.head.y, 0]} scale={[TOY.head.rx, TOY.head.ry, TOY.head.rz]}>
            <sphereGeometry args={[1, 32, 20]} />
          </mesh>
          <Headwear kind={reward as HeadwearId} rank={rank} />
        </group>
      </group>
    );
  }
  // Decorations lie on a slanted velvet board, three times life size so they read through the glass.
  const corded = reward === "decoration_aiguillette" || reward === "decoration_medals";
  return (
    <group position={[0, 1.2, -0.04]} rotation={[-0.38, 0, 0]}>
      <Box size={[0.56, 0.42, 0.03]} position={[0, 0, -0.015]} material={HM.velvet} cast={false} />
      <group scale={3} position={[0.02, 0.12, 0.002]}>
        <group position={[0.02, 0, 0]}><BreastDecoration decoration={reward} /></group>
        {corded && <group position={[-0.085, 0.02, 0]}><Aiguillette span={0.08} /></group>}
      </group>
      {!open && <Box size={[0.56, 0.42, 0.002]} position={[0, 0, 0.02]} material={HM.smoked} cast={false} />}
    </group>
  );
}

/** Heights of each case kind: the plinth's top and the glass above it. */
function caseShape(reward: RewardId): { plinth: number; glass: number } {
  const slot = slotOf(reward);
  if (slot === "uniform") return { plinth: 0.28, glass: 1.42 };
  return { plinth: 0.92, glass: 0.72 };
}

function DisplayCase({ item }: { item: Furniture }) {
  const t = useT();
  const index = Number(item.id.slice("level-pedestal-".length));
  const rewards = useProgression((s) => s.snapshot?.rewards);
  const level = useProgression((s) => s.subjects[PERSON_SUBJECT]?.level ?? 1);
  const bands = useProgression((s) => s.snapshot?.titles.person);
  const choices = useProgression((s) => s.choices.person);
  const road = useMemo(() => (rewards ? rewardRoad(rewards, "person") : []), [rewards]);
  const row = road[index];
  const reward = row?.rewardId;
  const at = row?.levels.person ?? 0;
  const open = !!row && level >= at;
  const worn = !!row && !!rewards && equippedFor(rewards, "person", level, choices)[row.slot] === reward;
  const rank = rankAt(bands, level);
  const onClick = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 6 || !reward) return;
    event.stopPropagation();
    useProgression.getState().openPanel("rewards", { subject: "person", reward });
  };
  const hover = (on: boolean) => (event: ThreeEvent<PointerEvent>) => {
    if (!reward) return;
    event.stopPropagation();
    document.body.style.cursor = on ? "pointer" : "";
  };
  useEffect(() => () => { document.body.style.cursor = ""; }, []);
  if (!reward) return null;
  const { plinth, glass } = caseShape(reward);
  const top = plinth + glass;
  const state = worn ? t("society.hall.equipped") : open ? t("society.hall.unlocked")
    : `${t(`society.level.title.${rankAt(bands, at)}`)} · ${t("society.level.lv").replace("{0}", String(at))}`;
  const plaque = plaqueMaterial([t(`society.level.reward.${reward}`), state], 448, 128);
  return (
    <group onClick={onClick} onPointerOver={hover(true)} onPointerOut={hover(false)}>
      {/* The walnut plinth with a brass band, a black base and a moulded top. */}
      <Rounded size={[CASE.w + 0.06, 0.08, CASE.d + 0.06]} radius={0.02} position={[0, 0.04, 0]} material={HM.walnutDark} />
      <Box size={[CASE.w, plinth - 0.1, CASE.d]} position={[0, 0.08 + (plinth - 0.1) / 2, 0]} material={HM.walnut} />
      <Box size={[CASE.w + 0.02, 0.025, CASE.d + 0.02]} position={[0, plinth - 0.03, 0]} material={HM.brass} />
      <Box size={[CASE.w + 0.04, 0.03, CASE.d + 0.04]} position={[0, plinth - 0.005, 0]} material={HM.walnutDark} />
      <mesh material={plaque} position={[0, plinth * 0.55, CASE.d / 2 + 0.002]}>
        <planeGeometry args={[0.5, 0.5 * (128 / 448)]} />
      </mesh>
      {/* The glass, its four brass posts and the lid with its light. */}
      <mesh position={[0, plinth + glass / 2, 0]} material={open ? HM.glass : HM.smoked} renderOrder={2}>
        <boxGeometry args={[CASE.w - 0.02, glass, CASE.d - 0.02]} />
      </mesh>
      {[[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([sx, sz]) => (
        <Box key={`${sx}${sz}`} size={[0.018, glass, 0.018]} position={[sx * (CASE.w / 2 - 0.01), plinth + glass / 2, sz * (CASE.d / 2 - 0.01)]} material={HM.brass} cast={false} />
      ))}
      <Box size={[CASE.w + 0.02, 0.05, CASE.d + 0.02]} position={[0, top + 0.025, 0]} material={HM.walnutDark} />
      <mesh material={open ? HM.caseLight : HM.caseDark} position={[0, top - 0.002, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <planeGeometry args={[CASE.w * 0.6, CASE.d * 0.6]} />
      </mesh>
      <CaseContent reward={reward} rank={rank} open={open} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// The runner and the service guide
// ---------------------------------------------------------------------------

function runnerTexture(): Texture | null {
  return cachedCanvasTexture("hall-runner", 256, 256, (ctx, w, h) => {
    ctx.fillStyle = "#7a1d25";
    ctx.fillRect(0, 0, w, h);
    // A fine woven grain.
    ctx.fillStyle = "rgba(0,0,0,0.08)";
    for (let y = 0; y < h; y += 4) ctx.fillRect(0, y, w, 1);
    ctx.fillStyle = "rgba(255,255,255,0.03)";
    for (let x = 0; x < w; x += 6) ctx.fillRect(x, 0, 1, h);
    // Gold borders with a thin inner line.
    ctx.fillStyle = "#c9a24a";
    ctx.fillRect(0, 0, 14, h);
    ctx.fillRect(w - 14, 0, 14, h);
    ctx.fillRect(22, 0, 3, h);
    ctx.fillRect(w - 25, 0, 3, h);
  });
}

function Runner({ w, d }: { w: number; d: number }) {
  const material = useMemo(() => {
    const base = runnerTexture();
    const map = base ? base.clone() : null;
    if (map) {
      map.wrapS = RepeatWrapping;
      map.wrapT = RepeatWrapping;
      map.repeat.set(1, Math.max(1, Math.round(d / w)));
      map.needsUpdate = true;
    }
    return new MeshStandardMaterial({ color: map ? "#ffffff" : "#7a1d25", map, roughness: 0.95 });
  }, [d, w]);
  useEffect(() => () => { material.map?.dispose(); material.dispose(); }, [material]);
  return (
    <mesh material={material} position={[0, 0.008, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
      <planeGeometry args={[w * 0.8, d]} />
    </mesh>
  );
}

interface GuideLines { title: string; subtitle: string; rows: { xp: string; text: string }[]; footer: string }

/** The guide's face: cream card stock, a navy rule, rows set like a printed order of service. */
function drawGuide(ctx: Ctx, w: number, h: number, lines: GuideLines): void {
  ctx.fillStyle = "#f2ecdd";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "#1a2240";
  ctx.lineWidth = 6;
  ctx.strokeRect(24, 24, w - 48, h - 48);
  ctx.lineWidth = 1.5;
  ctx.strokeRect(36, 36, w - 72, h - 72);
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillStyle = "#1a2240";
  ctx.font = `700 58px ${SERIF}`;
  ctx.fillText(fitText(ctx, lines.title, w - 140), w / 2, 100);
  ctx.fillStyle = "#5b5345";
  ctx.font = `500 28px ${FONT}`;
  ctx.fillText(fitText(ctx, lines.subtitle, w - 140), w / 2, 150);
  ctx.fillStyle = "#b8964f";
  ctx.fillRect(w / 2 - 90, 180, 180, 3);
  const rowH = 62;
  ctx.textAlign = "left";
  lines.rows.forEach((row, i) => {
    const y = 236 + i * rowH;
    ctx.fillStyle = "#1a2240";
    ctx.font = `800 32px ${FONT}`;
    ctx.fillText(row.xp, 90, y);
    ctx.fillStyle = "#2b2a26";
    ctx.font = `500 31px ${FONT}`;
    ctx.fillText(fitText(ctx, row.text, w - 380), 260, y);
    ctx.fillStyle = "rgba(26,34,64,0.12)";
    ctx.fillRect(90, y + rowH / 2 - 2, w - 180, 1.5);
  });
  ctx.textAlign = "center";
  ctx.fillStyle = "#5b5345";
  ctx.font = `600 26px ${FONT}`;
  ctx.fillText(fitText(ctx, lines.footer, w - 140), w / 2, h - 72);
}

const GUIDE = { w: FURNITURE_SIZE.levelGuide.w, h: FURNITURE_SIZE.levelGuide.h, screenW: 3.0, screenH: 1.66, screenY: 1.5 };
/** The guide lists the person's best-paying rules, at most this many. */
const GUIDE_ROWS = 6;
const NO_RULES: XpRuleRow[] = [];

function ServiceGuide() {
  const t = useT();
  const rules = useProgression((s) => s.snapshot?.rules ?? NO_RULES);
  const rows = useMemo(() => rules.filter((r) => r.kind === "person").sort((a, b) => b.xp - a.xp).slice(0, GUIDE_ROWS)
    .map((r) => ({ xp: `+${r.xp} XP`, text: t(`society.level.source.${r.source}`) })), [rules, t]);
  const lines: GuideLines = {
    title: t("society.hall.guide_title"), subtitle: t("society.hall.guide_subtitle"), rows, footer: t("society.hall.guide_footer"),
  };
  const face = useLiveTexture(1280, 704, JSON.stringify(lines), (ctx, w, h) => drawGuide(ctx, w, h, lines));
  const card = face
    ? <meshStandardMaterial map={face} roughness={0.8} />
    : <meshStandardMaterial color={C.cream} roughness={0.8} />;
  return (
    <group>
      {[-1, 1].map((side) => (
        <group key={side} position={[side * (GUIDE.w / 2 - 0.12), 0, 0]}>
          <Box size={[0.09, GUIDE.h - 0.05, 0.09]} position={[0, (GUIDE.h - 0.05) / 2, 0]} material={HM.walnutDark} />
          <Box size={[0.13, 0.03, 0.13]} position={[0, GUIDE.h - 0.03, 0]} material={HM.brass} />
          <Rounded size={[0.24, 0.06, 0.34]} radius={0.02} position={[0, 0.03, 0]} material={HM.walnutDark} />
        </group>
      ))}
      <Box size={[GUIDE.screenW + 0.16, GUIDE.screenH + 0.16, 0.1]} position={[0, GUIDE.screenY, 0]} material={HM.walnut} />
      <Box size={[GUIDE.screenW + 0.06, GUIDE.screenH + 0.06, 0.104]} position={[0, GUIDE.screenY, 0]} material={HM.brass} />
      {/* The same card on both faces. */}
      <mesh position={[0, GUIDE.screenY, 0.054]}><planeGeometry args={[GUIDE.screenW, GUIDE.screenH]} />{card}</mesh>
      <mesh position={[0, GUIDE.screenY, -0.054]} rotation={[0, Math.PI, 0]}><planeGeometry args={[GUIDE.screenW, GUIDE.screenH]} />{card}</mesh>
    </group>
  );
}

/** Renderers of the Level Hall's furniture kinds; merged into OfficeProps' exhaustive table. */
export const LEVEL_HALL_RENDERERS = {
  levelWall: () => <RankWall />,
  studioStage: () => <StudioStage />,
  rewardPedestal: ({ item }: { item: Furniture }) => <DisplayCase item={item} />,
  levelRoad: ({ item }: { item: Furniture }) => {
    const size = item.size ?? FURNITURE_SIZE.levelRoad;
    return <Runner w={size.w} d={size.d} />;
  },
  levelGuide: () => <ServiceGuide />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;

/**
 * What hangs over the hall rather than stands in it: a brass canopy over the
 * dais with four spotlights aimed down at it, and warm light on the rank wall
 * and the guide.
 */
export const LevelHallFittings = memo(function LevelHallFittings({ stage, guide }: { stage: Pick<Furniture, "x" | "z">; guide: Pick<Furniture, "x" | "z"> | null }) {
  return (
    <>
      <group position={[stage.x, 0, stage.z]}>
        <mesh material={HM.brass} position={[0, 3.12, 0]}>
          <cylinderGeometry args={[0.5, 0.5, 0.04, 40]} />
        </mesh>
        {[0, 1, 2, 3].map((i) => {
          const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
          return (
            <group key={i} position={[Math.cos(a) * 0.4, 3.0, Math.sin(a) * 0.4]} rotation={[Math.sin(a) * -0.35, 0, Math.cos(a) * 0.35]}>
              <mesh material={HM.walnutDark}><cylinderGeometry args={[0.05, 0.065, 0.16, 16]} /></mesh>
              <mesh material={HM.caseLight} position={[0, -0.081, 0]} rotation={[Math.PI / 2, 0, 0]}><circleGeometry args={[0.05, 16]} /></mesh>
            </group>
          );
        })}
        <pointLight position={[0, 2.6, 0.4]} color="#ffe6bf" intensity={6} distance={7} decay={2} />
      </group>
      <pointLight position={[stage.x, 2.9, stage.z - 2.6]} color="#ffe2b0" intensity={4} distance={6} decay={2} />
      {guide && <pointLight position={[guide.x, 2.8, guide.z - 1.4]} color="#fff1da" intensity={3} distance={6} decay={2} />}
    </>
  );
});
