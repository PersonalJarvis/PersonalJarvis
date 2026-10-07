/**
 * How a figure is dressed for its rank and loadout: the uniform it wears over
 * its own look, the insignia on its sleeves or shoulders, the decoration on
 * its chest and the hat on its head. The figure in the office, the studio
 * preview and the mannequins in the hall's display cases all dress through
 * here, so every surface shows the same pieces.
 */
import { useMemo } from "react";
import type { FigureRegalia } from "../../office/ToyFigure";
import { TOY, type ToyLook, type UniformStyle } from "../../office/toyFigureModel";
import { equippedFor, type Loadout } from "../cosmetics";
import { RANK_INFO, rankAt, type RankId, type RewardId } from "../levelCatalog";
import { rankArt } from "../insignia/rankArt";
import { useProgression } from "../progressionStore";
import { Aiguillette, BreastDecoration } from "./decorations3d";
import { Headwear, type HeadwearId } from "./headwear3d";
import { ShoulderBoard, SleevePatch } from "./insignia3d";

export type UniformId = Extract<RewardId, `uniform_${string}`>;

const GOLD = "#d2a33f";

/** Each uniform as the figure draws it; `officer` and `nco` add the trims those grades wear. */
export function uniformStyle(id: UniformId, rank: RankId): UniformStyle {
  const tier = RANK_INFO[rank].tier;
  const officer = tier === "officer" || tier === "general";
  const nco = tier !== "enlisted";
  switch (id) {
    case "uniform_service_shirt":
      return { cut: "shirt", coat: "#c8b38c", shirt: "#c8b38c", tie: "#433a28", tieKind: "long", trousers: "#8d7a6b", shoes: "#4b311d",
        buttons: "#7a6a4a", pockets: 2, belt: "#2f2418" };
    case "uniform_field_jacket":
      return { cut: "field", coat: "#5a613f", shirt: "#5a613f", tie: "#5a613f", tieKind: "long", trousers: "#5d6248", shoes: "#3b2f24",
        buttons: "#2b2b25", pockets: 4, tapes: "#2c3023" };
    case "uniform_service_greens":
      return { cut: "service", coat: "#3d4632", shirt: "#cbb892", tie: "#39331f", tieKind: "long", trousers: "#a08a7b", shoes: "#4a2e1a",
        buttons: "#c9a24a", pockets: 4, belt: "#3d4632" };
    case "uniform_dress_blues":
      return { cut: "service", coat: "#1c2541", shirt: "#f3f3ef", tie: "#121317", tieKind: "long", trousers: "#4a6a9e", shoes: "#0e0e10",
        buttons: "#d4a845", pockets: 4, stripe: nco ? GOLD : undefined, cuffBraid: officer ? GOLD : undefined };
    case "uniform_mess_dress":
    default:
      return { cut: "mess", coat: "#151e37", shirt: "#f7f7f3", tie: "#0f1013", tieKind: "bow", trousers: "#151e37", shoes: "#0e0e10",
        buttons: "#d4a845", pockets: 0, stripe: GOLD, cuffBraid: GOLD, lapels: "#22305a" };
  }
}

/** The look a figure shows with a uniform on: the uniform's colours over its own skin, hair and face. */
export function dressedLook(look: ToyLook, uniform: UniformId | undefined, rank: RankId): ToyLook {
  if (!uniform) return look;
  const u = uniformStyle(uniform, rank);
  return { ...look, outfit: "suit", shirt: u.coat, inner: u.shirt, shirtAccent: u.tie, pants: u.trousers, shoes: u.shoes, uniform: u };
}

/** Where the breast decoration hangs: over the left pocket, its top just under the shoulder line. */
const BREAST = { x: 0.075, y: 0.207, z: TOY.torso.d / 2 + 0.002 };
/** Where the aiguillette hangs from: the front of the right shoulder. */
const CORD = { x: -0.168, y: 0.252, z: TOY.torso.d / 2 - 0.012 };

/** The pieces a figure of `rank` wears with `loadout`; empty slots stay undefined. */
export function regaliaFor(rank: RankId, loadout: Loadout): FigureRegalia {
  const mount = rankArt(rank).mount;
  const decoration = loadout.decoration;
  const corded = decoration === "decoration_aiguillette" || decoration === "decoration_medals";
  return {
    sleeve: mount === "sleeve" ? <SleevePatch rank={rank} armRadius={TOY.armRadius} /> : undefined,
    shoulder: mount === "shoulder" ? <ShoulderBoard rank={rank} /> : undefined,
    chest: decoration ? (
      <>
        <group position={[BREAST.x, BREAST.y, BREAST.z]}><BreastDecoration decoration={decoration} /></group>
        {corded && <group position={[CORD.x, CORD.y, CORD.z]}><Aiguillette /></group>}
      </>
    ) : undefined,
    hat: loadout.headwear ? <Headwear kind={loadout.headwear as HeadwearId} rank={rank} /> : undefined,
  };
}

/** A figure's rank and loadout from the store: the person's own choices, an agent's best unlocks. */
export function useDress(kind: "person" | "agent", subjectId: string): { rank: RankId; loadout: Loadout } {
  const level = useProgression((s) => s.subjects[subjectId]?.level ?? 1);
  const rewards = useProgression((s) => s.snapshot?.rewards);
  const bands = useProgression((s) => s.snapshot?.titles[kind]);
  const choices = useProgression((s) => (kind === "person" ? s.choices.person : undefined));
  return useMemo(() => ({
    rank: rankAt(bands, level),
    loadout: rewards ? equippedFor(rewards, kind, level, choices) : {},
  }), [bands, level, rewards, kind, choices]);
}

/** The look and regalia a figure renders with, from its rank and loadout. */
export function useDressedFigure(kind: "person" | "agent", subjectId: string, look: ToyLook): { look: ToyLook; regalia: FigureRegalia } {
  const { rank, loadout } = useDress(kind, subjectId);
  return useMemo(() => ({
    look: dressedLook(look, loadout.uniform as UniformId | undefined, rank),
    regalia: regaliaFor(rank, loadout),
  }), [look, loadout, rank]);
}
