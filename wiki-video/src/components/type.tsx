import { useCurrentFrame } from "remotion";
import { COLORS, DISPLAY, EASE, INTER, lerp, MONO, TYPE } from "../theme";

/** Mono uppercase gold eyebrow with a leading tick, slides in from the left. */
export const Eyebrow: React.FC<{ children: React.ReactNode; delay?: number }> = ({
  children,
  delay = 0,
}) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 12], [0, 1], EASE.outExpo);
  const x = lerp(frame, [delay, delay + 14], [-18, 0], EASE.outExpo);
  return (
    <div
      style={{
        fontFamily: MONO,
        fontSize: TYPE.eyebrow,
        letterSpacing: 3,
        textTransform: "uppercase",
        color: COLORS.gold,
        display: "flex",
        alignItems: "center",
        gap: 12,
        opacity: o,
        transform: `translateX(${x}px)`,
      }}
    >
      <span
        style={{
          width: 26,
          height: 2,
          background: COLORS.gold,
          display: "inline-block",
        }}
      />
      {children}
    </div>
  );
};

/** Display headline. `gold` picks specific words to accent (by index array). */
export const Headline: React.FC<{
  children: React.ReactNode;
  size?: number;
  delay?: number;
}> = ({ children, size = TYPE.h1, delay = 0 }) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 16], [0, 1], EASE.outExpo);
  const y = lerp(frame, [delay, delay + 18], [22, 0], EASE.outExpo);
  return (
    <div
      style={{
        fontFamily: DISPLAY,
        fontWeight: 600,
        fontSize: size,
        lineHeight: 1.08,
        letterSpacing: -0.5,
        color: COLORS.headline,
        opacity: o,
        transform: `translateY(${y}px)`,
      }}
    >
      {children}
    </div>
  );
};

export const Body: React.FC<{
  children: React.ReactNode;
  size?: number;
  delay?: number;
  color?: string;
  maxWidth?: number;
}> = ({ children, size = TYPE.body, delay = 0, color = COLORS.body, maxWidth }) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 16], [0, 1], EASE.outExpo);
  const y = lerp(frame, [delay, delay + 18], [14, 0], EASE.outExpo);
  return (
    <div
      style={{
        fontFamily: INTER,
        fontWeight: 400,
        fontSize: size,
        lineHeight: 1.5,
        color,
        opacity: o,
        transform: `translateY(${y}px)`,
        maxWidth,
      }}
    >
      {children}
    </div>
  );
};

export const Gold: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span style={{ color: COLORS.gold }}>{children}</span>
);
