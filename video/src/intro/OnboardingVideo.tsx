import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { Background } from "./components/Background";
import { COLORS } from "./theme";
import { TL, TimelineScene } from "./onboarding/timeline";
import { CreatorIntro } from "./onboarding/scenes/CreatorIntro";
import { Concept } from "./onboarding/scenes/Concept";
import { RealApp } from "./onboarding/scenes/RealApp";
import { SetupKeys } from "./onboarding/scenes/SetupKeys";
import { SetupWake } from "./onboarding/scenes/SetupWake";
import { Examples } from "./onboarding/scenes/Examples";
import { ComputerUseYT } from "./onboarding/scenes/ComputerUseYT";
import { SubAgentsYT } from "./onboarding/scenes/SubAgentsYT";
import { PluginsYT } from "./onboarding/scenes/PluginsYT";
import { OutroYT } from "./onboarding/scenes/OutroYT";

const REGISTRY: Record<string, React.FC<{ scene: TimelineScene }>> = {
  intro: CreatorIntro,
  concept: Concept,
  real_app: RealApp,
  setup_keys: SetupKeys,
  setup_wake: SetupWake,
  examples: Examples,
  computer_use: ComputerUseYT,
  sub_agents: SubAgentsYT,
  plugins: PluginsYT,
  outro: OutroYT,
};

/**
 * The ~3-minute YouTube onboarding film. Scene order + lengths and every
 * voiceover clip's placement come from generated/timeline.json (built by
 * scripts/gen_vo.py), so picture and narration stay frame-locked. Consecutive
 * scenes overlap by TL.overlap for a true crossfade over the continuous
 * Background.
 */
export const OnboardingVideo: React.FC = () => {
  let from = 0;
  const sceneSeqs: React.ReactNode[] = [];

  TL.scenes.forEach((sc, i) => {
    const Comp = REGISTRY[sc.id];
    sceneSeqs.push(
      <Sequence key={sc.id} from={from} durationInFrames={sc.dur} name={sc.id}>
        {Comp ? <Comp scene={sc} /> : null}
      </Sequence>,
    );
    from += sc.dur - (i < TL.scenes.length - 1 ? TL.overlap : 0);
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Background />
      {sceneSeqs}
      {TL.audio.map((a) => (
        <Sequence key={a.file} from={a.from} durationInFrames={a.dur} name={`vo:${a.file}`}>
          <Audio src={staticFile(a.file)} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
