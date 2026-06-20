import { interpolate, Sequence, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Kicker, Title } from "../components/Text";
import { SpokenCommand } from "../components/SpokenCommand";
import { Icon } from "../components/Icons";
import { COLORS, FONT } from "../theme";

const STEPS = [
  { label: "Searching the web for sources", at: 120 },
  { label: "Reading & analysing 40+ pages", at: 196 },
  { label: "Writing the report", at: 270 },
] as const;

export const SubAgents: React.FC = () => {
  return (
    <SceneWrap>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 16,
          width: "100%",
        }}
      >
        <Kicker>Background agent</Kicker>
        <Title delay={8} size={54}>
          Hand off the hard stuff
        </Title>

        <Sequence from={28} layout="none">
          <SpokenCommand
            text="Write me a deep-dive report on the global EV market."
            speaker="user"
            size={25}
            compact
          />
        </Sequence>

        <Sequence from={58} layout="none">
          <AgentJob />
        </Sequence>
      </div>
    </SceneWrap>
  );
};

const AgentJob: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200 } });
  const allDone = frame >= 286;
  const progress = allDone
    ? 1
    : interpolate(frame, [10, 286], [0.04, 0.95], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });

  return (
    <div
      style={{
        marginTop: 26,
        width: 660,
        padding: 28,
        borderRadius: 20,
        backgroundColor: COLORS.bgCard,
        border: `1px solid ${allDone ? COLORS.good : COLORS.border}`,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [26, 0])}px)`,
      }}
    >
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 22 }}>
        <div
          style={{
            width: 48,
            height: 48,
            borderRadius: 13,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: allDone ? "rgba(74,222,128,0.15)" : "rgba(255,214,10,0.12)",
          }}
        >
          <Icon name="robot" size={28} color={allDone ? COLORS.good : COLORS.primary} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: FONT, fontSize: 23, fontWeight: 700, color: COLORS.text }}>
            Deep-research agent
          </div>
          <div style={{ fontFamily: FONT, fontSize: 16, fontWeight: 500, color: COLORS.textMuted }}>
            {allDone ? "Done — delivered to you" : "Working in the background…"}
          </div>
        </div>
      </div>

      {/* steps */}
      <div style={{ display: "flex", flexDirection: "column", gap: 13, marginBottom: 22 }}>
        {STEPS.map((s) => {
          const done = frame >= s.at;
          return (
            <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: "50%",
                  border: `2px solid ${done ? COLORS.good : COLORS.borderStrong}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: done ? "rgba(74,222,128,0.15)" : "transparent",
                }}
              >
                {done && <Icon name="check" size={14} color={COLORS.good} />}
              </div>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 18,
                  fontWeight: 500,
                  color: done ? COLORS.text : COLORS.textMuted,
                }}
              >
                {s.label}
              </span>
            </div>
          );
        })}
      </div>

      {/* progress */}
      <div style={{ height: 8, borderRadius: 8, backgroundColor: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
        <div
          style={{
            width: `${progress * 100}%`,
            height: "100%",
            borderRadius: 8,
            backgroundColor: allDone ? COLORS.good : COLORS.primary,
          }}
        />
      </div>

      {/* deliverable */}
      {allDone && (
        <div
          style={{
            marginTop: 20,
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "14px 18px",
            borderRadius: 12,
            backgroundColor: "rgba(74,222,128,0.10)",
            border: `1px solid ${COLORS.good}`,
          }}
        >
          <Icon name="book" size={24} color={COLORS.good} />
          <span style={{ fontFamily: FONT, fontSize: 19, fontWeight: 700, color: COLORS.text }}>
            ev-market-report.pdf
          </span>
          <span style={{ fontFamily: FONT, fontSize: 16, color: COLORS.textMuted }}>· 24 pages</span>
        </div>
      )}
    </div>
  );
};
