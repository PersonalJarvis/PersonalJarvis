import { AbsoluteFill } from "remotion";
import { Mascot } from "../Mascot";
import { Kicker, Subtitle, Title } from "../../components/Text";
import { Phrase } from "../Phrase";
import { AppShot } from "../AppShot";
import { line, TimelineScene } from "../timeline";

/** Opening: who I am, then the real GitHub repo, "from Germany". */
export const CreatorIntro: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const handoff = line(scene, "intro_2").localStart;
  return (
    <AbsoluteFill>
      {/* Phase A — the creator */}
      <Phrase start={0} end={handoff + 6}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18 }}>
          <Mascot size={150} />
          <Subtitle delay={6} size={28}>
            Hey — I'm
          </Subtitle>
          <Title delay={10} size={62}>
            Ruben's AI assistant
          </Title>
          <Kicker delay={18}>Made in Germany</Kicker>
        </div>
      </Phrase>

      {/* Phase B — the real open-source repo (genuine screenshot) */}
      <Phrase start={handoff} end={scene.dur}>
        <AppShot src="shot-github.png" srcW={3840} width={1060} />
      </Phrase>
    </AbsoluteFill>
  );
};
