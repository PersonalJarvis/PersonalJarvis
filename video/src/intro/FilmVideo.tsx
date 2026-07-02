import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { Background } from "./components/Background";
import { COLORS } from "./theme";
import { TL_FILM, TimelineScene } from "./film/timeline";
import { ProgressHairline, Scanlines } from "./film/fx";
import {
  AckScene,
  ActionScene,
  CommandScene,
  CriticScene,
  InstallScene,
  OpenScene,
  OutroScene,
  ProofScene,
  ResultScene,
} from "./film/scenes";

const REGISTRY: Record<string, React.FC<{ scene: TimelineScene }>> = {
  open: OpenScene,
  command: CommandScene,
  ack: AckScene,
  action: ActionScene,
  critic: CriticScene,
  result: ResultScene,
  proof: ProofScene,
  install: InstallScene,
  outro: OutroScene,
};

/**
 * The README film — the landing page brought to life. Same voiceover-locked
 * grammar as the other cuts (scene order, lengths and audio placement all come
 * from film/generated/timeline.json via scripts/gen_vo_film.py), re-skinned to
 * the website's black + signal-yellow look with the effects kit in film/fx.tsx.
 */
export const FilmVideo: React.FC = () => {
  let from = 0;
  const sceneSeqs: React.ReactNode[] = [];

  TL_FILM.scenes.forEach((sc, i) => {
    const Comp = REGISTRY[sc.id];
    sceneSeqs.push(
      <Sequence key={sc.id} from={from} durationInFrames={sc.dur} name={sc.id}>
        {Comp ? <Comp scene={sc} /> : null}
      </Sequence>,
    );
    from += sc.dur - (i < TL_FILM.scenes.length - 1 ? TL_FILM.overlap : 0);
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Background />
      {sceneSeqs}
      {TL_FILM.audio.map((a) => (
        <Sequence key={a.file} from={a.from} durationInFrames={a.dur} name={`vo:${a.file}`}>
          <Audio src={staticFile(a.file)} />
        </Sequence>
      ))}
      {/* global atmosphere, above scenes */}
      <Scanlines />
      <ProgressHairline />
    </AbsoluteFill>
  );
};
