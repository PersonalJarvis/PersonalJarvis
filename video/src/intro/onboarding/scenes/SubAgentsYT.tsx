import { interpolate, Sequence, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Title } from "../../components/Text";
import { SpokenCommand } from "../../components/SpokenCommand";
import { Icon } from "../../components/Icons";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

const STEPS = [
  { label: "Searching the web for sources", at: 60 },
  { label: "Reading & analysing 40+ pages", at: 140 },
  { label: "Writing the report", at: 220 },
  { label: "Checking its own work", at: 290 },
] as const;
const DONE_AT = 320;

/** Feature 02 — hand a hard job to a self-checking background worker. */
export const SubAgentsYT: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const cmd = line(scene, "sa_3");
  const commandAt = cmd.localStart;
  const jobAt = cmd.localStart + cmd.dur - 10;

  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14, width: "100%" }}>
        <Kicker>Feature 02 · Sub-Agents</Kicker>
        <Title delay={8} size={50}>
          Hand off the hard stuff
        </Title>

        <Sequence from={commandAt} layout="none">
          <SpokenCommand text="Write me a deep-dive report on the global EV market." speaker="user" size={24} compact wake="Hey Ruben" />
        </Sequence>

        <Sequence from={jobAt} layout="none">
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
  const allDone = frame >= DONE_AT;
  const progress = allDone
    ? 1
    : interpolate(frame, [10, DONE_AT], [0.04, 0.96], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <div
      style={{
        marginTop: 18,
        width: 680,
        padding: 26,
        borderRadius: 20,
        backgroundColor: COLORS.bgCard,
        border: `1px solid ${allDone ? COLORS.good : COLORS.border}`,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [26, 0])}px)`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 20 }}>
        <div
          style={{
            width: 46,
            height: 46,
            borderRadius: 13,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: allDone ? "rgba(74,222,128,0.15)" : "rgba(255,214,10,0.12)",
          }}
        >
          <Icon name="robot" size={26} color={allDone ? COLORS.good : COLORS.primary} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: FONT, fontSize: 22, fontWeight: 700, color: COLORS.text }}>
            Deep-research agent
          </div>
          <div style={{ fontFamily: FONT, fontSize: 15, fontWeight: 500, color: COLORS.textMuted }}>
            {allDone ? "Done — delivered to you" : "Working in the background…"}
          </div>
        </div>
        <span style={{ fontFamily: FONT, fontSize: 14, fontWeight: 700, color: COLORS.textFaint }}>
          {allDone ? "100%" : `${Math.round(progress * 100)}%`}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 20 }}>
        {STEPS.map((s) => {
          const done = frame >= s.at;
          return (
            <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div
                style={{
                  width: 23,
                  height: 23,
                  borderRadius: "50%",
                  border: `2px solid ${done ? COLORS.good : COLORS.borderStrong}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: done ? "rgba(74,222,128,0.15)" : "transparent",
                }}
              >
                {done && <Icon name="check" size={13} color={COLORS.good} />}
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

      {allDone && (
        <div
          style={{
            marginTop: 18,
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "13px 18px",
            borderRadius: 12,
            backgroundColor: "rgba(74,222,128,0.10)",
            border: `1px solid ${COLORS.good}`,
          }}
        >
          <Icon name="book" size={22} color={COLORS.good} />
          <span style={{ fontFamily: FONT, fontSize: 18, fontWeight: 700, color: COLORS.text }}>
            ev-market-report.pdf
          </span>
          <span style={{ fontFamily: FONT, fontSize: 15, color: COLORS.textMuted }}>· 24 pages</span>
          <span style={{ marginLeft: "auto", fontFamily: FONT, fontSize: 14, color: COLORS.textMuted }}>
            in Outputs
          </span>
        </div>
      )}
    </div>
  );
};
