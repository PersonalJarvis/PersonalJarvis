import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { Ghost, GoldRule } from "../components/Ghost";
import { COLORS, DISPLAY, EASE, lerp, MONO } from "../theme";

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

      {/* gold wordmark in the brand display font (Space Grotesk) — crisp, no
          raster seam, blends cleanly onto the charcoal stage. */}
      <div
        style={{
          fontFamily: DISPLAY,
          fontWeight: 700,
          fontSize: 92,
          letterSpacing: 6,
          textTransform: "uppercase",
          opacity: markO,
          transform: `translateY(${markY}px)`,
          backgroundImage: `linear-gradient(180deg, #ffe9b0 0%, ${COLORS.gold} 52%, #b98a2e 100%)`,
          WebkitBackgroundClip: "text",
          backgroundClip: "text",
          color: "transparent",
          textShadow: "0 0 34px rgba(231,196,110,0.28)",
          filter: "drop-shadow(0 2px 10px rgba(0,0,0,0.5))",
        }}
      >
        Personal Jarvis
      </div>

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
