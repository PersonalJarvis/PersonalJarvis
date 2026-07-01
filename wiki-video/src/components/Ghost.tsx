import { Img, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { bob, breathe, COLORS } from "../theme";

/**
 * The real brand logo: the "Gigi" bit-ghost (vector). Idle bob + tiny breathing
 * scale so it never freezes, plus a soft gold glow. Always rendered from the
 * real asset — never a placeholder. Uses the SVG for crisp scaling.
 */
export const Ghost: React.FC<{
  size: number;
  glow?: number;
  bobAmp?: number;
}> = ({ size, glow = 26, bobAmp = 6 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const y = bob(frame, fps, 0.5, bobAmp);
  const s = breathe(frame, fps, 0.6, 0.014);

  return (
    <div
      style={{
        width: size,
        height: size,
        transform: `translateY(${y}px) scale(${s})`,
        filter: `drop-shadow(0 0 ${glow}px rgba(231,196,110,0.45)) drop-shadow(0 10px 30px rgba(0,0,0,0.55))`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Img
        src={staticFile("gigi.svg")}
        style={{ width: size, height: size, display: "block" }}
      />
    </div>
  );
};

/** A thin gold hairline underline, optionally mask-wiped in. */
export const GoldRule: React.FC<{ width: number; opacity?: number }> = ({
  width,
  opacity = 1,
}) => (
  <div
    style={{
      width,
      height: 2,
      opacity,
      background: `linear-gradient(90deg, transparent, ${COLORS.gold}, transparent)`,
      borderRadius: 2,
    }}
  />
);
