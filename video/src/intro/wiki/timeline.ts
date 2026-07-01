import raw from "../generated/timeline-wiki.json";
import type { Timeline, TimelineScene, VoLine } from "../onboarding/timeline";

export type { Timeline, TimelineScene, VoLine };

export const TL_WIKI = raw as unknown as Timeline;

const EMPTY: VoLine = {
  id: "",
  kind: "narration",
  text: "",
  file: "",
  localStart: 0,
  dur: 30,
};

/** Scene-local line lookup; harmless default if a script edit removed it. */
export function line(scene: TimelineScene, id: string): VoLine {
  return scene.lines.find((l) => l.id === id) ?? EMPTY;
}
