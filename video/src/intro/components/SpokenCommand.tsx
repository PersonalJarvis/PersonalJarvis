import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";
import { Icon } from "./Icons";
import { WaveBars } from "./WaveBars";

/**
 * Represents a SPOKEN line, not a chat message — a mic (user) or the orb
 * (Jarvis) plus a live waveform with large quoted text. This is the core of the
 * "you talk to it, you don't type" message.
 *
 * `compact` lays it out as a single horizontal pill (mic · waveform · text) for
 * action scenes where it just triggers a demo and vertical space is tight.
 */
export const SpokenCommand: React.FC<{
  text: string;
  speaker?: "user" | "jarvis";
  delay?: number;
  size?: number;
  compact?: boolean;
}> = ({ text, speaker = "user", delay = 0, size = 40, compact = false }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.7 } });
  const isUser = speaker === "user";

  const dim = compact ? 40 : 52;
  const badge = isUser ? (
    <div
      style={{
        width: dim,
        height: dim,
        borderRadius: "50%",
        backgroundColor: "rgba(255,214,10,0.12)",
        border: `1px solid ${COLORS.primary}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      <Icon name="mic" size={compact ? 20 : 26} color={COLORS.primary} />
    </div>
  ) : (
    <div
      style={{
        width: dim,
        height: dim,
        borderRadius: "50%",
        backgroundColor: COLORS.bgElevated,
        border: `1px solid ${COLORS.primary}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        boxShadow: `0 0 16px ${COLORS.primaryGlow}`,
      }}
    >
      <Img src={staticFile("jarvis-mark.png")} style={{ width: dim * 0.74, height: dim * 0.74 }} />
    </div>
  );

  if (compact) {
    return (
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 16,
          padding: "12px 22px 12px 14px",
          borderRadius: 999,
          backgroundColor: COLORS.bgCard,
          border: `1px solid ${isUser ? "rgba(255,214,10,0.35)" : COLORS.border}`,
          opacity: s,
          transform: `translateY(${interpolate(s, [0, 1], [18, 0])}px)`,
        }}
      >
        {badge}
        <WaveBars width={70} height={26} active />
        <span
          style={{
            fontFamily: FONT,
            fontSize: size,
            fontWeight: 700,
            color: COLORS.text,
            whiteSpace: "nowrap",
          }}
        >
          “{text}”
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [22, 0])}px)`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 18,
        width: "100%",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        {badge}
        <WaveBars width={132} height={34} active />
      </div>
      <div
        style={{
          fontFamily: FONT,
          fontSize: size,
          fontWeight: 700,
          lineHeight: 1.25,
          color: isUser ? COLORS.text : COLORS.textMuted,
          textAlign: "center",
          maxWidth: 1000,
        }}
      >
        “{text}”
      </div>
    </div>
  );
};
