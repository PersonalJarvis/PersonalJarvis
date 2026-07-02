import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { AgentCard } from "../../intro/components/AgentCard";
import { SceneWrap } from "../../intro/components/SceneWrap";
import { SpokenCommand } from "../../intro/components/SpokenCommand";
import { Icon } from "../../intro/components/Icons";
import { COLORS, FONT } from "../../intro/theme";
import { ChapterHeader } from "../components/ChapterHeader";
import { ProgressRail } from "../components/ProgressRail";
import { TimelineScene, line } from "../timeline";

/**
 * Step 06 — delegate. One spoken sentence spawns a background agent; the
 * critic badge makes the quality gate visible; the deliverable pill shows
 * where finished work lands.
 */
export const Delegate: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const d2 = line(scene, "d2").localStart;
  const d3 = line(scene, "d3").localStart;
  const d4 = line(scene, "d4").localStart;

  const pillIn = spring({ frame: frame - (d4 + 8), fps, config: { damping: 200, mass: 0.7 } });

  return (
    <SceneWrap>
      <ChapterHeader num="06" title="Delegate" />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 30,
          marginTop: 64,
        }}
      >
        <SpokenCommand
          text="research the EV market and build me a report."
          speaker="user"
          wake="Hey Jarvis"
          delay={d2}
          size={34}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
          <AgentCard
            title="Jarvis-Agent"
            task="EV market — deep research report"
            delay={d3}
            doneAt={d4 + 4}
            width={400}
          />
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 10,
              opacity: spring({ frame: frame - (d3 + 26), fps, config: { damping: 200 } }),
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "10px 18px",
                borderRadius: 12,
                backgroundColor: COLORS.bgCard,
                border: `1px solid ${COLORS.border}`,
                fontFamily: FONT,
                fontSize: 19,
                fontWeight: 700,
                color: COLORS.text,
              }}
            >
              <Icon name="check" size={20} color={COLORS.good} />
              Critic reviews the result
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "10px 18px",
                borderRadius: 12,
                backgroundColor: COLORS.bgCard,
                border: `1px solid ${COLORS.border}`,
                fontFamily: FONT,
                fontSize: 19,
                fontWeight: 700,
                color: COLORS.text,
              }}
            >
              <Icon name="bolt" size={20} color={COLORS.primary} />
              Runs in the background
            </div>
          </div>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 26px",
            borderRadius: 999,
            backgroundColor: "rgba(74,222,128,0.10)",
            border: `1px solid ${COLORS.good}`,
            fontFamily: FONT,
            fontSize: 22,
            fontWeight: 700,
            color: COLORS.text,
            opacity: pillIn,
            transform: `translateY(${interpolate(pillIn, [0, 1], [18, 0])}px)`,
          }}
        >
          <Icon name="check" size={22} color={COLORS.good} />
          ev-market-report.pdf
          <span style={{ color: COLORS.textMuted, fontWeight: 600 }}>→ Outputs</span>
        </div>
      </div>
      <ProgressRail step={6} />
    </SceneWrap>
  );
};
