import { AbsoluteFill, Img, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Ghost, GoldRule } from "../components/Ghost";
import { COLORS, EASE, lerp, MONO } from "../theme";

export const S6Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const gIn = spring({ frame, fps, config: { damping: 200, mass: 0.6, stiffness: 90 } });
  const gScale = 0.75 + gIn * 0.25;
  const gOpacity = lerp(frame, [0, 18], [0, 1], EASE.outExpo);

  const markO = lerp(frame, [20, 44], [0, 1], EASE.outExpo);
  const markY = lerp(frame, [20, 46], [22, 0], EASE.outExpo);
  const ruleW = lerp(frame, [46, 72], [0, 560], EASE.outQuint);
  const tagO = lerp(frame, [58, 82], [0, 1], EASE.outExpo);

  return (
    <AbsoluteFill
      style={{ alignItems: "center", justifyContent: "center", flexDirection: "column" }}
    >
      <div style={{ opacity: gOpacity, transform: `scale(${gScale})`, marginBottom: 20 }}>
        <Ghost size={168} glow={34} />
      </div>

      {/* real gold wordmark — screen blend drops its black bg onto the charcoal stage */}
      <Img
        src={staticFile("wordmark.png")}
        style={{
          width: 760,
          height: "auto",
          opacity: markO,
          transform: `translateY(${markY}px)`,
          mixBlendMode: "screen",
        }}
      />

      <div style={{ marginTop: 18, marginBottom: 22 }}>
        <GoldRule width={ruleW} />
      </div>

      <div
        style={{
          fontFamily: MONO,
          fontSize: 26,
          letterSpacing: 4,
          textTransform: "uppercase",
          color: COLORS.gold,
          opacity: tagO,
        }}
      >
        Local · Private · Portable · Self-healing
      </div>
    </AbsoluteFill>
  );
};
