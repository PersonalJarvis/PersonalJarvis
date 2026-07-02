import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../../intro/theme";

/**
 * The tutorial's series identity: a huge outlined chapter numeral with a
 * stacked kicker + title, pinned top-left in every chapter scene. The numeral
 * is stroke-only (transparent fill) so it reads as a graphic, not a wall of
 * yellow; it slides in from the left as the scene opens.
 */
export const ChapterHeader: React.FC<{
  num: string;
  title: string;
  delay?: number;
}> = ({ num, title, delay = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.8 } });

  return (
    <div
      style={{
        position: "absolute",
        left: 96,
        top: 84,
        display: "flex",
        alignItems: "center",
        gap: 22,
        opacity: s,
        transform: `translateX(${interpolate(s, [0, 1], [-36, 0])}px)`,
      }}
    >
      <div
        style={{
          fontFamily: FONT,
          fontSize: 104,
          fontWeight: 800,
          lineHeight: 1,
          color: "transparent",
          WebkitTextStroke: `2.5px ${COLORS.primary}`,
          letterSpacing: 2,
        }}
      >
        {num}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div
          style={{
            fontFamily: FONT,
            fontSize: 17,
            letterSpacing: 5,
            textTransform: "uppercase",
            fontWeight: 700,
            color: COLORS.textFaint,
          }}
        >
          Step
        </div>
        <div
          style={{
            fontFamily: FONT,
            fontSize: 40,
            fontWeight: 800,
            color: COLORS.text,
            lineHeight: 1.05,
          }}
        >
          {title}
        </div>
      </div>
    </div>
  );
};
