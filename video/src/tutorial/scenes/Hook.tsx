import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../intro/components/SceneWrap";
import { Kicker, Subtitle, Title } from "../../intro/components/Text";
import { WaveBars } from "../../intro/components/WaveBars";
import { Mascot } from "../../intro/onboarding/Mascot";
import { COLORS, FONT } from "../../intro/theme";
import { TimelineScene, line } from "../timeline";

/**
 * Cold open: the assistant introduces ITSELF. Mascot front and center with a
 * live waveform while it speaks, then the name card and the promise.
 */
export const Hook: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const h2 = line(scene, "h2").localStart;
  const h3 = line(scene, "h3").localStart;
  const nameIn = spring({ frame: frame - h2, fps, config: { damping: 200, mass: 0.8 } });

  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 26 }}>
        <Mascot size={185} />
        <WaveBars width={170} height={30} active />
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 14,
            opacity: nameIn,
            transform: `translateY(${interpolate(nameIn, [0, 1], [24, 0])}px)`,
          }}
        >
          <Title size={78} delay={h2}>
            Personal{" "}
            <span style={{ color: COLORS.primary }}>Jarvis</span>
          </Title>
          <Kicker delay={h2 + 8}>Say it. It happens.</Kicker>
        </div>
        <Subtitle delay={h3} size={28}>
          Two minutes. That{"'"}s the whole setup.
        </Subtitle>
        <div
          style={{
            fontFamily: FONT,
            fontSize: 17,
            fontWeight: 600,
            letterSpacing: 2.5,
            textTransform: "uppercase",
            color: COLORS.textFaint,
            opacity: spring({ frame: frame - h3 - 14, fps, config: { damping: 200 } }),
          }}
        >
          Free · Open source · Your machine
        </div>
      </div>
    </SceneWrap>
  );
};
