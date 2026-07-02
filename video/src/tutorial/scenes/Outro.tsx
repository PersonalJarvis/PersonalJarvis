import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../intro/components/SceneWrap";
import { Subtitle, Title } from "../../intro/components/Text";
import { Mascot } from "../../intro/onboarding/Mascot";
import { COLORS, FONT } from "../../intro/theme";
import { TimelineScene, line } from "../timeline";

/** Close: the promise, the repo, and the wake-word callback. */
export const Outro: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const o2 = line(scene, "o2").localStart;
  const o3 = line(scene, "o3").localStart;
  const repoIn = spring({ frame: frame - (o3 + 4), fps, config: { damping: 200, mass: 0.8 } });

  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 26 }}>
        <Mascot size={150} />
        <Title size={62} delay={o2}>
          Free. Open source.{" "}
          <span style={{ color: COLORS.primary }}>Yours.</span>
        </Title>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 14,
            padding: "16px 34px",
            borderRadius: 16,
            backgroundColor: COLORS.bgCard,
            border: `1px solid rgba(255,214,10,0.4)`,
            boxShadow: `0 0 34px ${COLORS.primaryGlow}`,
            fontFamily: FONT,
            fontSize: 27,
            fontWeight: 700,
            color: COLORS.text,
            opacity: repoIn,
            transform: `translateY(${interpolate(repoIn, [0, 1], [20, 0])}px)`,
          }}
        >
          github.com/<span style={{ color: COLORS.primary }}>PersonalJarvis</span>
        </div>
        <Subtitle delay={o3 + 18} size={26}>
          Link in the description. Say the word.
        </Subtitle>
      </div>
    </SceneWrap>
  );
};
