import { AbsoluteFill, Sequence } from "remotion";
import { Background } from "./components/Background";
import { PrototypeBadge } from "./components/PrototypeBadge";
import { COLORS, OVERLAP, SCENES } from "./theme";
import { BrandIntro } from "./scenes/BrandIntro";
import { WakeWord } from "./scenes/WakeWord";
import { VoiceChat } from "./scenes/VoiceChat";
import { ComputerUse } from "./scenes/ComputerUse";
import { SubAgents } from "./scenes/SubAgents";
import { Integrations } from "./scenes/Integrations";
import { Outro } from "./scenes/Outro";

const ORDER: { Comp: React.FC; dur: number; name: string }[] = [
  { Comp: BrandIntro, dur: SCENES.brand, name: "brand" },
  { Comp: WakeWord, dur: SCENES.wakeWord, name: "wakeWord" },
  { Comp: VoiceChat, dur: SCENES.voiceChat, name: "voiceChat" },
  { Comp: ComputerUse, dur: SCENES.computerUse, name: "computerUse" },
  { Comp: SubAgents, dur: SCENES.subAgents, name: "subAgents" },
  { Comp: Integrations, dur: SCENES.moreFeatures, name: "integrations" },
  { Comp: Outro, dur: SCENES.outro, name: "outro" },
];

export const IntroVideo: React.FC = () => {
  let from = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Background />
      {ORDER.map(({ Comp, dur, name }, i) => {
        const seq = (
          <Sequence key={name} from={from} durationInFrames={dur} name={name}>
            <Comp />
          </Sequence>
        );
        from += dur - (i < ORDER.length - 1 ? OVERLAP : 0);
        return seq;
      })}
      <PrototypeBadge />
    </AbsoluteFill>
  );
};
