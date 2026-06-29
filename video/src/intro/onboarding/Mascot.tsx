import { Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { COLORS } from "../theme";

/**
 * The REAL Jarvis brand mark — the Gigi ghost mascot (public/jarvis-logo.png),
 * NOT the gold four-point star (which the maintainer rejects as "AI slop").
 * Renders with a soft yellow glow and a gentle float/pulse.
 */
export const Mascot: React.FC<{ size?: number; float?: boolean }> = ({
  size = 160,
  float = true,
}) => {
  const frame = useCurrentFrame();
  const pulse = (Math.sin(frame / 16) + 1) / 2; // 0..1
  const bob = float ? Math.sin(frame / 22) * 4 : 0;
  const glow = interpolate(pulse, [0, 1], [0.35, 0.7]);

  return (
    <div
      style={{
        position: "relative",
        width: size,
        height: size,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        transform: `translateY(${bob}px)`,
      }}
    >
      <div
        style={{
          position: "absolute",
          width: size * 1.25,
          height: size * 1.25,
          borderRadius: "50%",
          background: `radial-gradient(circle, rgba(255,214,10,${0.3 * glow}), rgba(255,214,10,0) 64%)`,
        }}
      />
      <Img
        src={staticFile("jarvis-logo.png")}
        style={{
          width: size,
          height: size,
          objectFit: "contain",
          filter: `drop-shadow(0 0 ${8 + glow * 16}px ${COLORS.primaryGlow})`,
        }}
      />
    </div>
  );
};
