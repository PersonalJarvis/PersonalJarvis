import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Subtitle, Title } from "../../components/Text";
import { Mascot } from "../Mascot";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

/** Close — wordmark, open-source line, repo link. */
export const OutroYT: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const oss = line(scene, "outro_2").localStart;
  const link = line(scene, "outro_3").localStart;

  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 22 }}>
        <Mascot size={156} />
        <Title delay={6} size={72}>
          Personal Jarvis
        </Title>
        <Subtitle delay={14} size={28}>
          Your voice. Your computer. Your own AI agent.
        </Subtitle>

        <Chip delay={oss}>★ Free &amp; open source</Chip>

        <Link delay={link} />
      </div>
    </SceneWrap>
  );
};

const Chip: React.FC<{ delay: number; children: React.ReactNode }> = ({ delay, children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        opacity: s,
        padding: "9px 20px",
        borderRadius: 999,
        border: `1px solid rgba(255,214,10,0.35)`,
        backgroundColor: "rgba(255,214,10,0.10)",
        fontFamily: FONT,
        fontSize: 19,
        fontWeight: 700,
        color: COLORS.primary,
      }}
    >
      {children}
    </div>
  );
};

const Link: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        opacity: s,
        marginTop: 4,
        fontFamily: FONT,
        fontSize: 26,
        fontWeight: 700,
        color: COLORS.text,
        letterSpacing: 0.3,
      }}
    >
      github.com/<span style={{ color: COLORS.primary }}>PersonalJarvis</span>/PersonalJarvis
    </div>
  );
};
