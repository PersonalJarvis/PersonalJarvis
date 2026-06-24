import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";

/**
 * A chat message bubble that springs in at `delay`. `from="user"` aligns right
 * with a yellow fill; `from="assistant"` aligns left on a dark card.
 */
export const ChatBubble: React.FC<{
  children: React.ReactNode;
  from: "user" | "assistant";
  delay?: number;
  size?: number;
}> = ({ children, from, delay = 0, size = 30 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.7 } });
  const isUser = from === "user";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        width: "100%",
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [22, 0])}px) scale(${interpolate(
          s,
          [0, 1],
          [0.96, 1],
        )})`,
      }}
    >
      <div
        style={{
          fontFamily: FONT,
          fontSize: size,
          fontWeight: 600,
          lineHeight: 1.35,
          maxWidth: "76%",
          padding: "20px 26px",
          borderRadius: 22,
          color: isUser ? COLORS.bg : COLORS.text,
          backgroundColor: isUser ? COLORS.primary : COLORS.bgCard,
          border: isUser ? "none" : `1px solid ${COLORS.border}`,
          borderBottomRightRadius: isUser ? 6 : 22,
          borderBottomLeftRadius: isUser ? 22 : 6,
        }}
      >
        {children}
      </div>
    </div>
  );
};
