import { interpolate, useCurrentFrame } from "remotion";
import { COLORS } from "../theme";

export interface CursorKey {
  frame: number;
  x: number;
  y: number;
  click?: boolean;
}

/**
 * An arrow cursor that moves through keyframes (eased) and shows a click pulse.
 * Coordinates are relative to the parent container.
 */
export const Cursor: React.FC<{ keys: CursorKey[] }> = ({ keys }) => {
  const frame = useCurrentFrame();
  const frames = keys.map((k) => k.frame);
  const x = interpolate(frame, frames, keys.map((k) => k.x), {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const y = interpolate(frame, frames, keys.map((k) => k.y), {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // click pulse: find the nearest click keyframe within a short window
  let clickP = 0;
  for (const k of keys) {
    if (k.click) {
      const d = frame - k.frame;
      if (d >= 0 && d < 18) clickP = Math.max(clickP, interpolate(d, [0, 18], [1, 0]));
    }
  }

  return (
    <div style={{ position: "absolute", left: x, top: y, pointerEvents: "none" }}>
      {clickP > 0 && (
        <div
          style={{
            position: "absolute",
            left: -4,
            top: -4,
            width: 8 + (1 - clickP) * 54,
            height: 8 + (1 - clickP) * 54,
            transform: "translate(-50%, -50%)",
            borderRadius: "50%",
            border: `3px solid ${COLORS.primary}`,
            opacity: clickP * 0.9,
          }}
        />
      )}
      <svg width="34" height="34" viewBox="0 0 24 24" style={{ display: "block" }}>
        <path
          d="M4 2 L4 20 L9 15 L12.5 22 L15 21 L11.5 14 L18 14 Z"
          fill={COLORS.text}
          stroke={COLORS.bg}
          strokeWidth="1.2"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
};
