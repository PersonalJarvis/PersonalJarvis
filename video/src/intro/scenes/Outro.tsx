import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Orb } from "../components/Orb";
import { Kicker, Subtitle, Title } from "../components/Text";
import { COLORS, FONT } from "../theme";

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const orbIn = spring({ frame, fps, config: { damping: 200, mass: 1.1 } });

  return (
    <SceneWrap>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 26,
        }}
      >
        <div style={{ transform: `scale(${0.7 + orbIn * 0.3})`, opacity: orbIn }}>
          <Orb size={160} />
        </div>
        <Kicker delay={16}>Ready when you are</Kicker>
        <Title delay={24} size={76}>
          Let’s get you set up
        </Title>
        <Subtitle delay={40}>
          It takes about a minute — and you can skip this intro anytime.
        </Subtitle>
        <div
          style={{
            marginTop: 16,
            fontFamily: FONT,
            fontSize: 24,
            fontWeight: 800,
            letterSpacing: 1,
            color: COLORS.primary,
            opacity: spring({ frame: frame - 56, fps, config: { damping: 200 } }),
          }}
        >
          Personal Jarvis
        </div>
      </div>
    </SceneWrap>
  );
};
