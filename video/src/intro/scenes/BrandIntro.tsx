import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Orb } from "../components/Orb";
import { Kicker, Subtitle, Title } from "../components/Text";

export const BrandIntro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const markIn = spring({ frame, fps, config: { damping: 14, mass: 0.8, stiffness: 120 } });
  const rot = interpolate(markIn, [0, 1], [-140, 0]);
  const scale = interpolate(markIn, [0, 1], [0.2, 1]);

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
        <div style={{ transform: `rotate(${rot}deg) scale(${scale})`, opacity: markIn }}>
          <Orb size={200} />
        </div>
        <Kicker delay={10}>Meet your assistant</Kicker>
        <Title delay={18} size={92}>
          Personal Jarvis
        </Title>
        <Subtitle delay={30}>
          Just talk to your computer — it listens, and it acts.
        </Subtitle>
      </div>
    </SceneWrap>
  );
};
