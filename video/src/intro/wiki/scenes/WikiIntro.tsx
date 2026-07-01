import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Subtitle, Title } from "../../components/Text";
import { Mascot } from "../../onboarding/Mascot";
import { line, TimelineScene } from "../timeline";

/** Opener: the ghost + the hook line, first-person. */
export const WikiIntro: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const sub = line(scene, "intro_2").localStart;
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 20 }}>
        <Mascot size={148} />
        <Kicker>Personal Jarvis · Memory</Kicker>
        <Title delay={6} size={72}>
          How do I remember you?
        </Title>
        <Subtitle delay={sub} size={30}>
          Not just for one chat. For good.
        </Subtitle>
      </div>
    </SceneWrap>
  );
};
