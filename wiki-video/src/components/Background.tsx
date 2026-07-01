import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS } from "../theme";

/**
 * The persistent stage: warm charcoal base (never pure black), a soft radial
 * vignette, a faint dot grid for depth, and ONE slow-drifting gold glow so the
 * frame breathes. Rendered once behind every scene, so there is never an empty
 * black frame at a scene boundary.
 */
export const Background: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const t = frame / fps;

  // One low, slow gold aura that drifts — the single accent light in the room.
  const gx = 50 + Math.sin(t * 0.18) * 12;
  const gy = 34 + Math.cos(t * 0.14) * 8;

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      {/* faint dot grid */}
      <AbsoluteFill
        style={{
          backgroundImage: `radial-gradient(${COLORS.hairline} 1px, transparent 1px)`,
          backgroundSize: "46px 46px",
          opacity: 0.5,
          maskImage:
            "radial-gradient(120% 100% at 50% 40%, #000 55%, transparent 100%)",
          WebkitMaskImage:
            "radial-gradient(120% 100% at 50% 40%, #000 55%, transparent 100%)",
        }}
      />
      {/* slow gold aura */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(${width * 0.5}px ${
            height * 0.5
          }px at ${gx}% ${gy}%, rgba(231,196,110,0.10), transparent 60%)`,
        }}
      />
      {/* vignette */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(130% 120% at 50% 45%, transparent 55%, rgba(0,0,0,0.55) 100%)",
        }}
      />
    </AbsoluteFill>
  );
};
