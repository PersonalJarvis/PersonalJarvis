import raw from "../generated/timeline.json";

/** A single synthesized voiceover line, placed scene-locally. */
export interface VoLine {
  id: string;
  kind: "narration" | "command";
  text: string;
  file: string;
  /** Frame (scene-local) at which this line's audio starts. */
  localStart: number;
  /** Length of the audio clip in frames. */
  dur: number;
}

export interface TimelineScene {
  id: string;
  /** Total visual length of the scene in frames. */
  dur: number;
  lines: VoLine[];
}

export interface AudioClip {
  file: string;
  from: number;
  dur: number;
}

export interface Timeline {
  fps: number;
  overlap: number;
  totalFrames: number;
  scenes: TimelineScene[];
  audio: AudioClip[];
}

export const TL = raw as unknown as Timeline;

const EMPTY: VoLine = {
  id: "",
  kind: "narration",
  text: "",
  file: "",
  localStart: 0,
  dur: 30,
};

/** Look up a scene's line by id; returns a harmless default if missing so a
 *  script edit can never crash a scene mid-render. */
export function line(scene: TimelineScene, id: string): VoLine {
  return scene.lines.find((l) => l.id === id) ?? EMPTY;
}
