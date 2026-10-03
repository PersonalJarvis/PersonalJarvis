/**
 * Gadgets a figure (the person or an agent) wears on its own body, so they
 * ride every hop, bob and lean instead of floating at a fixed height: the
 * wings on its back, the halo and the crown on its head, the drone beside it
 * in the body's frame. The pet keeps its gadget in `ProgressionLayer`.
 */
import type { ReactNode } from "react";
import type { FigureMode } from "../figures/FigureRig";
import { equippedFor } from "./cosmetics";
import { useProgression } from "./progressionStore";
import { AngelWings, type WingMotion } from "./effects/AngelWings";
import { GadgetModel, type GadgetKind } from "./effects/CosmeticWear";

/** The gadget this figure wears right now, or null (unlocked, and not swapped out by the person). */
export function useWornGadget(kind: "person" | "agent", subjectId: string): GadgetKind | null {
  return useProgression((s) => {
    const rewards = s.snapshot?.rewards;
    const level = s.subjects[subjectId]?.level;
    if (!rewards || !level) return null;
    const gadget = equippedFor(rewards, kind, level, kind === "person" ? s.choices.person : undefined).gadget;
    return (gadget as GadgetKind | undefined) ?? null;
  });
}

export interface FigureGadgetSlots {
  /** For ToyFigure's `back` slot. */
  back?: ReactNode;
  /** For ToyFigure's `headwear` slot. */
  headwear?: ReactNode;
  /** Rendered beside the figure inside the group that carries its hop. */
  beside?: ReactNode;
}

const HEADWEAR_SCALE = 1.25;

/** Where a figure wears `gadget`; every slot is empty when it wears none. */
export function figureGadgetSlots(gadget: GadgetKind | null | undefined, opts: {
  drive: { current: { mode: FigureMode } };
  /** Height of the figure's head top above its feet (m); the drone hovers off it. */
  top: number;
  paused: boolean;
  reduced: boolean;
} & WingMotion): FigureGadgetSlots {
  const { drive, top, paused, reduced, airborne } = opts;
  if (gadget === "gadget_wings") return { back: <AngelWings drive={drive} airborne={airborne} paused={paused} reduced={reduced} /> };
  if (gadget === "gadget_halo" || gadget === "gadget_crown") {
    // A touch larger than the pet's, so it reads on a full-size head.
    return { headwear: <GadgetModel key={gadget} kind={gadget} scale={HEADWEAR_SCALE} paused={paused} reduced={reduced} /> };
  }
  if (gadget === "gadget_drone") {
    return {
      beside: (
        <group position={[0, top, 0]}>
          <GadgetModel kind={gadget} paused={paused} reduced={reduced} />
        </group>
      ),
    };
  }
  return {};
}
