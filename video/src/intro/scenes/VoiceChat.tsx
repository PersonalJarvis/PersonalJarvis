import { Sequence } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Kicker, Title } from "../components/Text";
import { SpokenCommand } from "../components/SpokenCommand";

export const VoiceChat: React.FC = () => {
  return (
    <SceneWrap>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 18,
          width: "100%",
        }}
      >
        <Kicker>Just talk</Kicker>
        <Title delay={8} size={60}>
          Speak naturally — out loud
        </Title>

        <div
          style={{
            marginTop: 34,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 38,
            width: "100%",
          }}
        >
          <Sequence from={36} layout="none">
            <SpokenCommand text="Hey Alex, what’s on my plate today?" speaker="user" size={40} />
          </Sequence>
          <Sequence from={104} layout="none">
            <SpokenCommand
              text="Three meetings, and your report’s due at five."
              speaker="jarvis"
              size={36}
            />
          </Sequence>
        </div>
      </div>
    </SceneWrap>
  );
};
