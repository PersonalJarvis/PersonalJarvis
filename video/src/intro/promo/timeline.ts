import raw from "./generated/timeline.json";
import type { Timeline, TimelineScene, VoLine } from "../onboarding/timeline";

/** The README-promo cut's own generated timeline (scripts/gen_vo_promo.py). */
export const TL_PROMO = raw as unknown as Timeline;

const EMPTY: VoLine = {
  id: "",
  kind: "narration",
  text: "",
  file: "",
  localStart: 0,
  dur: 30,
};

export function line(scene: TimelineScene, id: string): VoLine {
  return scene.lines.find((l) => l.id === id) ?? EMPTY;
}

export type { TimelineScene };
