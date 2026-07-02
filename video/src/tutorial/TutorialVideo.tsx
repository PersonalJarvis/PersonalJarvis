import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { Background } from "../intro/components/Background";
import { COLORS } from "../intro/theme";
import { TL_TUT, TimelineScene } from "./timeline";
import { Hook } from "./scenes/Hook";
import { Install } from "./scenes/Install";
import { Keys } from "./scenes/Keys";
import { Wake } from "./scenes/Wake";
import { Talk } from "./scenes/Talk";
import { Act } from "./scenes/Act";
import { Delegate } from "./scenes/Delegate";
import { Outro } from "./scenes/Outro";

const REGISTRY: Record<string, React.FC<{ scene: TimelineScene }>> = {
  hook: Hook,
  install: Install,
  keys: Keys,
  wake: Wake,
  talk: Talk,
  act: Act,
  delegate: Delegate,
  outro: Outro,
};

/**
 * "Say it. It happens." — the setup tutorial, narrated by Jarvis itself.
 * Cold open, six numbered steps (install → key → wake → talk → act →
 * delegate), close. Scene lengths and every voiceover placement come from
 * generated/timeline.json (scripts/gen_vo_tutorial.py), so picture and
 * narration stay frame-locked; consecutive scenes crossfade over the
 * continuous Background.
 */
export const TutorialVideo: React.FC = () => {
  let from = 0;
  const sceneSeqs: React.ReactNode[] = [];

  TL_TUT.scenes.forEach((sc, i) => {
    const Comp = REGISTRY[sc.id];
    sceneSeqs.push(
      <Sequence key={sc.id} from={from} durationInFrames={sc.dur} name={sc.id}>
        {Comp ? <Comp scene={sc} /> : null}
      </Sequence>,
    );
    from += sc.dur - (i < TL_TUT.scenes.length - 1 ? TL_TUT.overlap : 0);
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Background />
      {sceneSeqs}
      {TL_TUT.audio.map((a) => (
        <Sequence key={a.file} from={a.from} durationInFrames={a.dur} name={`vo:${a.file}`}>
          <Audio src={staticFile(a.file)} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
