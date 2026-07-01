import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { Background } from "./components/Background";
import { COLORS } from "./theme";
import { TL_WIKI, TimelineScene } from "./wiki/timeline";
import { WikiIntro } from "./wiki/scenes/WikiIntro";
import { WikiIdea } from "./wiki/scenes/WikiIdea";
import { WikiMap } from "./wiki/scenes/WikiMap";
import { WikiPage } from "./wiki/scenes/WikiPage";
import { WikiForms } from "./wiki/scenes/WikiForms";
import { WikiRecall } from "./wiki/scenes/WikiRecall";
import { WikiOutro } from "./wiki/scenes/WikiOutro";

const REGISTRY: Record<string, React.FC<{ scene: TimelineScene }>> = {
  intro: WikiIntro,
  idea: WikiIdea,
  map: WikiMap,
  page: WikiPage,
  forms: WikiForms,
  recall: WikiRecall,
  outro: WikiOutro,
};

/**
 * The Wiki tutorial film. Scene order + lengths and every voiceover clip's
 * placement come from generated/timeline-wiki.json (built by
 * scripts/gen_vo_wiki.py), so picture and narration stay frame-locked.
 * Consecutive scenes overlap by TL_WIKI.overlap for a true crossfade over the
 * continuous Background. Authored in 1280×720; render at --scale=1.5 for 1080p.
 */
export const WikiTutorialVideo: React.FC = () => {
  let from = 0;
  const sceneSeqs: React.ReactNode[] = [];

  TL_WIKI.scenes.forEach((sc, i) => {
    const Comp = REGISTRY[sc.id];
    sceneSeqs.push(
      <Sequence key={sc.id} from={from} durationInFrames={sc.dur} name={sc.id}>
        {Comp ? <Comp scene={sc} /> : null}
      </Sequence>,
    );
    from += sc.dur - (i < TL_WIKI.scenes.length - 1 ? TL_WIKI.overlap : 0);
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Background />
      {sceneSeqs}
      {TL_WIKI.audio.map((a) => (
        <Sequence key={a.file} from={a.from} durationInFrames={a.dur} name={`vo:${a.file}`}>
          <Audio src={staticFile(a.file)} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
