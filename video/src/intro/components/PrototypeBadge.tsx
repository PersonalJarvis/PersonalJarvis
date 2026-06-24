import { interpolate, useCurrentFrame } from "remotion";
import { COLORS, FONT } from "../theme";

/**
 * Persistent "personal prototype" disclaimer pinned to the top, visible across
 * every scene. Intentionally self-contained so it can be removed in one line
 * (drop it from IntroVideo) once the video is finalised.
 */
export const PrototypeBadge: React.FC = () => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [4, 24], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        position: "absolute",
        top: 26,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        opacity,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 9,
          padding: "8px 18px",
          borderRadius: 999,
          backgroundColor: "rgba(10,10,10,0.62)",
          border: `1px solid ${COLORS.primary}`,
          fontFamily: FONT,
          fontSize: 15,
          fontWeight: 700,
          letterSpacing: 2.5,
          textTransform: "uppercase",
          color: COLORS.primary,
        }}
      >
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            backgroundColor: COLORS.primary,
            display: "inline-block",
          }}
        />
        Personal prototype
      </div>
    </div>
  );
};
