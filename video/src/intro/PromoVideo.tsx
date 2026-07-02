import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { Background } from "./components/Background";
import { COLORS } from "./theme";
import { TL_PROMO, TimelineScene } from "./promo/timeline";
import {
  AgentsPromo,
  HookPromo,
  MemoryPromo,
  OutroPromo,
  PrivatePromo,
  ProvidersPromo,
  RealAppPromo,
  VoicePromo,
} from "./promo/scenes";

const REGISTRY: Record<string, React.FC<{ scene: TimelineScene }>> = {
  hook: HookPromo,
  real_app: RealAppPromo,
  voice: VoicePromo,
  agents: AgentsPromo,
  memory: MemoryPromo,
  private: PrivatePromo,
  providers: ProvidersPromo,
  outro: OutroPromo,
};

/**
 * The ~87s README promo film — the onboarding example's narrated grammar
 * (voiceover-locked reveals, real screenshots, real logos, calm pace),
 * retold as a compact "what is Personal Jarvis" pitch. Scene order, lengths,
 * and audio placement all come from promo/generated/timeline.json
 * (scripts/gen_vo_promo.py).
 */
export const PromoVideo: React.FC = () => {
  let from = 0;
  const sceneSeqs: React.ReactNode[] = [];

  TL_PROMO.scenes.forEach((sc, i) => {
    const Comp = REGISTRY[sc.id];
    sceneSeqs.push(
      <Sequence key={sc.id} from={from} durationInFrames={sc.dur} name={sc.id}>
        {Comp ? <Comp scene={sc} /> : null}
      </Sequence>,
    );
    from += sc.dur - (i < TL_PROMO.scenes.length - 1 ? TL_PROMO.overlap : 0);
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Background />
      {sceneSeqs}
      {TL_PROMO.audio.map((a) => (
        <Sequence key={a.file} from={a.from} durationInFrames={a.dur} name={`vo:${a.file}`}>
          <Audio src={staticFile(a.file)} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
