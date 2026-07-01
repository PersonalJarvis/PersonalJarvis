import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { Ghost, GoldRule } from "../components/Ghost";
import { COLORS, DISPLAY, EASE, INTER, lerp, MONO, TYPE } from "../theme";

export const S1Intro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Ghost entrance: spring scale-up + fade. Starts with a faint presence at
  // frame 0 (opacity 0.22, scale 0.82) so the opener materializes rather than
  // fading from an empty frame.
  const gIn = spring({ frame, fps, config: { damping: 200, mass: 0.6, stiffness: 90 } });
  const gScale = 0.82 + gIn * 0.18;
  const gOpacity = lerp(frame, [0, 16], [0.22, 1], EASE.outExpo);

  const titleO = lerp(frame, [24, 44], [0, 1], EASE.outExpo);
  const titleY = lerp(frame, [24, 46], [26, 0], EASE.outExpo);
  const ruleW = lerp(frame, [40, 66], [0, 520], EASE.outQuint);
  const subO = lerp(frame, [52, 74], [0, 1], EASE.outExpo);

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ opacity: gOpacity, transform: `scale(${gScale})`, marginBottom: 26 }}>
        <Ghost size={224} glow={34} />
      </div>

      <div
        style={{
          fontFamily: MONO,
          fontSize: TYPE.eyebrow,
          letterSpacing: 6,
          textTransform: "uppercase",
          color: COLORS.gold,
          opacity: titleO,
        }}
      >
        Personal Jarvis · Memory
      </div>

      <div
        style={{
          fontFamily: DISPLAY,
          fontWeight: 700,
          fontSize: TYPE.hero,
          letterSpacing: -1.5,
          color: COLORS.headline,
          opacity: titleO,
          transform: `translateY(${titleY}px)`,
          marginTop: 10,
        }}
      >
        The Jarvis Wiki
      </div>

      <div style={{ marginTop: 20, marginBottom: 20, opacity: 1 }}>
        <GoldRule width={ruleW} />
      </div>

      <div
        style={{
          fontFamily: INTER,
          fontWeight: 400,
          fontSize: TYPE.body,
          color: COLORS.faint,
          opacity: subO,
          letterSpacing: 0.3,
        }}
      >
        How your assistant remembers what matters.
      </div>
    </AbsoluteFill>
  );
};
