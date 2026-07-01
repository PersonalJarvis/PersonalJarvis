import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Subtitle, Title } from "../../components/Text";
import { Mascot } from "../Mascot";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

const Tagline: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        opacity: s,
        marginTop: 6,
        padding: "9px 22px",
        borderRadius: 999,
        border: `1px solid rgba(255,214,10,0.35)`,
        backgroundColor: "rgba(255,214,10,0.10)",
        fontFamily: FONT,
        fontSize: 20,
        letterSpacing: 2,
        fontWeight: 700,
        color: COLORS.primary,
      }}
    >
      Local · Private · Portable
    </div>
  );
};

/** Close: the ghost, the wordmark, the tagline. */
export const WikiOutro: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const tag = line(scene, "outro_2").localStart;
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 20 }}>
        <Mascot size={150} />
        <Title delay={6} size={70}>
          That&apos;s how I remember.
        </Title>
        <Subtitle delay={14} size={28}>
          Personal Jarvis — your assistant&apos;s long-term memory.
        </Subtitle>
        <Tagline delay={tag} />
      </div>
    </SceneWrap>
  );
};
