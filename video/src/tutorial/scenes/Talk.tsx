import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../intro/components/SceneWrap";
import { SpokenCommand } from "../../intro/components/SpokenCommand";
import { COLORS, FONT } from "../../intro/theme";
import { ChapterHeader } from "../components/ChapterHeader";
import { ProgressRail } from "../components/ProgressRail";
import { TimelineScene, line } from "../timeline";

const LANGS = ["English", "Deutsch", "Español"] as const;

/**
 * Step 04 — just talk. The command ribbon: your sentence front and center,
 * the spoken answer beneath it, and the language chips as the multilingual
 * proof point.
 */
export const Talk: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t2 = line(scene, "t2").localStart;
  const t3 = line(scene, "t3").localStart;
  const t4 = line(scene, "t4").localStart;

  return (
    <SceneWrap>
      <ChapterHeader num="04" title="Just talk" />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 34,
          marginTop: 70,
        }}
      >
        <SpokenCommand
          text="What's on my calendar this afternoon?"
          speaker="user"
          wake="Hey Jarvis"
          delay={t2}
          size={38}
        />
        <SpokenCommand
          text="Two meetings. Design review at three — and a call at five."
          speaker="jarvis"
          jarvisSrc="jarvis-logo.png"
          delay={t3}
          size={30}
        />
        <div style={{ display: "flex", gap: 14 }}>
          {LANGS.map((label, i) => {
            const s = spring({
              frame: frame - (t4 + 26 + i * 8),
              fps,
              config: { damping: 200, mass: 0.7 },
            });
            return (
              <div
                key={label}
                style={{
                  padding: "8px 20px",
                  borderRadius: 999,
                  backgroundColor: COLORS.bgCard,
                  border: `1px solid ${COLORS.border}`,
                  fontFamily: FONT,
                  fontSize: 19,
                  fontWeight: 700,
                  color: COLORS.textMuted,
                  opacity: s,
                  transform: `translateY(${interpolate(s, [0, 1], [14, 0])}px)`,
                }}
              >
                {label}
              </div>
            );
          })}
        </div>
      </div>
      <ProgressRail step={4} />
    </SceneWrap>
  );
};
