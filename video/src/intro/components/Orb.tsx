import { Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { COLORS } from "../theme";

/**
 * The Jarvis presence — the REAL brand mark (public/jarvis-mark.png, the
 * four-point star in a ring) with a soft glow and, when `active`, gentle
 * listening rings. Not an invented orb; this is the official symbol.
 */
export const Orb: React.FC<{ size?: number; active?: boolean }> = ({
  size = 200,
  active = true,
}) => {
  const frame = useCurrentFrame();
  const pulse = (Math.sin(frame / 14) + 1) / 2; // 0..1
  const amp = active ? 1 : 0.5;
  const logoScale = 1 + pulse * 0.05 * amp;
  const glow = interpolate(pulse, [0, 1], [0.4, 0.85]) * amp;

  return (
    <div
      style={{
        position: "relative",
        width: size,
        height: size,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {/* subtle listening rings — only while active */}
      {active &&
        [0, 1].map((i) => {
          const local = (frame / 30 + i * 0.8) % 1.8;
          const scale = interpolate(local, [0, 1.8], [0.85, 1.7]);
          const op = interpolate(local, [0, 0.25, 1.8], [0, 0.22, 0]);
          return (
            <div
              key={i}
              style={{
                position: "absolute",
                width: size,
                height: size,
                borderRadius: "50%",
                border: `2px solid ${COLORS.primary}`,
                transform: `scale(${scale})`,
                opacity: op,
              }}
            />
          );
        })}
      {/* glow so the mark lifts off the dark background */}
      <div
        style={{
          position: "absolute",
          width: size * 1.45,
          height: size * 1.45,
          borderRadius: "50%",
          background: `radial-gradient(circle, rgba(255,214,10,${0.38 * glow}), rgba(255,214,10,0) 62%)`,
        }}
      />
      {/* the real brand mark */}
      <Img
        src={staticFile("jarvis-mark.png")}
        style={{
          width: size,
          height: size,
          transform: `scale(${logoScale})`,
          filter: `drop-shadow(0 0 ${10 + glow * 18}px ${COLORS.primaryGlow})`,
        }}
      />
    </div>
  );
};
