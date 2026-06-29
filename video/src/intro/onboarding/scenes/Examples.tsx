import { Sequence } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Subtitle, Title } from "../../components/Text";
import { SpokenCommand } from "../../components/SpokenCommand";
import { line, TimelineScene } from "../timeline";

/** Bridge beat: setup is done — now you just talk. Three real commands. */
export const Examples: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const c1 = line(scene, "ex_3").localStart;
  const c2 = line(scene, "ex_4").localStart;
  const c3 = line(scene, "ex_5").localStart;

  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16, width: "100%" }}>
        <Kicker>Voice-first</Kicker>
        <Title delay={8} size={54}>
          Just talk — ask anything
        </Title>
        <Subtitle delay={18} size={26}>
          No menus, no typing. Say it out loud.
        </Subtitle>

        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16, marginTop: 18 }}>
          <Sequence from={c1} layout="none">
            <SpokenCommand text="What's on my calendar today?" speaker="user" size={28} compact wake="Hey Ruben" />
          </Sequence>
          <Sequence from={c2} layout="none">
            <SpokenCommand text="Switch the brain to Gemini." speaker="user" size={28} compact wake="Hey Ruben" />
          </Sequence>
          <Sequence from={c3} layout="none">
            <SpokenCommand text="Summarise this article for me." speaker="user" size={28} compact wake="Hey Ruben" />
          </Sequence>
        </div>
      </div>
    </SceneWrap>
  );
};
