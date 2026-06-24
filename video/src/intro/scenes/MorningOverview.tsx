import { interpolate, Sequence, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Kicker, Title } from "../components/Text";
import { SpokenCommand } from "../components/SpokenCommand";
import { Icon } from "../components/Icons";
import { COLORS, FONT } from "../theme";

/**
 * Tutorial lesson 1 — "what can I actually do with this?" answered with a REAL,
 * useful outcome (a daily briefing) instead of a generic "speak naturally" line.
 * Command -> a result card that builds in (calendar + mail summary) -> the spoken
 * answer. This is the anti-slop pattern: show the concrete result, don't narrate
 * the feature.
 */
const EVENTS = [
  { time: "09:30", label: "Team standup", at: 100, urgent: false },
  { time: "13:00", label: "Lunch with Sarah", at: 114, urgent: false },
  { time: "16:00", label: "Q2 report due", at: 128, urgent: true },
] as const;

export const MorningOverview: React.FC = () => {
  return (
    <SceneWrap>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 14,
          width: "100%",
        }}
      >
        <Kicker>Real example · 1</Kicker>
        <Title delay={8} size={48}>
          Your morning, in one question
        </Title>

        <Sequence from={30} layout="none">
          <SpokenCommand
            text="Hey Ruben, what’s on my plate today?"
            speaker="user"
            size={24}
            compact
          />
        </Sequence>

        <Sequence from={70} layout="none">
          <BriefingCard />
        </Sequence>

        <Sequence from={196} layout="none">
          <SpokenCommand
            text="Three meetings — and your report’s due at five."
            speaker="jarvis"
            size={24}
            compact
          />
        </Sequence>
      </div>
    </SceneWrap>
  );
};

const BriefingCard: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200 } });

  return (
    <div
      style={{
        marginTop: 22,
        width: 620,
        padding: 26,
        borderRadius: 20,
        backgroundColor: COLORS.bgCard,
        border: `1px solid ${COLORS.border}`,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [26, 0])}px)`,
      }}
    >
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <div
          style={{
            width: 42,
            height: 42,
            borderRadius: 12,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "rgba(255,214,10,0.12)",
          }}
        >
          <Icon name="calendar" size={24} color={COLORS.primary} />
        </div>
        <div style={{ fontFamily: FONT, fontSize: 22, fontWeight: 700, color: COLORS.text }}>
          Today · Tue
        </div>
      </div>

      {/* calendar rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {EVENTS.map((e) => {
          const s = interpolate(frame, [e.at, e.at + 8], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          return (
            <div
              key={e.label}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 18,
                opacity: s,
                transform: `translateX(${interpolate(s, [0, 1], [-16, 0])}px)`,
              }}
            >
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 19,
                  fontWeight: 700,
                  color: e.urgent ? COLORS.primary : COLORS.textMuted,
                  width: 66,
                }}
              >
                {e.time}
              </span>
              <span style={{ fontFamily: FONT, fontSize: 20, fontWeight: 600, color: COLORS.text }}>
                {e.label}
              </span>
              {e.urgent && (
                <span
                  style={{
                    marginLeft: "auto",
                    fontFamily: FONT,
                    fontSize: 13,
                    fontWeight: 800,
                    letterSpacing: 1.5,
                    textTransform: "uppercase",
                    color: COLORS.primary,
                  }}
                >
                  due
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* mail summary */}
      <MailRow appearAt={158} />
    </div>
  );
};

const MailRow: React.FC<{ appearAt: number }> = ({ appearAt }) => {
  const frame = useCurrentFrame();
  const s = interpolate(frame, [appearAt, appearAt + 10], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        marginTop: 18,
        paddingTop: 16,
        borderTop: `1px solid ${COLORS.border}`,
        display: "flex",
        alignItems: "center",
        gap: 14,
        opacity: s,
      }}
    >
      <Icon name="mail" size={22} color={COLORS.textMuted} />
      <span style={{ fontFamily: FONT, fontSize: 19, fontWeight: 600, color: COLORS.text }}>
        2 unread
      </span>
      <span style={{ fontFamily: FONT, fontSize: 17, fontWeight: 500, color: COLORS.textMuted }}>
        · both flagged important, summarised
      </span>
    </div>
  );
};
