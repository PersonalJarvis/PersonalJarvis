import { Sequence } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Kicker, Subtitle, Title } from "../components/Text";
import { SpokenCommand } from "../components/SpokenCommand";
import { COLORS, FONT } from "../theme";

export const WakeWord: React.FC = () => {
  return (
    <SceneWrap>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 20,
          width: "100%",
        }}
      >
        <Kicker>Step 1 · Wake word</Kicker>
        <Title delay={8} size={62}>
          Choose your wake word
        </Title>
        <Subtitle delay={20}>Just say it out loud — “Hey”, then your word. No typing.</Subtitle>

        {/* The personalised example — deliberately NOT "Hey Jarvis". */}
        <Sequence from={64} layout="none">
          <WakeDemo />
        </Sequence>
      </div>
    </SceneWrap>
  );
};

const WakeDemo: React.FC = () => {
  return (
    <div
      style={{
        marginTop: 30,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 30,
        width: 1000,
      }}
    >
      <div
        style={{
          fontFamily: FONT,
          fontSize: 27,
          fontWeight: 600,
          color: COLORS.textMuted,
          textAlign: "center",
        }}
      >
        I picked <span style={{ color: COLORS.primary, fontWeight: 800 }}>“Ruben”</span> — so I just
        say:
      </div>
      <SpokenCommand text="Hey Ruben…" speaker="user" size={46} />
      <Sequence from={54} layout="none">
        <SpokenCommand text="Yes? I’m listening." speaker="jarvis" size={32} />
      </Sequence>
    </div>
  );
};
