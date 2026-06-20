import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";

/** Small uppercase label above a title. */
export const Kicker: React.FC<{ children: React.ReactNode; delay?: number }> = ({
  children,
  delay = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        fontFamily: FONT,
        fontSize: 22,
        letterSpacing: 6,
        textTransform: "uppercase",
        fontWeight: 700,
        color: COLORS.primary,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [12, 0])}px)`,
      }}
    >
      {children}
    </div>
  );
};

/** Large headline that springs up into place. */
export const Title: React.FC<{
  children: React.ReactNode;
  delay?: number;
  size?: number;
  align?: "center" | "left";
}> = ({ children, delay = 0, size = 76, align = "center" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.8 } });
  return (
    <div
      style={{
        fontFamily: FONT,
        fontSize: size,
        lineHeight: 1.08,
        fontWeight: 800,
        color: COLORS.text,
        textAlign: align,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [28, 0])}px)`,
        maxWidth: 1040,
      }}
    >
      {children}
    </div>
  );
};

/** Muted supporting line. */
export const Subtitle: React.FC<{
  children: React.ReactNode;
  delay?: number;
  size?: number;
  align?: "center" | "left";
}> = ({ children, delay = 0, size = 30, align = "center" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        fontFamily: FONT,
        fontSize: size,
        lineHeight: 1.4,
        fontWeight: 500,
        color: COLORS.textMuted,
        textAlign: align,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [16, 0])}px)`,
        maxWidth: 880,
      }}
    >
      {children}
    </div>
  );
};
