import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../../intro/theme";

const STEPS = ["Install", "Key", "Wake", "Talk", "Act", "Delegate"] as const;

/**
 * Six-step progress rail pinned to the bottom of every chapter scene — the
 * viewer always knows where they are in the setup. `step` is 1-based; passed
 * steps render as filled dots, the current one glows, future ones stay faint.
 */
export const ProgressRail: React.FC<{ step: number; delay?: number }> = ({
  step,
  delay = 6,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  const pulse = (Math.sin(frame / 11) + 1) / 2;

  return (
    <div
      style={{
        position: "absolute",
        bottom: 40,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        gap: 34,
        opacity: s * 0.95,
        transform: `translateY(${interpolate(s, [0, 1], [14, 0])}px)`,
      }}
    >
      {STEPS.map((label, i) => {
        const idx = i + 1;
        const done = idx < step;
        const current = idx === step;
        const color = done || current ? COLORS.primary : COLORS.textFaint;
        return (
          <div
            key={label}
            style={{ display: "flex", alignItems: "center", gap: 9 }}
          >
            <div
              style={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                backgroundColor: done || current ? COLORS.primary : "transparent",
                border: `1.5px solid ${color}`,
                boxShadow: current
                  ? `0 0 ${6 + pulse * 8}px ${COLORS.primaryGlow}`
                  : "none",
                opacity: done && !current ? 0.55 : 1,
              }}
            />
            <span
              style={{
                fontFamily: FONT,
                fontSize: 15,
                fontWeight: current ? 700 : 600,
                letterSpacing: 1.2,
                textTransform: "uppercase",
                color: current ? COLORS.text : COLORS.textFaint,
              }}
            >
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
};
